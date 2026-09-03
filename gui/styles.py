"""Loading the shared stylesheet.

Phase 6g pulled this out of `gui/main.py` so the theme switch can
re-read and re-derive the sheet at runtime, not only at startup - see
`gui.palette.build_stylesheet`.
"""

from __future__ import annotations

from pathlib import Path

from core.logging_config import get_logger

log = get_logger(__name__)

STYLESHEET_PATH = Path(__file__).parent / "styles.qss"


def load_stylesheet() -> str:
    """The dark-authored stylesheet, or "" if it cannot be read.

    A missing stylesheet leaves the app looking plain rather than
    stopping it starting - the palette still carries the base colours.
    """
    try:
        return STYLESHEET_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("Could not load stylesheet: %s", STYLESHEET_PATH)
        return ""
