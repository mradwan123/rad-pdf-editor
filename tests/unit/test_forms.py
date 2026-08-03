"""Unit tests for core/ops/forms.py (Flatten, Remove Annotations)."""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pikepdf

from core.model.document import DocumentSession
from core.ops.forms import FlattenOperation, RemoveAnnotationsOperation


def _make_pdf_with_annotation(
    path: Path, subtype: str = "Square", with_appearance: bool = True
) -> Path:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(300, 400))

    annot_dict: dict[str, object] = {
        "/Type": pikepdf.Name("/Annot"),
        "/Subtype": pikepdf.Name(f"/{subtype}"),
        "/Rect": pikepdf.Array([100, 100, 150, 150]),
    }
    if with_appearance:
        ap_stream = pikepdf.Stream(pdf, b"1 0 0 rg 0 0 50 50 re f")
        ap_stream.Type = pikepdf.Name("/XObject")
        ap_stream.Subtype = pikepdf.Name("/Form")
        ap_stream.BBox = pikepdf.Array([0, 0, 50, 50])
        annot_dict["/AP"] = pikepdf.Dictionary({"/N": ap_stream})

    annot = pdf.make_indirect(pikepdf.Dictionary(annot_dict))
    page.obj["/Annots"] = pikepdf.Array([annot])
    pdf.save(path)
    return path


def _session(tmp_path: Path, **kwargs: object) -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_pdf_with_annotation(session_dir / "working.pdf", **kwargs)
    return DocumentSession(working_path=working, source_path=None)


def _has_annots(path: Path) -> bool:
    with pikepdf.Pdf.open(path) as pdf:
        return "/Annots" in pdf.pages[0].obj


def _page_rects(path: Path) -> list[dict]:
    with pdfplumber.open(path) as pdf:
        return pdf.pages[0].rects


# --- Flatten -------------------------------------------------------------


def test_flatten_composites_appearance_onto_page_content(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(FlattenOperation())
    rects = _page_rects(result.working_path)
    assert len(rects) == 1
    assert rects[0]["x0"] == 100.0
    assert rects[0]["x1"] == 150.0


def test_flatten_removes_the_flattened_annotation(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(FlattenOperation())
    assert not _has_annots(result.working_path)


def test_flatten_leaves_annotations_without_appearance_untouched(tmp_path: Path) -> None:
    doc = _session(tmp_path, with_appearance=False)
    result = doc.apply(FlattenOperation())
    assert _has_annots(result.working_path)
    assert _page_rects(result.working_path) == []


def test_flatten_with_no_annotations_is_a_noop(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    working = session_dir / "working.pdf"
    pdf.save(working)
    doc = DocumentSession(working_path=working, source_path=None)

    result = doc.apply(FlattenOperation())
    with pikepdf.Pdf.open(result.working_path) as p:
        assert len(p.pages) == 1


def test_flatten_undo_restores_the_annotation(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(FlattenOperation())
    restored = result.undo()
    assert _has_annots(restored.working_path)


# --- Remove Annotations -------------------------------------------------------


def test_remove_annotations_with_no_subtypes_removes_everything(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(RemoveAnnotationsOperation())
    assert not _has_annots(result.working_path)


def test_remove_annotations_filters_by_subtype(tmp_path: Path) -> None:
    doc = _session(tmp_path, subtype="Highlight")
    result = doc.apply(RemoveAnnotationsOperation(subtypes=["Square"]))
    # only Square was targeted; the Highlight annotation should remain
    assert _has_annots(result.working_path)


def test_remove_annotations_matches_requested_subtype(tmp_path: Path) -> None:
    doc = _session(tmp_path, subtype="Highlight")
    result = doc.apply(RemoveAnnotationsOperation(subtypes=["Highlight"]))
    assert not _has_annots(result.working_path)


def test_remove_annotations_with_no_annotations_is_a_noop(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    working = session_dir / "working.pdf"
    pdf.save(working)
    doc = DocumentSession(working_path=working, source_path=None)

    result = doc.apply(RemoveAnnotationsOperation())
    with pikepdf.Pdf.open(result.working_path) as p:
        assert len(p.pages) == 1


def test_remove_annotations_undo_restores_the_annotation(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(RemoveAnnotationsOperation())
    restored = result.undo()
    assert _has_annots(restored.working_path)
