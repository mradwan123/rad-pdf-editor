"""Running an `Operation` off the UI thread, with progress and cancel.

Phase 6d (docs/GUI_PLAN.md §3.5). Until now every operation ran
synchronously behind a wait cursor, so OCR on a long scan or a
LibreOffice conversion froze the window outright - on Windows, into an
"application not responding" state.

**Operations are unchanged.** They stay synchronous and know nothing
about threads; this wraps them. `AppController` is deliberately Qt-free
(see `gui/controller.py`), so running `apply_operation` on a worker
touches no widgets. The progress dialog is window-modal, which keeps
the event loop turning - so the window repaints and Cancel works -
while stopping the user driving the same document from two directions
at once.

**Cancellation is cooperative, and safe because of how the document
model already works.** An operation writes its output to a *new*
working file and the session only adopts it on success, so cancelling
discards a file the document never referenced: the document is
untouched and the orphan is securely wiped with the session. Only
operations that report progress can actually be interrupted - the
callback raises `OperationCancelledError` between pages. For an opaque
one (a single call into LibreOffice or ocrmypdf) Cancel stops the
*wait*, and the result is discarded when it eventually arrives; the
dialog says so rather than pretending the work stopped.

The caller blocks on a nested `QEventLoop` rather than being handed a
future. Every call site is written as "apply, then refresh", and making
them all asynchronous would be a much larger change than this needs -
the point of 6d is that the *event loop* keeps running, not that the
call site stops being sequential.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEventLoop, QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QProgressDialog, QWidget

from core.errors import OperationCancelledError
from core.logging_config import get_logger
from core.model.operation import Operation
from core.model.progress import SupportsProgress

log = get_logger(__name__)

#: Operations quicker than this never show a dialog - flashing one up
#: for a 20 ms rotate is worse than showing nothing at all.
_DIALOG_DELAY_MS = 400


class _WorkerSignals(QObject):
    progress = Signal(int, int)
    finished = Signal()
    failed = Signal(object)


class _Worker(QRunnable):
    """Runs one callable on a `QThreadPool` thread."""

    def __init__(self, work: Callable[[], None], signals: _WorkerSignals) -> None:
        super().__init__()
        self._work = work
        self._signals = signals

    def run(self) -> None:
        try:
            self._work()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the UI thread
            # Everything, including OperationCancelledError: the UI
            # thread decides what to report. An exception escaping a
            # QRunnable would take the process down instead.
            self._signals.failed.emit(exc)
            return
        self._signals.finished.emit()


class OperationRunner(QObject):
    """Runs an `Operation` behind a cancellable progress dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._parent = parent
        self._cancelled = False
        self._error: BaseException | None = None
        self._dialog: QProgressDialog | None = None

    def run(self, operation: Operation, apply: Callable[[], None], label: str) -> bool:
        """Apply `operation` by calling `apply()` on a worker thread.

        Returns True if it completed and False if the user cancelled.
        Any `PDFEditorError` is re-raised on the calling thread, so the
        existing error handling at each call site is unchanged.
        """
        self._cancelled = False
        self._error = None

        signals = _WorkerSignals()
        signals.progress.connect(self._on_progress)

        # Held as the narrowed object rather than a bool, so the
        # callback calls below type-check without a cast.
        reporter = operation if isinstance(operation, SupportsProgress) else None
        determinate = reporter is not None
        if reporter is not None:
            reporter.set_progress_callback(
                lambda done, total: self._report(signals, done, total)
            )

        dialog = QProgressDialog(label, self.tr("Cancel"), 0, 100 if determinate else 0,
                                 self._parent)
        dialog.setWindowTitle(self.tr("Working"))
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(_DIALOG_DELAY_MS)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self._on_cancel_requested)
        dialog.setValue(0)  # starts the minimum-duration timer
        self._dialog = dialog

        loop = QEventLoop()
        signals.finished.connect(loop.quit)
        signals.failed.connect(self._on_failed)
        signals.failed.connect(loop.quit)
        QThreadPool.globalInstance().start(_Worker(apply, signals))
        loop.exec()

        dialog.reset()
        dialog.deleteLater()
        self._dialog = None
        if reporter is not None:
            # Not left dangling on the operation: it may be kept in the
            # undo stack, and a stale callback into a finished runner
            # would fire on a later undo/redo.
            reporter.set_progress_callback(None)

        if isinstance(self._error, OperationCancelledError):
            return False
        if self._error is not None:
            raise self._error
        return not self._cancelled

    def cancel(self) -> None:
        """Request cancellation, as the dialog's Cancel button does.
        Takes effect at the operation's next progress report; an opaque
        operation runs to completion and its result is discarded."""
        self._on_cancel_requested()

    @property
    def was_cancelled(self) -> bool:
        return self._cancelled

    # --- internals --------------------------------------------------------

    def _report(self, signals: _WorkerSignals, done: int, total: int) -> None:
        """Called on the *worker* thread, by the operation itself."""
        if self._cancelled:
            raise OperationCancelledError("Cancelled by the user.")
        signals.progress.emit(done, total)

    def _on_progress(self, done: int, total: int) -> None:
        if self._dialog is None or total <= 0:
            return
        self._dialog.setMaximum(100)
        self._dialog.setValue(int(done * 100 / total))

    def _on_cancel_requested(self) -> None:
        self._cancelled = True
        if self._dialog is not None:
            self._dialog.setLabelText(self.tr("Cancelling..."))

    def _on_failed(self, exc: object) -> None:
        if isinstance(exc, BaseException):
            self._error = exc
