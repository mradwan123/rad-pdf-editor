"""GUI entry point (SPEC.md's /gui row: "PySide6: main window,
thumbnail grid, tool panels, pipeline builder UI").

Wraps the entire app lifetime in `network_lockdown()` - defense in
depth on top of this codebase never calling out itself (SPEC.md
section 1 and 2's "Security layer").
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from core.logging_config import configure_logging, get_logger
from core.security.sandbox import network_lockdown
from gui.main_window import MainWindow
from gui.palette import build_dark_palette
from gui.resources import build_app_icon
from gui.single_instance import SingleInstanceGuard

log = get_logger(__name__)

_STYLESHEET_PATH = Path(__file__).parent / "styles.qss"


def _load_stylesheet() -> str:
    try:
        return _STYLESHEET_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("Could not load stylesheet: %s", _STYLESHEET_PATH)
        return ""


def _raise_window(window: MainWindow) -> None:
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()


def main() -> int:
    configure_logging()
    app = QApplication(sys.argv)

    guard = SingleInstanceGuard()
    if not guard.try_acquire():
        log.info("Rad PDF Editor is already running - asked it to come to the foreground.")
        return 0

    app.setStyle("Fusion")  # SPEC.md 6.2: Qt Fusion style, no custom component library
    app.setPalette(build_dark_palette())  # base colors - QSS alone doesn't reliably drive these
    app.setStyleSheet(_load_stylesheet())  # SPEC.md 6.2: one shared styles.qss, not per-dialog
    app.setWindowIcon(build_app_icon())
    window = MainWindow()
    guard.raise_requested.connect(lambda: _raise_window(window))
    window.show()
    # After show(), not inside MainWindow.__init__: the recovery
    # prompt is modal, and a constructor that can block on user input
    # is both bad practice and untestable.
    window.restore_autosaved_session()
    with network_lockdown():
        return app.exec()


if __name__ == "__main__":
    sys.exit(main())
