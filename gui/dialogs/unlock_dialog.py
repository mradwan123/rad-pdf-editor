from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class UnlockDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Unlock (Remove Password)"), parent)
        self.password = self.add_row(self.tr("Current password"), QLineEdit())
        self.password.setEchoMode(QLineEdit.EchoMode.Password)

    def values(self) -> dict[str, Any]:
        return {"password": self.password.text()}
