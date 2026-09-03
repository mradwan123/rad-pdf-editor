"""Running tools and workflows from `MainWindow`.

Split out of `gui/main_window.py` in Phase 6a (docs/GUI_PLAN.md §3.1).
See `gui/window_parts.py` for why this is a mixin rather than a
collaborator object.

Everything that turns a user gesture into an applied `Operation` lives
here: the Tools menu's dialog flow, the thumbnail context menu and
drag-reorder, and workflow build/run. Phase 6d replaces
`_busy_cursor`'s synchronous wait with a `QThreadPool` worker plus
progress and cancel; keeping these call sites in one module is what
makes that a contained change.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from core.errors import OperationError, PDFEditorError
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.ops.forms import list_form_field_names
from core.session.session_dir import SessionTempDir
from core.session.workflow_store import WorkflowStore
from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.fill_form_dialog import FillFormDialog
from gui.dialogs.run_workflow_dialog import RunWorkflowDialog
from gui.dialogs.sign_dialog import SignDialog
from gui.dialogs.tool_dialog_registry import DialogFactory
from gui.dialogs.workflow_builder_dialog import WorkflowBuilderDialog
from gui.document_tab import DocumentTab
from gui.operation_runner import OperationRunner
from gui.window_parts import WindowPart


def _selected_pages(tab: DocumentTab) -> list[int]:
    """1-based page numbers currently selected in the tab's sidebar."""
    return sorted(
        int(item.data(Qt.ItemDataRole.UserRole))
        for item in tab.thumbnail_list.selectedItems()
    )


