"""Smoke test for the frozen Pipeline contract.

Uses trivial fake Operations (no real PDF I/O) to prove run()/serialize()
mechanics in core/model/pipeline.py, independent of any real tool
implementation (those land in Phase 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.model.pipeline import Pipeline


@dataclass
class _Increment(Operation):
    amount: int

    def apply(self, doc: DocumentSession) -> DocumentSession:
        current = doc.working_path or 0  # type: ignore[assignment]
        return DocumentSession(
            working_path=current + self.amount,  # type: ignore[operator]
            source_path=doc.source_path,
        )

    def invert(self) -> Operation:
        return _Increment(amount=-self.amount)

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "increment", "amount": self.amount}

    def describe(self) -> str:
        return f"Increment by {self.amount}"


@dataclass
class _AlwaysFails(Operation):
    def apply(self, doc: DocumentSession) -> DocumentSession:
        raise ValueError("boom")

    def invert(self) -> Operation:
        return self

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "always_fails"}

    def describe(self) -> str:
        return "Always fails"


def test_run_applies_operations_in_order() -> None:
    doc = DocumentSession(working_path=0)  # type: ignore[arg-type]
    pipeline = Pipeline(name="add-then-double", operations=[_Increment(5), _Increment(2)])
    result = pipeline.run(doc)
    assert result.working_path == 7
    assert len(result.operation_log) == 2


def test_run_stops_and_raises_on_first_failure() -> None:
    doc = DocumentSession(working_path=0)  # type: ignore[arg-type]
    pipeline = Pipeline(name="broken", operations=[_Increment(5), _AlwaysFails(), _Increment(100)])
    with pytest.raises(OperationError):
        pipeline.run(doc)


def test_serialize_round_trips_shape() -> None:
    pipeline = Pipeline(name="my-workflow", operations=[_Increment(5)])
    assert pipeline.serialize() == {
        "schema_version": 1,
        "name": "my-workflow",
        "operations": [{"schema_version": 1, "type": "increment", "amount": 5}],
    }
