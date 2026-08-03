from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox, QLineEdit, QSpinBox, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.helpers import parse_int_list

_POSITIONS = ["bottom-right", "bottom-left", "bottom-center", "top-right", "top-left"]


class BatesNumberingDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Page Numbers"), parent)
        self.prefix = self.add_row(self.tr("Prefix"), QLineEdit())

        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.start.setValue(1)
        self.add_row(self.tr("Start at"), self.start)

        self.digits = QSpinBox()
        self.digits.setRange(1, 10)
        self.digits.setValue(5)
        self.add_row(self.tr("Digits"), self.digits)

        self.position = QComboBox()
        self.position.addItems(_POSITIONS)
        self.add_row(self.tr("Position"), self.position)

        self.font_size = QSpinBox()
        self.font_size.setRange(6, 72)
        self.font_size.setValue(10)
        self.add_row(self.tr("Font size"), self.font_size)

        self.pages = self.add_row(self.tr("Pages (blank = all)"), QLineEdit())
        self.pages.setPlaceholderText(self.tr("e.g. 1,3,5"))

    def values(self) -> dict[str, Any]:
        return {
            "prefix": self.prefix.text(),
            "start": self.start.value(),
            "digits": self.digits.value(),
            "position": self.position.currentText(),
            "font_size": self.font_size.value(),
            "pages": parse_int_list(self.pages.text()),
        }
