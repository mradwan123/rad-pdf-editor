"""Unit tests for gui/qt_message_filter.py.

Covers both halves of the contract: the Wayland mouse-grab warning is
dropped, and every other Qt message still reaches stderr unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QtMsgType, qInstallMessageHandler, qWarning

from gui.qt_message_filter import _forward, install_qt_message_filter

WAYLAND_GRAB_WARNING = "This plugin supports grabbing the mouse only for popup windows"


class _Context:
    """Stand-in for QMessageLogContext - `_forward` only reads `.category`."""

    def __init__(self, category: str = "default") -> None:
        self.category = category


@pytest.fixture
def filter_installed() -> Iterator[None]:
    """Install the filter, and restore Qt's default handler afterwards."""
    install_qt_message_filter()
    try:
        yield
    finally:
        qInstallMessageHandler(None)


def test_wayland_grab_warning_is_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    _forward(QtMsgType.QtWarningMsg, _Context(), WAYLAND_GRAB_WARNING)
    assert capsys.readouterr().err == ""


def test_other_messages_pass_through(capsys: pytest.CaptureFixture[str]) -> None:
    _forward(QtMsgType.QtWarningMsg, _Context(), "something worth reading")
    assert capsys.readouterr().err == "something worth reading\n"


def test_named_category_is_prefixed_like_qt_does(capsys: pytest.CaptureFixture[str]) -> None:
    _forward(QtMsgType.QtWarningMsg, _Context("qt.qpa.wayland"), "a categorised warning")
    assert capsys.readouterr().err == "qt.qpa.wayland: a categorised warning\n"


def test_suppression_survives_a_message_appended_by_qt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Qt has decorated this warning differently across versions, so the
    filter matches on a substring rather than the whole line."""
    _forward(QtMsgType.QtWarningMsg, _Context(), f"{WAYLAND_GRAB_WARNING} (window: foo)")
    assert capsys.readouterr().err == ""


@pytest.mark.usefixtures("filter_installed")
def test_installed_handler_filters_real_qt_warnings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: the handler is actually wired into Qt's logging."""
    qWarning(WAYLAND_GRAB_WARNING)
    qWarning("a genuine warning")
    assert capsys.readouterr().err == "a genuine warning\n"
