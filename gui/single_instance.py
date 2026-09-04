"""Single-instance guard for the GUI entry point (gui/main.py).

Double-clicking the desktop launcher a second time while the app is
already open used to spawn a second, fully independent window. This
uses `QLocalServer`/`QLocalSocket` - plain local IPC - rather than a
window-manager call (`wmctrl`, `xdotool`, ...) to ask the running
instance to come to the foreground: those tools only work reliably
under X11, not Wayland, and this app is used under both.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from core.logging_config import get_logger

log = get_logger(__name__)

_SERVER_NAME = "rad-pdf-editor-single-instance"
_RAISE_MESSAGE = b"raise"
_CONNECT_TIMEOUT_MS = 500


class SingleInstanceGuard(QObject):
    """Call `try_acquire()` once at startup. A `False` return means
    another instance already owns the app - that instance has been
    sent a raise request and this process should exit immediately
    without creating a window. A `True` return means this process is
    now the one instance; connect to `raise_requested` to bring its
    window to the foreground when a later launch asks it to.
    """

    raise_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server: QLocalServer | None = None

    def try_acquire(self) -> bool:
        probe = QLocalSocket(self)
        probe.connectToServer(_SERVER_NAME)
        if probe.waitForConnected(_CONNECT_TIMEOUT_MS):
            probe.write(_RAISE_MESSAGE)
            probe.waitForBytesWritten(_CONNECT_TIMEOUT_MS)
            probe.disconnectFromServer()
            return False

        # No live server answered: either this is the first instance,
        # or a previous one crashed and left its socket file behind.
        # removeServer() clears a stale leftover either way; it is a
        # no-op if nothing needed cleaning up.
        QLocalServer.removeServer(_SERVER_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        if not self._server.listen(_SERVER_NAME):
            log.warning(
                "Single-instance server failed to start (%s) - a second "
                "launch will open its own window instead of raising this one.",
                self._server.errorString(),
            )
        return True

    def _on_new_connection(self) -> None:
        assert self._server is not None
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda: self._on_ready_read(socket))
        socket.disconnected.connect(socket.deleteLater)

    def _on_ready_read(self, socket: QLocalSocket) -> None:
        if socket.readAll().data() == _RAISE_MESSAGE:
            self.raise_requested.emit()
