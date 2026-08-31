"""One open document tab: an `AppController` plus the thumbnail grid
showing it.

Each tab is a fully independent editing session - its own
`SessionTempDir`, `DocumentSession`, undo/redo stack, dirty flag and
autosave journal (all inside its own `AppController`) - so nothing
about editing one document can reach another. Only the plugin
`Registry`, the `AuditLog` and the recent-files list are shared
app-wide, and `MainWindow` owns those.

The thumbnail `QListWidget` lives here rather than in `MainWindow`
because there is one per document now; `MainWindow` connects its own
handlers to each tab's list signals as tabs are created.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QListWidget, QVBoxLayout, QWidget

from gui.controller import AppController
from gui.rendering import ThumbnailRenderer


class DocumentTab(QWidget):
    """The page widget for one tab of `MainWindow.tab_widget`."""

    def __init__(
        self,
        controller: AppController,
        thumbnail_size: QSize,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller

        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setAccessibleName(self.tr("Page thumbnails"))
        self.thumbnail_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbnail_list.setIconSize(thumbnail_size)
        self.thumbnail_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumbnail_list.setMovement(QListWidget.Movement.Static)
        self.thumbnail_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        # Drag-and-drop page reordering: Qt's own InternalMove handles
        # the drag gesture and visual reordering; rowsMoved tells
        # MainWindow when a drop actually changed the order so it can
        # apply the corresponding ReorderPagesOperation.
        self.thumbnail_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.thumbnail_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.thumbnail_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        # One renderer per tab, so each document gets its own page
        # cache and its own QPdfDocument. Parented to the tab (its
        # *document* handle is what has to be released deterministically
        # - see ThumbnailRenderer.release).
        self.renderer = ThumbnailRenderer(self.thumbnail_list, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.thumbnail_list)

    def document_name(self) -> str:
        """User-facing name for this tab's document: a Rename
        operation's `display_name` wins, then the source file's name,
        then a placeholder for a document built from scratch (Merge)
        or recovered without a known original."""
        doc = self.controller.doc
        if doc.display_name:
            return doc.display_name
        if doc.source_path is not None:
            return doc.source_path.name
        return self.tr("Untitled")
