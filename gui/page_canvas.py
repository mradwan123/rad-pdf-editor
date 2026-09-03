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

import fitz
from PySide6.QtCore import (
    QCoreApplication,
    QDeadlineTimer,
    QModelIndex,
    QPointF,
    QRectF,
    QSize,
    QSizeF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtPdf import QPdfDocument, QPdfLinkModel, QPdfPageRenderer
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

from core.logging_config import get_logger
from gui.rendering import PixmapCache, annotation_render_options, composite_on_white
from gui.text_selection import PageTextIndex

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
#: Smaller than this and a drag was a stray click, not a shape.
_MIN_DRAW_SIZE = 4.0
#: A sticky note is a fixed-size marker, not a dragged region.
_NOTE_SIZE = 20.0


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
        self._selection: list[QRectF] = []
        #: The shape currently being dragged out, in PDF points.
        self._draft: QRectF | None = None
        self._draft_strokes: list[list[tuple[float, float]]] = []
        #: Outline of the annotation currently picked, in PDF points.
        self._picked: QRectF | None = None
        self._scale = 1.0

    def set_highlights(self, rects: list[QRectF], scale: float) -> None:
        self._highlights = rects
        self._scale = scale
        self.update()

    def set_selection(self, rects: list[QRectF], scale: float) -> None:
        self._selection = rects
        self._scale = scale
        self.update()

    def set_picked(self, rect: QRectF | None) -> None:
        self._picked = rect
        self.update()

    def set_draft(
        self,
        rect: QRectF | None,
        strokes: list[list[tuple[float, float]]] | None = None,
    ) -> None:
        """Preview of the annotation being dragged out, before it is
        committed as an Operation."""
        self._draft = rect
        self._draft_strokes = strokes or []
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

    def _scaled(self, rect: QRectF) -> QRectF:
        """A rect held in PDF points, in this item's pixel space."""
        return QRectF(
            rect.x() * self._scale,
            rect.y() * self._scale,
            rect.width() * self._scale,
            rect.height() * self._scale,
        )

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
        for hit in self._selection:
            painter.fillRect(self._scaled(hit), QColor(64, 132, 214, 90))
        for hit in self._highlights:
            painter.fillRect(self._scaled(hit), QColor(255, 214, 0, 110))
        if self._picked is not None:
            painter.setPen(QPen(QColor(64, 132, 214), 2, Qt.PenStyle.DashLine))
            painter.drawRect(self._scaled(self._picked))
        if self._draft is not None:
            painter.setPen(QPen(QColor(64, 132, 214), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self._scaled(self._draft))
        for stroke in self._draft_strokes:
            painter.setPen(QPen(QColor(214, 64, 64), 2))
            for (x0, y0), (x1, y1) in zip(stroke, stroke[1:], strict=False):
                painter.drawLine(
                    QPointF(x0 * self._scale, y0 * self._scale),
                    QPointF(x1 * self._scale, y1 * self._scale),
                )
        painter.setPen(QPen(QColor(70, 70, 70)))
        painter.drawRect(rect)


#: Tools that draw a new annotation directly on the page. Text markup
#: is deliberately *not* here: highlight/underline/strikeout act on the
#: current text selection instead, which is both the familiar gesture
#: and a reuse of the selection machinery built in 6c.
DRAW_TOOLS = ("rect", "circle", "line", "ink", "note")


class PageCanvas(QGraphicsView):
    """Continuous vertical page viewer for one document."""

    #: 1-based page number of the page currently at the top of the view.
    current_page_changed = Signal(int)
    zoom_changed = Signal(float)
    #: (1-based page, url). An internal link reports its target page
    #: and an empty url; an external link reports page 0 and its url.
    link_activated = Signal(int, str)
    selection_changed = Signal(str)
    #: (1-based page, kind, payload) for a shape/ink/note drawn on the
    #: canvas. `payload` is a bottom-left-origin rect tuple, or a list
    #: of strokes for ink.
    annotation_drawn = Signal(int, str, object)
    #: (1-based page, annotation id, new bottom-left-origin rect).
    annotation_moved = Signal(int, str, object)
    #: (1-based page, annotation id) or (0, "") when nothing is picked.
    annotation_picked = Signal(int, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setBackgroundBrush(QColor(35, 36, 38))
        self.setAccessibleName(self.tr("Page view"))
        # NoDrag: the left button selects text. Scrolling is the wheel
        # and the scrollbars, as in every PDF reader's select mode.
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

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
        self._text_indices: dict[int, PageTextIndex] = {}
        self._links: dict[int, list[tuple[QRectF, int, str]]] = {}
        self._selection_page: int | None = None
        self._selection_anchor = 0
        self._selection_focus = 0
        #: "select", or one of gui.page_canvas.DRAW_TOOLS.
        self._tool = "select"
        self._draw_page: int | None = None
        self._draw_origin: QPointF | None = None
        self._draw_strokes: list[list[tuple[float, float]]] = []
        #: page -> [(rect in top-left PDF points, annotation id)], lazily
        #: read from the document and dropped whenever a page changes.
        self._annotations: dict[int, list[tuple[QRectF, str]]] = {}
        self._picked: tuple[int, str] | None = None
        self._move_origin: QPointF | None = None
        self._move_rect: QRectF | None = None
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
        self._text_indices.clear()
        self._links.clear()
        self._annotations.clear()
        self.clear_selection()
        self.clear_annotation_selection()

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

    # --- existing annotations ---------------------------------------------

    @property
    def selected_annotation(self) -> tuple[int, str] | None:
        """(1-based page, id) of the picked annotation, or None."""
        return self._picked

    def clear_annotation_selection(self) -> None:
        picked = self._picked
        self._picked = None
        self._move_origin = None
        self._move_rect = None
        if picked is not None and 1 <= picked[0] <= len(self._items):
            self._items[picked[0] - 1].set_picked(None)
        self.annotation_picked.emit(0, "")

    def _page_annotations(self, page: int) -> list[tuple[QRectF, str]]:
        """Annotations on `page`, newest last, in top-left PDF points.

        Read straight from the document rather than tracked separately:
        the page image already shows them (PyMuPDF renders annotations
        into the page), so this only needs to supply hit-testing.
        """
        if self._path is None:
            return []
        cached = self._annotations.get(page)
        if cached is not None:
            return cached
        found: list[tuple[QRectF, str]] = []
        try:
            with fitz.open(self._path) as document:
                if 1 <= page <= document.page_count:
                    for annot in document[page - 1].annots():
                        rect = annot.rect
                        annot_id = annot.info.get("id", "")
                        if annot_id:
                            found.append(
                                (
                                    QRectF(
                                        rect.x0, rect.y0, rect.x1 - rect.x0, rect.y1 - rect.y0
                                    ),
                                    annot_id,
                                )
                            )
        except Exception as exc:  # noqa: BLE001 - hit-testing must never crash the view
            log.warning("Could not read annotations on page %s: %s", page, exc)
        self._annotations[page] = found
        return found

    def _annotation_at(self, page: int, point: QPointF) -> tuple[QRectF, str] | None:
        # Reversed: the most recently added annotation is on top, so it
        # is what a click on overlapping annotations should pick.
        for rect, annot_id in reversed(self._page_annotations(page)):
            if rect.contains(point):
                return rect, annot_id
        return None

    def _pick_annotation(self, page: int, rect: QRectF, annot_id: str) -> None:
        self.clear_annotation_selection()
        self._picked = (page, annot_id)
        self._move_rect = QRectF(rect)
        self._items[page - 1].set_picked(rect)
        self.annotation_picked.emit(page, annot_id)

    # --- tools ------------------------------------------------------------

    @property
    def tool(self) -> str:
        return self._tool

    def set_tool(self, tool: str) -> None:
        """Switch between selecting text and drawing an annotation."""
        if tool != "select" and tool not in DRAW_TOOLS:
            raise ValueError(f"Unknown canvas tool {tool!r}")
        self._tool = tool
        self._clear_draft()
        if tool != "select":
            self.clear_selection()
        self.setCursor(
            Qt.CursorShape.CrossCursor if tool != "select" else Qt.CursorShape.IBeamCursor
        )

    def _clear_draft(self) -> None:
        page = self._draw_page
        self._draw_page = None
        self._draw_origin = None
        self._draw_strokes = []
        if page is not None and 1 <= page <= len(self._items):
            self._items[page - 1].set_draft(None)

    def selection_markup_rects(self) -> tuple[int, list[tuple[float, float, float, float]]]:
        """The current text selection as bottom-left-origin rects, ready
        for AddAnnotationOperation. `(0, [])` when nothing is selected.

        PageTextIndex works in top-left-origin points (QtPdf's
        convention); operations in this codebase take bottom-left. The
        flip happens here, once, rather than in every caller.
        """
        page = self._selection_page
        if page is None or self._pdf is None:
            return 0, []
        index = self._text_index(page)
        if index is None:
            return 0, []
        height = self._pdf.pagePointSize(page - 1).height()
        rects = [
            (r.x(), height - r.y() - r.height(), r.x() + r.width(), height - r.y())
            for r in index.rects_between(self._selection_anchor, self._selection_focus)
        ]
        return page, rects

    # --- text selection ---------------------------------------------------

    @property
    def selected_text(self) -> str:
        """The currently selected text, or "" when nothing is selected."""
        if self._selection_page is None:
            return ""
        index = self._text_index(self._selection_page)
        if index is None:
            return ""
        return index.text_between(self._selection_anchor, self._selection_focus)

    def clear_selection(self) -> None:
        page = self._selection_page
        self._selection_page = None
        self._selection_anchor = self._selection_focus = 0
        if page is not None and 1 <= page <= len(self._items):
            self._items[page - 1].set_selection([], self._zoom)
        self.selection_changed.emit("")

    def select_all_on_page(self, page: int) -> None:
        index = self._text_index(page)
        if index is None or index.is_empty:
            return
        self._selection_page = page
        self._selection_anchor = 0
        self._selection_focus = index.length
        self._paint_selection()

    def copy_selection(self) -> bool:
        """Copy the selection to the clipboard. False if there was
        nothing selected."""
        text = self.selected_text
        if not text:
            return False
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:  # no clipboard under some headless setups
            return False
        clipboard.setText(text)
        return True

    def _text_index(self, page: int) -> PageTextIndex | None:
        if self._pdf is None or not 1 <= page <= len(self._items):
            return None
        cached = self._text_indices.get(page)
        if cached is None:
            cached = PageTextIndex(self._pdf, page)
            self._text_indices[page] = cached
        return cached

    def _paint_selection(self) -> None:
        page = self._selection_page
        if page is None:
            return
        index = self._text_index(page)
        if index is None:
            return
        rects = index.rects_between(self._selection_anchor, self._selection_focus)
        self._items[page - 1].set_selection(rects, self._zoom)
        self.selection_changed.emit(self.selected_text)

    def _page_point(self, scene_pos: QPointF) -> tuple[int, QPointF] | None:
        """Which page `scene_pos` is over, and where on it in PDF points."""
        for item in self._items:
            rect = QRectF(item.pos(), item.boundingRect().size())
            if rect.contains(scene_pos):
                local = scene_pos - item.pos()
                return item.page_number, QPointF(
                    local.x() / self._zoom, local.y() / self._zoom
                )
        return None

    # --- links ------------------------------------------------------------

    def _page_links(self, page: int) -> list[tuple[QRectF, int, str]]:
        """(rect in PDF points, 1-based target page or 0, url) per link."""
        if self._pdf is None:
            return []
        cached = self._links.get(page)
        if cached is not None:
            return cached
        model = QPdfLinkModel()
        model.setDocument(self._pdf)
        model.setPage(page - 1)
        links: list[tuple[QRectF, int, str]] = []
        for row in range(model.rowCount(QModelIndex())):
            index = model.index(row, 0)
            rect = model.data(index, QPdfLinkModel.Role.Rectangle.value)
            target = model.data(index, QPdfLinkModel.Role.Page.value)
            url = model.data(index, QPdfLinkModel.Role.Url.value)
            if not isinstance(rect, QRectF):
                continue
            # An external link reports page -1 and carries a url; an
            # internal one reports a 0-based target page.
            page_target = target + 1 if isinstance(target, int) and target >= 0 else 0
            links.append((rect, page_target, url.toString() if url is not None else ""))
        self._links[page] = links
        return links

    def _link_at(self, page: int, point: QPointF) -> tuple[int, str] | None:
        for rect, target, url in self._page_links(page):
            if rect.contains(point):
                return target, url
        return None

    # --- mouse ------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        hit = self._page_point(self.mapToScene(event.position().toPoint()))
        if hit is None:
            self.clear_selection()
            super().mousePressEvent(event)
            return
        page, point = hit
        if self._tool in DRAW_TOOLS:
            self._begin_draw(page, point)
            return
        annotation = self._annotation_at(page, point)
        if annotation is not None:
            rect, annot_id = annotation
            self._pick_annotation(page, rect, annot_id)
            self._move_origin = point
            return
        self.clear_annotation_selection()
        link = self._link_at(page, point)
        if link is not None:
            target, url = link
            self.link_activated.emit(target, url)
            return
        index = self._text_index(page)
        if index is None or index.is_empty:
            self.clear_selection()
            return
        self._selection_page = page
        self._selection_anchor = self._selection_focus = index.index_at(point)
        self._paint_selection()

    def _begin_draw(self, page: int, point: QPointF) -> None:
        self._draw_page = page
        self._draw_origin = point
        self._draw_strokes = [[(point.x(), point.y())]] if self._tool == "ink" else []
        if self._tool == "note":
            # A note is a point, not a drag - commit it immediately.
            self._commit_draw(point)

    def _extend_draw(self, point: QPointF) -> None:
        if self._draw_page is None or self._draw_origin is None:
            return
        item = self._items[self._draw_page - 1]
        if self._tool == "ink":
            self._draw_strokes[-1].append((point.x(), point.y()))
            item.set_draft(None, self._draw_strokes)
            return
        item.set_draft(QRectF(self._draw_origin, point).normalized())

    def _commit_draw(self, point: QPointF) -> None:
        page, origin, tool = self._draw_page, self._draw_origin, self._tool
        strokes = self._draw_strokes
        self._clear_draft()
        if page is None or origin is None or self._pdf is None:
            return
        height = self._pdf.pagePointSize(page - 1).height()

        if tool == "ink":
            if len(strokes) != 1 or len(strokes[0]) < 2:
                return  # a click, not a stroke
            flipped = [[(x, height - y) for x, y in strokes[0]]]
            self.annotation_drawn.emit(page, "ink", flipped)
            return

        rect = QRectF(origin, point).normalized()
        if tool == "note":
            # Anchor a fixed-size marker where the click landed.
            rect = QRectF(point.x(), point.y(), _NOTE_SIZE, _NOTE_SIZE)
        elif rect.width() < _MIN_DRAW_SIZE or rect.height() < _MIN_DRAW_SIZE:
            return  # a stray click, not a shape
        bottom_left = (
            rect.x(),
            height - rect.y() - rect.height(),
            rect.x() + rect.width(),
            height - rect.y(),
        )
        self.annotation_drawn.emit(page, tool, bottom_left)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if (
            self._picked is not None
            and self._move_origin is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._finish_move(self.mapToScene(event.position().toPoint()))
            return
        if self._draw_page is not None and event.button() == Qt.MouseButton.LeftButton:
            hit = self._page_point(self.mapToScene(event.position().toPoint()))
            point = hit[1] if hit is not None and hit[0] == self._draw_page else None
            if point is not None:
                self._commit_draw(point)
            else:
                self._clear_draft()
            return
        super().mouseReleaseEvent(event)

    def _finish_move(self, scene_pos: QPointF) -> None:
        assert self._picked is not None
        page, annot_id = self._picked
        origin, rect = self._move_origin, self._move_rect
        self._move_origin = None
        hit = self._page_point(scene_pos)
        if hit is None or hit[0] != page or origin is None or rect is None or self._pdf is None:
            return
        delta = hit[1] - origin
        if abs(delta.x()) < _MIN_DRAW_SIZE and abs(delta.y()) < _MIN_DRAW_SIZE:
            return  # a click to select, not a drag to move
        moved = rect.translated(delta.x(), delta.y())
        height = self._pdf.pagePointSize(page - 1).height()
        self.annotation_moved.emit(
            page,
            annot_id,
            (
                moved.x(),
                height - moved.y() - moved.height(),
                moved.x() + moved.width(),
                height - moved.y(),
            ),
        )

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._draw_page is not None and event.buttons() & Qt.MouseButton.LeftButton:
            hit = self._page_point(self.mapToScene(event.position().toPoint()))
            if hit is not None and hit[0] == self._draw_page:
                self._extend_draw(hit[1])
            return
        if self._picked is not None and self._move_origin is not None and (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            hit = self._page_point(self.mapToScene(event.position().toPoint()))
            if hit is not None and hit[0] == self._picked[0] and self._move_rect is not None:
                delta = hit[1] - self._move_origin
                moved = self._move_rect.translated(delta.x(), delta.y())
                self._items[self._picked[0] - 1].set_picked(moved)
            return
        if self._selection_page is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        hit = self._page_point(self.mapToScene(event.position().toPoint()))
        # A drag that wanders onto another page keeps extending within
        # the page it started on. Selection across pages is a separate
        # piece of work - see docs/GUI_PLAN.md - and silently selecting
        # the wrong page's text would be worse than not extending.
        if hit is None or hit[0] != self._selection_page:
            return
        index = self._text_index(self._selection_page)
        if index is None:
            return
        self._selection_focus = index.index_at(hit[1])
        self._paint_selection()

    def invalidate(self, pages: list[int] | None) -> None:
        self._cache.invalidate(pages)
        # The text and links of an edited page have changed too.
        if pages is None:
            self._text_indices.clear()
            self._links.clear()
            self._annotations.clear()
            self.clear_selection()
            self.clear_annotation_selection()
        else:
            for page in pages:
                self._text_indices.pop(page, None)
                self._links.pop(page, None)
                self._annotations.pop(page, None)
            if self._picked is not None and self._picked[0] in pages:
                self.clear_annotation_selection()
            # A selection on an edited page refers to text that may no
            # longer exist at those indices, so it is dropped too.
            if self._selection_page in pages:
                self.clear_selection()
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
        self._paint_selection()
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
            self._ensure_renderer().requestPage(
                page - 1, QSize(width, height), annotation_render_options()
            )

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
