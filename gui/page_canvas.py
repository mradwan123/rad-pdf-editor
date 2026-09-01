"""The page viewer: a continuous, scrollable, zoomable view of the
document at readable size.

Phase 6c (docs/GUI_PLAN.md §3.2). Until now a tab showed only a grid of
120 px thumbnails, so the document could be *organised* but never read.

A `QGraphicsScene` holds one `PageItem` per page, stacked vertically.
Scene coordinates are **device pixels at the current zoom**, not PDF
points scaled by a view transform: each page is rendered at exactly the
pixel size it occupies, so zooming in produces a genuinely sharper page
rather than a magnified blurry one. The view transform stays at
identity throughout.

Rendering reuses Phase 6b's machinery - `QPdfPageRenderer` for async
delivery and `PixmapCache` for reuse - with one addition that 6b
deliberately left out: **only pages actually on screen are rendered**,
plus a small lookahead. That is what makes a 500-page document viable
here, where a full-size page costs ~2 MB against a thumbnail's 75 KB.
`docs/GUI_PLAN.md` §3.5.1 records why eager rendering was acceptable
for thumbnails and is not acceptable here.

Placeholders are painted, never allocated: an un-rendered page draws a
white rectangle and its page number in `paint()`. Allocating a real
placeholder `QPixmap` per page - as the thumbnail grid does, where they
are small - would cost ~1 GB for 500 unrendered A4 pages at 100%.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QDeadlineTimer,
    QPointF,
    QRectF,
    QSize,
    QSizeF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QResizeEvent
from PySide6.QtPdf import QPdfDocument, QPdfPageRenderer
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

from core.logging_config import get_logger
from gui.rendering import PixmapCache, composite_on_white

log = get_logger(__name__)

#: Gap between consecutive pages, in scene pixels.
_PAGE_GAP = 14
#: Pages rendered beyond the visible range, in each direction, so a
#: slow scroll usually finds the next page already drawn.
_LOOKAHEAD = 2
#: Zoom bounds. The upper bound is a memory guard as much as a UI one:
#: an A4 page at 4.0 is 2380x3368 px, ~32 MB as ARGB32.
_MIN_ZOOM = 0.10
_MAX_ZOOM = 4.0
_ZOOM_FACTOR = 1.25
#: Used when the viewport has no real size yet - a canvas that has
#: never been shown or resized reports 0x0, and "nothing is visible"
#: would mean nothing ever renders. Headless tests hit this constantly.
_NOMINAL_VIEWPORT = QSize(900, 700)


class PageItem(QGraphicsItem):
    """One page of the document, positioned in scene pixels.

    Paints its rendered pixmap when it has one and a plain white page
    otherwise, so an un-rendered page costs nothing but a `paint()`
    call - see the module docstring on why placeholders are drawn
    rather than allocated.
    """

    def __init__(self, page_number: int, width: float, height: float) -> None:
        super().__init__()
        self.page_number = page_number  # 1-based
        self._width = width
        self._height = height
        self._pixmap: QPixmap | None = None
        #: Highlight rects in *PDF points*, scaled at paint time. Kept
        #: in points rather than pixels so a zoom change cannot leave
        #: them stale - there is one source of truth for where a hit is.
        self._highlights: list[QRectF] = []
        self._scale = 1.0

    def set_highlights(self, rects: list[QRectF], scale: float) -> None:
        self._highlights = rects
        self._scale = scale
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt override
        return QRectF(0.0, 0.0, self._width, self._height)

    def set_page_size(self, width: float, height: float) -> None:
        if (width, height) == (self._width, self._height):
            return
        self.prepareGeometryChange()
        self._width = width
        self._height = height
        self._pixmap = None  # the old pixmap is the wrong size now
        self.update()

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    @property
    def has_pixmap(self) -> bool:
        return self._pixmap is not None

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        rect = self.boundingRect()
        if self._pixmap is not None:
            painter.drawPixmap(rect.topLeft(), self._pixmap)
        else:
            painter.fillRect(rect, Qt.GlobalColor.white)
            painter.setPen(QPen(QColor(150, 150, 150)))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(self.page_number))
        for hit in self._highlights:
            painter.fillRect(
                QRectF(
                    hit.x() * self._scale,
                    hit.y() * self._scale,
                    hit.width() * self._scale,
                    hit.height() * self._scale,
                ),
                QColor(255, 214, 0, 110),
            )
        painter.setPen(QPen(QColor(70, 70, 70)))
        painter.drawRect(rect)


class PageCanvas(QGraphicsView):
    """Continuous vertical page viewer for one document."""

    #: 1-based page number of the page currently at the top of the view.
    current_page_changed = Signal(int)
    zoom_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setBackgroundBrush(QColor(35, 36, 38))
        self.setAccessibleName(self.tr("Page view"))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

        # Unparented and owned solely by these attributes, so clearing
        # them destroys the objects under refcounting. A QPdfDocument
        # keeps the working file open, and Windows refuses to unlink an
        # open file - which would silently defeat the secure wipe. Same
        # discipline as ThumbnailRenderer and PagePlacementCanvas;
        # QPdfDocument.close() alone does not release the descriptor.
        self._pdf: QPdfDocument | None = None
        self._renderer: QPdfPageRenderer | None = None
        self._path: Path | None = None

        self._cache = PixmapCache()
        self._items: list[PageItem] = []
        #: page -> the pixel size it was last requested at. Keyed by
        #: size, not just page, so a late result for a superseded
        #: zoom cannot clear the entry belonging to the current one.
        self._pending: dict[int, tuple[int, int]] = {}
        self._zoom = 1.0
        self._highlights: dict[int, list[QRectF]] = {}
        self._fit_mode: str | None = "width"
        self._current_page = 0

        bar = self.verticalScrollBar()
        bar.valueChanged.connect(lambda _v: self._on_view_changed())

    # --- document ---------------------------------------------------------

    def set_document(self, path: Path) -> bool:
        """Show `path`. Keeps the current zoom and fit mode, and keeps
        the cache - the caller invalidates it via `invalidate()` when an
        operation actually changed pages, exactly as the thumbnail grid
        does."""
        if self._pdf is not None and self._path == path:
            self._rebuild_items()
            return True
        self.release()
        pdf = QPdfDocument()
        if pdf.load(str(path)) != QPdfDocument.Error.None_:
            log.error("Could not load PDF for the page view: %s", path)
            self._clear_items()
            return False
        self._pdf = pdf
        self._path = path
        self._rebuild_items()
        return True

    def release(self) -> None:
        """Drop every OS handle on the document. Must run before the
        tab's session dir is wiped. Idempotent."""
        if self._renderer is not None:
            self._renderer = None
        if self._pdf is not None:
            self._pdf.close()
            self._pdf = None
        self._path = None
        self._pending.clear()

    def clear(self) -> None:
        self.release()
        self._cache.invalidate(None)
        self._clear_items()

    @property
    def document(self) -> QPdfDocument | None:
        """The loaded document, for the QtPdf model classes that work
        off one (outline, search) - see docs/GUI_PLAN.md §2.1."""
        return self._pdf

    @property
    def page_count(self) -> int:
        return len(self._items)

    @property
    def current_page(self) -> int:
        """1-based page at the top of the viewport, or 0 if empty."""
        return self._current_page

    def set_highlights(self, by_page: dict[int, list[QRectF]]) -> None:
        """Show search hits. Rects are in top-left-origin PDF points -
        the convention QPdfSearchModel and getSelectionAtIndex both use
        (verified against a page with text at known positions)."""
        self._highlights = by_page
        for item in self._items:
            item.set_highlights(by_page.get(item.page_number, []), self._zoom)

    def scroll_to_rect(self, page: int, rect: QRectF) -> None:
        """Put a specific spot on `page` (1-based, PDF points) in view."""
        if not 1 <= page <= len(self._items):
            return
        item = self._items[page - 1]
        target = item.pos().y() + rect.y() * self._zoom
        # A third of the way down reads better than pinned to the top.
        self.verticalScrollBar().setValue(
            max(0, int(target - self._usable_viewport().height() / 3))
        )
        self._on_view_changed()

    def invalidate(self, pages: list[int] | None) -> None:
        self._cache.invalidate(pages)
        for item in self._items:
            if pages is None or item.page_number in pages:
                item.set_pixmap(None)
        self._request_visible()

    # --- zoom -------------------------------------------------------------

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float, *, fit_mode: str | None = None) -> None:
        zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, zoom))
        self._fit_mode = fit_mode
        if abs(zoom - self._zoom) < 1e-6:
            return
        self._zoom = zoom
        self._relayout()
        self.zoom_changed.emit(zoom)

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * _ZOOM_FACTOR)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / _ZOOM_FACTOR)

    def reset_zoom(self) -> None:
        self.set_zoom(1.0)

    def fit_width(self) -> None:
        widest = self._widest_page_pt()
        if widest > 0:
            self.set_zoom(self._usable_viewport().width() / widest, fit_mode="width")

    def fit_page(self) -> None:
        """Fit the *current* page entirely, so the zoom suits whichever
        page is being read rather than the first one."""
        size = self._page_size_pt(max(self._current_page, 1))
        if size is None or size.width() <= 0 or size.height() <= 0:
            return
        viewport = self._usable_viewport()
        self.set_zoom(
            min(viewport.width() / size.width(), viewport.height() / size.height()),
            fit_mode="page",
        )

    # --- navigation -------------------------------------------------------

    def scroll_to_page(self, page: int) -> None:
        """Put 1-based `page` at the top of the viewport."""
        if not 1 <= page <= len(self._items):
            return
        item = self._items[page - 1]
        self.verticalScrollBar().setValue(int(item.pos().y()) - _PAGE_GAP // 2)
        self._on_view_changed()

    # --- Qt overrides -----------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # A fit mode is a standing instruction, not a one-off: resizing
        # the window has to recompute it or "fit width" stops fitting.
        if self._fit_mode == "width":
            self.fit_width()
        elif self._fit_mode == "page":
            self.fit_page()
        else:
            self._on_view_changed()

    # --- layout and rendering --------------------------------------------

    def _clear_items(self) -> None:
        self._scene.clear()
        self._items = []
        self._pending.clear()
        self._current_page = 0
        self._scene.setSceneRect(QRectF())

    def _rebuild_items(self) -> None:
        self._clear_items()
        if self._pdf is None:
            return
        for page in range(1, self._pdf.pageCount() + 1):
            item = PageItem(page, 0.0, 0.0)
            self._scene.addItem(item)
            self._items.append(item)
        self._relayout()

    def _relayout(self) -> None:
        """Position every page for the current zoom. Scene units are
        device pixels, so a page's scene size *is* its render size."""
        if self._pdf is None or not self._items:
            return
        widest = self._widest_page_pt() * self._zoom
        y = float(_PAGE_GAP)
        for item in self._items:
            size = self._page_size_pt(item.page_number)
            if size is None:
                continue
            width = size.width() * self._zoom
            height = size.height() * self._zoom
            item.set_page_size(width, height)
            item.setPos((widest - width) / 2.0, y)
            y += height + _PAGE_GAP
        self._scene.setSceneRect(QRectF(0.0, 0.0, widest, y))
        # Every page is a different pixel size now, so anything still in
        # flight is for the old one. _pending records the size each page
        # was requested at, so _request_visible would re-issue these
        # anyway; dropping them here just stops superseded entries
        # accumulating. (Before _pending tracked sizes, a page left in it
        # was skipped as "already requested" and the viewer silently
        # showed blank pages after any zoom - including the fit-width
        # that runs on first show.)
        self._pending.clear()
        for item in self._items:
            item.set_highlights(self._highlights.get(item.page_number, []), self._zoom)
        self._on_view_changed()

    def _on_view_changed(self) -> None:
        self._update_current_page()
        self._request_visible()

    def _visible_scene_rect(self) -> QRectF:
        viewport = self._usable_viewport()
        top_left = self.mapToScene(0, 0)
        return QRectF(top_left, QPointF(top_left.x() + viewport.width(),
                                        top_left.y() + viewport.height()))

    def _usable_viewport(self) -> QSize:
        size = self.viewport().size()
        if size.width() <= 1 or size.height() <= 1:
            # Never shown or never resized - see _NOMINAL_VIEWPORT.
            return _NOMINAL_VIEWPORT
        return size

    def _update_current_page(self) -> None:
        visible = self._visible_scene_rect()
        for item in self._items:
            if item.pos().y() + item.boundingRect().height() > visible.top():
                if item.page_number != self._current_page:
                    self._current_page = item.page_number
                    self.current_page_changed.emit(item.page_number)
                return

    def _visible_pages(self) -> list[int]:
        visible = self._visible_scene_rect()
        pages = [
            item.page_number
            for item in self._items
            if QRectF(item.pos(), item.boundingRect().size()).intersects(visible)
        ]
        if not pages:
            return []
        first, last = pages[0], pages[-1]
        return list(
            range(max(1, first - _LOOKAHEAD), min(len(self._items), last + _LOOKAHEAD) + 1)
        )

    def _request_visible(self) -> None:
        """Render only what is on screen (plus a lookahead). Cached
        pages are applied immediately; the rest are requested."""
        if self._pdf is None:
            return
        for page in self._visible_pages():
            item = self._items[page - 1]
            rect = item.boundingRect()
            width, height = int(rect.width()), int(rect.height())
            if width <= 0 or height <= 0:
                continue
            key = (page, width, height)
            cached = self._cache.get(key)
            if cached is not None:
                if not item.has_pixmap:
                    item.set_pixmap(cached)
                continue
            if self._pending.get(page) == (width, height):
                continue
            self._pending[page] = (width, height)
            self._ensure_renderer().requestPage(page - 1, QSize(width, height))

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
        delivered = (size.width(), size.height())
        if self._pending.get(page) == delivered:
            self._pending.pop(page, None)
        if not 1 <= page <= len(self._items):
            return
        item = self._items[page - 1]
        rect = item.boundingRect()
        if (int(rect.width()), int(rect.height())) != delivered:
            # A result for a superseded zoom. Dropping it keeps a late
            # arrival from painting a wrongly-sized page - and note the
            # pending entry above is only cleared when the delivered
            # size is the one still wanted: clearing it unconditionally
            # let a stale result cancel the *current* request, so two
            # quick zooms in a row rendered nothing at all.
            return
        pixmap = QPixmap.fromImage(composite_on_white(image, size))
        self._cache.put((page, size.width(), size.height()), pixmap)
        item.set_pixmap(pixmap)

    # --- helpers ----------------------------------------------------------

    def _page_size_pt(self, page: int) -> QSizeF | None:
        if self._pdf is None or not 1 <= page <= self._pdf.pageCount():
            return None
        return self._pdf.pagePointSize(page - 1)

    def _widest_page_pt(self) -> float:
        if self._pdf is None:
            return 0.0
        widths = [self._pdf.pagePointSize(i).width() for i in range(self._pdf.pageCount())]
        return max(widths) if widths else 0.0

    # --- testing ----------------------------------------------------------

    def wait_until_idle(self, timeout_ms: int = 10_000) -> bool:
        """Spin the event loop until every requested page has arrived.
        For tests and benchmarks; the viewer itself never blocks."""
        deadline = QDeadlineTimer(timeout_ms)
        while self._pending and not deadline.hasExpired():
            QCoreApplication.processEvents()
        return not self._pending

    @property
    def rendered_page_count(self) -> int:
        """How many pages currently hold a real image (for tests)."""
        return sum(1 for item in self._items if item.has_pixmap)
