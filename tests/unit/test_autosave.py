"""Unit tests for core/session/autosave.py."""

from __future__ import annotations

from pathlib import Path

import pikepdf

from core.model.document import DocumentSession
from core.session.autosave import AutosaveJournal


def _session_with_working_file(tmp_path: Path, content: bytes = b"pdf bytes") -> DocumentSession:
    working = tmp_path / "source" / "working.pdf"
    working.parent.mkdir(parents=True)
    working.write_bytes(content)
    return DocumentSession(working_path=working, source_path=None, display_name="report.pdf")


def _session_with_real_pdf(tmp_path: Path) -> DocumentSession:
    working = tmp_path / "source" / "working.pdf"
    working.parent.mkdir(parents=True)
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.save(working)
    return DocumentSession(working_path=working, source_path=None, display_name="report.pdf")


def test_recover_returns_none_when_never_checkpointed(tmp_path: Path) -> None:
    journal = AutosaveJournal("session-1", root=tmp_path)
    assert journal.recover() is None


def test_checkpoint_then_recover_round_trips_working_file(tmp_path: Path) -> None:
    doc = _session_with_working_file(tmp_path, b"original bytes")
    journal = AutosaveJournal("session-1", root=tmp_path)

    journal.checkpoint(doc)
    recovery = journal.recover()

    assert recovery is not None
    assert recovery.checkpoint_path is not None
    assert recovery.checkpoint_path.read_bytes() == b"original bytes"
    assert recovery.display_name == "report.pdf"


def test_checkpoint_includes_serialized_operation_log(tmp_path: Path) -> None:
    doc = _session_with_real_pdf(tmp_path)
    from core.ops.organize import RotatePagesOperation

    applied = doc.apply(RotatePagesOperation(angle=90))
    journal = AutosaveJournal("session-1", root=tmp_path)

    journal.checkpoint(applied)
    recovery = journal.recover()

    assert recovery is not None
    assert len(recovery.operation_log) == 1
    assert recovery.operation_log[0]["type"] == "rotate_pages"


def test_checkpoint_overwrites_previous_checkpoint(tmp_path: Path) -> None:
    doc = _session_with_working_file(tmp_path, b"first version")
    journal = AutosaveJournal("session-1", root=tmp_path)
    journal.checkpoint(doc)

    doc.working_path.write_bytes(b"second version")
    journal.checkpoint(doc)

    recovery = journal.recover()
    assert recovery is not None
    assert recovery.checkpoint_path.read_bytes() == b"second version"


def test_checkpoint_with_no_working_file_records_null_checkpoint(tmp_path: Path) -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    journal = AutosaveJournal("session-1", root=tmp_path)

    journal.checkpoint(doc)
    recovery = journal.recover()

    assert recovery is not None
    assert recovery.checkpoint_path is None


def test_discard_removes_all_autosave_data(tmp_path: Path) -> None:
    doc = _session_with_working_file(tmp_path)
    journal = AutosaveJournal("session-1", root=tmp_path)
    journal.checkpoint(doc)

    journal.discard()

    assert journal.recover() is None
    assert not journal.dir.exists()


def test_distinct_sessions_get_distinct_journals(tmp_path: Path) -> None:
    doc = _session_with_working_file(tmp_path)
    a = AutosaveJournal("session-a", root=tmp_path)
    b = AutosaveJournal("session-b", root=tmp_path)

    a.checkpoint(doc)

    assert a.recover() is not None
    assert b.recover() is None
