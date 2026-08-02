"""Process-level network lockdown - defense in depth beyond "we just
don't call requests.get()" (SPEC.md section 2, "Security layer").

This does not replace the primary guarantee (no networking library is
ever imported or called in this codebase's own code); it's a backstop
against a future dependency or contributor accidentally introducing
one. Loopback connections are still allowed, since local IPC (e.g. a
subprocess health-check on 127.0.0.1) is not a confidentiality risk
the way an outbound connection is.

This is a heuristic, not a kernel-enforced boundary: it patches
`socket.socket.connect`/`connect_ex` for the current process only, and
only recognizes loopback by exact hostname/address match (not full
CIDR/IPv6-mapped-address parsing). True OS-level enforcement (a
firewall rule per subprocess) is an open item - see SPEC.md section 5.
"""

from __future__ import annotations

import contextlib
import socket
from collections.abc import Iterator
from typing import Any

from core.errors import SecurityError
from core.logging_config import get_logger

log = get_logger(__name__)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", ""}

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


def _is_loopback(address: Any) -> bool:
    if isinstance(address, tuple) and address:
        host = address[0]
        return isinstance(host, str) and (host in _LOOPBACK_HOSTS or host.startswith("127."))
    # Unix domain sockets (path or abstract-namespace strings/bytes) are
    # local IPC, not network egress.
    return isinstance(address, (str, bytes))


def _guarded_connect(self: socket.socket, address: Any) -> None:
    if not _is_loopback(address):
        raise SecurityError(f"Network lockdown: outbound connection to {address!r} blocked.")
    _original_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    if not _is_loopback(address):
        raise SecurityError(f"Network lockdown: outbound connection to {address!r} blocked.")
    return _original_connect_ex(self, address)


@contextlib.contextmanager
def network_lockdown() -> Iterator[None]:
    """Block outbound (non-loopback) socket connections for the
    duration of the `with` block, in this process only."""
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign, assignment]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign, assignment]
    log.info("Network lockdown enabled")
    try:
        yield
    finally:
        socket.socket.connect = _original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = _original_connect_ex  # type: ignore[method-assign]
        log.info("Network lockdown disabled")
