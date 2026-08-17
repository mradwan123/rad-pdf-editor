"""WorkflowBuilderDialog: assemble a named, ordered sequence of
Operations into a `Pipeline` for later replay (Phase 5's Workflow
builder UI, SPEC.md section 4).

Not tied to one tool_id, so it isn't part of `TOOL_DIALOGS` -
constructed directly by `MainWindow._build_workflow` with the live
`Registry` (needed to enumerate tools and build real Operations),
mirroring `FillFormDialog`'s deviation from the plain `(parent=None)`
constructor every ordinary tool dialog uses.

Reuses `merge_dialog.py`'s exact shape (a `QListWidget` of steps plus
Add/Remove/Move Up/Move Down buttons via `add_full_width`), but each
"file" is a real `Operation` built through that tool's own dialog -
picked via `TOOL_DIALOGS` (gui/dialogs/tool_dialog_registry.py) so a
workflow step is configured with the exact same UI as running that
tool directly, not a second hand-rolled form.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.errors import PDFEditorError
from core.model.operation import Operation
from core.model.pipeline import Pipeline
from core.registry.registry import Registry
from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.tool_dialog_registry import TOOL_DIALOGS


class WorkflowBuilderDialog(BaseToolDialog):
    def __init__(self, registry: Registry, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Build Workflow"), parent)
        self._registry = registry
        self._operations: list[Operation] = []

        self.name_edit = self.add_row(self.tr("Workflow name"), QLineEdit())

        self.step_list = QListWidget()
        self.step_list.setAccessibleName(self.tr("Workflow steps"))

        add_button = QPushButton(self.tr("Add Step..."))
        add_button.clicked.connect(self._add_step)
        remove_button = QPushButton(self.tr("Remove Selected"))
        remove_button.clicked.connect(self._remove_selected)
        up_button = QPushButton(self.tr("Move Up"))
        up_button.clicked.connect(lambda: self._move(-1))
        down_button = QPushButton(self.tr("Move Down"))
        down_button.clicked.connect(lambda: self._move(1))

        buttons_row = QHBoxLayout()
        for button in (add_button, remove_button, up_button, down_button):
            buttons_row.addWidget(button)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(self.step_list)
        container_layout.addLayout(buttons_row)
        self.add_full_width(container)

    def _add_step(self) -> None:
        # fill_form needs a live document's actual AcroForm field names
        # (see FillFormDialog's docstring / MainWindow._run_tool) which
        # a workflow being built in the abstract, against no particular
        # document, can't supply - excluded from the picker outright
        # rather than attempting a broken/empty-fields instantiation.
        choices = sorted(
            (
                (plugin.tool_id, plugin.display_name)
                for plugin in self._registry.all_plugins()
                if plugin.tool_id != "fill_form"
            ),
            key=lambda pair: pair[1],
        )
        labels = [display_name for _tool_id, display_name in choices]
        label, ok = QInputDialog.getItem(
            self, self.tr("Add Step"), self.tr("Tool:"), labels, 0, editable=False
        )
        if not ok or not label:
            return
        tool_id = choices[labels.index(label)][0]

        # Third-party plugins (core/registry/registry.py's plugin.json
        # scan) have no entry in TOOL_DIALOGS - unlike every first-
        # party tool, nothing adds one for them automatically, since
        # that dict is still hand-populated per tool. Rather than
        # crash on a KeyError, treat "no registered dialog" as "this
        # tool takes no configuration" and build it directly - true
        # for this project's own example plugin (plugins/example_plugin),
        # and a reasonable fallback for any third-party plugin that
        # doesn't ship its own dialog (see plugins/README.md).
        if tool_id in TOOL_DIALOGS:
            tool_dialog = TOOL_DIALOGS[tool_id](self)
            # Same contract as MainWindow._run_tool: whoever exec()s a
            # tool dialog releases whatever it holds afterwards. Nothing
            # reachable from here opens a file today (a step is
            # configured against no particular document, so SignDialog
            # gets no path and builds no canvas), but the rule is the
            # dialog's, not this call site's.
            try:
                if tool_dialog.exec() != BaseToolDialog.DialogCode.Accepted:
                    return
                values = tool_dialog.values()
            finally:
                tool_dialog.release_resources()
        else:
            values = {}

        try:
            operation = self._registry.get(tool_id).build_operation(**values)
        except PDFEditorError as exc:
            QMessageBox.critical(self, self.tr("Error"), str(exc))
            return

        self._operations.append(operation)
        self.step_list.addItem(operation.describe())

    def _remove_selected(self) -> None:
        rows = sorted(
            (self.step_list.row(item) for item in self.step_list.selectedItems()), reverse=True
        )
        for row in rows:
            self.step_list.takeItem(row)
            del self._operations[row]

    def _move(self, offset: int) -> None:
        row = self.step_list.currentRow()
        new_row = row + offset
        if row < 0 or not (0 <= new_row < self.step_list.count()):
            return
        item = self.step_list.takeItem(row)
        self.step_list.insertItem(new_row, item)
        self.step_list.setCurrentRow(new_row)
        self._operations[row], self._operations[new_row] = (
            self._operations[new_row],
            self._operations[row],
        )

    def accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(
                self, self.tr("Build Workflow"), self.tr("Enter a name for this workflow.")
            )
            return
        if not self._operations:
            QMessageBox.warning(
                self, self.tr("Build Workflow"), self.tr("Add at least one step.")
            )
            return
        super().accept()

    def build_pipeline(self) -> Pipeline:
        """Not `values()` - this dialog builds a `Pipeline`, not
        `ToolPlugin.build_operation()` kwargs, a different shape than
        every other tool dialog. `BaseToolDialog.values()` is a
        convention other dialogs follow, not an enforced contract."""
        return Pipeline(name=self.name_edit.text(), operations=list(self._operations))
