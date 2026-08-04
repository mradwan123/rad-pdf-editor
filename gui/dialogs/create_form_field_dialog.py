from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from gui.dialogs.base_tool_dialog import BaseToolDialog


def _coord_spinbox() -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, 20000)
    box.setSuffix(" pt")
    return box


class CreateFormFieldDialog(BaseToolDialog):
    """Authors a brand-new AcroForm field - distinct from Fill Form,
    which only edits values of fields that already exist. No
    click-to-place canvas yet (same as Sign): page + rect are entered
    explicitly."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Create Form Field"), parent)

        self.field_name = self.add_row(self.tr("Field name"), QLineEdit())

        self.field_type = self.add_row(self.tr("Field type"), QComboBox())
        self.field_type.addItems(["text", "checkbox", "radio"])

        self.page = self.add_row(self.tr("Page"), QSpinBox())
        self.page.setRange(1, 100000)

        self.x0 = _coord_spinbox()
        self.y0 = _coord_spinbox()
        self.x1 = _coord_spinbox()
        self.x1.setValue(200)
        self.y1 = _coord_spinbox()
        self.y1.setValue(20)

        rect_row = QWidget()
        rect_layout = QHBoxLayout(rect_row)
        rect_layout.setContentsMargins(0, 0, 0, 0)
        for box in (self.x0, self.y0, self.x1, self.y1):
            rect_layout.addWidget(box)
        self.add_row(self.tr("Position (x0, y0, x1, y1 - from bottom-left)"), rect_row)

        self.default_value = self.add_row(
            self.tr("Default text (text fields only)"), QLineEdit()
        )
        self.checked = self.add_row(
            self.tr("Initially checked (checkbox/radio only)"), QCheckBox()
        )

    def values(self) -> dict[str, Any]:
        return {
            "page": self.page.value(),
            "field_name": self.field_name.text(),
            "field_type": self.field_type.currentText(),
            "rect": (self.x0.value(), self.y0.value(), self.x1.value(), self.y1.value()),
            "default_value": self.default_value.text(),
            "checked": self.checked.isChecked(),
        }
