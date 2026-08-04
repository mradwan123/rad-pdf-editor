"""Unit tests for core/ops/forms.py (Flatten, Remove Annotations, Fill
Form, Sign)."""

from __future__ import annotations

from pathlib import Path

import fitz
import pdfplumber
import pikepdf
import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.forms import (
    CreateFormFieldOperation,
    FillFormOperation,
    FlattenOperation,
    RemoveAnnotationsOperation,
    SignOperation,
    list_form_field_names,
)


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


# --- Fill Form -------------------------------------------------------


def _make_pdf_with_text_field(path: Path, field_name: str = "name") -> Path:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(300, 400))
    field = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/FT": pikepdf.Name("/Tx"),
                "/T": pikepdf.String(field_name),
                "/Rect": pikepdf.Array([50, 300, 250, 320]),
                "/Subtype": pikepdf.Name("/Widget"),
                "/Type": pikepdf.Name("/Annot"),
                "/V": pikepdf.String(""),
                "/DA": pikepdf.String("/Helv 12 Tf 0 g"),
            }
        )
    )
    page.obj["/Annots"] = pikepdf.Array([field])
    pdf.Root["/AcroForm"] = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Fields": pikepdf.Array([field]),
                "/NeedAppearances": True,
                "/DR": pikepdf.Dictionary(
                    {
                        "/Font": pikepdf.Dictionary(
                            {
                                "/Helv": pdf.make_indirect(
                                    pikepdf.Dictionary(
                                        {
                                            "/Type": pikepdf.Name("/Font"),
                                            "/Subtype": pikepdf.Name("/Type1"),
                                            "/BaseFont": pikepdf.Name("/Helvetica"),
                                        }
                                    )
                                )
                            }
                        )
                    }
                ),
            }
        )
    )
    pdf.save(path)
    return path


def _form_session(tmp_path: Path, field_name: str = "name") -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_pdf_with_text_field(session_dir / "working.pdf", field_name)
    return DocumentSession(working_path=working, source_path=None)


def _extracted_text(path: Path) -> str:
    with fitz.open(path) as pdf:
        return pdf[0].get_text()


