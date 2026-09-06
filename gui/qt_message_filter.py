"""Drops known-harmless noise from Qt's own console output.

On Wayland, Qt's platform plugin prints

    This plugin supports grabbing the mouse only for popup windows

every time a widget takes a mouse grab on a window that isn't a
`Qt::Popup`. Qt takes those grabs internally (menus, combo boxes,
splitters, rubber-band selection), so the line appears repeatedly
while the app is running even though this codebase never calls
`grabMouse()` itself. On Wayland the grab is simply refused, the
widget keeps working through normal event delivery, and there is
nothing for the user to act on - it is pure console noise.

It can't be filtered with `QT_LOGGING_RULES`: Qt emits it with a
plain `qWarning()`, which lands in the *default* logging category
rather than `qt.qpa.wayland`, so the only rule that catches it is
`default.warning=false` - which would also swallow every genuine Qt
warning. Hence an explicit message handler that suppresses this one
message and passes everything else through untouched.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler

# Substrings matched against each Qt message; a hit is dropped. Kept as a
# tuple (rather than inlined) so further known-harmless platform chatter can
# be added here with the reason recorded alongside it.
_SUPPRESSED: tuple[str, ...] = (
    # Qt Wayland, on every internal mouse grab of a non-popup window.
    "This plugin supports grabbing the mouse only for popup windows",
)


def _forward(msg_type: QtMsgType, context: QMessageLogContext, message: str) -> None:
    """Qt message handler: drop the suppressed lines, re-emit the rest.

    Re-emission mirrors Qt's own default message pattern, which prefixes
    the category only when it isn't the catch-all "default" one, so
    surviving messages look exactly as they did before this filter.
    """
    if any(noise in message for noise in _SUPPRESSED):
        return

    category = context.category or "default"
    prefix = "" if category == "default" else f"{category}: "
    print(f"{prefix}{message}", file=sys.stderr)


def install_qt_message_filter() -> None:
    """Install the filter. Call once at GUI startup, before `QApplication`."""
    qInstallMessageHandler(_forward)
