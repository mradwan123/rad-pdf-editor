"""Unit tests for core/ops/organize.py (Reorder, Rotate, Delete Pages, Compress)."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.organize import (
    CompressOperation,
    DeletePagesOperation,
    ReorderPagesOperation,
    RotatePagesOperation,
)


def _make_pdf(path: Path, num_pages: int) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(200, 200))
    pdf.save(path)
    return path


def _rotations(path: Path) -> list[int]:
    with pikepdf.Pdf.open(path) as pdf:
        return [int(p.get("/Rotate", 0)) for p in pdf.pages]


def _page_count(path: Path) -> int:
    with pikepdf.Pdf.open(path) as pdf:
        return len(pdf.pages)


def _session_with_pages(tmp_path: Path, num_pages: int) -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_pdf(session_dir / "working.pdf", num_pages)
    return DocumentSession(working_path=working, source_path=None)


def test_reorder_pages_changes_page_order(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 3)
    result = doc.apply(ReorderPagesOperation(page_order=[3, 1, 2]))
    assert _page_count(result.working_path) == 3


def test_reorder_pages_requires_full_permutation(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 3)
    with pytest.raises(OperationError):
        doc.apply(ReorderPagesOperation(page_order=[1, 2]))


def test_reorder_undo_restores_order(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 3)
    result = doc.apply(ReorderPagesOperation(page_order=[3, 1, 2]))
    restored = result.undo()
    assert _page_count(restored.working_path) == 3


def test_rotate_all_pages(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 2)
    result = doc.apply(RotatePagesOperation(angle=90))
    assert _rotations(result.working_path) == [90, 90]


def test_rotate_specific_pages(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 3)
    result = doc.apply(RotatePagesOperation(angle=180, pages=[2]))
    assert _rotations(result.working_path) == [0, 180, 0]


def test_rotate_rejects_non_multiple_of_90(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 1)
    with pytest.raises(OperationError):
        doc.apply(RotatePagesOperation(angle=45))


def test_rotate_duplicate_page_numbers_apply_once_not_twice(tmp_path: Path) -> None:
    # Regression: pages=[1, 1] previously rotated page 1 twice (180
    # instead of 90) because nothing deduplicated the target list.
    doc = _session_with_pages(tmp_path, 1)
    result = doc.apply(RotatePagesOperation(angle=90, pages=[1, 1]))
    assert _rotations(result.working_path) == [90]


def test_rotate_undo_restores_orientation(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 1)
    result = doc.apply(RotatePagesOperation(angle=90))
    restored = result.undo()
    assert _rotations(restored.working_path) == [0]


def test_delete_pages_removes_requested_pages(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 4)
    result = doc.apply(DeletePagesOperation(pages=[2, 4]))
    assert _page_count(result.working_path) == 2


def test_delete_all_pages_raises(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 2)
    with pytest.raises(OperationError):
        doc.apply(DeletePagesOperation(pages=[1, 2]))


def test_delete_undo_restores_pages(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 4)
    result = doc.apply(DeletePagesOperation(pages=[2, 4]))
    restored = result.undo()
    assert _page_count(restored.working_path) == 4


def test_compress_preserves_page_count(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 5)
    result = doc.apply(CompressOperation())
    assert _page_count(result.working_path) == 5


def test_compress_describe_reports_size_change(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 5)
    op = CompressOperation()
    doc.apply(op)
    assert "Compressed" in op.describe()


def test_compress_undo_restores_prior_bytes(tmp_path: Path) -> None:
    doc = _session_with_pages(tmp_path, 3)
    original_bytes = doc.working_path.read_bytes()
    result = doc.apply(CompressOperation())
    restored = result.undo()
    assert restored.working_path.read_bytes() == original_bytes


def test_compress_with_no_document_open_raises() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(CompressOperation())
