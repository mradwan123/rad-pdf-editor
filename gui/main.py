"""GUI entry point (SPEC.md's /gui row: "PySide6: main window,
thumbnail grid, tool panels, pipeline builder UI").

Wraps the entire app lifetime in `network_lockdown()` - defense in
depth on top of this codebase never calling out itself (SPEC.md
section 1 and 2's "Security layer").
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from core.logging_config import configure_logging
from core.security.sandbox import network_lockdown
from gui.main_window import MainWindow


def main() -> int:
    configure_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # SPEC.md 6.2: Qt Fusion style, no custom component library
    window = MainWindow()
    window.show()
    with network_lockdown():
        return app.exec()


if __name__ == "__main__":
    sys.exit(main())
