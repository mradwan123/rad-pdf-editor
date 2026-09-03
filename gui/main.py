"""GUI entry point (SPEC.md's /gui row: "PySide6: main window,
thumbnail grid, tool panels, pipeline builder UI").

Wraps the entire app lifetime in `network_lockdown()` - defense in
depth on top of this codebase never calling out itself (SPEC.md
section 1 and 2's "Security layer").
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from core.logging_config import configure_logging, get_logger
from core.security.sandbox import network_lockdown
from core.session.ui_state import load_ui_state
from gui.main_window import MainWindow
from gui.palette import build_palette, build_stylesheet
from gui.resources import build_app_icon
from gui.styles import load_stylesheet

log = get_logger(__name__)

def main() -> int:
    configure_logging()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # SPEC.md 6.2: Qt Fusion style, no custom component library
    # The saved theme, read before the window exists so the first paint
    # is already correct rather than flashing dark then switching.
    saved = load_ui_state()
    app.setPalette(build_palette(saved.theme))  # QSS alone can't drive these roles
    # SPEC.md 6.2: one shared styles.qss, not per-dialog. Derived for
    # the saved theme - see gui.palette.build_stylesheet.
    app.setStyleSheet(build_stylesheet(load_stylesheet(), saved.theme))
    app.setWindowIcon(build_app_icon())
    window = MainWindow()
    window.show()
    # After show(), not inside MainWindow.__init__: the recovery
    # prompt is modal, and a constructor that can block on user input
    # is both bad practice and untestable.
    window.restore_autosaved_session()
    # Layout and, if the preference allows, the documents that were open
    # last time - see core/session/ui_state.py on why that is a choice.
    window.restore_ui_state()
    with network_lockdown():
        return app.exec()


if __name__ == "__main__":
    sys.exit(main())
