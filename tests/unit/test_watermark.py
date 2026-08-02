"""Unit tests for core/ops/watermark.py."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.watermark import WatermarkOperation


def _make_pdf(path: Path, sizes: list[tuple[int, int]]) -> Path:
    pdf = pikepdf.Pdf.new()
    for size in sizes:
        pdf.add_blank_page(page_size=size)
    pdf.save(path)
    return path


def _session(tmp_path: Path, sizes: list[tuple[int, int]]) -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_pdf(session_dir / "working.pdf", sizes)
    return DocumentSession(working_path=working, source_path=None)


def test_watermark_adds_content_to_every_page(tmp_path: Path) -> None:
    doc = _session(tmp_path, [(300, 400), (600, 800)])
    original_size = doc.working_path.stat().st_size

    result = doc.apply(WatermarkOperation(text="CONFIDENTIAL"))

    with pikepdf.Pdf.open(result.working_path) as pdf:
        assert len(pdf.pages) == 2
    # Watermarking necessarily adds an XObject + content per page.
    assert result.working_path.stat().st_size > original_size


def test_watermark_rejects_empty_text(tmp_path: Path) -> None:
    doc = _session(tmp_path, [(300, 400)])
    with pytest.raises(OperationError):
        doc.apply(WatermarkOperation(text="   "))


def test_watermark_rejects_out_of_range_opacity(tmp_path: Path) -> None:
    doc = _session(tmp_path, [(300, 400)])
    with pytest.raises(OperationError):
        doc.apply(WatermarkOperation(text="X", opacity=1.5))


def test_watermark_with_no_document_open_raises() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(WatermarkOperation(text="X"))


def test_watermark_undo_restores_prior_bytes(tmp_path: Path) -> None:
    doc = _session(tmp_path, [(300, 400)])
    original_bytes = doc.working_path.read_bytes()
    result = doc.apply(WatermarkOperation(text="X"))
    restored = result.undo()
    assert restored.working_path.read_bytes() == original_bytes


def test_watermark_handles_mixed_page_sizes(tmp_path: Path) -> None:
    doc = _session(tmp_path, [(200, 200), (1000, 1500)])
    result = doc.apply(WatermarkOperation(text="X"))
    with pikepdf.Pdf.open(result.working_path) as pdf:
        assert len(pdf.pages) == 2
