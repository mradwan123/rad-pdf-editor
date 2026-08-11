"""Interactive "place this image on that page" canvas.

A `QGraphicsView` showing one real rendered PDF page with a movable,
corner-resizable overlay rectangle on top of it. Built for
`gui/dialogs/sign_dialog.py` so a signature image can be positioned by
mouse instead of by typing four raw numbers, but deliberately written
against nothing Sign-specific: it takes a PDF path, a page number and
an optional overlay pixmap, and hands back a rect in this package's
PDF-native, bottom-left-origin point coordinates - exactly the shape
every rect-taking `Operation` in `core/ops/` already expects.

Three coordinate systems meet here, which is where the classic
off-by-flip bugs live:

1. **PDF points, origin bottom-left, y up.** What `SignOperation` (and
   Crop/Resize/Watermark/HeaderFooter) take, and the only thing this
   module exposes publicly (`pdf_rect()` / `set_pdf_rect()`).
2. **Scene pixels, origin top-left, y down.** The rendered page pixmap
   sits at scene (0, 0) with size `page_points * _scale`, so scene
   coordinates are just PDF points flipped in y and multiplied by the
   render scale. All the interactive geometry lives here.
3. **View pixels.** Whatever the widget happens to be sized to. The
   view `fitInView`s the whole page, so this scales freely with the
   dialog *without touching the maths* - conversion only ever goes
   between (1) and (2), never through the widget's own size. That
   decoupling is the reason resizing the dialog can't corrupt a
   placement.

The page is rendered with `QPdfDocument.render()` - the same QtPdf path
`MainWindow._render_thumbnails` already uses for thumbnails, including
its white-backdrop compositing (QtPdf leaves unpainted areas fully
transparent, invisible on a blank page).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, QSizeF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QResizeEvent
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

from core.logging_config import get_logger

log = get_logger(__name__)

#: Long edge, in scene pixels, every page is rendered to. Big enough to
#: place an image precisely (a thumbnail is 120x160), small enough to
#: render instantly for any page size.
_TARGET_LONG_EDGE = 800.0

#: Corner grab-handle size, in scene pixels.
_HANDLE = 10.0

#: Smallest overlay the user can shrink to by dragging a handle, in
#: scene pixels - below this there'd be nothing left to grab.
_MIN_SIZE = 16.0

_HANDLE_CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor,
    "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor,
    "bl": Qt.CursorShape.SizeBDiagCursor,
}


class PlacementItem(QGraphicsItem):
    """The draggable/resizable overlay rectangle.

    The item's `pos()` is pinned at scene (0, 0) for its whole life and
    the rectangle is stored in scene coordinates instead. That is not
    the most common Qt idiom (usually you move `pos()` and keep a
    local-origin rect), but it removes an entire class of bug from this
    widget: item-local and scene coordinates are then always identical,
    so a drag can never be applied in the wrong frame of reference and
    `pdf_rect()` never has to `mapToScene` anything.

    Dragging the body moves it; dragging one of the four corner handles
    resizes it. Both are clamped to the page, so a placement can never
    end up partly off the page (which `SignOperation` would happily
    accept and then render half-missing).
    """

    def __init__(
        self,
        rect: QRectF,
        bounds: QRectF,
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._bounds = QRectF(bounds)
        self._rect = self._clamped(QRectF(rect))
        self._on_changed = on_changed
        self._pixmap = QPixmap()
        self._mode: str | None = None
        self._press_scene_pos = QPointF()
        self._press_rect = QRectF()
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(1)

    # --- geometry ---------------------------------------------------------

    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def set_rect(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        self._rect = self._clamped(QRectF(rect))
        self.update()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def _clamped(self, rect: QRectF) -> QRectF:
        """Keep `rect` inside the page, shrinking it only if it is
        genuinely bigger than the page itself."""
        rect = rect.normalized()
        if rect.width() > self._bounds.width():
            rect.setWidth(self._bounds.width())
        if rect.height() > self._bounds.height():
            rect.setHeight(self._bounds.height())
        if rect.left() < self._bounds.left():
            rect.moveLeft(self._bounds.left())
        if rect.top() < self._bounds.top():
            rect.moveTop(self._bounds.top())
        if rect.right() > self._bounds.right():
            rect.moveRight(self._bounds.right())
        if rect.bottom() > self._bounds.bottom():
            rect.moveBottom(self._bounds.bottom())
        return rect

    def _handle_rects(self) -> dict[str, QRectF]:
        r = self._rect
        points = {
            "tl": r.topLeft(),
            "tr": r.topRight(),
            "bl": r.bottomLeft(),
            "br": r.bottomRight(),
        }
        half = _HANDLE / 2.0
        return {
            name: QRectF(p.x() - half, p.y() - half, _HANDLE, _HANDLE)
            for name, p in points.items()
        }

    def handle_at(self, pos: QPointF) -> str | None:
        """Which corner handle (if any) is under `pos` (scene/item
        coordinates - the same thing here, see the class docstring)."""
        for name, handle in self._handle_rects().items():
            if handle.contains(pos):
                return name
        return None

    # --- painting ---------------------------------------------------------

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt override, fixed name
        # Half a handle would do; a whole one keeps the outline's pen
        # width inside the bounds too.
        return self._rect.adjusted(-_HANDLE, -_HANDLE, _HANDLE, _HANDLE)

    def image_target_rect(self) -> QRectF:
        """Where the image will actually be drawn inside the placement
        rect - which is *not* always the whole placement rect, because
        `fitz.Page.insert_image()` (what `SignOperation` calls) fits the
        image inside it preserving aspect ratio and centres it.

        Reproduced here rather than approximated, so the preview shows
        what the output will really look like, including its one
        genuinely odd case: see `_stretches_to_fill`.
        """
        if self._pixmap.isNull() or self._stretches_to_fill():
            return QRectF(self._rect)
        size = self._pixmap.size()
        ratio = min(
            self._rect.width() / size.width(), self._rect.height() / size.height()
        )
        target = QRectF(
            QPointF(0, 0), QSizeF(size.width() * ratio, size.height() * ratio)
        )
        target.moveCenter(self._rect.center())
        return target

    def _stretches_to_fill(self) -> bool:
        """PyMuPDF (1.28.2) ignores its own `keep_proportion=True`
        default for an image whose pixel width and height are *exactly*
        equal: a 100x100 px image dropped into a 100x40 pt rect fills
        the whole rect, distorted, while a 101x100 px one into the same
        rect is correctly fitted to 40.4x40 and centred. Verified by
        hand against real rendered output (blue-pixel bounding box in
        the rasterised page, not just the reported `get_image_info`
        bbox) across square/near-square/wide/tall images.

        Mirrored rather than papered over: if a user's signature is
        going to come out stretched, the preview should show it
        stretched, not quietly disagree with the file it produces.
        """
        size = self._pixmap.size()
        return size.width() == size.height()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        if self._pixmap.isNull():
            painter.fillRect(self._rect, QColor(90, 140, 200, 60))
        else:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(
                self.image_target_rect(), self._pixmap, QRectF(self._pixmap.rect())
            )

        painter.setPen(QPen(QColor(120, 175, 255), 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._rect)

        painter.setPen(QPen(QColor(20, 20, 20), 1.0))
        painter.setBrush(QColor(120, 175, 255))
        for handle in self._handle_rects().values():
            painter.drawRect(handle)

    # --- interaction ------------------------------------------------------

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        handle = self.handle_at(event.pos())
        self.setCursor(_HANDLE_CURSORS.get(handle or "", Qt.CursorShape.SizeAllCursor))

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        # Handles win over the body: their squares straddle the corners,
        # so the inner half of every handle overlaps the draggable body
        # and this hit-testing order is what makes a corner grab
        # possible at all.
        self._mode = self.handle_at(event.pos())
        if self._mode is None and self._rect.contains(event.pos()):
            self._mode = "move"
        if self._mode is None:
            event.ignore()
            return
        self._press_rect = QRectF(self._rect)
        self._press_scene_pos = event.scenePos()
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._mode is None:
            event.ignore()
            return
        delta = event.scenePos() - self._press_scene_pos
        if self._mode == "move":
            self.set_rect(self._press_rect.translated(delta))
        else:
            self.set_rect(self._resized(self._mode, delta.x(), delta.y()))
        if self._on_changed is not None:
            self._on_changed()
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        self._mode = None
        event.accept()

    def _resized(self, corner: str, dx: float, dy: float) -> QRectF:
        """New rect for dragging `corner` by (dx, dy) from the rect as
        it was at mouse-press. Each moved edge is clamped both to the
        page and to `_MIN_SIZE` away from the opposite edge, so a
        handle drag can neither leave the page nor turn the rect inside
        out."""
        base = self._press_rect
        left, top = base.left(), base.top()
        right, bottom = base.right(), base.bottom()
        if "l" in corner:
            left = min(max(self._bounds.left(), left + dx), right - _MIN_SIZE)
        if "r" in corner:
            right = max(min(self._bounds.right(), right + dx), left + _MIN_SIZE)
        if "t" in corner:
            top = min(max(self._bounds.top(), top + dy), bottom - _MIN_SIZE)
        if "b" in corner:
            bottom = max(min(self._bounds.bottom(), bottom + dy), top + _MIN_SIZE)
        return QRectF(QPointF(left, top), QPointF(right, bottom))


class PagePlacementCanvas(QGraphicsView):
    """One rendered PDF page plus a `PlacementItem` on top of it.

    Public surface is deliberately all in PDF points
    (`pdf_rect()`/`set_pdf_rect()`) so callers never see scene pixels.
    """

    #: Emitted whenever the user drags/resizes the overlay (never for a
    #: programmatic `set_pdf_rect`, so a caller syncing spin boxes into
    #: the canvas can't feed itself back into a loop).
    rect_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(360, 440)
        self.setAccessibleName(self.tr("Page placement canvas"))

        # Parented to this view (not left unowned) and reused across
        # page changes: a dialog is short-lived, unlike MainWindow,
        # where a self-parented QPdfDocument per thumbnail refresh
        # would leak one instance per operation.
        self._pdf: QPdfDocument | None = None
        self._page_item: QGraphicsPixmapItem | None = None
        self._item: PlacementItem | None = None
        self._page_size_pt = QSizeF(0, 0)
        self._scale = 1.0
        self._overlay_pixmap = QPixmap()

    # --- loading ----------------------------------------------------------

    def load_document(self, path: Path) -> bool:
        """Load `path` for previewing. Returns False (and logs) if it
        can't be rendered, so a caller can silently fall back to plain
        numeric entry rather than blocking the tool on a preview."""
        pdf = QPdfDocument(self)
        if pdf.load(str(path)) != QPdfDocument.Error.None_:
            log.error("Could not load PDF for placement preview: %s", path)
            return False
        self._pdf = pdf
        return True

    def page_count(self) -> int:
        return self._pdf.pageCount() if self._pdf is not None else 0

    def page_size_points(self) -> QSizeF:
        return QSizeF(self._page_size_pt)

    def show_page(self, page_number: int) -> None:
        """Render page `page_number` (1-indexed) and rebuild the scene,
        keeping the current placement rect (in PDF points, so it means
        the same thing on a differently sized page)."""
        if self._pdf is None:
            return
        index = page_number - 1
        if not 0 <= index < self._pdf.pageCount():
            return

        previous = self.pdf_rect() if self._item is not None else None

        self._page_size_pt = self._pdf.pagePointSize(index)
        long_edge = max(self._page_size_pt.width(), self._page_size_pt.height())
        self._scale = _TARGET_LONG_EDGE / long_edge if long_edge else 1.0
        pixel_size = QSize(
            max(1, round(self._page_size_pt.width() * self._scale)),
            max(1, round(self._page_size_pt.height() * self._scale)),
        )

        rendered = self._pdf.render(index, pixel_size)
        # QtPdf leaves unpainted areas of a page fully transparent
        # rather than white - the same fix MainWindow._render_thumbnails
        # documents. Without it a blank page reads as "no page at all".
        page_image = QImage(pixel_size, QImage.Format.Format_ARGB32_Premultiplied)
        page_image.fill(Qt.GlobalColor.white)
        painter = QPainter(page_image)
        painter.drawImage(0, 0, rendered)
        painter.end()

        self._scene.clear()
        # QGraphicsScene.clear() deletes the items it owned, so both
        # references are dangling until they're rebuilt below.
        self._page_item = self._scene.addPixmap(QPixmap.fromImage(page_image))
        self._page_item.setPos(0, 0)
        self._item = None
        bounds = QRectF(0, 0, pixel_size.width(), pixel_size.height())
        self._scene.setSceneRect(bounds)

        default = previous if previous is not None else self._default_pdf_rect()
        item = PlacementItem(self._pdf_to_scene(default), bounds, self.rect_changed.emit)
        item.set_pixmap(self._overlay_pixmap)
        self._scene.addItem(item)
        self._item = item
        self._fit()

    def _default_pdf_rect(self) -> tuple[float, float, float, float]:
        """A signature-sized box in the lower-left of the page - the
        same corner the dialog's numeric defaults have always used."""
        width = min(200.0, self._page_size_pt.width())
        height = min(80.0, self._page_size_pt.height())
        return (0.0, 0.0, width, height)

    def has_page(self) -> bool:
        return self._item is not None

    def set_overlay_pixmap(self, pixmap: QPixmap) -> None:
        self._overlay_pixmap = pixmap
        if self._item is not None:
            self._item.set_pixmap(pixmap)

    # --- coordinates ------------------------------------------------------

    def _pdf_to_scene(self, rect: tuple[float, float, float, float]) -> QRectF:
        x0, y0, x1, y1 = rect
        height = self._page_size_pt.height()
        return QRectF(
            x0 * self._scale,
            (height - y1) * self._scale,
            (x1 - x0) * self._scale,
            (y1 - y0) * self._scale,
        )

    def _scene_to_pdf(self, rect: QRectF) -> tuple[float, float, float, float]:
        height = self._page_size_pt.height()
        return (
            rect.left() / self._scale,
            height - rect.bottom() / self._scale,
            rect.right() / self._scale,
            height - rect.top() / self._scale,
        )

    def pdf_rect(self) -> tuple[float, float, float, float] | None:
        """The overlay's current position as (x0, y0, x1, y1) in PDF
        points with a bottom-left origin - the exact tuple
        `SignOperation` takes. None when no page is loaded."""
        if self._item is None:
            return None
        return self._scene_to_pdf(self._item.rect())

    def image_pdf_rect(self) -> tuple[float, float, float, float] | None:
        """Where the image itself (not the placement box around it)
        will land, in PDF points - i.e. the exact bbox the output file
        should come back with. `pdf_rect()` is what `SignOperation`
        takes; this is what that rect will visibly produce."""
        if self._item is None:
            return None
        return self._scene_to_pdf(self._item.image_target_rect())

    def set_pdf_rect(self, rect: tuple[float, float, float, float]) -> None:
        """Move/resize the overlay to a rect given in PDF points
        (bottom-left origin). Does not emit `rect_changed`."""
        if self._item is None:
            return
        self._item.set_rect(self._pdf_to_scene(rect))

    def placement_item(self) -> PlacementItem | None:
        """The live overlay item - exposed so tests can drive it
        directly (real mouse drags aren't reliably simulatable under
        QT_QPA_PLATFORM=offscreen, the same reason the thumbnail-
        reorder test calls `model().moveRow(...)` itself)."""
        return self._item

    # --- view -------------------------------------------------------------

    def _fit(self) -> None:
        if not self._scene.sceneRect().isEmpty():
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Only the view transform changes; scene coordinates - and so
        # every rect this widget reports - are unaffected by the
        # widget's size.
        self._fit()
