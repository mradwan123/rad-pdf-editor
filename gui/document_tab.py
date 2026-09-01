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

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gui.controller import AppController
from gui.find_bar import FindBar
from gui.outline_panel import OutlinePanel
from gui.page_canvas import PageCanvas
from gui.rendering import ThumbnailRenderer

#: The thumbnail sidebar is navigation, not the main event - wide
#: enough for one column of thumbnails at the default 120 px.
_SIDEBAR_MAX_WIDTH = 260
_SIDEBAR_DEFAULT_WIDTH = 170


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

        # Phase 6c: the thumbnail grid becomes a navigation sidebar and
        # the page viewer becomes the primary pane. `thumbnail_list`
        # keeps its name and its behaviour (selection, context menu,
        # drag-reordering) - only where it sits on screen changed.
        self.canvas = PageCanvas()
        self.outline = OutlinePanel()

        # Pages and Outline share the sidebar. Both are navigation, and
        # a document usually has far more pages than bookmarks, so they
        # are tabs rather than a split - one is nearly always empty.
        self.sidebar = QTabWidget()
        self.sidebar.setDocumentMode(True)
        self.sidebar.addTab(self.thumbnail_list, self.tr("Pages"))
        self.sidebar.addTab(self.outline, self.tr("Outline"))
        self.sidebar.setMaximumWidth(_SIDEBAR_MAX_WIDTH)

        self.find_bar = FindBar()
        self.find_bar.setVisible(False)
        self.find_bar.result_activated.connect(self.canvas.scroll_to_rect)
        self.find_bar.results_changed.connect(self.canvas.set_highlights)
        self.outline.page_requested.connect(self.canvas.scroll_to_page)

        viewer = QWidget()
        viewer_layout = QVBoxLayout(viewer)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(0)
        viewer_layout.addWidget(self.find_bar)
        viewer_layout.addWidget(self.canvas, 1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(viewer)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([_SIDEBAR_DEFAULT_WIDTH, 700])

        # Clicking a thumbnail scrolls the page view to it. Deliberately
        # one-way: syncing the selection back from the viewer's scroll
        # position would fight the user's own selection (the context
        # menu and every page-range tool read selectedItems()).
        self.thumbnail_list.itemClicked.connect(self._on_thumbnail_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)

    def set_document(self, path: Path) -> None:
        """Point the viewer, outline and find bar at the same document.

        They share the canvas's `QPdfDocument` rather than each opening
        the file again - one OS handle per tab. That sharing makes the
        *order* here load-bearing: the models must be detached before
        the canvas destroys the document they point at, or they are left
        holding freed memory. Getting this wrong segfaulted the suite,
        not raised - see `detach_document`.
        """
        self.detach_document()
        loaded = self.canvas.set_document(path)
        document = self.canvas.document if loaded else None
        self.outline.set_document(document)
        self.find_bar.set_document(document)

    def detach_document(self) -> None:
        """Point the outline and find models away from the canvas's
        document, so it can be destroyed safely. Idempotent."""
        self.outline.set_document(None)
        self.find_bar.set_document(None)

    def clear_document(self) -> None:
        self.detach_document()
        self.canvas.clear()

    def release(self) -> None:
        """Drop every OS handle this tab holds on its working file -
        the thumbnail renderer's and the page view's - after detaching
        the models that share the latter. Must run before the tab's
        session dir is wiped: Windows refuses to unlink an open file,
        which would defeat the secure wipe.
        """
        self.detach_document()
        self.renderer.release()
        self.canvas.release()

    def _on_thumbnail_clicked(self, item: QListWidgetItem) -> None:
        page = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(page, int):
            self.canvas.scroll_to_page(page)

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
