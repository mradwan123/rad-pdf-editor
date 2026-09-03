"""Phase 6e: annotation operations.

The first operations in this codebase that edit content *on* a page.
Checked against real PyMuPDF output - the annotation is found, its type
and geometry are right, and undo genuinely removes it - rather than
"apply() didn't raise".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.annotate import (
    AddAnnotationOperation,
    DeleteAnnotationOperation,
    EditAnnotationOperation,
    find_annotation,
)

PAGE_W, PAGE_H = 400.0, 600.0


@pytest.fixture
def session(tmp_path: Path) -> Iterator[DocumentSession]:
    path = tmp_path / "working.pdf"
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W, height=PAGE_H)
    doc.new_page(width=PAGE_W, height=PAGE_H)
    page = doc[0]
    page.insert_text((50, 100), "annotate this line", fontsize=18)
    doc.save(str(path))
    doc.close()
    yield DocumentSession(working_path=path, source_path=path)


def _annots(session: DocumentSession, page: int = 1) -> list[dict[str, object]]:
    with pymupdf.open(session.working_path) as doc:
        return [
            {
                "id": a.info.get("id"),
                "type": a.type[1],
                "rect": a.rect,
                "colors": a.colors,
                "opacity": a.opacity,
                "content": a.info.get("content", ""),
            }
            for a in doc[page - 1].annots()
        ]


# --- adding ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected_type"),
    [
        ("highlight", "Highlight"),
        ("underline", "Underline"),
        ("strikeout", "StrikeOut"),
        ("squiggly", "Squiggly"),
        ("rect", "Square"),
        ("circle", "Circle"),
        ("line", "Line"),
    ],
)
def test_each_kind_creates_the_right_annotation(
    session: DocumentSession, kind: str, expected_type: str
) -> None:
    operation = AddAnnotationOperation(page=1, kind=kind, rect=(50.0, 480.0, 220.0, 510.0))
    result = operation.apply(session)

    annots = _annots(result)
    assert len(annots) == 1
    assert annots[0]["type"] == expected_type
    assert annots[0]["id"] == operation.annot_id


def test_the_rect_is_interpreted_bottom_left_origin(session: DocumentSession) -> None:
    """This package's convention throughout - a rect near the *top* of
    a 600pt page has a high y, and must land near the top in fitz's
    top-left frame (a low y), not near the bottom."""
    operation = AddAnnotationOperation(page=1, kind="rect", rect=(50.0, 480.0, 220.0, 510.0))
    result = operation.apply(session)

    rect = _annots(result)[0]["rect"]
    assert isinstance(rect, pymupdf.Rect)
    # y0 = 600 - 510 = 90, y1 = 600 - 480 = 120, allowing for the
    # annotation border PyMuPDF adds.
    assert 85 <= rect.y0 <= 95
    assert 115 <= rect.y1 <= 125


def test_ink_takes_strokes_not_a_rect(session: DocumentSession) -> None:
    operation = AddAnnotationOperation(
        page=1,
        kind="ink",
        strokes=[[(60.0, 300.0), (100.0, 330.0), (140.0, 300.0)]],
        color=(1.0, 0.0, 0.0),
    )
    result = operation.apply(session)

    annots = _annots(result)
    assert annots[0]["type"] == "Ink"
    assert annots[0]["colors"]["stroke"] == pytest.approx([1.0, 0.0, 0.0])  # type: ignore[index]


def test_a_note_carries_its_text(session: DocumentSession) -> None:
    operation = AddAnnotationOperation(
        page=1, kind="note", rect=(300.0, 400.0, 320.0, 420.0), text="check this"
    )
    result = operation.apply(session)

    annots = _annots(result)
    assert annots[0]["type"] == "Text"
    assert annots[0]["content"] == "check this"


def test_opacity_is_applied(session: DocumentSession) -> None:
    operation = AddAnnotationOperation(
        page=1, kind="rect", rect=(50.0, 100.0, 200.0, 200.0), opacity=0.4
    )
    result = operation.apply(session)
    assert _annots(result)[0]["opacity"] == pytest.approx(0.4, abs=0.01)


def test_annotations_land_on_the_page_asked_for(session: DocumentSession) -> None:
    operation = AddAnnotationOperation(page=2, kind="rect", rect=(50.0, 100.0, 200.0, 200.0))
    result = operation.apply(session)

    assert _annots(result, page=1) == []
    assert len(_annots(result, page=2)) == 1


def test_the_id_survives_a_pikepdf_round_trip(session: DocumentSession, tmp_path: Path) -> None:
    """Why identity is /NM and not xref: every operation writes a new
    working file, and a pikepdf round-trip renumbers xrefs while /NM
    survives. Without this, an annotation could not be edited after any
    subsequent edit to the document."""
    operation = AddAnnotationOperation(page=1, kind="rect", rect=(50.0, 100.0, 200.0, 200.0))
    result = operation.apply(session)

    with pymupdf.open(result.working_path) as doc:
        xref_before = next(iter(doc[0].annots())).xref

    round_tripped = tmp_path / "round.pdf"
    with pikepdf.Pdf.open(result.working_path) as pdf:
        pdf.save(round_tripped)

    with pymupdf.open(round_tripped) as doc:
        annot = next(iter(doc[0].annots()))
        assert annot.info.get("id") == operation.annot_id
        assert annot.xref != xref_before, "xrefs are expected to churn - that is the point"


# --- validation ------------------------------------------------------------


def test_an_unknown_kind_is_rejected() -> None:
    with pytest.raises(OperationError, match="Unknown annotation kind"):
        AddAnnotationOperation(page=1, kind="sparkle", rect=(0.0, 0.0, 10.0, 10.0))


def test_a_rect_is_required_for_non_ink_kinds() -> None:
    with pytest.raises(OperationError, match="needs a rect"):
        AddAnnotationOperation(page=1, kind="highlight")


def test_ink_requires_a_usable_stroke() -> None:
    with pytest.raises(OperationError, match="stroke"):
        AddAnnotationOperation(page=1, kind="ink", strokes=[[(1.0, 1.0)]])


def test_a_degenerate_rect_is_rejected() -> None:
    with pytest.raises(OperationError, match="positive width and height"):
        AddAnnotationOperation(page=1, kind="rect", rect=(100.0, 100.0, 50.0, 200.0))


def test_a_page_beyond_the_document_is_rejected(session: DocumentSession) -> None:
    operation = AddAnnotationOperation(page=99, kind="rect", rect=(0.0, 0.0, 10.0, 10.0))
    with pytest.raises(OperationError, match="out of range"):
        operation.apply(session)


# --- undo ------------------------------------------------------------------


def test_undoing_an_add_removes_exactly_that_annotation(session: DocumentSession) -> None:
    """A precise inverse, not a snapshot restore - the annotation is
    addressable by the id the add assigned."""
    first = AddAnnotationOperation(page=1, kind="rect", rect=(50.0, 100.0, 200.0, 200.0))
    after_first = first.apply(session)
    second = AddAnnotationOperation(page=1, kind="circle", rect=(50.0, 300.0, 200.0, 400.0))
    after_second = second.apply(after_first)
    assert len(_annots(after_second)) == 2

    undone = second.invert().apply(after_second)

    remaining = _annots(undone)
    assert len(remaining) == 1
    assert remaining[0]["id"] == first.annot_id


# --- editing ---------------------------------------------------------------


def test_editing_moves_and_restyles_an_annotation(session: DocumentSession) -> None:
    added = AddAnnotationOperation(page=1, kind="rect", rect=(50.0, 100.0, 200.0, 200.0))
    result = added.apply(session)

    edit = EditAnnotationOperation(
        page=1,
        annot_id=added.annot_id,
        rect=(120.0, 320.0, 300.0, 390.0),
        color=(0.0, 1.0, 0.0),
        opacity=0.75,
    )
    edited = edit.apply(result)

    annot = _annots(edited)[0]
    assert annot["id"] == added.annot_id
    rect = annot["rect"]
    assert isinstance(rect, pymupdf.Rect)
    assert rect.x0 == pytest.approx(120, abs=2)
    assert rect.y0 == pytest.approx(PAGE_H - 390, abs=2)
    assert annot["colors"]["stroke"] == pytest.approx([0.0, 1.0, 0.0])  # type: ignore[index]
    assert annot["opacity"] == pytest.approx(0.75, abs=0.01)


def test_editing_only_what_was_given(session: DocumentSession) -> None:
    """The canvas sends a move without a colour; the colour must not be
    reset as a side effect."""
    added = AddAnnotationOperation(
        page=1, kind="rect", rect=(50.0, 100.0, 200.0, 200.0), color=(1.0, 0.0, 0.0)
    )
    result = added.apply(session)

    moved = EditAnnotationOperation(
        page=1, annot_id=added.annot_id, rect=(60.0, 110.0, 210.0, 210.0)
    ).apply(result)

    assert _annots(moved)[0]["colors"]["stroke"] == pytest.approx([1.0, 0.0, 0.0])  # type: ignore[index]


def test_undoing_an_edit_restores_the_previous_geometry(session: DocumentSession) -> None:
    added = AddAnnotationOperation(page=1, kind="rect", rect=(50.0, 100.0, 200.0, 200.0))
    result = added.apply(session)
    before = _annots(result)[0]["rect"]

    edit = EditAnnotationOperation(
        page=1, annot_id=added.annot_id, rect=(120.0, 320.0, 300.0, 390.0)
    )
    edited = edit.apply(result)
    restored = edit.invert().apply(edited)

    after = _annots(restored)[0]["rect"]
    assert isinstance(before, pymupdf.Rect) and isinstance(after, pymupdf.Rect)
    assert after.x0 == pytest.approx(before.x0, abs=2)
    assert after.y0 == pytest.approx(before.y0, abs=2)


def test_editing_a_missing_annotation_is_an_error(session: DocumentSession) -> None:
    with pytest.raises(OperationError, match="No annotation"):
        EditAnnotationOperation(page=1, annot_id="nope", rect=(1.0, 1.0, 2.0, 2.0)).apply(session)


# --- deleting --------------------------------------------------------------


def test_deleting_removes_the_annotation(session: DocumentSession) -> None:
    added = AddAnnotationOperation(page=1, kind="rect", rect=(50.0, 100.0, 200.0, 200.0))
    result = added.apply(session)

    deleted = DeleteAnnotationOperation(page=1, annot_id=added.annot_id).apply(result)

    assert _annots(deleted) == []


def test_undoing_a_delete_brings_it_back(session: DocumentSession) -> None:
    """Deleting discards the annotation's full definition, so the
    inverse is a snapshot restore - the same honest approach OCR and the
    other lossy operations here already take."""
    added = AddAnnotationOperation(
        page=1, kind="rect", rect=(50.0, 100.0, 200.0, 200.0), color=(1.0, 0.0, 0.0)
    )
    result = added.apply(session)
    delete = DeleteAnnotationOperation(page=1, annot_id=added.annot_id)
    deleted = delete.apply(result)

    restored = delete.invert().apply(deleted)

    annots = _annots(restored)
    assert len(annots) == 1
    assert annots[0]["id"] == added.annot_id


# --- integration with the rest of the framework ----------------------------


def test_affected_pages_reports_just_the_edited_page() -> None:
    assert AddAnnotationOperation(
        page=3, kind="rect", rect=(0.0, 0.0, 1.0, 1.0)
    ).affected_pages() == [3]
    assert EditAnnotationOperation(page=2, annot_id="x").affected_pages() == [2]
    assert DeleteAnnotationOperation(page=4, annot_id="x").affected_pages() == [4]


def test_serialize_round_trips_through_the_registry() -> None:
    """Every operation's serialize()["type"] must match its plugin's
    tool_id - that convention is what makes saved workflows
    reconstructible (core/session/workflow_store.py)."""
    from core.registry.registry import Registry, discover_and_load

    registry = Registry()
    discover_and_load(registry)

    operation = AddAnnotationOperation(page=1, kind="highlight", rect=(1.0, 2.0, 3.0, 4.0))
    data = operation.serialize()
    assert data["type"] == "add_annotation"

    rebuilt = registry.get("add_annotation").build_operation(
        page=data["page"],
        kind=data["kind"],
        rect=tuple(data["rect"]),  # type: ignore[arg-type]
        annot_id=data["annot_id"],
    )
    assert isinstance(rebuilt, AddAnnotationOperation)
    assert rebuilt.annot_id == operation.annot_id, "the id must survive a round trip"


def test_find_annotation_returns_none_when_absent(session: DocumentSession) -> None:
    with pymupdf.open(session.working_path) as doc:
        assert find_annotation(doc[0], "missing") is None
