"""Opt-in progress reporting for long-running operations.

Phase 6d (docs/GUI_PLAN.md §3.5, decision 11). Most operations in this
codebase are a single opaque call into pikepdf, LibreOffice, ocrmypdf
or Ghostscript and cannot report a percentage at all. The ones that
loop over pages can, and those are the ones where it matters - OCR and
deskew on a long scan are exactly the multi-minute waits that look like
a hang.

**Why a mixin rather than a parameter on `Operation.apply`.** Adding
`apply(self, doc, progress=None)` to the frozen base would look
additive and is not: every existing subclass declares `apply(self,
doc)`, so calling one with a `progress=` argument raises `TypeError`.
A separate opt-in mixin leaves the frozen signature untouched and lets
the runner feature-detect with `isinstance`, showing an indeterminate
bar for everything else. See SPEC.md 6.1 on the interface freeze.

Cancellation rides on the same channel: the callback may raise
`OperationCancelledError`, which unwinds the operation through its normal
error path. That is safe precisely because operations write their
output to a *new* working file (`allocate_working_path`) and the
session only adopts it on success - a cancelled operation leaves the
document untouched and an orphaned partial file in the session temp
dir, which is securely wiped with the rest of the session.
"""

from __future__ import annotations

from collections.abc import Callable

#: Called with (completed units, total units). May raise
#: `core.errors.OperationCancelledError` to abort the operation.
ProgressCallback = Callable[[int, int], None]


class SupportsProgress:
    """Mixin for operations that can report incremental progress.

    Subclasses call `self.report_progress(done, total)` as they work.
    An operation that does not inherit this is reported as
    indeterminate by the GUI, which is honest rather than fabricated.
    """

    #: Not a dataclass field - set after construction by the runner, so
    #: it never appears in `serialize()` and never reaches a saved
    #: Workflow or the audit log.
    _progress_callback: ProgressCallback | None = None

    def set_progress_callback(self, callback: ProgressCallback | None) -> None:
        self._progress_callback = callback

    def report_progress(self, done: int, total: int) -> None:
        """Report progress, and give the caller a chance to cancel.

        Propagates whatever the callback raises - `OperationCancelledError`
        by convention - so an operation needs no cancellation logic of
        its own beyond calling this between units of work.
        """
        if self._progress_callback is not None and total > 0:
            self._progress_callback(done, total)
