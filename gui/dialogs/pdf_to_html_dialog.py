from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class PdfToHtmlDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("PDF to HTML"), parent)
        self.add_full_width(
            QLabel(self.tr("Exports the current document's text as a simple HTML file."))
        )

    def values(self) -> dict[str, Any]:
        return {}
