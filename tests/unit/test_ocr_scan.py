"""Unit tests for core/ops/ocr_scan.py (OCR, Deskew).

OCR tests are `pytest.mark.skipif`-guarded on `tesseract_available()` -
unlike Phase 3's LibreOffice, there is no pure-Python fallback for real
OCR, so a machine without `tesseract` installed simply can't exercise
these. Deskew needs no system binary (the `deskew` package is pure
Python), so its tests always run.

Fixtures render real 300 DPI, realistic-page-size scans - a lower-DPI
fixture earlier in this project's development looked like a genuine
OCR failure and turned out to be a bad fixture, not a real problem.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import fitz
import numpy as np
import pytest
from deskew import determine_skew
from PIL import Image

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.ocr_scan import DeskewOperation, OCROperation, tesseract_available

_HAS_TESSERACT = tesseract_available()

_LOREM = [
    "This is a denser paragraph of realistic scanned text, meant to",
    "simulate an actual printed page rather than a sparse fixture,",
    "so OCR and skew detection both have enough content to work with.",
    "Repeating similar lines helps establish a reliable baseline.",
]


def _make_scan_pdf(path: Path, angle: float = 0.0, dpi: int = 300) -> Path:
    """A realistic image-only "scan" - real text rendered then
    rasterized at `dpi`, optionally rotated by `angle` degrees to
    simulate a skewed scan."""
    src = fitz.open()
    page = src.new_page(width=612, height=792)
    for i, line in enumerate(_LOREM):
        page.insert_text((72, 100 + i * 20), line, fontsize=14)
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
    src.close()

    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if angle:
        img = img.rotate(angle, expand=True, fillcolor="white")

    page_w_pt = img.width / dpi * 72
    page_h_pt = img.height / dpi * 72
    doc = fitz.open()
    p = doc.new_page(width=page_w_pt, height=page_h_pt)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    p.insert_image(p.rect, stream=buf.getvalue())
    doc.save(path)
    doc.close()
    return path


def _session_with(tmp_path: Path, pdf_path: Path) -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir(exist_ok=True)
    working = session_dir / "working.pdf"
    shutil.copyfile(pdf_path, working)
    return DocumentSession(working_path=working, source_path=None)


# --- OCROperation -----------------------------------------------------


def test_ocr_rejects_force_and_skip_together() -> None:
    with pytest.raises(OperationError):
        OCROperation(force_ocr=True, skip_text=True)


def test_ocr_with_no_document_open_raises() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(OCROperation())


def test_ocr_raises_clearly_when_tesseract_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.ops.ocr_scan.tesseract_available", lambda: False)
    scan = _make_scan_pdf(tmp_path / "scan.pdf")
    session = _session_with(tmp_path, scan)
    # DocumentSession.apply() wraps every exception from
    # Operation.apply() into OperationError - the underlying
    # ConversionError is in the message, not the raised type.
    with pytest.raises(OperationError, match="not installed"):
        session.apply(OCROperation())


@pytest.mark.skipif(not _HAS_TESSERACT, reason="tesseract not installed on this machine")
def test_ocr_adds_real_extractable_text(tmp_path: Path) -> None:
    scan = _make_scan_pdf(tmp_path / "scan.pdf")
    session = _session_with(tmp_path, scan)

    with fitz.open(scan) as before:
        assert before[0].get_text().strip() == ""

    result = session.apply(OCROperation())
    with fitz.open(result.working_path) as after:
        text = after[0].get_text()
        assert "denser paragraph" in text
        assert "reliable baseline" in text


@pytest.mark.skipif(not _HAS_TESSERACT, reason="tesseract not installed on this machine")
def test_ocr_skip_text_leaves_existing_text_alone(tmp_path: Path) -> None:
    # a normal (non-image) page with real vector text already present
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    doc = fitz.open()
    doc.new_page(width=300, height=400)
    doc[0].insert_text((50, 50), "Already digital text")
    working = session_dir / "working.pdf"
    doc.save(working)
    doc.close()

    session = DocumentSession(working_path=working, source_path=None)
    result = session.apply(OCROperation(skip_text=True))
    with fitz.open(result.working_path) as after:
        assert "Already digital text" in after[0].get_text()


@pytest.mark.skipif(not _HAS_TESSERACT, reason="tesseract not installed on this machine")
def test_ocr_undo_restores_original(tmp_path: Path) -> None:
    scan = _make_scan_pdf(tmp_path / "scan.pdf")
    session = _session_with(tmp_path, scan)
    result = session.apply(OCROperation())
    restored = result.undo()
    with fitz.open(restored.working_path) as doc:
        assert doc[0].get_text().strip() == ""


# --- DeskewOperation ----------------------------------------------------


def test_deskew_rejects_too_low_dpi() -> None:
    with pytest.raises(OperationError):
        DeskewOperation(dpi=10)


def test_deskew_with_no_document_open_raises() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(DeskewOperation())


def test_deskew_corrects_a_real_skewed_page(tmp_path: Path) -> None:
    scan = _make_scan_pdf(tmp_path / "skewed.pdf", angle=8.0)
    session = _session_with(tmp_path, scan)

    result = session.apply(DeskewOperation())
    assert result.operation_log[-1].describe() == "Deskewed 1 of 1 page(s)"

    # the corrected page should be genuinely closer to level: verify
    # via re-running skew detection on the corrected output, not just
    # trusting describe()'s claim
    with fitz.open(result.working_path) as doc:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), colorspace=fitz.csGRAY)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        residual_angle = determine_skew(arr)
        assert residual_angle is None or abs(float(residual_angle)) < 1.0


def test_deskew_leaves_already_level_pages_untouched(tmp_path: Path) -> None:
    scan = _make_scan_pdf(tmp_path / "level.pdf", angle=0.0)
    session = _session_with(tmp_path, scan)

    result = session.apply(DeskewOperation())
    assert result.operation_log[-1].describe() == "Deskewed 0 of 1 page(s)"


def test_deskew_only_affects_selected_pages(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    skewed_a = _make_scan_pdf(tmp_path / "a.pdf", angle=8.0)
    skewed_b = _make_scan_pdf(tmp_path / "b.pdf", angle=8.0)

    merged = fitz.open()
    with fitz.open(skewed_a) as a, fitz.open(skewed_b) as b:
        merged.insert_pdf(a)
        merged.insert_pdf(b)
    working = session_dir / "working.pdf"
    merged.save(working)
    merged.close()

    session = DocumentSession(working_path=working, source_path=None)
    result = session.apply(DeskewOperation(pages=[1]))
    assert result.operation_log[-1].describe() == "Deskewed 1 of 1 page(s)"


def test_deskew_undo_restores_original(tmp_path: Path) -> None:
    scan = _make_scan_pdf(tmp_path / "skewed.pdf", angle=8.0)
    session = _session_with(tmp_path, scan)
    result = session.apply(DeskewOperation())
    restored = result.undo()
    assert restored.working_path is not None and restored.working_path.exists()
