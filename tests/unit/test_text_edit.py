"""Phase 6h: editing text already on the page.

The experimental slice, and the tests say so: they pin both outcomes -
the *exact* one where the font is embedded and can be re-embedded, and
the *substituted* one where it cannot. The second is not a failure, but
it must be detectable before anything is written (decision 12).
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import fitz
import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.text_edit import (
    FALLBACK_FONT,
    EditTextSpanOperation,
    find_text_spans,
    resolve_font,
    span_at,
)

PAGE_W, PAGE_H = 400.0, 300.0
ORIGINAL = "Total due: 4200.00"


def _embedded_font() -> str | None:
    """A real font file to embed, or None if fontconfig has none."""
    try:
        found = subprocess.run(
            ["fc-match", "-f", "%{file}", "DejaVu Sans"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return found if found and Path(found).exists() else None


def _make_pdf(path: Path, *, font_file: str | None = None) -> Path:
    doc = fitz.open()
    doc.new_page(width=PAGE_W, height=PAGE_H)
    if font_file:
        doc[0].insert_text(
            (50, 100), ORIGINAL, fontsize=14, fontfile=font_file, fontname="Custom"
        )
    else:
        doc[0].insert_text((50, 100), ORIGINAL, fontsize=14, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def session(tmp_path: Path) -> Iterator[DocumentSession]:
    path = _make_pdf(tmp_path / "working.pdf")
    yield DocumentSession(working_path=path, source_path=path)


def _text(session: DocumentSession) -> str:
    with fitz.open(session.working_path) as doc:
        return doc[0].get_text().strip()


# --- reading spans ---------------------------------------------------------


def test_spans_report_font_size_and_position(session: DocumentSession) -> None:
    assert session.working_path is not None
    spans = find_text_spans(session.working_path, 1)

    assert len(spans) == 1
    span = spans[0]
    assert span.text == ORIGINAL
    assert span.font_size == pytest.approx(14.0)
    assert "Helvetica" in span.font_name
    # Bottom-left origin: drawn at a baseline 100pt from the top of a
    # 300pt page, so it sits near y=200 here, not near y=100.
    assert 190 < span.origin[1] < 210


def test_span_at_finds_the_run_under_a_point(session: DocumentSession) -> None:
    assert session.working_path is not None
    span = find_text_spans(session.working_path, 1)[0]
    x0, y0, x1, y1 = span.rect

    hit = span_at(session.working_path, 1, (x0 + x1) / 2, (y0 + y1) / 2)
    assert hit is not None
    assert hit.text == ORIGINAL

    assert span_at(session.working_path, 1, 5.0, 5.0) is None


def test_no_spans_on_a_page_out_of_range(session: DocumentSession) -> None:
    assert session.working_path is not None
    assert find_text_spans(session.working_path, 99) == []


# --- font resolution (decision 12) ----------------------------------------


def test_a_base14_font_cannot_be_reproduced(session: DocumentSession) -> None:
    """Measured, not assumed: extract_font returns a 0-byte buffer for a
    non-embedded font, which is the test - the name looks perfectly
    ordinary."""
    assert session.working_path is not None
    resolution = resolve_font(session.working_path, 1, "Helvetica")

    assert resolution.is_exact is False
    assert resolution.resolved == FALLBACK_FONT
    assert "cannot be reproduced" in resolution.warning
    assert "appearance will change" in resolution.warning


def test_an_embedded_font_can_be_reproduced(tmp_path: Path) -> None:
    font_file = _embedded_font()
    if font_file is None:
        pytest.skip("no system font available to embed")
    path = _make_pdf(tmp_path / "embedded.pdf", font_file=font_file)
    span = find_text_spans(path, 1)[0]

    resolution = resolve_font(path, 1, span.font_name)

    assert resolution.is_exact is True
    assert resolution.warning == ""


# --- editing ---------------------------------------------------------------


def test_editing_replaces_the_text(session: DocumentSession) -> None:
    span = find_text_spans(session.working_path, 1)[0]  # type: ignore[arg-type]

    result = EditTextSpanOperation(
        page=1, rect=span.rect, new_text="Total due: 9999.99"
    ).apply(session)

    assert _text(result) == "Total due: 9999.99"
    assert ORIGINAL not in _text(result)


def test_the_old_glyphs_are_removed_not_hidden(session: DocumentSession) -> None:
    """Redact-and-reinsert, saved with garbage collection - the same
    reason redaction needs it. Painting over would leave the original
    recoverable."""
    span = find_text_spans(session.working_path, 1)[0]  # type: ignore[arg-type]

    result = EditTextSpanOperation(page=1, rect=span.rect, new_text="9999").apply(session)

    assert result.working_path is not None
    assert ORIGINAL.encode() not in result.working_path.read_bytes()


def test_an_edit_keeps_size_and_position(session: DocumentSession) -> None:
    span = find_text_spans(session.working_path, 1)[0]  # type: ignore[arg-type]

    result = EditTextSpanOperation(page=1, rect=span.rect, new_text="Total due: 1.00").apply(
        session
    )

    edited = find_text_spans(result.working_path, 1)[0]  # type: ignore[arg-type]
    assert edited.font_size == pytest.approx(span.font_size)
    assert edited.origin[0] == pytest.approx(span.origin[0], abs=1)
    assert edited.origin[1] == pytest.approx(span.origin[1], abs=1)


def test_an_edit_reports_when_it_substituted_a_font(session: DocumentSession) -> None:
    """The fact must be visible after the fact too - in the undo stack
    and the audit log - not only in the pre-commit preview."""
    span = find_text_spans(session.working_path, 1)[0]  # type: ignore[arg-type]
    operation = EditTextSpanOperation(page=1, rect=span.rect, new_text="x")

    operation.apply(session)

    assert "substituted font" in operation.describe()


def test_an_embedded_font_is_re_embedded(tmp_path: Path) -> None:
    """The good case: the replacement renders in the *original*
    typeface, not a substitute."""
    font_file = _embedded_font()
    if font_file is None:
        pytest.skip("no system font available to embed")
    path = _make_pdf(tmp_path / "embedded.pdf", font_file=font_file)
    session = DocumentSession(working_path=path, source_path=path)
    span = find_text_spans(path, 1)[0]
    operation = EditTextSpanOperation(page=1, rect=span.rect, new_text="Total due: 1.00")

    result = operation.apply(session)

    assert "substituted font" not in operation.describe()
    edited = find_text_spans(result.working_path, 1)[0]  # type: ignore[arg-type]
    assert edited.font_name == span.font_name, "the original font must be reused"


def test_undo_restores_the_original_text(session: DocumentSession) -> None:
    span = find_text_spans(session.working_path, 1)[0]  # type: ignore[arg-type]
    operation = EditTextSpanOperation(page=1, rect=span.rect, new_text="changed")
    edited = operation.apply(session)
    assert _text(edited) == "changed"

    restored = operation.invert().apply(edited)

    assert _text(restored) == ORIGINAL


def test_editing_a_region_with_no_text_is_an_error(session: DocumentSession) -> None:
    with pytest.raises(OperationError, match="No text found"):
        EditTextSpanOperation(page=1, rect=(1.0, 1.0, 20.0, 20.0), new_text="x").apply(session)


def test_validation() -> None:
    with pytest.raises(OperationError, match="1-based"):
        EditTextSpanOperation(page=0, rect=(1.0, 1.0, 2.0, 2.0), new_text="x")
    with pytest.raises(OperationError, match="positive size"):
        EditTextSpanOperation(page=1, rect=(10.0, 10.0, 5.0, 20.0), new_text="x")


def test_a_page_beyond_the_document_is_rejected(session: DocumentSession) -> None:
    with pytest.raises(OperationError, match="out of range"):
        EditTextSpanOperation(page=9, rect=(1.0, 1.0, 20.0, 20.0), new_text="x").apply(session)


def test_registered_and_serializable() -> None:
    from core.registry.registry import Registry, discover_and_load

    registry = Registry()
    discover_and_load(registry)
    operation = EditTextSpanOperation(page=1, rect=(1.0, 2.0, 3.0, 4.0), new_text="x")

    assert operation.serialize()["type"] == "edit_text"
    rebuilt = registry.get("edit_text").build_operation(
        page=1, rect=(1.0, 2.0, 3.0, 4.0), new_text="x"
    )
    assert isinstance(rebuilt, EditTextSpanOperation)
