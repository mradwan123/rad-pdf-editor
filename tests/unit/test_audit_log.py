"""Unit tests for core/session/audit_log.py."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.model.document import DocumentSession
from core.model.operation import Operation
from core.session.audit_log import AuditLog


@dataclass
class _NoOp(Operation):
    label: str = "noop"

    def apply(self, doc: DocumentSession) -> DocumentSession:
        return doc

    def invert(self) -> Operation:
        return self

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "noop", "label": self.label}

    def describe(self) -> str:
        return f"No-op: {self.label}"


def test_record_operation_appends_a_line(tmp_path: Path) -> None:
    audit = AuditLog(path=tmp_path / "audit.jsonl")
    audit.record_operation(_NoOp("first"))
    audit.record_operation(_NoOp("second"))

    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_read_all_returns_entries_in_order(tmp_path: Path) -> None:
    audit = AuditLog(path=tmp_path / "audit.jsonl")
    audit.record_operation(_NoOp("first"), document_label="a.pdf")
    audit.record_operation(_NoOp("second"), document_label="a.pdf")

    entries = audit.read_all()
    assert [e["description"] for e in entries] == ["No-op: first", "No-op: second"]
    assert entries[0]["document"] == "a.pdf"
    assert entries[0]["operation"] == {"schema_version": 1, "type": "noop", "label": "first"}
    assert "timestamp" in entries[0]


def test_read_all_returns_empty_list_when_no_file_exists(tmp_path: Path) -> None:
    audit = AuditLog(path=tmp_path / "does-not-exist.jsonl")
    assert audit.read_all() == []


def test_creates_parent_directory(tmp_path: Path) -> None:
    audit = AuditLog(path=tmp_path / "nested" / "dir" / "audit.jsonl")
    audit.record_operation(_NoOp())
    assert (tmp_path / "nested" / "dir" / "audit.jsonl").exists()


def test_two_audit_log_instances_share_the_same_append_only_file(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path=path).record_operation(_NoOp("first"))
    AuditLog(path=path).record_operation(_NoOp("second"))
    assert len(AuditLog(path=path).read_all()) == 2
