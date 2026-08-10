""""Open this document where?" - New Tab / Replace Current Tab /
Cancel, shown whenever a document is opened while at least one tab is
already open (File > Open and File > Open Recent both route through it).

Deliberately a real `QDialog` subclass rather than a `QMessageBox` with
custom buttons. Two reasons, one product and one testing:

- Qt 6 dropped `QMessageBox.setButtonText`, so custom-labelled choices
  mean a hand-built message box anyway.
- `QMessageBox`, like `QMenu`, is a compiled PySide6 class, so
  `patch.object(QMessageBox, "exec", fake)` does *not* intercept an
  instance's `.exec()` call - it silently runs the real modal dialog
  and hangs headlessly (CLAUDE.md documents this the hard way for
  `QMenu.exec`). A plain Python subclass patches exactly like every
  `BaseToolDialog` in this project already does, so the real flow
  stays testable end to end.

Not a `BaseToolDialog`: there's no options form and no `values()` to
feed a `ToolPlugin.build_operation()` - this picks a window placement,
it doesn't configure an `Operation`.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

#: `MainWindow` passes these around rather than bare strings.
PLACEMENT_NEW_TAB = "new"
PLACEMENT_REPLACE_CURRENT = "replace"


class TabPlacementDialog(QDialog):
    """`placement` holds the choice after an accepted `exec()`; a
    rejected dialog (Cancel) leaves it None and means "do nothing.\""""

    def __init__(self, document_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Open Document"))
        self.setModal(True)
        self.placement: str | None = None

        prompt = (
            self.tr("Open '{0}' in a new tab, or replace the current tab?").format(document_name)
            if document_name
            else self.tr("Open in a new tab, or replace the current tab?")
        )
        label = QLabel(prompt)
        label.setWordWrap(True)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.new_tab_button = self.buttons.addButton(
            self.tr("&New Tab"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.replace_button = self.buttons.addButton(
            self.tr("&Replace Current Tab"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.new_tab_button.setDefault(True)
        self.new_tab_button.clicked.connect(self._choose_new_tab)
        self.replace_button.clicked.connect(self._choose_replace)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(self.buttons)

    def _choose_new_tab(self) -> None:
        self.placement = PLACEMENT_NEW_TAB
        self.accept()

    def _choose_replace(self) -> None:
        self.placement = PLACEMENT_REPLACE_CURRENT
        self.accept()

    def button_for(self, placement: str) -> QPushButton:
        """The button that selects `placement` - lets tests click the
        real button (and so run the real handler) instead of setting
        `self.placement` behind the dialog's back."""
        return (
            self.new_tab_button if placement == PLACEMENT_NEW_TAB else self.replace_button
        )
