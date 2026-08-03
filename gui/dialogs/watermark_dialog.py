from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QDoubleSpinBox, QLineEdit, QSpinBox, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class WatermarkDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Watermark"), parent)
        self.text = self.add_row(self.tr("Text"), QLineEdit())

        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(0.05, 1.0)
        self.opacity.setSingleStep(0.05)
        self.opacity.setValue(0.3)
        self.add_row(self.tr("Opacity"), self.opacity)

        self.font_size = QSpinBox()
        self.font_size.setRange(8, 200)
        self.font_size.setValue(40)
        self.add_row(self.tr("Font size"), self.font_size)

    def values(self) -> dict[str, Any]:
        return {
            "text": self.text.text(),
            "opacity": self.opacity.value(),
            "font_size": self.font_size.value(),
        }
