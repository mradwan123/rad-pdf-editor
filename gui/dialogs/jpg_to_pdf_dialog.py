from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.dialogs.base_tool_dialog import BaseToolDialog


class JpgToPdfDialog(BaseToolDialog):
    """Same reorderable-list shape as MergeDialog, but for images."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("JPG to PDF"), parent)

        self.file_list = QListWidget()
        self.file_list.setAccessibleName(self.tr("Images to convert"))

        add_button = QPushButton(self.tr("Choose Images..."))
        add_button.clicked.connect(self._add_files)
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
        container_layout.addWidget(self.file_list)
        container_layout.addLayout(buttons_row)
        self.add_full_width(container)

    def _add_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self,
            self.tr("Choose Images..."),
            "",
            self.tr("Images (*.jpg *.jpeg *.png)"),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        self.file_list.addItems(paths)

    def _remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    def _move(self, offset: int) -> None:
        row = self.file_list.currentRow()
        new_row = row + offset
        if row < 0 or not (0 <= new_row < self.file_list.count()):
            return
        item = self.file_list.takeItem(row)
        self.file_list.insertItem(new_row, item)
        self.file_list.setCurrentRow(new_row)

    def values(self) -> dict[str, Any]:
        sources = [Path(self.file_list.item(i).text()) for i in range(self.file_list.count())]
        return {"sources": sources}
