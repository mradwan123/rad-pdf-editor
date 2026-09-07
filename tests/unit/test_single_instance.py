"""Unit tests for gui/single_instance.py - plain Qt-network IPC, no
display server needed (unlike gui/main.py's actual window)."""

from __future__ import annotations

import uuid

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from gui.single_instance import SingleInstanceGuard


@pytest.fixture
def qcoreapp() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture(autouse=True)
def _isolated_server_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # A name unique per test run/process so this test can never collide
    # with a real running instance's socket.
    monkeypatch.setattr("gui.single_instance._SERVER_NAME", f"rad-pdf-editor-test-{uuid.uuid4().hex}")


def _pump(timeout_ms: int = 1000) -> None:
    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


def test_first_instance_acquires(qcoreapp: QCoreApplication) -> None:
    guard = SingleInstanceGuard()
    assert guard.try_acquire() is True


def test_second_instance_is_rejected_and_raises_the_first(qcoreapp: QCoreApplication) -> None:
    first = SingleInstanceGuard()
    assert first.try_acquire() is True

    received: list[bool] = []
    loop = QEventLoop()
    first.raise_requested.connect(lambda: (received.append(True), loop.quit()))

    second = SingleInstanceGuard()
    assert second.try_acquire() is False

    QTimer.singleShot(2000, loop.quit)  # safety net if the signal never arrives
    loop.exec()

    assert received == [True]
