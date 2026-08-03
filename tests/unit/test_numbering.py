"""Unit tests for core/ops/numbering.py (Header/Footer, Bates numbering).

Uses pdfplumber to verify the actual rendered text, not just that the
operation didn't crash - a broken text-placement bug could otherwise
pass every test while producing blank or garbled stamps.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pikepdf
import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.numbering import BatesNumberingOperation, HeaderFooterOperation


def _make_pdf(path: Path, num_pages: int) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(300, 400))
    pdf.save(path)
    return path


def _session(tmp_path: Path, num_pages: int = 1) -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_pdf(session_dir / "working.pdf", num_pages)
    return DocumentSession(working_path=working, source_path=None)


def _page_text(path: Path, page: int = 0) -> str:
    with pdfplumber.open(path) as pdf:
        return pdf.pages[page].extract_text() or ""


# --- Header/Footer -------------------------------------------------------


def test_header_and_footer_text_appears(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(HeaderFooterOperation(header_text="CONFIDENTIAL", footer_text="Acme Inc"))
    text = _page_text(result.working_path)
    assert "CONFIDENTIAL" in text
    assert "Acme Inc" in text


def test_header_only(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(HeaderFooterOperation(header_text="DRAFT"))
    assert "DRAFT" in _page_text(result.working_path)


def test_rejects_when_both_texts_empty() -> None:
    with pytest.raises(OperationError):
        HeaderFooterOperation(header_text="", footer_text="")


def test_header_footer_with_no_document_open_raises() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(HeaderFooterOperation(header_text="X"))


def test_header_footer_undo_restores_prior_state(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(HeaderFooterOperation(header_text="DRAFT"))
    restored = result.undo()
    assert "DRAFT" not in _page_text(restored.working_path)


def test_header_footer_only_affects_selected_pages(tmp_path: Path) -> None:
    doc = _session(tmp_path, num_pages=2)
    result = doc.apply(HeaderFooterOperation(header_text="DRAFT", pages=[1]))
    assert "DRAFT" in _page_text(result.working_path, page=0)
    assert "DRAFT" not in _page_text(result.working_path, page=1)


# --- Bates numbering -------------------------------------------------------


def test_bates_numbers_increment_across_pages(tmp_path: Path) -> None:
    doc = _session(tmp_path, num_pages=3)
    result = doc.apply(BatesNumberingOperation(prefix="DOC-", start=10, digits=3))
    with pdfplumber.open(result.working_path) as pdf:
        texts = [page.extract_text() for page in pdf.pages]
    assert texts == ["DOC-010", "DOC-011", "DOC-012"]


def test_bates_zero_pads_to_requested_digits(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(BatesNumberingOperation(start=1, digits=5))
    assert _page_text(result.working_path) == "00001"


def test_bates_rejects_invalid_position() -> None:
    with pytest.raises(OperationError):
        BatesNumberingOperation(position="middle")


def test_bates_rejects_negative_start() -> None:
    with pytest.raises(OperationError):
        BatesNumberingOperation(start=-1)


def test_bates_rejects_zero_digits() -> None:
    with pytest.raises(OperationError):
        BatesNumberingOperation(digits=0)


def test_bates_with_no_document_open_raises() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(BatesNumberingOperation())


def test_bates_undo_restores_prior_state(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(BatesNumberingOperation(start=1, digits=3))
    restored = result.undo()
    assert _page_text(restored.working_path) == ""


def test_bates_only_numbers_selected_pages_in_order(tmp_path: Path) -> None:
    doc = _session(tmp_path, num_pages=3)
    result = doc.apply(BatesNumberingOperation(start=1, digits=2, pages=[3, 1]))
    with pdfplumber.open(result.working_path) as pdf:
        texts = [page.extract_text() for page in pdf.pages]
    # pages [3, 1] resolved/sorted ascending -> page 1 gets 01, page 3 gets 02
    assert texts[0] == "01"
    assert texts[1] == ""
    assert texts[2] == "02"
