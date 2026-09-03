"""Light and dark QPalette pairs for the Fusion style.

QSS alone doesn't reliably drive certain palette-backed colors (e.g.
QListWidget's selection highlight in IconMode falls back to the
platform's default blue unless the QPalette::Highlight role itself is
set) - this is the standard, robust way to theme a Fusion app; styles.qss
handles the rest (borders, radius, padding, hover states).

Phase 6g adds the light theme. Both are the *same* set of roles filled
from a different table rather than two hand-written builders, so a role
cannot be themed in one and forgotten in the other - which is exactly
how half-themed light modes happen.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QColor, QPalette

#: The two themes, as the same set of roles. Keeping them in one table
#: is what stops a role being themed in dark and forgotten in light.
_THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "window": "#1b1c1e",
        "base": "#17181a",
        "alternate_base": "#202124",
        "text": "#e8e9eb",
        "button": "#2e3035",
        "bright_text": "#ffffff",
        "highlight": "#565963",
        "highlighted_text": "#f2f3f5",
        "tooltip_base": "#232427",
        "placeholder": "#6b6d74",
        "link": "#8b8f99",
        "disabled_text": "#5c5d63",
        "disabled_base": "#1e1f22",
    },
    "light": {
        "window": "#f2f3f5",
        "base": "#ffffff",
        "alternate_base": "#e9eaee",
        "text": "#1c1d20",
        "button": "#e4e5e9",
        "bright_text": "#000000",
        "highlight": "#b9bcc4",
        "highlighted_text": "#14151a",
        "tooltip_base": "#ffffff",
        "placeholder": "#8b8d95",
        "link": "#4a4d55",
        "disabled_text": "#a2a4ab",
        "disabled_base": "#eceef1",
    },
}

THEMES = tuple(_THEMES)


def build_palette(theme: str = "dark") -> QPalette:
    """The QPalette for `theme`. Unknown names fall back to dark rather
    than raising - a bad value in persisted UI state should not stop the
    app starting."""
    colours = _THEMES.get(theme, _THEMES["dark"])

    def colour(role: str) -> QColor:
        return QColor(colours[role])

    palette = QPalette()

    palette.setColor(QPalette.ColorRole.Window, colour("window"))
    palette.setColor(QPalette.ColorRole.WindowText, colour("text"))
    palette.setColor(QPalette.ColorRole.Base, colour("base"))
    palette.setColor(QPalette.ColorRole.AlternateBase, colour("alternate_base"))
    palette.setColor(QPalette.ColorRole.Text, colour("text"))
    palette.setColor(QPalette.ColorRole.Button, colour("button"))
    palette.setColor(QPalette.ColorRole.ButtonText, colour("text"))
    palette.setColor(QPalette.ColorRole.BrightText, colour("bright_text"))
    palette.setColor(QPalette.ColorRole.Highlight, colour("highlight"))
    palette.setColor(QPalette.ColorRole.HighlightedText, colour("highlighted_text"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, colour("tooltip_base"))
    palette.setColor(QPalette.ColorRole.ToolTipText, colour("text"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, colour("placeholder"))
    palette.setColor(QPalette.ColorRole.Link, colour("link"))

    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, colour("disabled_text"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, colour("disabled_text"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, colour("disabled_text"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, colour("disabled_base"))

    return palette


def build_dark_palette() -> QPalette:
    """Kept as the original name used by gui/main.py and the tests."""
    return build_palette("dark")


def _invert_lightness(colour: QColor) -> QColor:
    """Same hue and saturation, mirrored lightness."""
    # hueF() is -1 for an achromatic colour, which fromHslF rejects.
    hue = max(colour.hueF(), 0.0)
    return QColor.fromHslF(hue, colour.saturationF(), 1.0 - colour.lightnessF())


def build_stylesheet(source: str, theme: str = "dark") -> str:
    """The stylesheet for `theme`, derived from the dark original.

    `styles.qss` is authored dark and every colour in it is a
    near-greyscale tone, so the light theme is the same sheet with each
    colour's *lightness* mirrored and its hue and saturation kept. That
    is a mechanical transformation with nothing to keep in sync - the
    alternative, tokenising 31 hex values by hand, is exactly the sort
    of thing that drifts into a half-themed light mode.

    The limitation is worth stating: this works *because* the design is
    greyscale. A saturated brand colour would invert into something
    unintended and would need naming explicitly rather than deriving.
    """
    if theme != "light":
        return source

    def replace(match: re.Match[str]) -> str:
        return _invert_lightness(QColor(match.group(0))).name()

    return re.sub(r"#[0-9a-fA-F]{6}\b", replace, source)
