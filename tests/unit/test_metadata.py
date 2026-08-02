"""Unit tests for core/ops/metadata.py (SetMetadata, Rename)."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.metadata import RenameOperation, SetMetadataOperation


def _make_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.save(path)
    return path


def _docinfo(path: Path) -> dict[str, str]:
    with pikepdf.Pdf.open(path) as pdf:
        return {str(k): str(v) for k, v in pdf.docinfo.items()}


def _session(tmp_path: Path) -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_pdf(session_dir / "working.pdf")
    return DocumentSession(working_path=working, source_path=None)


def test_set_metadata_writes_fields(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(SetMetadataOperation(fields={"title": "Q1 Report", "author": "Radwan"}))
    info = _docinfo(result.working_path)
    assert info["/Title"] == "Q1 Report"
    assert info["/Author"] == "Radwan"


def test_set_metadata_rejects_unknown_field(tmp_path: Path) -> None:
    with pytest.raises(OperationError):
        SetMetadataOperation(fields={"bogus": "x"})


def test_set_metadata_undo_restores_prior_fields(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    once = doc.apply(SetMetadataOperation(fields={"title": "First"}))
    twice = once.apply(SetMetadataOperation(fields={"title": "Second"}))
    restored = twice.undo()
    assert _docinfo(restored.working_path)["/Title"] == "First"


def test_rename_sets_display_name(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    assert doc.display_name is None
    result = doc.apply(RenameOperation(new_name="report-final.pdf"))
    assert result.display_name == "report-final.pdf"


def test_rename_rejects_empty_name(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    with pytest.raises(OperationError):
        doc.apply(RenameOperation(new_name="   "))


def test_rename_undo_restores_none_when_previously_unset(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(RenameOperation(new_name="report-final.pdf"))
    restored = result.undo()
    assert restored.display_name is None


def test_rename_undo_restores_previous_name(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    once = doc.apply(RenameOperation(new_name="draft.pdf"))
    twice = once.apply(RenameOperation(new_name="final.pdf"))
    restored = twice.undo()
    assert restored.display_name == "draft.pdf"


def test_rename_does_not_touch_pdf_bytes(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    original_bytes = doc.working_path.read_bytes()
    result = doc.apply(RenameOperation(new_name="renamed.pdf"))
    assert result.working_path == doc.working_path
    assert result.working_path.read_bytes() == original_bytes


def test_display_name_survives_unrelated_operation(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    renamed = doc.apply(RenameOperation(new_name="renamed.pdf"))
    retitled = renamed.apply(SetMetadataOperation(fields={"title": "X"}))
    assert retitled.display_name == "renamed.pdf"
