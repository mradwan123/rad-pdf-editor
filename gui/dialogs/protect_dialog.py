from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class ProtectDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Protect (Add Password)"), parent)
        self.user_password = self.add_row(self.tr("User password"), QLineEdit())
        self.user_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.owner_password = self.add_row(
            self.tr("Owner password (optional)"), QLineEdit()
        )
        self.owner_password.setEchoMode(QLineEdit.EchoMode.Password)

    def values(self) -> dict[str, Any]:
        return {
            "user_password": self.user_password.text(),
            "owner_password": self.owner_password.text() or None,
        }
