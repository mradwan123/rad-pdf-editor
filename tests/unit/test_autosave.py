"""Unit tests for core/session/autosave.py."""

from __future__ import annotations

from pathlib import Path

import pikepdf

from core.model.document import DocumentSession
from core.session.autosave import (
    ACTIVE_SESSION_POINTER,
    AutosaveJournal,
    active_session_id,
    discard_active_session,
    mark_active_session,
    recover_active_session,
)


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


def test_checkpoint_records_the_original_source_path(tmp_path: Path) -> None:
    # Needed so a recovered document keeps the identity of the file it
    # came from, not the identity of the checkpoint file it's restored
    # through.
    doc = _session_with_working_file(tmp_path)
    doc.source_path = tmp_path / "originals" / "report.pdf"
    journal = AutosaveJournal("session-1", root=tmp_path)

    journal.checkpoint(doc)

    recovery = journal.recover()
    assert recovery is not None
    assert recovery.source_path == tmp_path / "originals" / "report.pdf"


# --- most-recently-active-session pointer -----------------------------------
#
# With one journal per open tab, crash recovery is scoped to a single
# session. These cover the pointer that decides which one.


def test_recover_active_session_returns_the_marked_sessions_checkpoint(tmp_path: Path) -> None:
    doc = _session_with_working_file(tmp_path, b"tab a bytes")
    other = _session_with_working_file(tmp_path / "other", b"tab b bytes")
    AutosaveJournal("session-a", root=tmp_path).checkpoint(doc)
    AutosaveJournal("session-b", root=tmp_path).checkpoint(other)

    mark_active_session("session-a", root=tmp_path)

    assert active_session_id(root=tmp_path) == "session-a"
    recovery = recover_active_session(root=tmp_path)
    assert recovery is not None
    # session-b checkpointed *later* but was not the active one - only
    # the marked session is offered.
    assert recovery.checkpoint_path.read_bytes() == b"tab a bytes"


def test_recover_active_session_is_none_with_no_pointer(tmp_path: Path) -> None:
    doc = _session_with_working_file(tmp_path)
    AutosaveJournal("session-a", root=tmp_path).checkpoint(doc)

    assert recover_active_session(root=tmp_path) is None


def test_recover_active_session_is_none_after_the_session_closed_cleanly(
    tmp_path: Path,
) -> None:
    doc = _session_with_working_file(tmp_path)
    journal = AutosaveJournal("session-a", root=tmp_path)
    journal.checkpoint(doc)
    mark_active_session("session-a", root=tmp_path)

    journal.discard()  # what a normal document close does

    assert recover_active_session(root=tmp_path) is None
    # ...and looking must not have resurrected the wiped journal
    # directory as an empty orphan.
    assert not journal.dir.exists()


def test_marking_a_session_active_does_not_disturb_its_journal(tmp_path: Path) -> None:
    # The pointer lives beside the per-session directories, so
    # AutosaveJournal.discard() (which wipes only its own dir) can't
    # take it with it - and vice versa.
    doc = _session_with_working_file(tmp_path)
    journal = AutosaveJournal("session-a", root=tmp_path)
    journal.checkpoint(doc)

    mark_active_session("session-a", root=tmp_path)

    assert (tmp_path / ACTIVE_SESSION_POINTER).exists()
    assert journal.recover() is not None


def test_discard_active_session_wipes_the_journal_and_the_pointer(tmp_path: Path) -> None:
    doc = _session_with_working_file(tmp_path)
    journal = AutosaveJournal("session-a", root=tmp_path)
    journal.checkpoint(doc)
    mark_active_session("session-a", root=tmp_path)

    discard_active_session(root=tmp_path)

    assert not journal.dir.exists()
    assert active_session_id(root=tmp_path) is None
    assert recover_active_session(root=tmp_path) is None


def test_clearing_the_pointer_leaves_nothing_to_recover(tmp_path: Path) -> None:
    doc = _session_with_working_file(tmp_path)
    AutosaveJournal("session-a", root=tmp_path).checkpoint(doc)
    mark_active_session("session-a", root=tmp_path)

    mark_active_session(None, root=tmp_path)

    assert active_session_id(root=tmp_path) is None
    assert recover_active_session(root=tmp_path) is None


def test_a_corrupt_pointer_means_no_recovery_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / ACTIVE_SESSION_POINTER).write_text("{not json", encoding="utf-8")

    assert active_session_id(root=tmp_path) is None
    assert recover_active_session(root=tmp_path) is None


def test_recover_active_session_is_none_when_the_checkpoint_file_is_gone(
    tmp_path: Path,
) -> None:
    doc = _session_with_working_file(tmp_path)
    journal = AutosaveJournal("session-a", root=tmp_path)
    journal.checkpoint(doc)
    mark_active_session("session-a", root=tmp_path)
    recovery = journal.recover()
    assert recovery is not None
    recovery.checkpoint_path.unlink()

    assert recover_active_session(root=tmp_path) is None
