"""Unit tests for core/ops/layout.py (Crop, Resize, N-up, Grayscale)."""

from __future__ import annotations

from pathlib import Path

import fitz
import pikepdf
import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.layout import CropOperation, GrayscaleOperation, NUpOperation, ResizeOperation


def _make_pdf(path: Path, num_pages: int, page_size: tuple[int, int] = (300, 400)) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=page_size)
    pdf.save(path)
    return path


def _session(tmp_path: Path, num_pages: int = 1, page_size: tuple[int, int] = (300, 400)) -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_pdf(session_dir / "working.pdf", num_pages, page_size)
    return DocumentSession(working_path=working, source_path=None)


def _mediabox(path: Path, page: int = 0) -> list[float]:
    with pikepdf.Pdf.open(path) as pdf:
        return [float(x) for x in pdf.pages[page].mediabox]


def _page_count(path: Path) -> int:
    with pikepdf.Pdf.open(path) as pdf:
        return len(pdf.pages)


# --- Crop -------------------------------------------------------------


def test_crop_trims_mediabox_by_margins(tmp_path: Path) -> None:
    doc = _session(tmp_path, page_size=(300, 400))
    result = doc.apply(
        CropOperation(margin_top=20, margin_right=10, margin_bottom=20, margin_left=10)
    )
    assert _mediabox(result.working_path) == [10.0, 20.0, 290.0, 380.0]


