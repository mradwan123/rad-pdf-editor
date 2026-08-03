from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLineEdit, QSpinBox, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.helpers import parse_int_list


class HeaderFooterDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Header / Footer"), parent)
        self.header_text = self.add_row(self.tr("Header text"), QLineEdit())
        self.footer_text = self.add_row(self.tr("Footer text"), QLineEdit())

        self.font_size = QSpinBox()
        self.font_size.setRange(6, 72)
        self.font_size.setValue(10)
        self.add_row(self.tr("Font size"), self.font_size)

        self.pages = self.add_row(self.tr("Pages (blank = all)"), QLineEdit())
        self.pages.setPlaceholderText(self.tr("e.g. 1,3,5"))

    def values(self) -> dict[str, Any]:
        return {
            "header_text": self.header_text.text(),
            "footer_text": self.footer_text.text(),
            "font_size": self.font_size.value(),
            "pages": parse_int_list(self.pages.text()),
        }