def test_list_form_field_names_finds_the_field(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_pdf_with_text_field(session_dir / "working.pdf", "name")
    assert list_form_field_names(working) == ["name"]


def test_list_form_field_names_empty_for_document_without_a_form(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    working = session_dir / "working.pdf"
    pdf.save(working)
    assert list_form_field_names(working) == []


def test_fill_form_sets_value_and_generates_visible_appearance(tmp_path: Path) -> None:
    doc = _form_session(tmp_path)
    result = doc.apply(FillFormOperation(field_values={"name": "Jane Smith"}))
    assert "Jane Smith" in _extracted_text(result.working_path)


def test_fill_form_rejects_unknown_field(tmp_path: Path) -> None:
    doc = _form_session(tmp_path)
    with pytest.raises(OperationError):
        doc.apply(FillFormOperation(field_values={"bogus": "x"}))


def test_fill_form_rejects_empty_field_values() -> None:
    with pytest.raises(OperationError):
        FillFormOperation(field_values={})


def test_fill_form_on_document_without_a_form_raises(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    working = session_dir / "working.pdf"
    pdf.save(working)
    doc = DocumentSession(working_path=working, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(FillFormOperation(field_values={"name": "x"}))


def test_fill_form_undo_restores_empty_value(tmp_path: Path) -> None:
    doc = _form_session(tmp_path)
    result = doc.apply(FillFormOperation(field_values={"name": "Jane Smith"}))
    restored = result.undo()
    assert "Jane Smith" not in _extracted_text(restored.working_path)


# --- Sign -------------------------------------------------------


def _make_signature_image(path: Path) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    ImageDraw.Draw(img).line((10, 60, 190, 20), fill=(0, 0, 200, 255), width=6)
    img.save(path)
    return path


def _sign_session(tmp_path: Path) -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    pdf.add_blank_page(page_size=(300, 400))
    working = session_dir / "working.pdf"
    pdf.save(working)
    return DocumentSession(working_path=working, source_path=None)


def _has_ink(path: Path, page: int = 0) -> bool:
    with fitz.open(path) as pdf:
        pix = pdf[page].get_pixmap()
        samples = pix.samples
        return any(
            not (samples[i] == samples[i + 1] == samples[i + 2])
            for i in range(0, len(samples), pix.n)
        )


def test_sign_places_image_on_the_correct_page(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    sig = _make_signature_image(tmp_path / "sig.png")
    result = doc.apply(SignOperation(image_path=sig, page=2, rect=(50, 50, 250, 130)))
    assert not _has_ink(result.working_path, page=0)
    assert _has_ink(result.working_path, page=1)


def test_sign_preserves_page_count(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    sig = _make_signature_image(tmp_path / "sig.png")
    result = doc.apply(SignOperation(image_path=sig, page=1, rect=(50, 50, 250, 130)))
    with pikepdf.Pdf.open(result.working_path) as pdf:
        assert len(pdf.pages) == 2


def test_sign_rejects_missing_image(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    with pytest.raises(OperationError):
        doc.apply(SignOperation(image_path=tmp_path / "missing.png", page=1, rect=(0, 0, 100, 50)))


def test_sign_rejects_out_of_range_page(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    sig = _make_signature_image(tmp_path / "sig.png")
    with pytest.raises(OperationError):
        doc.apply(SignOperation(image_path=sig, page=99, rect=(0, 0, 100, 50)))


def test_sign_rejects_degenerate_rect(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    sig = _make_signature_image(tmp_path / "sig.png")
    with pytest.raises(OperationError):
        doc.apply(SignOperation(image_path=sig, page=1, rect=(100, 100, 100, 100)))


def test_sign_with_no_document_open_raises(tmp_path: Path) -> None:
    sig = _make_signature_image(tmp_path / "sig.png")
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(SignOperation(image_path=sig, page=1, rect=(0, 0, 100, 50)))


def test_sign_undo_removes_the_image(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    sig = _make_signature_image(tmp_path / "sig.png")
    result = doc.apply(SignOperation(image_path=sig, page=1, rect=(50, 50, 250, 130)))
    restored = result.undo()
    assert not _has_ink(restored.working_path, page=0)


def _widgets(path: Path, page: int = 0) -> list[fitz.Widget]:
    with fitz.open(path) as pdf:
        return list(pdf[page].widgets())


def test_create_text_field_adds_a_fillable_field(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    result = doc.apply(
        CreateFormFieldOperation(
            page=1,
            field_name="full_name",
            field_type="text",
            rect=(50, 300, 250, 320),
            default_value="Jane Doe",
        )
    )
    widgets = _widgets(result.working_path, page=0)
    assert len(widgets) == 1
    assert widgets[0].field_name == "full_name"
    assert widgets[0].field_type_string == "Text"
    assert widgets[0].field_value == "Jane Doe"


def test_create_checkbox_field_sets_initial_checked_state(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    result = doc.apply(
        CreateFormFieldOperation(
            page=1,
            field_name="agree",
            field_type="checkbox",
            rect=(50, 260, 70, 280),
            checked=True,
        )
    )
    widgets = _widgets(result.working_path, page=0)
    assert widgets[0].field_type_string == "CheckBox"
    assert widgets[0].field_value not in (False, "Off", None)


def test_create_checkbox_field_defaults_to_unchecked(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    result = doc.apply(
        CreateFormFieldOperation(
            page=1, field_name="agree", field_type="checkbox", rect=(50, 260, 70, 280)
        )
    )
    widgets = _widgets(result.working_path, page=0)
    assert widgets[0].field_value in (False, "Off", None)


def test_create_radio_field_adds_a_toggle_widget(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    result = doc.apply(
        CreateFormFieldOperation(
            page=1, field_name="choice", field_type="radio", rect=(50, 220, 70, 240)
        )
    )
    widgets = _widgets(result.working_path, page=0)
    assert widgets[0].field_type_string == "RadioButton"


def test_create_form_field_places_on_the_correct_page(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    result = doc.apply(
        CreateFormFieldOperation(
            page=2, field_name="on_page_2", field_type="text", rect=(50, 300, 250, 320)
        )
    )
    assert _widgets(result.working_path, page=0) == []
    assert len(_widgets(result.working_path, page=1)) == 1


def test_create_form_field_rejects_invalid_field_type(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    with pytest.raises(OperationError):
        doc.apply(
            CreateFormFieldOperation(
                page=1, field_name="x", field_type="dropdown", rect=(0, 0, 100, 20)
            )
        )


def test_create_form_field_rejects_empty_field_name(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    with pytest.raises(OperationError):
        doc.apply(
            CreateFormFieldOperation(page=1, field_name="  ", field_type="text", rect=(0, 0, 100, 20))
        )


def test_create_form_field_rejects_degenerate_rect(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    with pytest.raises(OperationError):
        doc.apply(
            CreateFormFieldOperation(
                page=1, field_name="x", field_type="text", rect=(100, 100, 100, 100)
            )
        )


def test_create_form_field_rejects_out_of_range_page(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    with pytest.raises(OperationError):
        doc.apply(
            CreateFormFieldOperation(
                page=99, field_name="x", field_type="text", rect=(0, 0, 100, 20)
            )
        )


def test_create_form_field_with_no_document_open_raises() -> None:
    doc = DocumentSession(working_path=None, source_path=None)
    with pytest.raises(OperationError):
        doc.apply(
            CreateFormFieldOperation(
                page=1, field_name="x", field_type="text", rect=(0, 0, 100, 20)
            )
        )


def test_create_form_field_undo_removes_the_field(tmp_path: Path) -> None:
    doc = _sign_session(tmp_path)
    result = doc.apply(
        CreateFormFieldOperation(
            page=1, field_name="full_name", field_type="text", rect=(50, 300, 250, 320)
        )
    )
    restored = result.undo()
    assert _widgets(restored.working_path, page=0) == []
