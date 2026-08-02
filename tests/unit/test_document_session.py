"""Smoke test for the frozen Operation / DocumentSession contract.

This intentionally uses a trivial fake Operation (no real PDF I/O) —
its only job is to prove the undo/redo/apply mechanics in
core/model/document.py behave correctly against the abstract contract
in core/model/operation.py, independent of any real tool
implementation (those land in Phase 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.model.operation import Operation


@dataclass
class _Increment(Operation):
    """Fake operation: bumps a counter stored on working_path (as an
    int, abusing the field for test simplicity) by `amount`."""

    amount: int

    def apply(self, doc: DocumentSession) -> DocumentSession:
        current = doc.working_path or 0  # type: ignore[assignment]
        new = DocumentSession(
            working_path=current + self.amount,  # type: ignore[operator]
            source_path=doc.source_path,
        )
        return new

    def invert(self) -> Operation:
        return _Increment(amount=-self.amount)

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "increment", "amount": self.amount}

    def describe(self) -> str:
        return f"Increment by {self.amount}"


def test_apply_appends_to_operation_log() -> None:
    doc = DocumentSession(working_path=0)  # type: ignore[arg-type]
    doc = doc.apply(_Increment(5))
    assert doc.working_path == 5
    assert len(doc.operation_log) == 1


def test_undo_reverts_and_populates_redo_stack() -> None:
    doc = DocumentSession(working_path=0)  # type: ignore[arg-type]
    doc = doc.apply(_Increment(5))
    doc = doc.undo()
    assert doc.working_path == 0
    assert doc.operation_log == []
    assert len(doc.redo_stack) == 1


def test_redo_reapplies() -> None:
    doc = DocumentSession(working_path=0)  # type: ignore[arg-type]
    doc = doc.apply(_Increment(5)).undo().redo()
    assert doc.working_path == 5
    assert len(doc.operation_log) == 1


def test_new_apply_after_undo_clears_redo_stack() -> None:
    doc = DocumentSession(working_path=0)  # type: ignore[arg-type]
    doc = doc.apply(_Increment(5)).undo().apply(_Increment(10))
    assert doc.working_path == 10
    assert doc.redo_stack == []


def test_undo_with_empty_log_raises() -> None:
    doc = DocumentSession(working_path=0)  # type: ignore[arg-type]
    with pytest.raises(OperationError):
        doc.undo()


def test_serialize_log_round_trips_shape() -> None:
    doc = DocumentSession(working_path=0)  # type: ignore[arg-type]
    doc = doc.apply(_Increment(5))
    log = doc.serialize_log()
    assert log == [{"schema_version": 1, "type": "increment", "amount": 5}]
