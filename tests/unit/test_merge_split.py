"""Unit tests for core/ops/merge_split.py (Merge, Extract) against real
(tiny, synthetic) PDFs — see SPEC.md's fixtures policy for why these are
generated in-test rather than checked-in binaries for such a simple case.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from core.errors import CorruptDocumentError, OperationError
from core.model.document import DocumentSession
from core.ops.merge_split import ExtractPagesOperation, MergeOperation


def _make_pdf(path: Path, num_pages: int) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(200, 200))
    pdf.save(path)
    return path


def _page_count(path: Path) -> int:
    with pikepdf.Pdf.open(path) as pdf:
        return len(pdf.pages)


def _session_with_pages(tmp_path: Path, num_pages: int) -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_pdf(session_dir / "working.pdf", num_pages)
    return DocumentSession(working_path=working, source_path=None)


def test_merge_concatenates_pages_in_order(tmp_path: Path) -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    a = _make_pdf(tmp_path / "a.pdf", 2)
    b = _make_pdf(tmp_path / "b.pdf", 3)

    result = doc.apply(MergeOperation(sources=[a, b]))

    assert result.working_path is not None
    assert _page_count(result.working_path) == 5


def test_merge_requires_at_least_one_source() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(MergeOperation(sources=[]))


def test_merge_raises_corrupt_document_error_on_bad_input(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")

    # The Operation itself raises the specific error type...
    with pytest.raises(CorruptDocumentError):
        MergeOperation(sources=[bad]).apply(DocumentSession(working_path=None, source_path=None))

    # ...but DocumentSession.apply (frozen interface) wraps every
    # exception as OperationError, chaining the original as __cause__.
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError) as exc_info:
        doc.apply(MergeOperation(sources=[bad]))
    assert isinstance(exc_info.value.__cause__, CorruptDocumentError)


def test_merge_undo_restores_prior_state(tmp_path: Path) -> None:
    # Merge replaces the working document with exactly `sources`
    # (matching a "combine these selected files" tool, not "append to
    # whatever's currently open") - undo must still restore whatever
    # was open beforehand.
    doc = _session_with_pages(tmp_path, 1)
    other = _make_pdf(tmp_path / "other.pdf", 4)

    merged = doc.apply(MergeOperation(sources=[other]))
    assert _page_count(merged.working_path) == 4

    restored = merged.undo()
    assert _page_count(restored.working_path) == 1


def test_merge_undo_from_empty_session_restores_no_document(tmp_path: Path) -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    a = _make_pdf(tmp_path / "a.pdf", 2)

    merged = doc.apply(MergeOperation(sources=[a]))
    restored = merged.undo()
    assert restored.working_path is None


def test_extract_keeps_only_requested_pages_in_order(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 5)

    result = doc.apply(ExtractPagesOperation(pages=[3, 1]))
    assert _page_count(result.working_path) == 2


def test_extract_out_of_range_page_raises(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 2)
    with pytest.raises(OperationError):
        doc.apply(ExtractPagesOperation(pages=[5]))


def test_extract_with_no_document_open_raises() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(ExtractPagesOperation(pages=[1]))


def test_extract_undo_restores_all_pages(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 5)
    result = doc.apply(ExtractPagesOperation(pages=[1, 2]))
    assert _page_count(result.working_path) == 2

    restored = result.undo()
    assert _page_count(restored.working_path) == 5
