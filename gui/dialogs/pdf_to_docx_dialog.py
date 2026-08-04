from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class PdfToDocxDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("PDF to Word"), parent)
        self.add_full_width(
            QLabel(self.tr("Converts the current document to a Word (.docx) file."))
        )

    def values(self) -> dict[str, Any]:
        return {}
