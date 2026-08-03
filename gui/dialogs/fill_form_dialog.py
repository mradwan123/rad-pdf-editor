from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class FillFormDialog(BaseToolDialog):
    """Unlike every other tool dialog, this needs to know the open
    document's actual AcroForm field names before it can lay out its
    form - so it's constructed with `field_names` up front rather than
    just `(parent)`. See gui/main_window.py's `_run_tool` for the
    special-cased instantiation."""

    def __init__(self, field_names: list[str], parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Fill Form"), parent)
        self._inputs: dict[str, QLineEdit] = {}
        if not field_names:
            self.add_full_width(QLabel(self.tr("This document has no fillable form fields.")))
        for name in field_names:
            self._inputs[name] = self.add_row(name, QLineEdit())

    def values(self) -> dict[str, Any]:
        return {
            "field_values": {
                name: edit.text() for name, edit in self._inputs.items() if edit.text()
            }
        }
