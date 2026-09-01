"""Thumbnail rasterisation: asynchronous, cached, page-targeted.

Phase 6b (docs/GUI_PLAN.md §3.5). Before this, `_render_tab` cleared
the grid and re-rendered **every page from disk, synchronously**, after
every operation, undo, redo and zoom step. Measured on a 500-page
document on this machine:

    default zoom (120x160)   ~1.1 s per refresh   (1.6 ms / page)
    max zoom     (720x960)   ~2.2 s per refresh   (4.7 ms / page)

So rotating a single page of a long document paid the whole 2.2 s, on
the UI thread. Phase 6's per-edit commits (one `Operation` per
highlight) would have made that the cost of every mark.

Three things fix it, and they only work together:

1. **Async delivery.** `QPdfPageRenderer.requestPage()` queues a render
   and returns immediately - measured at 0.1 ms to submit 40 pages
   versus 18 ms to render them - and results arrive on the
   `pageRendered` signal. Note this buys *responsiveness, not
   throughput*: total render time is unchanged (measured 18 ms sync vs
   19 ms async for the same 40 pages). The event loop simply stops
   being blocked while it happens.
2. **A cache**, so unchanged pages are never re-rasterised.
3. **Targeted invalidation** via `Operation.affected_pages()`, which is
   what makes the cache actually hit. The cache is keyed by page and
   size and lives on the tab, deliberately *not* keyed by the working
   file's path: `allocate_working_path` mints a fresh `mkstemp` name for
   every operation, so a path-keyed cache would miss 100% of the time
   after any edit.

Items appear synchronously with a correctly-sized blank page as their
icon, and each real page replaces its placeholder as it arrives. That
keeps `count()`, page-number data and `QIcon.actualSize()` correct the
instant `render()` returns - the properties the rest of the window and
the test suite rely on - while the pixels stream in.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QObject, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument, QPdfPageRenderer
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from core.logging_config import get_logger

log = get_logger(__name__)

#: Translation context. Kept as "MainWindow" rather than "Rendering" so
#: the user-visible strings moved out of main_window.py keep the context
#: they were already collected under (SPEC.md 6.2 - i18n readiness).
_TR_CONTEXT = "MainWindow"

#: Cache budget in bytes. A bound is not optional: one page at the
#: 720x960 zoom ceiling is 720*960*4 = 2.7 MB, so caching a 500-page
#: document at max zoom unbounded would hold ~1.4 GB.
#:
#: Measured consequence of the 192 MB budget, on a 500-page document:
#:
#:   default 120x160   75 KB/page   budget holds 2621   all 500 cached
#:   max     720x960   2.7 MB/page  budget holds   72   72 of 500 cached
#:
#: So at the default zoom an edit re-renders exactly the page it
#: touched (1 of 500, 2 ms). At maximum zoom the document cannot fit,
#: LRU eviction wins, and an edit re-renders ~428 pages again - in the
#: background rather than blocking, but still real work.
#:
#: The proper fix is to request only the pages actually on screen plus
#: a small lookahead, which is what docs/GUI_PLAN.md §3.2 already
#: specifies for the 6c viewer; thumbnails render eagerly today.
#: Raising the budget instead would only move the cliff, since page
#: cost grows with the square of the zoom.
_CACHE_BUDGET_BYTES = 192 * 1024 * 1024

_CacheKey = tuple[int, int, int]  # (page number, width, height)


class PixmapCache:
    """Byte-budgeted LRU cache of rendered pages, keyed by
    `(page, width, height)`.

    Shared by the thumbnail grid and the page viewer. Deliberately not
    keyed by the working file's path: `allocate_working_path` mints a
    fresh `mkstemp` name for every operation, so a path-keyed cache
    would miss 100% of the time after any edit. Invalidation is
    explicit instead, driven by `Operation.affected_pages()`.
    """

    def __init__(self, budget_bytes: int = _CACHE_BUDGET_BYTES) -> None:
        self._budget = budget_bytes
        self._entries: OrderedDict[_CacheKey, QPixmap] = OrderedDict()
        self._bytes = 0

    def get(self, key: _CacheKey) -> QPixmap | None:
        pixmap = self._entries.get(key)
        if pixmap is not None:
            self._entries.move_to_end(key)  # LRU: most recently used
        return pixmap

    def put(self, key: _CacheKey, pixmap: QPixmap) -> None:
        if key in self._entries:
            self.drop(key)
        self._entries[key] = pixmap
        self._bytes += _pixmap_bytes(pixmap)
        while self._bytes > self._budget and len(self._entries) > 1:
            self.drop(next(iter(self._entries)))  # oldest first

    def drop(self, key: _CacheKey) -> None:
        pixmap = self._entries.pop(key, None)
        if pixmap is not None:
            self._bytes -= _pixmap_bytes(pixmap)

    def invalidate(self, pages: list[int] | None) -> None:
        """Drop `pages` (1-based), or everything when None - which is
        what `Operation.affected_pages()` reports for anything that
        adds, removes or reorders pages, since that shifts every later
        page's identity."""
        if pages is None:
            self._entries.clear()
            self._bytes = 0
            return
        targets = set(pages)
        for key in [k for k in self._entries if k[0] in targets]:
            self.drop(key)

    def __len__(self) -> int:
        return len(self._entries)


