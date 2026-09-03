"""The undo stack, made visible.

Phase 6g (docs/GUI_PLAN.md §3.6). Every `Operation` in this codebase
has described itself since Phase 0 - `describe()` feeds the audit log -
and until now the GUI showed none of it. The stack existed and was
invisible.

Deliberately read-only, with one exception: clicking an entry undoes or
redoes *to* that point, by stepping the existing undo/redo one at a
time. Jumping directly would need the document model to address a
history position, which it does not, and inventing that here would put
a second notion of "current state" next to the one `DocumentSession`
already owns.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


class HistoryPanel(QWidget):
    """Applied operations, oldest first, with the redoable ones greyed."""

    #: How many steps to move: negative undoes, positive redoes.
    step_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.list = QListWidget()
        self.list.setAccessibleName(self.tr("Edit history"))
        self.list.itemClicked.connect(self._on_clicked)

        self.empty = QLabel(self.tr("No edits yet."))
        self.empty.setObjectName("historyEmpty")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.empty)
        layout.addWidget(self.list)
        self._applied = 0

    def update_history(self, applied: list[str], redoable: list[str]) -> None:
        """`applied` is the undo stack oldest-first; `redoable` is what
        has been undone and could come back, in the order it would."""
        self.list.clear()
        self._applied = len(applied)
        for description in applied:
            self.list.addItem(QListWidgetItem(description))
        for description in redoable:
            item = QListWidgetItem(description)
            item.setForeground(self.palette().placeholderText())
            self.list.addItem(item)
        if applied:
            self.list.setCurrentRow(len(applied) - 1)
        self.empty.setVisible(not applied and not redoable)
        self.list.setVisible(bool(applied or redoable))

    def _on_clicked(self, item: QListWidgetItem) -> None:
        row = self.list.row(item)
        # Rows [0, applied) are done; the rest are undone. Clicking the
        # last applied row is a no-op, which is the right answer.
        self.step_requested.emit(row + 1 - self._applied)