def test_crop_rejects_negative_margins(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    with pytest.raises(OperationError):
        doc.apply(CropOperation(margin_left=-5))


def test_crop_rejects_margins_that_eliminate_the_page(tmp_path: Path) -> None:
    doc = _session(tmp_path, page_size=(300, 400))
    with pytest.raises(OperationError):
        doc.apply(CropOperation(margin_left=200, margin_right=200))


def test_crop_with_no_document_open_raises() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(CropOperation(margin_top=10))


def test_crop_undo_restores_original_mediabox(tmp_path: Path) -> None:
    doc = _session(tmp_path, page_size=(300, 400))
    result = doc.apply(CropOperation(margin_top=20))
    restored = result.undo()
    assert _mediabox(restored.working_path) == [0.0, 0.0, 300.0, 400.0]


def test_crop_duplicate_page_numbers_apply_once_not_twice(tmp_path: Path) -> None:
    # Regression: pages=[1, 1] previously cropped page 1 twice
    # (40pt off the top instead of 20) because nothing deduplicated
    # the target list.
    doc = _session(tmp_path, page_size=(300, 400))
    result = doc.apply(CropOperation(margin_top=20, pages=[1, 1]))
    assert _mediabox(result.working_path) == [0.0, 0.0, 300.0, 380.0]


# --- Resize -------------------------------------------------------------


def test_resize_sets_new_mediabox(tmp_path: Path) -> None:
    doc = _session(tmp_path, page_size=(300, 400))
    result = doc.apply(ResizeOperation(width=600, height=800))
    assert _mediabox(result.working_path) == [0.0, 0.0, 600.0, 800.0]
    assert _page_count(result.working_path) == 1


def test_resize_rejects_non_positive_dimensions(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    with pytest.raises(OperationError):
        doc.apply(ResizeOperation(width=0, height=100))


def test_resize_only_affects_selected_pages(tmp_path: Path) -> None:
    doc = _session(tmp_path, num_pages=3, page_size=(300, 400))
    result = doc.apply(ResizeOperation(width=100, height=100, pages=[2]))
    with pikepdf.Pdf.open(result.working_path) as pdf:
        assert [float(x) for x in pdf.pages[0].mediabox] == [0.0, 0.0, 300.0, 400.0]
        assert [float(x) for x in pdf.pages[1].mediabox] == [0.0, 0.0, 100.0, 100.0]
        assert [float(x) for x in pdf.pages[2].mediabox] == [0.0, 0.0, 300.0, 400.0]


def test_resize_undo_restores_original_size(tmp_path: Path) -> None:
    doc = _session(tmp_path, page_size=(300, 400))
    result = doc.apply(ResizeOperation(width=600, height=800))
    restored = result.undo()
    assert _mediabox(restored.working_path) == [0.0, 0.0, 300.0, 400.0]


# --- N-up -------------------------------------------------------------


def test_n_up_combines_pages_into_fewer_sheets(tmp_path: Path) -> None:
    doc = _session(tmp_path, num_pages=4)
    result = doc.apply(NUpOperation(pages_per_sheet=2, sheet_width=612, sheet_height=792))
    assert _page_count(result.working_path) == 2
    assert _mediabox(result.working_path) == [0.0, 0.0, 612.0, 792.0]


def test_n_up_handles_uneven_final_sheet(tmp_path: Path) -> None:
    doc = _session(tmp_path, num_pages=5)
    result = doc.apply(NUpOperation(pages_per_sheet=4, sheet_width=612, sheet_height=792))
    # 5 pages at 4-per-sheet -> 2 sheets (4 + 1)
    assert _page_count(result.working_path) == 2


def test_n_up_rejects_non_positive_pages_per_sheet() -> None:
    with pytest.raises(OperationError):
        NUpOperation(pages_per_sheet=0, sheet_width=612, sheet_height=792)


def test_n_up_rejects_non_positive_sheet_size(tmp_path: Path) -> None:
    doc = _session(tmp_path, num_pages=2)
    with pytest.raises(OperationError):
        doc.apply(NUpOperation(pages_per_sheet=2, sheet_width=0, sheet_height=792))


def test_n_up_undo_restores_original_pages(tmp_path: Path) -> None:
    doc = _session(tmp_path, num_pages=4)
    result = doc.apply(NUpOperation(pages_per_sheet=2, sheet_width=612, sheet_height=792))
    restored = result.undo()
    assert _page_count(restored.working_path) == 4


# --- Grayscale -------------------------------------------------------


def _make_colored_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.draw_rect(fitz.Rect(50, 50, 150, 150), color=(1, 0, 0), fill=(1, 0, 0))
    doc.save(path)
    doc.close()
    return path


def _has_only_gray_pixels(path: Path) -> bool:
    with fitz.open(path) as doc:
        pix = doc[0].get_pixmap()
        # a genuinely grayscale-rendered page has r==g==b at every pixel
        samples = pix.samples
        for i in range(0, len(samples), pix.n):
            r, g, b = samples[i], samples[i + 1], samples[i + 2]
            if not (r == g == b):
                return False
    return True


def test_grayscale_removes_color(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_colored_pdf(session_dir / "working.pdf")
    doc = DocumentSession(working_path=working, source_path=None)

    assert not _has_only_gray_pixels(working)
    result = doc.apply(GrayscaleOperation(dpi=100))
    assert _has_only_gray_pixels(result.working_path)


def test_grayscale_preserves_page_count(tmp_path: Path) -> None:
    doc = _session(tmp_path, num_pages=3)
    result = doc.apply(GrayscaleOperation(dpi=100))
    assert _page_count(result.working_path) == 3


def test_grayscale_only_affects_selected_pages(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    src = fitz.open()
    for _ in range(2):
        page = src.new_page(width=300, height=400)
        page.draw_rect(fitz.Rect(50, 50, 150, 150), color=(1, 0, 0), fill=(1, 0, 0))
    working = session_dir / "working.pdf"
    src.save(working)
    src.close()
    doc = DocumentSession(working_path=working, source_path=None)

    result = doc.apply(GrayscaleOperation(pages=[1], dpi=100))
    with fitz.open(result.working_path) as out:
        pix0 = out[0].get_pixmap()
        pix1 = out[1].get_pixmap()
        samples0 = pix0.samples
        samples1 = pix1.samples
        page0_all_gray = all(
            samples0[i] == samples0[i + 1] == samples0[i + 2]
            for i in range(0, len(samples0), pix0.n)
        )
        page1_has_color = any(
            not (samples1[i] == samples1[i + 1] == samples1[i + 2])
            for i in range(0, len(samples1), pix1.n)
        )
        assert page0_all_gray
        assert page1_has_color


def test_grayscale_rejects_too_low_dpi() -> None:
    with pytest.raises(OperationError):
        GrayscaleOperation(dpi=10)


def test_grayscale_with_no_document_open_raises() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(GrayscaleOperation())


def test_grayscale_undo_restores_color(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_colored_pdf(session_dir / "working.pdf")
    doc = DocumentSession(working_path=working, source_path=None)

    result = doc.apply(GrayscaleOperation(dpi=100))
    restored = result.undo()
    assert not _has_only_gray_pixels(restored.working_path)
