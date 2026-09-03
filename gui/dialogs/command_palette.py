"""Searchable launcher for every tool and command.

Phase 6g (docs/GUI_PLAN.md §3.6). Forty tools across eight Tools
submenus plus Annotate, Edit and View is more than a menu bar can make
findable - the palette is how you reach a tool whose category you have
forgotten.

A plain `QDialog` subclass, not a `QMenu` or a `QCompleter` popup, and
that is a testing constraint as much as a design one: `QMenu.exec` and
an instance `QMessageBox.exec` are compiled methods that
`patch.object` silently fails to intercept, so a menu-driven palette
could not be exercised headlessly at all (CLAUDE.md documents both
traps the hard way). Every `BaseToolDialog` in this project patches
cleanly because it is a real Python class; this follows that.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


class Command:
    """One launchable thing: a label, where it lives, and what it does."""

    def __init__(
        self,
        label: str,
        category: str,
        run: Callable[[], None],
        keywords: str = "",
    ) -> None:
        self.label = label
        self.category = category
        self.run = run
        #: Extra searchable text that is not shown - a tool's `tool_id`,
        #: so "docx" finds "Word to PDF". Display names are written for
        #: users, not for searching, and the id is often what someone
        #: half-remembers.
        self.keywords = keywords

    @property
    def haystack(self) -> str:
        return f"{self.category} {self.label} {self.keywords}".lower()


def matches(command: Command, query: str) -> bool:
    """Every whitespace-separated term must appear somewhere.

    Deliberately not fuzzy/subsequence matching: with 40 similarly
    named tools subsequence matching returns almost everything for a
    short query, which is worse than nothing. Requiring every term keeps
    "page num" -> Page Numbers, and "docx pdf" -> Word to PDF via the
    tool_id keywords.
    """
    return all(term in command.haystack for term in query.lower().split())


class CommandPalette(QDialog):
    """Type to filter, Enter to run."""

    def __init__(self, commands: list[Command], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Commands"))
        self.setModal(True)
        self._commands = commands
        self.chosen: Command | None = None

        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr("Type a command..."))
        self.search.setAccessibleName(self.tr("Command search"))
        self.search.textChanged.connect(self._refilter)
        self.search.returnPressed.connect(self._accept_current)

        self.list = QListWidget()
        self.list.setAccessibleName(self.tr("Matching commands"))
        self.list.itemActivated.connect(lambda _item: self._accept_current())
        self.list.itemClicked.connect(lambda _item: self._accept_current())

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        self.resize(520, 420)
        self._refilter("")

    def _refilter(self, query: str) -> None:
        self.list.clear()
        for command in self._commands:
            if not query or matches(command, query):
                item = QListWidgetItem(f"{command.category}  ·  {command.label}")
                item.setData(Qt.ItemDataRole.UserRole, command)
                self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _accept_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.chosen = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        # Up/Down move the result list while the search box keeps focus,
        # so a query can be refined without reaching for the mouse.
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.list.count():
            step = 1 if event.key() == Qt.Key.Key_Down else -1
            self.list.setCurrentRow(
                max(0, min(self.list.count() - 1, self.list.currentRow() + step))
            )
            return
        super().keyPressEvent(event)
