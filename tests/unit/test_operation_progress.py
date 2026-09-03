"""Phase 6d: progress reporting and cancellation.

Two things are under test and they are separable: the *mixin* that lets
an operation report progress and be interrupted, and the *runner* that
drives one off the UI thread behind a cancellable dialog.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pikepdf
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.errors import OperationCancelledError
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.model.progress import SupportsProgress
from core.ops.organize import RotatePagesOperation
from gui.operation_runner import OperationRunner


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_pdf(path: Path, pages: int = 5) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(200, 300))
    pdf.save(path)
    return path


@pytest.fixture
def session(tmp_path: Path) -> Iterator[DocumentSession]:
    working = _make_pdf(tmp_path / "working.pdf", 5)
    yield DocumentSession(working_path=working, source_path=working)


# --- the mixin -------------------------------------------------------------


def test_an_operation_without_the_mixin_is_not_reported_as_determinate() -> None:
    """The runner feature-detects, so an operation that cannot report
    progress gets an indeterminate bar rather than a fabricated one."""
    from core.ops.organize import DeletePagesOperation

    assert not isinstance(DeletePagesOperation(pages=[1]), SupportsProgress)
    assert isinstance(RotatePagesOperation(angle=90), SupportsProgress)


def test_reporting_is_a_no_op_until_a_callback_is_set(session: DocumentSession) -> None:
    """Every existing caller - the CLI, workflows, the tests - applies
    operations with no callback at all, and must be unaffected."""
    operation = RotatePagesOperation(angle=90)
    operation.apply(session)  # must not raise


def test_a_page_looping_operation_reports_each_page(session: DocumentSession) -> None:
    seen: list[tuple[int, int]] = []
    operation = RotatePagesOperation(angle=90)
    operation.set_progress_callback(lambda done, total: seen.append((done, total)))

    operation.apply(session)

    assert seen == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]


def test_reporting_only_the_targeted_pages(session: DocumentSession) -> None:
    seen: list[tuple[int, int]] = []
    operation = RotatePagesOperation(angle=90, pages=[2, 4])
    operation.set_progress_callback(lambda done, total: seen.append((done, total)))

    operation.apply(session)

    assert seen == [(1, 2), (2, 2)]


def test_raising_from_the_callback_cancels_and_leaves_the_document_alone(
    session: DocumentSession, tmp_path: Path,
) -> None:
    """Cancellation is safe because an operation writes to a *new*
    working file and the session only adopts it on success - so the
    document the user has is untouched."""
    before = Path(session.working_path).read_bytes()

    def cancel_after_two(done: int, _total: int) -> None:
        if done >= 2:
            raise OperationCancelledError("stop")

    operation = RotatePagesOperation(angle=90)
    operation.set_progress_callback(cancel_after_two)

    with pytest.raises(OperationCancelledError):
        operation.apply(session)

    assert Path(session.working_path).read_bytes() == before


# --- the runner ------------------------------------------------------------


class _ProgressOperation(Operation, SupportsProgress):
    """A stand-in with controllable timing. The runner takes the work
    as a callable, so this never has to touch a real document."""

    def apply(self, doc: DocumentSession) -> DocumentSession:  # pragma: no cover
        return doc

    def invert(self) -> Operation:  # pragma: no cover
        return self

    def serialize(self) -> dict[str, Any]:  # pragma: no cover
        return {"schema_version": self.schema_version, "type": "test_progress"}

    def describe(self) -> str:
        return "Test operation"


def test_the_runner_completes_and_reports_progress(qapp: QApplication) -> None:
    operation = _ProgressOperation()
    steps: list[int] = []

    def work() -> None:
        for i in range(5):
            operation.report_progress(i + 1, 5)
            steps.append(i)

    runner = OperationRunner()
    assert runner.run(operation, work, "Testing") is True
    assert steps == [0, 1, 2, 3, 4]
    assert runner.was_cancelled is False


def test_cancelling_stops_the_work_and_reports_false(qapp: QApplication) -> None:
    """The cancel flag is read on the worker thread by report_progress,
    which raises OperationCancelledError - so the operation unwinds
    through its normal error path rather than being killed."""
    operation = _ProgressOperation()
    runner = OperationRunner()
    completed: list[int] = []

    def work() -> None:
        for i in range(60):
            operation.report_progress(i + 1, 60)
            completed.append(i)
            time.sleep(0.01)

    # ~0.6s of work; cancel a tenth of the way in. Driven from the event
    # loop the runner is already spinning, which is exactly how the
    # dialog's Cancel button reaches it.
    QTimer.singleShot(60, runner.cancel)

    assert runner.run(operation, work, "Testing") is False
    assert runner.was_cancelled is True
    assert len(completed) < 60, "the work must actually have stopped early"


def test_an_error_is_re_raised_on_the_calling_thread(qapp: QApplication) -> None:
    """A worker exception must not escape the QRunnable and take the
    process down - it comes back to the caller unchanged."""
    from core.errors import OperationError

    operation = _ProgressOperation()

    def work() -> None:
        raise OperationError("boom")

    with pytest.raises(OperationError, match="boom"):
        OperationRunner().run(operation, work, "Testing")
