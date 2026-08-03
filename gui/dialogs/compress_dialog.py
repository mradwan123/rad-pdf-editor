from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog


class CompressDialog(BaseToolDialog):
    """No options to collect - just a confirmation, but still goes
    through the same dialog shell as every other tool (SPEC.md 6.2's
    "consistent layout ... every time")."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Compress"), parent)
        self.add_full_width(QLabel(self.tr("Optimize this document's internal structure.")))

    def values(self) -> dict[str, Any]:
        return {}
