from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class MetadataDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Metadata"), parent)
        self.title = self.add_row(self.tr("Title"), QLineEdit())
        self.author = self.add_row(self.tr("Author"), QLineEdit())
        self.subject = self.add_row(self.tr("Subject"), QLineEdit())
        self.keywords = self.add_row(self.tr("Keywords"), QLineEdit())
        self.creation_date = self.add_row(self.tr("Creation date"), QLineEdit())
        self.creation_date.setPlaceholderText(self.tr("ISO 8601, e.g. 2025-06-03T12:00:00+00:00"))
        self.mod_date = self.add_row(self.tr("Modification date"), QLineEdit())
        self.mod_date.setPlaceholderText(self.tr("ISO 8601, e.g. 2025-06-03T12:00:00+00:00"))

    def values(self) -> dict[str, Any]:
        raw = {
            "title": self.title.text(),
            "author": self.author.text(),
            "subject": self.subject.text(),
            "keywords": self.keywords.text(),
            "creation_date": self.creation_date.text(),
            "mod_date": self.mod_date.text(),
        }
        return {"fields": {k: v for k, v in raw.items() if v}}
