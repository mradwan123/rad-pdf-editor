from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.helpers import parse_int_list


class ExtractPagesDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Split / Extract Pages"), parent)
        self.pages = self.add_row(self.tr("Pages to keep"), QLineEdit())
        self.pages.setPlaceholderText(self.tr("e.g. 1,3,5"))

    def values(self) -> dict[str, Any]:
        return {"pages": parse_int_list(self.pages.text())}
