from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QDoubleSpinBox, QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.helpers import parse_int_list


class ResizeDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Resize"), parent)

        # Named page_width/page_height, not width/height - QWidget
        # already defines width()/height() as geometry methods, and
        # assigning over them breaks Qt's own layout machinery.
        self.page_width = QDoubleSpinBox()
        self.page_width.setRange(1, 20000)
        self.page_width.setValue(612.0)
        self.page_width.setSuffix(" pt")
        self.add_row(self.tr("Width"), self.page_width)

        self.page_height = QDoubleSpinBox()
        self.page_height.setRange(1, 20000)
        self.page_height.setValue(792.0)
        self.page_height.setSuffix(" pt")
        self.add_row(self.tr("Height"), self.page_height)

        self.pages = self.add_row(self.tr("Pages (blank = all)"), QLineEdit())
        self.pages.setPlaceholderText(self.tr("e.g. 1,3,5"))

    def values(self) -> dict[str, Any]:
        return {
            "width": self.page_width.value(),
            "height": self.page_height.value(),
            "pages": parse_int_list(self.pages.text()),
        }
