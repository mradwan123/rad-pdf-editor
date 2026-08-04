from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class PdfToXlsxDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("PDF to Excel"), parent)
        self.add_full_width(
            QLabel(
                self.tr("Extracts tables from the current document into an Excel workbook.")
            )
        )

    def values(self) -> dict[str, Any]:
        return {}
