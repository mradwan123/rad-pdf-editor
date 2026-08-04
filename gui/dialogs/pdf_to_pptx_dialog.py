from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QSpinBox, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class PdfToPptxDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("PDF to PowerPoint"), parent)
        self.dpi = QSpinBox()
        self.dpi.setRange(72, 600)
        self.dpi.setValue(150)
        self.add_row(self.tr("Image quality (DPI)"), self.dpi)

    def values(self) -> dict[str, Any]:
        return {"dpi": self.dpi.value()}
