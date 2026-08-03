from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class RenameDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Rename"), parent)
        self.new_name = self.add_row(self.tr("New name"), QLineEdit())

    def values(self) -> dict[str, Any]:
        return {"new_name": self.new_name.text()}
