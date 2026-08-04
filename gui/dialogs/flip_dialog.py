from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox, QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.helpers import parse_int_list


class FlipDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Flip"), parent)
        self.direction = self.add_row(self.tr("Direction"), QComboBox())
        self.direction.addItem(self.tr("Horizontal"), "horizontal")
        self.direction.addItem(self.tr("Vertical"), "vertical")
        self.pages = self.add_row(self.tr("Pages (blank = all)"), QLineEdit())
        self.pages.setPlaceholderText(self.tr("e.g. 1,3,5"))

    def values(self) -> dict[str, Any]:
        return {
            "direction": self.direction.currentData(),
            "pages": parse_int_list(self.pages.text()),
        }
