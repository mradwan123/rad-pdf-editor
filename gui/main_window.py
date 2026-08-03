"""Main window: thumbnail grid, Tools menu, undo/redo (SPEC.md section
2, Phase 1 scope: "basic thumbnail UI + undo/redo wired to the
framework")."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QToolBar,
    QWidget,
)

from core.errors import PDFEditorError
from core.logging_config import get_logger
from gui.controller import AppController
from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.compress_dialog import CompressDialog
from gui.dialogs.delete_pages_dialog import DeletePagesDialog
from gui.dialogs.extract_pages_dialog import ExtractPagesDialog
from gui.dialogs.merge_dialog import MergeDialog
from gui.dialogs.metadata_dialog import MetadataDialog
from gui.dialogs.protect_dialog import ProtectDialog
from gui.dialogs.rename_dialog import RenameDialog
from gui.dialogs.reorder_pages_dialog import ReorderPagesDialog
from gui.dialogs.rotate_dialog import RotateDialog
from gui.dialogs.unlock_dialog import UnlockDialog
from gui.dialogs.watermark_dialog import WatermarkDialog

log = get_logger(__name__)

_THUMBNAIL_SIZE = QSize(120, 160)

#: Every concrete BaseToolDialog subclass takes (parent=None), a
#: different signature than BaseToolDialog.__init__'s own (title,
#: parent) - so the factory type is this Callable, not type[BaseToolDialog].
_DialogFactory = Callable[[QWidget | None], BaseToolDialog]

#: tool_id -> dialog class, drives the Tools menu generically instead
#: of one hand-written branch per tool.
_TOOL_DIALOGS: dict[str, _DialogFactory] = {
    "merge": MergeDialog,
    "extract_pages": ExtractPagesDialog,
    "reorder_pages": ReorderPagesDialog,
    "rotate_pages": RotateDialog,
    "delete_pages": DeletePagesDialog,
    "compress": CompressDialog,
    "set_metadata": MetadataDialog,
    "rename": RenameDialog,
    "protect": ProtectDialog,
    "unlock": UnlockDialog,
    "watermark": WatermarkDialog,
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(self.tr("PDF Editor"))
        self.resize(900, 700)

        self.controller = AppController()

        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setAccessibleName(self.tr("Page thumbnails"))
        self.thumbnail_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbnail_list.setIconSize(_THUMBNAIL_SIZE)
        self.thumbnail_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumbnail_list.setMovement(QListWidget.Movement.Static)
        self.thumbnail_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setCentralWidget(self.thumbnail_list)

        self.tool_actions: dict[str, QAction] = {}
        self._build_actions()
        self._refresh()

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
        file_menu.addAction(self.save_as_action)
        file_menu.addAction(self.close_action)

        edit_menu = self.menuBar().addMenu(self.tr("&Edit"))
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)

        tools_menu = self.menuBar().addMenu(self.tr("&Tools"))
        for tool_id, dialog_cls in _TOOL_DIALOGS.items():
            plugin = self.controller.get_plugin(tool_id)
            action = QAction(plugin.display_name, self)
            action.triggered.connect(self._make_tool_handler(tool_id, dialog_cls))
            tools_menu.addAction(action)
            self.tool_actions[tool_id] = action

        toolbar = QToolBar(self.tr("Main"))
        toolbar.setAccessibleName(self.tr("Main toolbar"))
        self.addToolBar(toolbar)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.save_as_action)
        toolbar.addSeparator()
        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)

    def _make_tool_handler(self, tool_id: str, dialog_cls: _DialogFactory) -> Any:
        return lambda: self._run_tool(tool_id, dialog_cls)

    # --- document lifecycle ----------------------------------------------

    def _open_document(self) -> None:
        path_str, _selected_filter = QFileDialog.getOpenFileName(
            self, self.tr("Open PDF"), "", self.tr("PDF files (*.pdf)")
        )
        if not path_str:
            return
        try:
            self.controller.open_document(Path(path_str))
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

    def _save_as(self) -> None:
        if not self.controller.is_open:
            return
        path_str, _selected_filter = QFileDialog.getSaveFileName(
            self, self.tr("Save PDF As"), "", self.tr("PDF files (*.pdf)")
        )
        if not path_str:
            return
        try:
            self.controller.save_as(Path(path_str))
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self.statusBar().showMessage(self.tr("Saved to {0}").format(path_str), 5000)

    def _close_document(self) -> None:
        self.controller.close_session()
        self._refresh()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override, fixed name
        self.controller.close_session()
        super().closeEvent(event)

    # --- undo/redo ---------------------------------------------------------

    def _undo(self) -> None:
        try:
            self.controller.undo()
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

    def _redo(self) -> None:
        try:
            self.controller.redo()
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

    # --- tools ---------------------------------------------------------------

    def _run_tool(self, tool_id: str, dialog_cls: _DialogFactory) -> None:
        if tool_id != "merge" and not self.controller.is_open:
            self._show_error_message(self.tr("Open a document first."))
            return
        dialog = dialog_cls(self)
        if dialog.exec() != BaseToolDialog.DialogCode.Accepted:
            return
        try:
            plugin = self.controller.get_plugin(tool_id)
            operation = plugin.build_operation(**dialog.values())
            self.controller.apply_operation(operation)
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

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
            self.setWindowTitle(self.tr("PDF Editor - {0}").format(label))
            self.statusBar().showMessage(self.tr("{0} page(s)").format(self.thumbnail_list.count()))
        else:
            self.setWindowTitle(self.tr("PDF Editor"))
            self.statusBar().showMessage(self.tr("No document open"))
        self._update_action_state()

    def _render_thumbnails(self, path: Path) -> None:
        pdf_doc = QPdfDocument(self)
        if pdf_doc.load(str(path)) != QPdfDocument.Error.None_:
            log.error("Could not load PDF for thumbnail rendering: %s", path)
            return
        for i in range(pdf_doc.pageCount()):
            image = pdf_doc.render(i, _THUMBNAIL_SIZE)
            item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), self.tr("Page {0}").format(i + 1))
            self.thumbnail_list.addItem(item)

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
