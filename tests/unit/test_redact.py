"""Phase 6f: redaction.

The assertions here deliberately go past `get_text()` to the **raw file
bytes**. A plain `Document.save()` after `apply_redactions()` leaves the
superseded content stream in the file as an unreferenced object: the
extracted text is clean while the redacted string is still sitting in
the file, trivially recoverable. A text-only test passes that version,
which is precisely the failure mode redaction cannot afford.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import fitz
import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.redact import RedactOperation, scan_for_text

SECRET = "Jane Doe"
PAGE_W, PAGE_H = 400.0, 600.0


@pytest.fixture
def session(tmp_path: Path) -> Iterator[DocumentSession]:
    path = tmp_path / "working.pdf"
    doc = fitz.open()
    doc.new_page(width=PAGE_W, height=PAGE_H)
    doc.new_page(width=PAGE_W, height=PAGE_H)
    for i in (0, 1):
        page = doc[i]
        page.insert_text((50, 100), f"{SECRET} is the claimant", fontsize=14)
        page.insert_text((50, 200), "public information here", fontsize=14)
    doc.set_metadata(
        {"author": SECRET, "title": f"{SECRET} statement", "subject": "confidential"}
    )
    doc.set_toc([[1, f"{SECRET} - background", 1], [1, "Other section", 2]])
    doc.save(str(path))
    doc.close()
    yield DocumentSession(working_path=path, source_path=path)


def _text(session: DocumentSession) -> str:
    with fitz.open(session.working_path) as doc:
        return "\n".join(page.get_text() for page in doc)


def _raw(session: DocumentSession) -> bytes:
    assert session.working_path is not None
    return session.working_path.read_bytes()


# --- scanning --------------------------------------------------------------


def test_scan_finds_every_place_the_term_lives(session: DocumentSession) -> None:
    """Page content is not the only leak path, and the others are the
    ones a user does not think of."""
    assert session.working_path is not None
    scan = scan_for_text(session.working_path, SECRET)

    assert sorted(scan.page_hits) == [1, 2]
    assert scan.total_page_hits == 2
    assert sorted(scan.metadata_keys) == ["author", "title"]
    assert scan.bookmarks == [f"{SECRET} - background"]
    assert not scan.is_empty


def test_scan_reports_nothing_for_an_absent_term(session: DocumentSession) -> None:
    assert session.working_path is not None
    assert scan_for_text(session.working_path, "Nobody At All").is_empty


def test_scan_rects_are_bottom_left_origin(session: DocumentSession) -> None:
    assert session.working_path is not None
    scan = scan_for_text(session.working_path, SECRET)

    x0, y0, x1, y1 = scan.page_hits[1][0]
    assert x1 > x0 and y1 > y0
    # Drawn at a baseline 100pt from the top of a 600pt page, so in
    # bottom-left coordinates it sits near y=500, not near y=100.
    assert 480 < y0 < 520


# --- redacting -------------------------------------------------------------


def test_redacting_a_term_removes_it_from_the_raw_bytes(session: DocumentSession) -> None:
    """The assertion that matters. `garbage=4` on save is what makes
    this true; without it the extracted text is clean and the string is
    still in the file."""
    assert SECRET.encode() in _raw(session)

    result = RedactOperation(search_text=SECRET).apply(session)

    assert SECRET not in _text(result)
    assert SECRET.encode() not in _raw(result), "redacted text survived in the file"


def test_redaction_leaves_the_rest_of_the_page_alone(session: DocumentSession) -> None:
    result = RedactOperation(search_text=SECRET).apply(session)
    assert "public information here" in _text(result)
    assert "is the claimant" in _text(result)


def test_redacting_scrubs_metadata_and_bookmarks(session: DocumentSession) -> None:
    result = RedactOperation(search_text=SECRET).apply(session)

    with fitz.open(result.working_path) as doc:
        assert not any(
            isinstance(v, str) and SECRET in v
            for k, v in (doc.metadata or {}).items()
            if k != "format"
        )
        assert all(SECRET not in entry[1] for entry in doc.get_toc())
        # A bookmark that did not mention the term is kept.
        assert any("Other section" in entry[1] for entry in doc.get_toc())


def test_scrubbing_can_be_switched_off(session: DocumentSession) -> None:
    result = RedactOperation(
        search_text=SECRET,
        scrub_metadata=False,
        scrub_bookmarks=False,
    ).apply(session)

    with fitz.open(result.working_path) as doc:
        assert doc.metadata is not None
        assert doc.metadata.get("author") == SECRET
        assert any(SECRET in entry[1] for entry in doc.get_toc())


def test_redacting_an_explicit_region(session: DocumentSession) -> None:
    """The GUI's review step narrows to explicit regions; the operation
    must be equally capable either way."""
    assert session.working_path is not None
    scan = scan_for_text(session.working_path, SECRET)
    x0, y0, x1, y1 = scan.page_hits[1][0]

    result = RedactOperation(rects=[(1, x0, y0, x1, y1)]).apply(session)

    with fitz.open(result.working_path) as doc:
        assert SECRET not in doc[0].get_text()
        assert SECRET in doc[1].get_text(), "only the region asked for is redacted"


def test_a_region_redaction_does_not_scrub_metadata(session: DocumentSession) -> None:
    """Without a search term there is nothing to match metadata
    against, so blanking it unasked would be a surprise."""
    assert session.working_path is not None
    scan = scan_for_text(session.working_path, SECRET)
    x0, y0, x1, y1 = scan.page_hits[1][0]

    result = RedactOperation(rects=[(1, x0, y0, x1, y1)]).apply(session)

    with fitz.open(result.working_path) as doc:
        assert doc.metadata is not None
        assert doc.metadata.get("author") == SECRET


# --- framework integration -------------------------------------------------


def test_undo_restores_the_document(session: DocumentSession) -> None:
    """Redaction is destructive by design, so the only honest inverse is
    the document as it was."""
    operation = RedactOperation(search_text=SECRET)
    result = operation.apply(session)
    assert SECRET not in _text(result)

    restored = operation.invert().apply(result)

    assert SECRET in _text(restored)


def test_describe_reports_what_was_actually_removed(session: DocumentSession) -> None:
    operation = RedactOperation(search_text=SECRET)
    operation.apply(session)
    # 2 page hits + 2 metadata keys + 1 bookmark.
    assert "occurrence(s)" in operation.describe()
    assert operation._removed >= 3


def test_affected_pages_is_unknown_for_a_search(session: DocumentSession) -> None:
    """A term can appear anywhere, so the conservative answer is right."""
    assert RedactOperation(search_text=SECRET).affected_pages() is None
    assert RedactOperation(rects=[(2, 1.0, 1.0, 2.0, 2.0)]).affected_pages() == [2]


def test_validation() -> None:
    with pytest.raises(OperationError, match="either regions or a search term"):
        RedactOperation()
    with pytest.raises(OperationError, match="positive width and height"):
        RedactOperation(rects=[(1, 10.0, 10.0, 5.0, 20.0)])
    with pytest.raises(OperationError, match="1-based"):
        RedactOperation(rects=[(0, 1.0, 1.0, 2.0, 2.0)])


def test_a_page_beyond_the_document_is_rejected(session: DocumentSession) -> None:
    with pytest.raises(OperationError, match="out of range"):
        RedactOperation(rects=[(99, 1.0, 1.0, 2.0, 2.0)]).apply(session)


def test_it_reports_progress(session: DocumentSession) -> None:
    seen: list[tuple[int, int]] = []
    operation = RedactOperation(search_text=SECRET)
    operation.set_progress_callback(lambda done, total: seen.append((done, total)))

    operation.apply(session)

    assert seen == [(0, 2), (1, 2)]


def test_registered_and_reconstructible() -> None:
    from core.registry.registry import Registry, discover_and_load

    registry = Registry()
    discover_and_load(registry)
    operation = RedactOperation(search_text=SECRET)
    assert operation.serialize()["type"] == "redact"

    rebuilt = registry.get("redact").build_operation(search_text=SECRET)
    assert isinstance(rebuilt, RedactOperation)
