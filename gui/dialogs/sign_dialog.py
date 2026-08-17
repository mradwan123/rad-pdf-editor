"""Sign: place an image on a page.

Two ways to say where, both live at once and always in sync:

1. **The canvas** (`gui/placement_canvas.py`) - the real target page,
   rendered, with the chosen image draggable/resizable on top of it.
   Only available when the dialog is given the document being edited,
   which is why `MainWindow._run_tool` special-cases `"sign"` the same
   way it already special-cases `"fill_form"`.
2. **The four numeric spin boxes** - kept, not replaced. They are the
   only thing available when there is no document to preview against
   (the Workflow builder configures a step in the abstract, against no
   particular file), they're the only way to type an exact value, and
   they double as the canvas's live read-out.

`values()` always reads the spin boxes: a drag writes its result into
them, so there is exactly one source of truth for the rect regardless
of which way the user set it, and `build_operation(**values)` receives
the identical shape it always has - `SignOperation` never learns that
a canvas exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from core.errors import OperationError
from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.placement_canvas import PagePlacementCanvas


def _coord_spinbox() -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, 20000)
    box.setDecimals(2)
    box.setSuffix(" pt")
    return box


class SignDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None, pdf_path: Path | None = None) -> None:
        super().__init__(self.tr("Sign"), parent)

        self._image_path: Path | None = None
        self._syncing = False

        self._image_button = QPushButton(self.tr("Choose Image..."))
        self._image_button.clicked.connect(self._choose_image)
        self.add_row(self.tr("Signature image"), self._image_button)

        self.page = QSpinBox()
        self.page.setRange(1, 100000)
        self.add_row(self.tr("Page"), self.page)

        self.x0 = _coord_spinbox()
        self.y0 = _coord_spinbox()
        self.x1 = _coord_spinbox()
        self.x1.setValue(200)
        self.y1 = _coord_spinbox()
        self.y1.setValue(80)

        rect_row = QWidget()
        rect_layout = QHBoxLayout(rect_row)
        rect_layout.setContentsMargins(0, 0, 0, 0)
        for box in (self.x0, self.y0, self.x1, self.y1):
            rect_layout.addWidget(box)
        self.add_row(self.tr("Position (x0, y0, x1, y1 - from bottom-left)"), rect_row)

        self.canvas: PagePlacementCanvas | None = None
        if pdf_path is not None:
            self._build_canvas(pdf_path)

    # --- canvas -------------------------------------------------------------

    def _build_canvas(self, pdf_path: Path) -> None:
        """Add the interactive preview. A document that can't be
        rendered is not an error - the dialog just stays numeric-only,
        exactly as it behaves with no document at all."""
        canvas = PagePlacementCanvas(self)
        if not canvas.load_document(pdf_path):
            canvas.deleteLater()
            return

        self.canvas = canvas
        self.page.setRange(1, max(1, canvas.page_count()))
        hint = QLabel(
            self.tr("Drag the image to move it; drag a corner handle to resize it.")
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.add_full_width(hint)
        self.add_full_width(canvas)
        canvas.show_page(self.page.value())
        # The canvas clamps the incoming rect to the real page, so this
        # first push-then-read-back is also what makes the numeric
        # defaults (0, 0, 200, 80) sane on a page smaller than that.
        canvas.set_pdf_rect(self._rect_values())
        self._pull_rect_from_canvas()

        canvas.rect_changed.connect(self._pull_rect_from_canvas)
        self.page.valueChanged.connect(self._on_page_changed)
        for box in (self.x0, self.y0, self.x1, self.y1):
            box.valueChanged.connect(self._push_rect_to_canvas)

    def _on_page_changed(self, page_number: int) -> None:
        if self.canvas is None:
            return
        self.canvas.show_page(page_number)
        # show_page re-clamps the rect against the new page's size,
        # which can legitimately change it (pages in one PDF needn't be
        # the same size), so the spin boxes follow rather than lead.
        self._pull_rect_from_canvas()

    def _push_rect_to_canvas(self) -> None:
        if self.canvas is None or self._syncing:
            return
        self._syncing = True
        try:
            self.canvas.set_pdf_rect(self._rect_values())
        finally:
            self._syncing = False

    def _pull_rect_from_canvas(self) -> None:
        if self.canvas is None or self._syncing:
            return
        rect = self.canvas.pdf_rect()
        if rect is None:
            return
        self._syncing = True
        try:
            for box, value in zip((self.x0, self.y0, self.x1, self.y1), rect, strict=True):
                box.setValue(value)
        finally:
            self._syncing = False

    def _rect_values(self) -> tuple[float, float, float, float]:
        return (self.x0.value(), self.y0.value(), self.x1.value(), self.y1.value())

    # --- image --------------------------------------------------------------

    def _choose_image(self) -> None:
        path_str, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.tr("Choose signature image"),
            "",
            self.tr("Images (*.png *.jpg *.jpeg)"),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path_str:
            self.set_image_path(Path(path_str))

    def set_image_path(self, path: Path) -> None:
        self._image_path = path
        self._image_button.setText(path.name)
        if self.canvas is not None:
            self.canvas.set_overlay_pixmap(QPixmap(str(path)))

    # --- result -------------------------------------------------------------

    def release_resources(self) -> None:
        """Close the previewed document (see
        `BaseToolDialog.release_resources`). This is the one dialog in
        the app that opens a file of its own: the canvas holds the
        *session working copy* open through a `QPdfDocument` for as
        long as it's loaded, and that file gets securely wiped when the
        document/session closes.

        Safe to call before `values()` is read: the rect always comes
        from the spin boxes, never from the canvas (see the module
        docstring), so releasing the preview can't change the result.
        """
        if self.canvas is not None:
            self.canvas.release_document()

    def values(self) -> dict[str, Any]:
        if self._image_path is None:
            # _run_tool only catches PDFEditorError, not bare
            # exceptions - raising anything else here would crash the
            # GUI instead of showing a clean error dialog.
            raise OperationError("No signature image selected.")
        return {
            "image_path": self._image_path,
            "page": self.page.value(),
            "rect": self._rect_values(),
        }
