from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog, QPushButton, QWidget

from core.errors import OperationError
from gui.dialogs.base_tool_dialog import BaseToolDialog


class PptxToPdfDialog(BaseToolDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("PowerPoint to PDF"), parent)

        self._source_path: Path | None = None
        self._source_button = QPushButton(self.tr("Choose PowerPoint File..."))
        self._source_button.clicked.connect(self._choose_source)
        self.add_row(self.tr("PowerPoint file"), self._source_button)

    def _choose_source(self) -> None:
        path_str, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.tr("Choose PowerPoint file"),
            "",
            self.tr("PowerPoint presentations (*.pptx)"),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path_str:
            self._source_path = Path(path_str)
            self._source_button.setText(self._source_path.name)

    def values(self) -> dict[str, Any]:
        if self._source_path is None:
            raise OperationError("No PowerPoint file selected.")
        return {"source_path": self._source_path}
