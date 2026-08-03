from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.helpers import parse_int_list


class ReorderPagesDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Reorder Pages"), parent)
        self.page_order = self.add_row(self.tr("New page order"), QLineEdit())
        self.page_order.setPlaceholderText(self.tr("full permutation, e.g. 3,1,2"))

    def values(self) -> dict[str, Any]:
        return {"page_order": parse_int_list(self.page_order.text())}