class ToolRunnerMixin(WindowPart):
    """Tool dialogs, thumbnail-driven operations, and workflows."""

    def _make_tool_handler(self, tool_id: str, dialog_cls: DialogFactory) -> Any:
        return lambda: self._run_tool(tool_id, dialog_cls)

    @contextmanager
    def _busy_cursor(self) -> Iterator[None]:
        """Visual feedback around a wait that still runs on the UI
        thread.

        Phase 6d moved applied operations onto a worker
        (`_apply_operation`), so this now covers only the two cases that
        are not a single `Operation`:

        - undo/redo, which restore a snapshot - a file copy, not a
          computation, so a progress bar would be noise; and
        - running a saved workflow, which is a whole `Pipeline` against
          a throwaway session rather than one operation applied to a
          tab.

        Both are candidates for the worker later; neither is the freeze
        6d existed to fix."""
        self.statusBar().showMessage(self.tr("Working..."))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()

    def _apply_operation(self, tab: DocumentTab, operation: Operation) -> bool:
        """Apply `operation` to `tab` off the UI thread, behind a
        cancellable progress dialog. False means the user cancelled;
        errors propagate as before so each call site's existing
        handling is unchanged."""
        applied = OperationRunner(self).run(
            operation,
            lambda: tab.controller.apply_operation(operation),
            operation.describe(),
        )
        if applied:
            affected = operation.affected_pages()
            tab.renderer.invalidate(affected)
            tab.canvas.invalidate(affected)
        return applied

    # --- tools ---------------------------------------------------------------

    def _run_tool(self, tool_id: str, dialog_cls: DialogFactory) -> None:
        tab = self.current_tab
        if tool_id != "merge" and (tab is None or not tab.controller.is_open):
            self._show_error_message(self.tr("Open a document first."))
            return

        if tool_id == "fill_form":
            assert tab is not None  # guaranteed by the check above
            working_path = tab.controller.doc.working_path
            assert working_path is not None
            dialog: BaseToolDialog = FillFormDialog(list_form_field_names(working_path), self)
        elif tool_id == "sign":
            # Like fill_form, SignDialog can do more when it knows which
            # document is being edited: given the working path it shows
            # the real page and lets the image be placed by mouse. It
            # still works without one (the Workflow builder builds it
            # that way), falling back to numeric entry only.
            assert tab is not None  # guaranteed by the check above
            working_path = tab.controller.doc.working_path
            assert working_path is not None
            dialog = SignDialog(self, working_path)
        else:
            dialog = dialog_cls(self)

        # A dialog can hold an OS handle on a file in the session temp
        # dir (SignDialog's placement canvas keeps the working copy open
        # through a QPdfDocument), and every dialog here is parented to
        # this window, so it outlives exec() by however long Qt takes to
        # destroy it - potentially past the point close_session()
        # securely wipes that dir, which Windows then refuses to do
        # (WinError 32). Released deterministically here, on every path
        # out, rather than left to destruction order.
        if tab is not None:
            dialog.set_page_selection(_selected_pages(tab))
        try:
            if dialog.exec() != BaseToolDialog.DialogCode.Accepted:
                return
            created_tab = tab is None
            if created_tab:
                # Merge with no tabs open: it builds a document from
                # scratch, so it gets a fresh tab - created only now
                # that the dialog was actually accepted, so a cancelled
                # Merge can't strand an empty tab. activate=False: don't
                # switch to (and render) it until apply_operation below
                # actually succeeds - see _add_tab's docstring for the
                # black-empty-tab bug this avoids.
                tab = self._add_tab(activate=False)
            assert tab is not None  # either pre-existing (checked above) or just created
            try:
                plugin = self.registry.get(tool_id)
                operation = plugin.build_operation(**dialog.values())
                if not self._apply_operation(tab, operation):
                    # Cancelled: nothing was applied, so a tab created
                    # for this run has no document and must not linger.
                    if created_tab:
                        self._discard_tab(tab)
                    return
            except PDFEditorError as exc:
                if created_tab:
                    # Don't strand an empty tab for a Merge that built
                    # nothing (e.g. every input file was invalid).
                    self._discard_tab(tab)
                self._show_error(exc)
                return
            finally:
                self._mark_active_session()
            if created_tab:
                self.tab_widget.setCurrentWidget(tab)
            self._refresh()
        finally:
            dialog.release_resources()

    # --- annotations ----------------------------------------------------------

    def _make_markup_handler(self, kind: str) -> Any:
        return lambda: self._markup_selection(kind)

    def _make_canvas_tool_handler(self, tool: str) -> Any:
        return lambda: self._set_canvas_tool(tool)

    def _set_canvas_tool(self, tool: str) -> None:
        for tab in self.tabs():
            tab.canvas.set_tool(tool)

    def _markup_selection(self, kind: str) -> None:
        """Highlight/underline/strikeout/squiggly the selected text.

        One annotation over every rect of the selection rather than one
        per line, so a highlight spanning three lines is a single undo
        step and a single audit entry."""
        tab = self.current_tab
        if tab is None or not tab.controller.is_open:
            self._show_error_message(self.tr("Open a document first."))
            return
        page, rects = tab.canvas.selection_markup_rects()
        if not rects:
            self._show_error_message(self.tr("Select some text first."))
            return
        self._apply_to_tab(
            tab, "add_annotation", page=page, kind=kind, rects=rects
        )
        tab.canvas.clear_selection()

    def _on_annotation_drawn(self, tab: DocumentTab, page: int, kind: str, payload: Any) -> None:
        """A shape, ink stroke or note drawn directly on the page."""
        if kind == "ink":
            self._apply_to_tab(tab, "add_annotation", page=page, kind=kind, strokes=payload)
        else:
            self._apply_to_tab(tab, "add_annotation", page=page, kind=kind, rect=tuple(payload))

    def _on_annotation_moved(
        self, tab: DocumentTab, page: int, annot_id: str, rect: Any
    ) -> None:
        """A picked annotation was dragged to a new position."""
        self._apply_to_tab(
            tab, "edit_annotation", page=page, annot_id=annot_id, rect=tuple(rect)
        )

    def _delete_annotation_under_cursor(self) -> None:
        """Delete the annotation the page view currently has selected."""
        tab = self.current_tab
        if tab is None or not tab.controller.is_open:
            return
        selected = tab.canvas.selected_annotation
        if selected is None:
            self._show_error_message(self.tr("Select an annotation first."))
            return
        page, annot_id = selected
        self._apply_to_tab(tab, "delete_annotation", page=page, annot_id=annot_id)

    # --- thumbnail-driven operations ------------------------------------------

    def _on_thumbnails_reordered(self, tab: DocumentTab) -> None:
        # Deferred to the next event loop turn: applying an operation
        # (which rebuilds the tab's thumbnail list via _refresh) from
        # directly inside the model's own rowsMoved signal would fight
        # with Qt's own post-move bookkeeping for the same signal - the
        # standard-idiom fix is to let the current emission finish first
        # (see QTimer.singleShot(0, ...)).
        QTimer.singleShot(0, lambda: self._apply_thumbnail_reorder(tab))

    def _apply_thumbnail_reorder(self, tab: DocumentTab) -> None:
        page_order = [
            tab.thumbnail_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(tab.thumbnail_list.count())
        ]
        if page_order == list(range(1, len(page_order) + 1)):
            # No actual change (e.g. a drag that ends where it started,
            # or - for a multi-item drag - a second rowsMoved signal for
            # the same gesture arriving after the first one already
            # applied the reorder and _refresh() reset everything to
            # sequential order). Applying here would push a no-op
            # ReorderPagesOperation onto the undo stack for nothing.
            return
        self._apply_to_tab(tab, "reorder_pages", page_order=page_order)

    def _show_thumbnail_context_menu(self, tab: DocumentTab, pos: QPoint) -> None:
        selected = tab.thumbnail_list.selectedItems()
        if not selected:
            return
        pages = sorted(int(item.data(Qt.ItemDataRole.UserRole)) for item in selected)

        menu = QMenu(self)
        rotate_left_action = menu.addAction(self.tr("Rotate Left"))
        rotate_right_action = menu.addAction(self.tr("Rotate Right"))
        menu.addSeparator()
        delete_action = menu.addAction(self.tr("Delete Selected"))

        chosen = menu.exec(tab.thumbnail_list.viewport().mapToGlobal(pos))
        if chosen is rotate_left_action:
            self._apply_thumbnail_rotate(tab, pages, angle=-90)
        elif chosen is rotate_right_action:
            self._apply_thumbnail_rotate(tab, pages, angle=90)
        elif chosen is delete_action:
            self._apply_thumbnail_delete(tab, pages)

    def _apply_thumbnail_rotate(self, tab: DocumentTab, pages: list[int], angle: int) -> None:
        self._apply_to_tab(tab, "rotate_pages", angle=angle, pages=pages)

    def _apply_thumbnail_delete(self, tab: DocumentTab, pages: list[int]) -> None:
        self._apply_to_tab(tab, "delete_pages", pages=pages)

    def _apply_to_tab(self, tab: DocumentTab, tool_id: str, **kwargs: Any) -> None:
        """Apply a dialog-free operation (thumbnail drag-reorder,
        context-menu rotate/delete) to one specific tab's document."""
        try:
            operation = self.registry.get(tool_id).build_operation(**kwargs)
            self._apply_operation(tab, operation)
        except PDFEditorError as exc:
            self._show_error(exc)
        # Refresh either way: on success this rebuilds thumbnails from
        # the new document (confirming the change), on failure it
        # discards any stale drag-and-drop visual order so the grid
        # matches the actual (unchanged) document again.
        self._refresh()

    # --- workflows -------------------------------------------------------------

    def _build_workflow(self) -> None:
        """Document-independent by design: a workflow is a saved, named
        sequence of Operations, not a live edit against any open tab's
        document (that's what _run_tool is for)."""
        dialog = WorkflowBuilderDialog(self.registry, self)
        if dialog.exec() != BaseToolDialog.DialogCode.Accepted:
            return
        pipeline = dialog.build_pipeline()
        try:
            WorkflowStore().save(pipeline)
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self.statusBar().showMessage(self.tr("Saved workflow '{0}'").format(pipeline.name), 5000)

    def _run_workflow(self) -> None:
        """A standalone batch run against an input/output file pair -
        deliberately touching no tab's AppController, document or undo
        stack (only the app-wide audit_log, to record the steps), the
        same "external file(s) in" shape Merge and the Phase 3
        conversion ops already use, run through a throwaway
        SessionTempDir rather than any live editing session. It never
        opens a tab either."""
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
                pipeline = WorkflowStore().load(values["workflow_name"], self.registry)
                with SessionTempDir() as session:
                    input_path: Path = values["input_path"]
                    working = session.path / f"working{input_path.suffix or '.pdf'}"
                    shutil.copyfile(input_path, working)
                    doc = DocumentSession(working_path=working, source_path=input_path)
                    result = pipeline.run(doc)
                    if result.working_path is None:
                        raise OperationError("Workflow produced no output document.")
                    shutil.copyfile(result.working_path, values["output_path"])
                    # Every other path that applies an Operation (tool
                    # dialogs, via AppController.apply_operation; the
                    # CLI's own run-workflow) records to the audit trail
                    # - a workflow run through the GUI must too, or its
                    # steps would be invisible to the audit log despite
                    # having actually modified a document.
                    for operation in pipeline.operations:
                        self.audit_log.record_operation(
                            operation, document_label=str(values["output_path"])
                        )
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self.statusBar().showMessage(
            self.tr("Workflow run complete: {0}").format(values["output_path"]), 5000
        )
