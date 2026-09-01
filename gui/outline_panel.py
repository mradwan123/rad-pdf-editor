"""Document outline (bookmarks) for the sidebar.

Phase 6c (docs/GUI_PLAN.md §3.2). `QPdfBookmarkModel` is one of the
QtPdf model classes that works off a `QPdfDocument` with no `QPdfView`
involved (§2.1), so the outline costs a tree view and a signal.

Verified against a real table of contents rather than assumed: the
model exposes `Title` and `Page` per row, and nests sub-bookmarks as
child rows, so a `QTreeView` renders the hierarchy directly.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtPdf import QPdfBookmarkModel, QPdfDocument
from PySide6.QtWidgets import QLabel, QStackedWidget, QTreeView, QVBoxLayout, QWidget


class OutlinePanel(QWidget):
    """The document's bookmark tree; emits the page a row points at."""

    #: 1-based page number of the bookmark that was activated.
    page_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = QPdfBookmarkModel(self)

        self.tree = QTreeView()
        self.tree.setModel(self._model)
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName(self.tr("Document outline"))
        self.tree.activated.connect(self._on_activated)
        self.tree.clicked.connect(self._on_activated)

        self._empty = QLabel(self.tr("This document has no outline."))
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setObjectName("outlineEmpty")

        self._stack = QStackedWidget()
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self.tree)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

    def set_document(self, document: QPdfDocument | None) -> None:
        if document is None:
            self._model.setDocument(QPdfDocument(self))
        else:
            self._model.setDocument(document)
        self.tree.expandToDepth(1)
        self._stack.setCurrentWidget(self.tree if self.has_outline else self._empty)

    @property
    def has_outline(self) -> bool:
        return self._model.rowCount(QModelIndex()) > 0

    def _on_activated(self, index: QModelIndex) -> None:
        page = self._model.data(index, QPdfBookmarkModel.Role.Page.value)
        if isinstance(page, int):
            # QtPdf reports 0-based pages; everything user-facing in this
            # project is 1-based (see Operation.affected_pages, the
            # thumbnail UserRole data, every tool's `pages` parameter).
            self.page_requested.emit(page + 1)
