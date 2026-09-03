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
    QLineEdit,
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

    def set_page_selection(self, pages: list[int]) -> None:
        """Prefill this dialog's page-range field from the pages the
        user already selected in the sidebar.

        Twelve of the tool dialogs express a page range as a
        comma-separated `QLineEdit` named `pages`, so this fills that
        one field by convention rather than each dialog reimplementing
        it. A dialog without such a field simply has nothing to do -
        which is why this looks the attribute up instead of requiring
        an override.

        Without it, selecting pages 2, 5 and 9 in the sidebar and then
        opening Rotate still asked the user to *type* "2,5,9".
        """
        if not pages:
            return
        field = getattr(self, "pages", None)
        if isinstance(field, QLineEdit) and not field.text().strip():
            # Only when empty: a value the dialog set for itself, or one
            # the user has already typed, is not ours to overwrite.
            field.setText(",".join(str(p) for p in pages))

    def release_resources(self) -> None:
        """Drop any OS resource (an open file handle, a loaded
        document) this dialog holds. Called by whoever ran the dialog,
        once it's finished with - a no-op for the ordinary dialogs,
        which only own widgets.

        Why this exists rather than relying on the dialog being
        destroyed: every tool dialog is parented to `MainWindow`, so
        Qt keeps it alive long after `exec()` returns, and the working
        file it may have open lives in the session temp dir that
        `AppController.close_session()` securely wipes. Windows
        refuses to overwrite or unlink a file any handle still has
        open, so a dialog still holding one turns "wipe the
        confidential working copy on close" (SPEC.md section 1) into a
        `SecurityError` - see `SignDialog.release_resources`.
        """
