from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QDoubleSpinBox, QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.helpers import parse_int_list


def _margin_spinbox() -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, 5000)
    box.setSuffix(" pt")
    return box


class CropDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Crop"), parent)
        self.margin_top = self.add_row(self.tr("Top margin"), _margin_spinbox())
        self.margin_right = self.add_row(self.tr("Right margin"), _margin_spinbox())
        self.margin_bottom = self.add_row(self.tr("Bottom margin"), _margin_spinbox())
        self.margin_left = self.add_row(self.tr("Left margin"), _margin_spinbox())
        self.pages = self.add_row(self.tr("Pages (blank = all)"), QLineEdit())
        self.pages.setPlaceholderText(self.tr("e.g. 1,3,5"))

    def values(self) -> dict[str, Any]:
        return {
            "margin_top": self.margin_top.value(),
            "margin_right": self.margin_right.value(),
            "margin_bottom": self.margin_bottom.value(),
            "margin_left": self.margin_left.value(),
            "pages": parse_int_list(self.pages.text()),
        }
