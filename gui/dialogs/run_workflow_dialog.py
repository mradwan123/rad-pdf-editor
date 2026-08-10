"""RunWorkflowDialog: pick a saved workflow and an input/output PDF
pair to replay it against (Phase 5's Workflow builder UI, SPEC.md
section 4).

Non-standard constructor, mirroring `FillFormDialog`'s pattern of
taking data the caller already has rather than reaching into a store
itself: `MainWindow._run_workflow` already calls
`WorkflowStore().list_workflows()` to decide whether to open this
dialog at all (an empty list short-circuits to an info message
instead), so it just hands the names over.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QComboBox, QFileDialog, QPushButton, QWidget

from core.errors import OperationError
from gui.dialogs.base_tool_dialog import BaseToolDialog


class RunWorkflowDialog(BaseToolDialog):
    def __init__(self, workflow_names: list[str], parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Run Workflow"), parent)

        self.workflow_combo = self.add_row(self.tr("Workflow"), QComboBox())
        self.workflow_combo.addItems(workflow_names)

        self._input_path: Path | None = None
        self._input_button = QPushButton(self.tr("Choose Input PDF..."))
        self._input_button.clicked.connect(self._choose_input)
        self.add_row(self.tr("Input PDF"), self._input_button)

        self._output_path: Path | None = None
        self._output_button = QPushButton(self.tr("Choose Output Location..."))
        self._output_button.clicked.connect(self._choose_output)
        self.add_row(self.tr("Output PDF"), self._output_button)

    def _choose_input(self) -> None:
        path_str, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.tr("Choose input PDF"),
            "",
            self.tr("PDF files (*.pdf)"),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path_str:
            self._input_path = Path(path_str)
            self._input_button.setText(self._input_path.name)

    def _choose_output(self) -> None:
        path_str, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.tr("Choose output PDF"),
            "",
            self.tr("PDF files (*.pdf)"),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path_str:
            self._output_path = Path(path_str)
            self._output_button.setText(self._output_path.name)

    def values(self) -> dict[str, Any]:
        if self._input_path is None:
            raise OperationError("No input PDF selected.")
        if self._output_path is None:
            raise OperationError("No output location selected.")
        return {
            "workflow_name": self.workflow_combo.currentText(),
            "input_path": self._input_path,
            "output_path": self._output_path,
        }
