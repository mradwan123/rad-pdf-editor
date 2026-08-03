from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLineEdit, QSpinBox, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.helpers import parse_int_list


class GrayscaleDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Grayscale"), parent)
        self.pages = self.add_row(self.tr("Pages (blank = all)"), QLineEdit())
        self.pages.setPlaceholderText(self.tr("e.g. 1,3,5"))

        self.dpi = QSpinBox()
        self.dpi.setRange(36, 1200)
        self.dpi.setValue(200)
        self.add_row(self.tr("Resolution (DPI)"), self.dpi)

    def values(self) -> dict[str, Any]:
        return {"pages": parse_int_list(self.pages.text()), "dpi": self.dpi.value()}
