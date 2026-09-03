"""Editing one text span, with the font warning decision 12 requires.

Phase 6h. The point of this dialog is the *warning*: when the original
font is not embedded, the replacement will look different, and the user
finds that out here rather than after saving. `resolve_font()` decides;
this shows it, renders both versions side by side, and lets the edit be
abandoned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from core.ops.text_edit import FontResolution, TextSpan
from gui.dialogs.base_tool_dialog import BaseToolDialog


class EditTextDialog(BaseToolDialog):
    """Replace a span's text, having been told what it will look like."""

    def __init__(
        self,
        parent: QWidget | None = None,
        span: TextSpan | None = None,
        resolution: FontResolution | None = None,
    ) -> None:
        super().__init__(self.tr("Edit Text"), parent)
        self._span = span
        self._resolution = resolution

        original = span.text if span else ""
        self.original = self.add_row(self.tr("Current text"), QLabel(original))
        self.new_text = self.add_row(self.tr("Replace with"), QLineEdit(original))
        self.new_text.selectAll()

        if span is not None:
            self.add_row(
                self.tr("Font"),
                QLabel(f"{span.font_name}  ·  {span.font_size:.0f}pt"),
            )

        # The comparison decision 12 asks for: the same words in the
        # font that will actually be used, next to the original.
        self.preview = QLabel(original or self.tr("(nothing selected)"))
        self.preview.setWordWrap(True)
        self.add_row(self.tr("Preview"), self.preview)
        self.new_text.textChanged.connect(self.preview.setText)

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setObjectName("fontWarning")
        self.add_full_width(self.warning)

        if resolution is not None and not resolution.is_exact:
            self.warning.setText("⚠  " + resolution.warning)
            # Show the preview in *a* substitute rather than the
            # original, so the difference is visible rather than
            # described. Helvetica is the fallback the operation uses.
            self.preview.setFont(QFont("Helvetica", 12))
        elif resolution is not None:
            self.warning.setText(
                self.tr("The original font is embedded and will be reused.")
            )
            self.warning.setAlignment(Qt.AlignmentFlag.AlignLeft)

    @property
    def would_substitute_font(self) -> bool:
        return self._resolution is not None and not self._resolution.is_exact

    def values(self) -> dict[str, Any]:
        if self._span is None:
            return {"page": 1, "rect": (0.0, 0.0, 1.0, 1.0), "new_text": self.new_text.text()}
        return {
            "page": self._span.page,
            "rect": self._span.rect,
            "new_text": self.new_text.text(),
        }


def build_edit_text_dialog(
    parent: QWidget | None, path: Path, page: int, x: float, y: float
) -> EditTextDialog | None:
    """The dialog for the span at a point, or None if there is no text
    there - the caller reports that rather than opening an empty form."""
    from core.ops.text_edit import resolve_font, span_at

    span = span_at(path, page, x, y)
    if span is None:
        return None
    return EditTextDialog(parent, span, resolve_font(path, page, span.font_name))
