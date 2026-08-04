from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QLineEdit, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class OcrDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("OCR"), parent)

        self.language = self.add_row(
            self.tr("Language (Tesseract code)"), QLineEdit("eng")
        )
        self.language.setPlaceholderText(self.tr("e.g. eng, fra, deu"))

        self.force_ocr = self.add_row(
            self.tr("Force"),
            QCheckBox(
                self.tr("Force OCR (re-OCR pages that already have text)")
            ),
        )
        self.skip_text = self.add_row(
            self.tr("Skip"), QCheckBox(self.tr("Skip pages that already have text"))
        )

        # Mutually exclusive per the backend's __post_init__ (rejects
        # both True at once) - give immediate UI feedback rather than
        # relying solely on the backend to reject the combination.
        self.force_ocr.toggled.connect(self._on_force_toggled)
        self.skip_text.toggled.connect(self._on_skip_toggled)

        self.skip_text.setChecked(True)
        self.force_ocr.setChecked(False)

    def _on_force_toggled(self, checked: bool) -> None:
        if checked:
            self.skip_text.setChecked(False)

    def _on_skip_toggled(self, checked: bool) -> None:
        if checked:
            self.force_ocr.setChecked(False)

    def values(self) -> dict[str, Any]:
        return {
            "language": self.language.text(),
            "force_ocr": self.force_ocr.isChecked(),
            "skip_text": self.skip_text.isChecked(),
        }
