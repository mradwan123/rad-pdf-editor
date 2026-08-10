"""Main window: thumbnail grid, Tools menu, undo/redo (SPEC.md section
2, Phase 1 scope: "basic thumbnail UI + undo/redo wired to the
framework")."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QImage, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.errors import OperationError, PDFEditorError
from core.logging_config import get_logger
from core.model.document import DocumentSession
from core.ops.forms import list_form_field_names
from core.session.recent_files import RecentFiles
from core.session.session_dir import SessionTempDir
from core.session.workflow_store import WorkflowStore
from gui.controller import AppController
from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.fill_form_dialog import FillFormDialog
from gui.dialogs.run_workflow_dialog import RunWorkflowDialog
from gui.dialogs.tool_dialog_registry import TOOL_DIALOGS, DialogFactory
from gui.dialogs.workflow_builder_dialog import WorkflowBuilderDialog
from gui.resources import build_logo_pixmap

log = get_logger(__name__)

_APP_NAME = "Rad PDF Editor"
_THUMBNAIL_SIZE = QSize(120, 160)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_APP_NAME)
        self.resize(900, 700)

        self.controller = AppController()
        self.recent_files = RecentFiles()

        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setAccessibleName(self.tr("Page thumbnails"))
        self.thumbnail_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbnail_list.setIconSize(_THUMBNAIL_SIZE)
        self.thumbnail_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumbnail_list.setMovement(QListWidget.Movement.Static)
        self.thumbnail_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        # Drag-and-drop page reordering: Qt's own InternalMove handles
        # the drag gesture and visual reordering; rowsMoved tells us
        # when a drop actually changed the order so we can apply the
        # corresponding ReorderPagesOperation (see _on_thumbnails_reordered).
        self.thumbnail_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.thumbnail_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.thumbnail_list.model().rowsMoved.connect(self._on_thumbnails_reordered)
        self.thumbnail_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.thumbnail_list.customContextMenuRequested.connect(self._show_thumbnail_context_menu)

        self.empty_state = self._build_empty_state()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty_state)
        self.stack.addWidget(self.thumbnail_list)
        self.setCentralWidget(self.stack)

        self.tool_actions: dict[str, QAction] = {}
        self._build_actions()
        self._refresh()

    def _build_empty_state(self) -> QWidget:
        """Branded welcome screen shown in place of the thumbnail grid
        when no document is open."""
        widget = QWidget()
        widget.setObjectName("emptyState")

        logo_label = QLabel()
        logo_label.setPixmap(build_logo_pixmap(128))
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_label = QLabel(_APP_NAME)
        title_label.setObjectName("emptyStateTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle_label = QLabel(self.tr("Open a PDF to get started"))
        subtitle_label.setObjectName("emptyStateSubtitle")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        open_button = QPushButton(self.tr("Open PDF..."))
        open_button.clicked.connect(self._open_document)

        layout = QVBoxLayout(widget)
        layout.addStretch(1)
        layout.addWidget(logo_label)
        layout.addSpacing(12)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        layout.addSpacing(16)
        layout.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        return widget

    # --- action / menu / toolbar setup ----------------------------------

    def _build_actions(self) -> None:
        self.open_action = QAction(self.tr("&Open..."), self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(self._open_document)

        self.save_as_action = QAction(self.tr("&Save As..."), self)
        self.save_as_action.setShortcut("Ctrl+S")
        self.save_as_action.triggered.connect(self._save_as)

        self.close_action = QAction(self.tr("&Close Document"), self)
        self.close_action.triggered.connect(self._close_document)

        self.undo_action = QAction(self.tr("&Undo"), self)
        self.undo_action.setShortcut("Ctrl+Z")
        self.undo_action.triggered.connect(self._undo)

        self.redo_action = QAction(self.tr("&Redo"), self)
        self.redo_action.setShortcut("Ctrl+Shift+Z")
        self.redo_action.triggered.connect(self._redo)

        file_menu = self.menuBar().addMenu(self.tr("&File"))
        file_menu.addAction(self.open_action)
        self.recent_files_menu = file_menu.addMenu(self.tr("Open &Recent"))
        self.recent_files_menu.aboutToShow.connect(self._populate_recent_files_menu)
        file_menu.addAction(self.save_as_action)
        file_menu.addAction(self.close_action)

        edit_menu = self.menuBar().addMenu(self.tr("&Edit"))
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)

        tools_menu = self.menuBar().addMenu(self.tr("&Tools"))
        for tool_id, dialog_cls in TOOL_DIALOGS.items():
            plugin = self.controller.get_plugin(tool_id)
            action = QAction(plugin.display_name, self)
            action.triggered.connect(self._make_tool_handler(tool_id, dialog_cls))
            tools_menu.addAction(action)
            self.tool_actions[tool_id] = action

        # Building/running a workflow is document-independent (Build
        # doesn't touch the currently-open document at all; Run works
        # against a standalone input/output pair), so these two actions
        # are hand-wired here rather than going through TOOL_DIALOGS /
        # the Tools-menu loop above, and are never added to
        # self.tool_actions (which _update_action_state disables when
        # no document is open).
        self.build_workflow_action = QAction(self.tr("&Build Workflow..."), self)
        self.build_workflow_action.triggered.connect(self._build_workflow)

        self.run_workflow_action = QAction(self.tr("&Run Workflow..."), self)
        self.run_workflow_action.triggered.connect(self._run_workflow)

        workflows_menu = self.menuBar().addMenu(self.tr("&Workflows"))
        workflows_menu.addAction(self.build_workflow_action)
        workflows_menu.addAction(self.run_workflow_action)

        toolbar = QToolBar(self.tr("Main"))
        toolbar.setAccessibleName(self.tr("Main toolbar"))
        self.addToolBar(toolbar)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.save_as_action)
        toolbar.addSeparator()
        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)

    def _make_tool_handler(self, tool_id: str, dialog_cls: DialogFactory) -> Any:
        return lambda: self._run_tool(tool_id, dialog_cls)

    @contextmanager
    def _busy_cursor(self) -> Iterator[None]:
        """Visual feedback around a synchronous operation that might
        take a moment (large PDFs, compress, N-up rendering, etc.) -
        deliberately not a background-thread rewrite, just making the
        otherwise-unresponsive-looking wait visible rather than silent."""
        self.statusBar().showMessage(self.tr("Working..."))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()

    # --- document lifecycle ----------------------------------------------

    def _open_document(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        path_str, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.tr("Open PDF"),
            "",
            self.tr("PDF files (*.pdf)"),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path_str:
            return
        self._open_document_path(Path(path_str))

    def _open_document_path(self, path: Path) -> None:
        """Shared by the Open dialog and the Recent Files menu."""
        try:
            self.controller.open_document(path)
        except PDFEditorError as exc:
            # A recent-file entry that fails to open (moved/deleted
            # since last time) is stale - drop it so it doesn't keep
            # reappearing in the menu instead of just erroring forever.
            self.recent_files.remove(path)
            self._show_error(exc)
            return
        self.recent_files.add(path)
        self._refresh()

    def _populate_recent_files_menu(self) -> None:
        self.recent_files_menu.clear()
        paths = self.recent_files.list()
        if not paths:
            empty_action = self.recent_files_menu.addAction(self.tr("(No recent files)"))
            empty_action.setEnabled(False)
            return
        for path in paths:
            action = self.recent_files_menu.addAction(path.name)
            action.setToolTip(str(path))
            action.triggered.connect(self._make_recent_file_handler(path))
        self.recent_files_menu.addSeparator()
        clear_action = self.recent_files_menu.addAction(self.tr("Clear Recent Files"))
        clear_action.triggered.connect(self.recent_files.clear)

    def _make_recent_file_handler(self, path: Path) -> Any:
        return lambda: self._open_recent_file(path)

    def _open_recent_file(self, path: Path) -> None:
        if not self._confirm_discard_if_dirty():
            return
        self._open_document_path(path)

    def _save_as(self) -> bool:
        """Returns True if the document was actually saved (used by
        the unsaved-changes prompt to know whether to proceed)."""
        if not self.controller.is_open:
            return False
        path_str, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.tr("Save PDF As"),
            "",
            self.tr("PDF files (*.pdf)"),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path_str:
            return False
        try:
            self.controller.save_as(Path(path_str))
        except PDFEditorError as exc:
            self._show_error(exc)
            return False
        self.statusBar().showMessage(self.tr("Saved to {0}").format(path_str), 5000)
        return True

    def _close_document(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        self.controller.close_session()
        self._refresh()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override, fixed name
        if not self._confirm_discard_if_dirty():
            event.ignore()
            return
        self.controller.close_session()
        super().closeEvent(event)

    def _confirm_discard_if_dirty(self) -> bool:
        """True if it's safe to proceed (open a different file, close,
        or exit): either there's nothing that could be lost, or the
        user explicitly chose to save or discard. False means the
        caller should abort and leave everything as-is."""
        if not self.controller.is_dirty:
            return True
        response = QMessageBox.warning(
            self,
            self.tr("Unsaved Changes"),
            self.tr("This document has unsaved changes. Save before continuing?"),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if response == QMessageBox.StandardButton.Cancel:
            return False
        if response == QMessageBox.StandardButton.Save:
            return self._save_as()
        return True  # Discard

    # --- undo/redo ---------------------------------------------------------

    def _undo(self) -> None:
        try:
            with self._busy_cursor():
                self.controller.undo()
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

    def _redo(self) -> None:
        try:
            with self._busy_cursor():
                self.controller.redo()
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

    # --- tools ---------------------------------------------------------------

    def _run_tool(self, tool_id: str, dialog_cls: DialogFactory) -> None:
        if tool_id != "merge" and not self.controller.is_open:
            self._show_error_message(self.tr("Open a document first."))
            return

        if tool_id == "fill_form":
            working_path = self.controller.doc.working_path
            assert working_path is not None  # guaranteed by the is_open check above
            dialog: BaseToolDialog = FillFormDialog(list_form_field_names(working_path), self)
        else:
            dialog = dialog_cls(self)

        if dialog.exec() != BaseToolDialog.DialogCode.Accepted:
            return
        try:
            plugin = self.controller.get_plugin(tool_id)
            operation = plugin.build_operation(**dialog.values())
            with self._busy_cursor():
                self.controller.apply_operation(operation)
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

    # --- workflows -------------------------------------------------------------

    def _build_workflow(self) -> None:
        """Document-independent by design: a workflow is a saved,
        named sequence of Operations, not a live edit against
        self.controller's currently-open document (that's what
        _run_tool is for)."""
        dialog = WorkflowBuilderDialog(self.controller.registry, self)
        if dialog.exec() != BaseToolDialog.DialogCode.Accepted:
            return
        pipeline = dialog.build_pipeline()
        try:
            WorkflowStore().save(pipeline)
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self.statusBar().showMessage(
            self.tr("Saved workflow '{0}'").format(pipeline.name), 5000
        )

    def _run_workflow(self) -> None:
        """A standalone batch run against an input/output file pair -
        deliberately not touching self.controller or the currently-open
        document at all, the same "external file(s) in" shape Merge and
        the Phase 3 conversion ops already use, run through a throwaway
        SessionTempDir rather than the app's live editing session."""
        names = WorkflowStore().list_workflows()
        if not names:
            QMessageBox.information(
                self,
                self.tr("Run Workflow"),
                self.tr("No saved workflows yet. Use Build Workflow... first."),
            )
            return

        dialog = RunWorkflowDialog(names, self)
        if dialog.exec() != BaseToolDialog.DialogCode.Accepted:
            return

        try:
            values = dialog.values()
            with self._busy_cursor():
                pipeline = WorkflowStore().load(values["workflow_name"], self.controller.registry)
                with SessionTempDir() as session:
                    input_path: Path = values["input_path"]
                    working = session.path / f"working{input_path.suffix or '.pdf'}"
                    shutil.copyfile(input_path, working)
                    doc = DocumentSession(working_path=working, source_path=input_path)
                    result = pipeline.run(doc)
                    if result.working_path is None:
                        raise OperationError("Workflow produced no output document.")
                    shutil.copyfile(result.working_path, values["output_path"])
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self.statusBar().showMessage(
            self.tr("Workflow run complete: {0}").format(values["output_path"]), 5000
        )

    # --- rendering ------------------------------------------------------------

    def _refresh(self) -> None:
        self.thumbnail_list.clear()
        working_path = self.controller.doc.working_path
        if self.controller.is_open and working_path is not None:
            self._render_thumbnails(working_path)
            label = self.controller.doc.display_name or (
                self.controller.doc.source_path.name
                if self.controller.doc.source_path
                else self.tr("Untitled")
            )
            self.setWindowTitle(f"{_APP_NAME} - {label}")
            self.statusBar().showMessage(self.tr("{0} page(s)").format(self.thumbnail_list.count()))
            self.stack.setCurrentWidget(self.thumbnail_list)
        else:
            self.setWindowTitle(_APP_NAME)
            self.statusBar().showMessage(self.tr("No document open"))
            self.stack.setCurrentWidget(self.empty_state)
        self._update_action_state()

    def _render_thumbnails(self, path: Path) -> None:
        # No parent: this is a short-lived, throwaway document used
        # only to render thumbnails for this one _refresh() call. A
        # `self`-parented QPdfDocument would live as long as
        # MainWindow itself - confirmed via review to leak one
        # instance per call (every operation/undo/redo triggers a
        # _refresh()), unbounded over a session.
        pdf_doc = QPdfDocument()
        if pdf_doc.load(str(path)) != QPdfDocument.Error.None_:
            log.error("Could not load PDF for thumbnail rendering: %s", path)
            return
        for i in range(pdf_doc.pageCount()):
            rendered = pdf_doc.render(i, _THUMBNAIL_SIZE)
            # QtPdf leaves any unpainted area of the page fully
            # transparent (alpha=0) rather than opaque white - most
            # visible on blank/near-empty pages. Composite onto a
            # white backdrop so a thumbnail always reads as a page,
            # not as "nothing" wherever the source PDF painted nothing.
            page_image = QImage(_THUMBNAIL_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
            page_image.fill(Qt.GlobalColor.white)
            painter = QPainter(page_image)
            painter.drawImage(0, 0, rendered)
            painter.end()
            item = QListWidgetItem(
                QIcon(QPixmap.fromImage(page_image)), self.tr("Page {0}").format(i + 1)
            )
            # Tracks which page this item represents in the *current*
            # working document, independent of drag position - read
            # back in visual order by _apply_thumbnail_reorder to
            # build the ReorderPagesOperation's page_order.
            item.setData(Qt.ItemDataRole.UserRole, i + 1)
            self.thumbnail_list.addItem(item)

    def _on_thumbnails_reordered(self, *args: object) -> None:
        # Deferred to the next event loop turn: applying an operation
        # (which rebuilds thumbnail_list via _refresh) from directly
        # inside the model's own rowsMoved signal would fight with
        # Qt's own post-move bookkeeping for the same signal - the
        # standard-idiom fix is to let the current emission finish
        # first (see QTimer.singleShot(0, ...)).
        QTimer.singleShot(0, self._apply_thumbnail_reorder)

    def _apply_thumbnail_reorder(self) -> None:
        page_order = [
            self.thumbnail_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.thumbnail_list.count())
        ]
        if page_order == list(range(1, len(page_order) + 1)):
            # No actual change (e.g. a drag that ends where it started,
            # or - for a multi-item drag - a second rowsMoved signal
            # for the same gesture arriving after the first one already
            # applied the reorder and _refresh() reset everything to
            # sequential order). Applying here would push a no-op
            # ReorderPagesOperation onto the undo stack for nothing.
            return
        try:
            plugin = self.controller.get_plugin("reorder_pages")
            operation = plugin.build_operation(page_order=page_order)
            with self._busy_cursor():
                self.controller.apply_operation(operation)
        except PDFEditorError as exc:
            self._show_error(exc)
        # Refresh either way: on success this rebuilds thumbnails from
        # the new document (confirming the drag), on failure it
        # discards the stale drag-and-drop visual order so the grid
        # matches the actual (unchanged) document again.
        self._refresh()

    def _show_thumbnail_context_menu(self, pos: QPoint) -> None:
        selected = self.thumbnail_list.selectedItems()
        if not selected:
            return
        pages = sorted(int(item.data(Qt.ItemDataRole.UserRole)) for item in selected)

        menu = QMenu(self)
        rotate_left_action = menu.addAction(self.tr("Rotate Left"))
        rotate_right_action = menu.addAction(self.tr("Rotate Right"))
        menu.addSeparator()
        delete_action = menu.addAction(self.tr("Delete Selected"))

        chosen = menu.exec(self.thumbnail_list.viewport().mapToGlobal(pos))
        if chosen is rotate_left_action:
            self._apply_thumbnail_rotate(pages, angle=-90)
        elif chosen is rotate_right_action:
            self._apply_thumbnail_rotate(pages, angle=90)
        elif chosen is delete_action:
            self._apply_thumbnail_delete(pages)

    def _apply_thumbnail_rotate(self, pages: list[int], angle: int) -> None:
        try:
            plugin = self.controller.get_plugin("rotate_pages")
            operation = plugin.build_operation(angle=angle, pages=pages)
            with self._busy_cursor():
                self.controller.apply_operation(operation)
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

    def _apply_thumbnail_delete(self, pages: list[int]) -> None:
        try:
            plugin = self.controller.get_plugin("delete_pages")
            operation = plugin.build_operation(pages=pages)
            with self._busy_cursor():
                self.controller.apply_operation(operation)
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

    def _update_action_state(self) -> None:
        is_open = self.controller.is_open
        self.save_as_action.setEnabled(is_open)
        self.close_action.setEnabled(is_open)
        self.undo_action.setEnabled(self.controller.can_undo)
        self.redo_action.setEnabled(self.controller.can_redo)
        for tool_id, action in self.tool_actions.items():
            action.setEnabled(is_open or tool_id == "merge")

    # --- error display -----------------------------------------------------------

    def _show_error(self, exc: Exception) -> None:
        self._show_error_message(str(exc))

    def _show_error_message(self, message: str) -> None:
        QMessageBox.critical(self, self.tr("Error"), message)
