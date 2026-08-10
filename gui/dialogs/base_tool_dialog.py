"""Shared dialog shell every tool dialog subclasses (SPEC.md 6.2):
consistent layout across all tool dialogs - an options form, then
action buttons in the same position every time. Native Qt Fusion
widgets only, no custom component library.
"""

from __future__ import annotations

from typing import Any, TypeVar

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

_W = TypeVar("_W", bound=QWidget)

# Every tool dialog is 25% wider than its natural layout-computed width
# (see sizeHint() override below) - a deliberate sizing decision, not
# decoration, applied here once so it covers every current and future
# BaseToolDialog subclass automatically, including ones with
# non-standard constructors (FillFormDialog, WorkflowBuilderDialog).
_WIDTH_MULTIPLIER = 1.25


class BaseToolDialog(QDialog):
    """Subclass and call `add_row(label, widget)` / `add_full_width(widget)`
    from `__init__` to populate the options form, and implement
    `values()` to return the kwargs `ToolPlugin.build_operation()`
    expects. OK/Cancel wiring (accept/reject) is handled here.

    Every interactive widget added via `add_row` gets an accessible
    name set from its label automatically (SPEC.md 6.2: "accessible
    names ... set on every interactive widget as it's built").
    """

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        self._form = QFormLayout()
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(self._form)
        layout.addWidget(self._buttons)

    def add_row(self, label: str, widget: _W) -> _W:
        widget.setAccessibleName(label)
        self._form.addRow(label, widget)
        return widget

    def add_full_width(self, widget: _W) -> _W:
        self._form.addRow(widget)
        return widget

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override, fixed name
        """No dialog in this codebase sets an explicit size anywhere -
        every one is purely layout-driven via Qt's own `sizeHint()`
        machinery (confirmed by grep across gui/dialogs/*.py before
        this override was added; the only explicit `resize()` call in
        `gui/` is `MainWindow`'s own, unrelated to tool dialogs). Qt
        uses this return value to size the dialog on first show() when
        nothing else has resized it, so widening it here - rather than
        touching each dialog file - covers every subclass, including
        ones with non-standard constructors like `FillFormDialog`/
        `WorkflowBuilderDialog`.
        """
        hint = super().sizeHint()
        return QSize(int(hint.width() * _WIDTH_MULTIPLIER), hint.height())

    def values(self) -> dict[str, Any]:
        """Return the kwargs to pass to `ToolPlugin.build_operation()`.
        Subclasses must override this."""
        raise NotImplementedError
