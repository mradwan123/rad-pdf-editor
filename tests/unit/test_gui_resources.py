"""Unit tests for gui/resources.py (the "Rad PDF Editor" logo)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gui.resources import build_app_icon, build_logo_pixmap


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_build_logo_pixmap_is_not_null() -> None:
    _qapp()
    pixmap = build_logo_pixmap(128)
    assert not pixmap.isNull()
    assert pixmap.width() == 128
    assert pixmap.height() == 128


def test_build_logo_pixmap_respects_requested_size() -> None:
    _qapp()
    pixmap = build_logo_pixmap(32)
    assert pixmap.width() == 32
    assert pixmap.height() == 32


def test_build_app_icon_is_not_null() -> None:
    _qapp()
    icon = build_app_icon()
    assert not icon.isNull()