def _pixmap_bytes(pixmap: QPixmap) -> int:
    return pixmap.width() * pixmap.height() * 4  # ARGB32


def composite_on_white(rendered: QImage, size: QSize) -> QImage:
    """QtPdf leaves any unpainted area of a page fully transparent
    (alpha=0) rather than opaque white - most visible on blank or
    near-empty pages. Composite onto a white backdrop so a page always
    reads as a page, not as "nothing" wherever the source PDF painted
    nothing."""
    page_image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    page_image.fill(Qt.GlobalColor.white)
    painter = QPainter(page_image)
    painter.drawImage(0, 0, rendered)
    painter.end()
    return page_image


def blank_page(size: QSize) -> QPixmap:
    """A blank page at exactly `size`, used until a real render arrives.
    Sized precisely so an item's `QIcon.actualSize()` and a canvas
    page's geometry are already correct before its pixels exist."""
    pixmap = QPixmap(size)
    pixmap.fill(Qt.GlobalColor.white)
    return pixmap


class ThumbnailRenderer(QObject):
    """Renders one document's pages into one `QListWidget`.

    Owned by a `DocumentTab`, so each open document gets its own cache
    and its own `QPdfDocument`.
    """

    #: Emitted when every requested page has been delivered. Tests and
    #: benchmarks wait on this; the window does not need it.
    idle = Signal()

    def __init__(self, thumbnail_list: QListWidget, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._list = thumbnail_list
        # Unparented and held only by these attributes, so that clearing
        # them destroys the objects there and then under CPython
        # refcounting. This is load-bearing on Windows: a QPdfDocument
        # keeps the working file open, and Windows refuses to overwrite
        # or unlink an open file, which breaks the secure wipe in
        # core/security/secure_delete.py. QPdfDocument.close() is *not*
        # enough - verified via /proc/self/fd when the same bug was
        # fixed in gui/placement_canvas.py: the descriptor survives
        # close() and only goes away on destruction.
        self._pdf: QPdfDocument | None = None
        self._renderer: QPdfPageRenderer | None = None
        self._path: Path | None = None

        self._cache = PixmapCache()
        #: page number -> the item currently showing it, for this pass only.
        self._items: dict[int, QListWidgetItem] = {}
        self._pending: set[int] = set()
        #: Bumped on every render pass; results from an older pass are
        #: dropped rather than written into items that no longer exist.
        self._generation = 0
        self._size = QSize()

    # --- public API -------------------------------------------------------

    def render(self, path: Path, size: QSize) -> None:
        """Rebuild the grid for `path` at `size`.

        Returns as soon as the items exist. Cached pages already carry
        their real image; the rest are requested and fill in.
        """
        self._generation += 1
        self._pending.clear()
        self._items.clear()
        self._size = QSize(size)
        self._list.clear()

        if not self._ensure_document(path):
            self.idle.emit()
            return
        assert self._pdf is not None

        placeholder = QIcon(blank_page(size))
        wanted: list[int] = []
        for page in range(1, self._pdf.pageCount() + 1):
            cached = self._cache.get((page, size.width(), size.height()))
            item = QListWidgetItem(
                QIcon(cached) if cached is not None else placeholder,
                QCoreApplication.translate(_TR_CONTEXT, "Page {0}").format(page),
            )
            # Which page this item represents in the *current* working
            # document, independent of drag position - read back in
            # visual order by _apply_thumbnail_reorder to build a
            # ReorderPagesOperation's page_order.
            item.setData(Qt.ItemDataRole.UserRole, page)
            self._list.addItem(item)
            self._items[page] = item
            if cached is None:
                wanted.append(page)

        if not wanted:
            self.idle.emit()
            return
        self._pending = set(wanted)
        renderer = self._ensure_renderer()
        for page in wanted:
            renderer.requestPage(page - 1, size)  # QtPdf is 0-based

    def invalidate(self, pages: list[int] | None) -> None:
        """Drop cached images for `pages` (1-based), or all of them when
        `pages` is None. See `PixmapCache.invalidate`."""
        self._cache.invalidate(pages)

    def release(self) -> None:
        """Close the document and drop every OS handle on it.

        Must be called before the tab's session temp dir is wiped - see
        the ownership note in `__init__`. Idempotent.
        """
        if self._renderer is not None:
            # Disconnect first: a MultiThreaded render already in flight
            # would otherwise try to deliver into a half-torn-down object.
            with suppress(RuntimeError, TypeError):  # already gone / never connected
                self._renderer.pageRendered.disconnect(self._on_page_rendered)
            self._renderer = None
        if self._pdf is not None:
            self._pdf.close()
            self._pdf = None
        self._path = None
        self._pending.clear()
        self._items.clear()

    def wait_until_idle(self, timeout_ms: int = 10_000) -> bool:
        """Spin the event loop until every pending page has arrived.

        For tests and benchmarks - the window itself never blocks. True
        if rendering finished, False on timeout.
        """
        deadline = QDeadlineTimer(timeout_ms)
        while self._pending and not deadline.hasExpired():
            QCoreApplication.processEvents()
        return not self._pending

    @property
    def is_idle(self) -> bool:
        return not self._pending

    # --- internals --------------------------------------------------------

    def _ensure_document(self, path: Path) -> bool:
        """Load `path` if it isn't already loaded. False if it won't
        open, matching the previous behaviour of leaving the grid empty
        and logging."""
        if self._pdf is not None and self._path == path:
            return True
        self.release()
        pdf = QPdfDocument()
        if pdf.load(str(path)) != QPdfDocument.Error.None_:
            log.error("Could not load PDF for thumbnail rendering: %s", path)
            return False
        self._pdf = pdf
        self._path = path
        return True

    def _ensure_renderer(self) -> QPdfPageRenderer:
        if self._renderer is None:
            assert self._pdf is not None
            renderer = QPdfPageRenderer()
            renderer.setRenderMode(QPdfPageRenderer.RenderMode.MultiThreaded)
            renderer.setDocument(self._pdf)
            renderer.pageRendered.connect(self._on_page_rendered)
            self._renderer = renderer
        return self._renderer

    def _on_page_rendered(
        self,
        page_index: int,
        size: QSize,
        image: QImage,
        _options: object,
        _request_id: int,
    ) -> None:
        page = page_index + 1
        if size != self._size or page not in self._pending:
            # A result for a superseded pass (different zoom, or the
            # grid was rebuilt since). Dropping it is what keeps a late
            # arrival from writing into an item that no longer exists.
            return
        pixmap = QPixmap.fromImage(composite_on_white(image, size))
        self._cache.put((page, size.width(), size.height()), pixmap)
        item = self._items.get(page)
        if item is not None:
            item.setIcon(QIcon(pixmap))
        self._pending.discard(page)
        if not self._pending:
            self.idle.emit()

    @property
    def cache_size(self) -> int:
        """Number of cached page images (for tests)."""
        return len(self._cache)
