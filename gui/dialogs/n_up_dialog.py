from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class NUpDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("N-up"), parent)

        self.pages_per_sheet = QComboBox()
        self.pages_per_sheet.addItems(["2", "4", "6", "9"])
        self.add_row(self.tr("Pages per sheet"), self.pages_per_sheet)

        self.sheet_width = QDoubleSpinBox()
        self.sheet_width.setRange(1, 20000)
        self.sheet_width.setValue(612.0)
        self.sheet_width.setSuffix(" pt")
        self.add_row(self.tr("Sheet width"), self.sheet_width)

        self.sheet_height = QDoubleSpinBox()
        self.sheet_height.setRange(1, 20000)
        self.sheet_height.setValue(792.0)
        self.sheet_height.setSuffix(" pt")
        self.add_row(self.tr("Sheet height"), self.sheet_height)

    def values(self) -> dict[str, Any]:
        return {
            "pages_per_sheet": int(self.pages_per_sheet.currentText()),
            "sheet_width": self.sheet_width.value(),
            "sheet_height": self.sheet_height.value(),
        }
