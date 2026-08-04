from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QSpinBox, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class PdfToJpgDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("PDF to JPG"), parent)
        self.page = QSpinBox()
        self.page.setRange(1, 100000)
        self.add_row(self.tr("Page"), self.page)

        self.dpi = QSpinBox()
        self.dpi.setRange(72, 600)
        self.dpi.setValue(200)
        self.add_row(self.tr("Image quality (DPI)"), self.dpi)

    def values(self) -> dict[str, Any]:
        return {"page": self.page.value(), "dpi": self.dpi.value()}
