"""Dark silver/gray/black QPalette for the Fusion style.

QSS alone doesn't reliably drive certain palette-backed colors (e.g.
QListWidget's selection highlight in IconMode falls back to the
platform's default blue unless the QPalette::Highlight role itself is
set) - this is the standard, robust way to theme a Fusion app; styles.qss
handles the rest (borders, radius, padding, hover states).
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

_WINDOW = QColor("#1b1c1e")
_BASE = QColor("#17181a")
_ALTERNATE_BASE = QColor("#202124")
_TEXT = QColor("#e8e9eb")
_BUTTON = QColor("#2e3035")
_BRIGHT_TEXT = QColor("#ffffff")
_HIGHLIGHT = QColor("#565963")
_HIGHLIGHTED_TEXT = QColor("#f2f3f5")
_TOOLTIP_BASE = QColor("#232427")
_PLACEHOLDER = QColor("#6b6d74")
_LINK = QColor("#8b8f99")
_DISABLED_TEXT = QColor("#5c5d63")
_DISABLED_BASE = QColor("#1e1f22")


def build_dark_palette() -> QPalette:
    palette = QPalette()

    palette.setColor(QPalette.ColorRole.Window, _WINDOW)
    palette.setColor(QPalette.ColorRole.WindowText, _TEXT)
    palette.setColor(QPalette.ColorRole.Base, _BASE)
    palette.setColor(QPalette.ColorRole.AlternateBase, _ALTERNATE_BASE)
    palette.setColor(QPalette.ColorRole.Text, _TEXT)
    palette.setColor(QPalette.ColorRole.Button, _BUTTON)
    palette.setColor(QPalette.ColorRole.ButtonText, _TEXT)
    palette.setColor(QPalette.ColorRole.BrightText, _BRIGHT_TEXT)
    palette.setColor(QPalette.ColorRole.Highlight, _HIGHLIGHT)
    palette.setColor(QPalette.ColorRole.HighlightedText, _HIGHLIGHTED_TEXT)
    palette.setColor(QPalette.ColorRole.ToolTipBase, _TOOLTIP_BASE)
    palette.setColor(QPalette.ColorRole.ToolTipText, _TEXT)
    palette.setColor(QPalette.ColorRole.PlaceholderText, _PLACEHOLDER)
    palette.setColor(QPalette.ColorRole.Link, _LINK)

    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, _DISABLED_TEXT)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, _DISABLED_TEXT)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, _DISABLED_TEXT)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, _DISABLED_BASE)

    return palette
