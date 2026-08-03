from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QWidget,
)

from core.errors import OperationError
from gui.dialogs.base_tool_dialog import BaseToolDialog


def _coord_spinbox() -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, 20000)
    box.setSuffix(" pt")
    return box


class SignDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Sign"), parent)

        self._image_path: Path | None = None
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
        self.add_row(
            self.tr("Position (x0, y0, x1, y1 - from bottom-left)"), rect_row
        )

    def _choose_image(self) -> None:
        path_str, _selected_filter = QFileDialog.getOpenFileName(
            self, self.tr("Choose signature image"), "", self.tr("Images (*.png *.jpg *.jpeg)")
        )
        if path_str:
            self._image_path = Path(path_str)
            self._image_button.setText(self._image_path.name)

    def values(self) -> dict[str, Any]:
        if self._image_path is None:
            # _run_tool only catches PDFEditorError, not bare
            # exceptions - raising anything else here would crash the
            # GUI instead of showing a clean error dialog.
            raise OperationError("No signature image selected.")
        return {
            "image_path": self._image_path,
            "page": self.page.value(),
            "rect": (self.x0.value(), self.y0.value(), self.x1.value(), self.y1.value()),
        }
