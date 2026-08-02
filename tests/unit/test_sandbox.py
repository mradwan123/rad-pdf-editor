"""Unit tests for core/security/sandbox.py."""

from __future__ import annotations

import socket

import pytest

from core.errors import SecurityError
from core.security.sandbox import network_lockdown


def test_blocks_outbound_connection() -> None:
    with network_lockdown():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(SecurityError):
            s.connect(("example.com", 80))


def test_blocks_outbound_connect_ex() -> None:
    with network_lockdown():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(SecurityError):
            s.connect_ex(("example.com", 80))


def test_allows_loopback_connection_attempt() -> None:
    with network_lockdown():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        try:
            s.connect(("127.0.0.1", 65530))
        except SecurityError:
            pytest.fail("loopback connection should not be blocked by network_lockdown")
        except OSError:
            pass  # nothing listening there - the OS refusing it proves our guard let it through


def test_restores_original_connect_after_exit() -> None:
    original = socket.socket.connect
    with network_lockdown():
        assert socket.socket.connect is not original
    assert socket.socket.connect is original


def test_restores_original_connect_even_on_exception() -> None:
    original = socket.socket.connect
    with pytest.raises(RuntimeError), network_lockdown():
        raise RuntimeError("boom")
    assert socket.socket.connect is original
