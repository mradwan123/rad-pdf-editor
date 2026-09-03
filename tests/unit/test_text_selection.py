"""Phase 6c: text selection and links.

Selection is hand-built because **`QPdfDocument.getSelection()` is
unusable in PySide6 6.11.1** - it returns an invalid, empty selection
for every point range, including ranges squarely over text that
`getAllText()` reports. `gui/text_selection.py` maps a point to a
character index instead, via per-line bounds plus a binary search.
These tests pin that mapping against text at known positions, because
"a selection happened" is not the same as "the right text was
selected".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication

from gui.page_canvas import PageCanvas
from gui.text_selection import PageTextIndex

PAGE_W, PAGE_H = 400.0, 600.0
LINE_ONE = "ALPHA BETA GAMMA"
LINE_TWO = "second line here"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_pdf(path: Path, *, with_link: bool = False) -> Path:
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W, height=PAGE_H)
    doc.new_page(width=PAGE_W, height=PAGE_H)
    page = doc[0]
    page.insert_text((50, 100), LINE_ONE, fontsize=20)
    page.insert_text((50, 200), LINE_TWO, fontsize=20)
    if with_link:
        page.insert_link({
            "kind": pymupdf.LINK_GOTO,
            "from": pymupdf.Rect(50, 286, 200, 304),
            "page": 1,
            "to": pymupdf.Point(0, 0),
        })
        page.insert_link({
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(50, 386, 200, 404),
            "uri": "https://example.invalid/x",
        })
    doc.save(str(path))
    doc.close()
    return path


# --- PageTextIndex ---------------------------------------------------------


@pytest.fixture
def index(qapp: QApplication, tmp_path: Path) -> Iterator[PageTextIndex]:
    doc = QPdfDocument()
    assert doc.load(str(_make_pdf(tmp_path / "t.pdf"))) == QPdfDocument.Error.None_
    yield PageTextIndex(doc, 1)
    doc.close()


def test_lines_and_bounds_line_up(index: PageTextIndex) -> None:
    """The whole mapping rests on line *k* of the text matching polygon
    *k* of bounds(); if those ever disagree the page is unselectable
    rather than mis-mapped."""
    assert index.text == f"{LINE_ONE}\r\n{LINE_TWO}"
    assert not index.is_empty
    assert len(index._line_rects) == len(index._line_ranges) == 2


def test_a_point_maps_to_the_character_under_it(index: PageTextIndex) -> None:
    # "ALPHA " is 6 characters, so a point past it lands on "BETA".
    assert index.index_at(QPointF(51, 95)) == 0
    assert index.text[index.index_at(QPointF(120, 95)) :].startswith("BETA")
    # The second line has its own index range, after the \r\n.
    assert index.index_at(QPointF(51, 195)) == len(LINE_ONE) + 2


def test_a_point_left_or_right_of_the_text_clamps_to_the_line(
    index: PageTextIndex,
) -> None:
    assert index.index_at(QPointF(0, 95)) == 0
    assert index.index_at(QPointF(PAGE_W, 95)) == len(LINE_ONE)


def test_a_point_between_lines_picks_the_nearest(index: PageTextIndex) -> None:
    """A click in the gap still selects, rather than doing nothing."""
    assert index.index_at(QPointF(51, 150)) in (
        len(LINE_ONE),
        len(LINE_ONE) + 2,
    )


def test_text_and_rects_between_indices(index: PageTextIndex) -> None:
    assert index.text_between(0, 5) == "ALPHA"
    assert index.text_between(5, 0) == "ALPHA", "order must not matter"
    assert index.text_between(3, 3) == ""
    # One rect per line covered.
    assert len(index.rects_between(0, index.length)) == 2


def test_rects_are_in_top_left_origin_pdf_points(index: PageTextIndex) -> None:
    """The convention shared with search hits and getSelectionAtIndex -
    "ALPHA" sits near y=86 on a page whose baseline is at y=100, not
    near y=500 as a bottom-left reading would put it."""
    rect = index.rects_between(0, 5)[0]
    assert 80 < rect.y() < 105
    assert 45 < rect.x() < 60


# --- selection on the canvas ----------------------------------------------


@pytest.fixture
def canvas(qapp: QApplication, tmp_path: Path) -> Iterator[PageCanvas]:
    view = PageCanvas()
    view.resize(600, 700)
    view.set_document(_make_pdf(tmp_path / "c.pdf", with_link=True))
    view.set_zoom(1.0)
    yield view
    view.release()


def _viewport_point(canvas: PageCanvas, page: int, x: float, y: float) -> QPoint:
    item = canvas._items[page - 1]
    scene = item.pos() + QPointF(x * canvas.zoom, y * canvas.zoom)
    return canvas.mapFromScene(scene)


def _press(canvas: PageCanvas, pos: QPoint) -> None:
    canvas.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(pos),
            QPointF(pos),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def _drag_to(canvas: PageCanvas, pos: QPoint) -> None:
    canvas.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(pos),
            QPointF(pos),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_dragging_selects_exactly_the_text_under_the_drag(canvas: PageCanvas) -> None:
    """Real drag gestures are not reliably simulatable under
    QT_QPA_PLATFORM=offscreen, so the handlers are driven with real
    QMouseEvent objects - the same technique the sign placement tests
    use for QGraphicsSceneMouseEvent."""
    _press(canvas, _viewport_point(canvas, 1, 50, 95))
    _drag_to(canvas, _viewport_point(canvas, 1, 175, 95))

    assert canvas.selected_text == "ALPHA BETA"
    assert canvas._items[0]._selection, "the selection must actually be painted"


def test_a_drag_can_span_lines(canvas: PageCanvas) -> None:
    _press(canvas, _viewport_point(canvas, 1, 50, 95))
    _drag_to(canvas, _viewport_point(canvas, 1, 175, 195))

    assert canvas.selected_text.startswith(LINE_ONE)
    assert "second line" in canvas.selected_text


def test_a_drag_onto_another_page_stays_on_the_anchor_page(canvas: PageCanvas) -> None:
    """Documented boundary: selection is within one page. Extending
    onto another page would silently select the wrong page's text, so
    the drag simply stops extending."""
    _press(canvas, _viewport_point(canvas, 1, 50, 95))
    _drag_to(canvas, _viewport_point(canvas, 1, 175, 95))
    before = canvas.selected_text

    _drag_to(canvas, _viewport_point(canvas, 2, 100, 100))

    assert canvas.selected_text == before


def test_select_all_and_copy(canvas: PageCanvas, qapp: QApplication) -> None:
    canvas.select_all_on_page(1)
    assert LINE_ONE in canvas.selected_text
    assert LINE_TWO in canvas.selected_text

    assert canvas.copy_selection() is True
    clipboard = QApplication.clipboard()
    assert clipboard is not None
    assert LINE_ONE in clipboard.text()


def test_copying_nothing_reports_false(canvas: PageCanvas) -> None:
    canvas.clear_selection()
    assert canvas.copy_selection() is False


def test_clearing_removes_the_painted_selection(canvas: PageCanvas) -> None:
    canvas.select_all_on_page(1)
    assert canvas._items[0]._selection
    canvas.clear_selection()
    assert canvas._items[0]._selection == []
    assert canvas.selected_text == ""


def test_editing_a_page_drops_its_cached_text(canvas: PageCanvas) -> None:
    """The text index and links belong to a document revision; an
    operation that changed the page must not leave them stale."""
    canvas.select_all_on_page(1)
    assert 1 in canvas._text_indices

    canvas.invalidate([1])

    assert 1 not in canvas._text_indices
    assert canvas.selected_text == ""


# --- links -----------------------------------------------------------------


def test_clicking_an_internal_link_reports_a_one_based_page(canvas: PageCanvas) -> None:
    seen: list[tuple[int, str]] = []
    canvas.link_activated.connect(lambda page, url: seen.append((page, url)))

    _press(canvas, _viewport_point(canvas, 1, 100, 295))

    assert seen == [(2, "")], "internal links report their target page"


def test_clicking_an_external_link_reports_its_url_and_no_page(
    canvas: PageCanvas,
) -> None:
    """QPdfLinkModel reports page -1 for an external link; the canvas
    normalises that to page 0 so callers can tell the two apart."""
    seen: list[tuple[int, str]] = []
    canvas.link_activated.connect(lambda page, url: seen.append((page, url)))

    _press(canvas, _viewport_point(canvas, 1, 100, 395))

    assert seen == [(0, "https://example.invalid/x")]


def test_clicking_a_link_does_not_start_a_selection(canvas: PageCanvas) -> None:
    _press(canvas, _viewport_point(canvas, 1, 100, 295))
    assert canvas.selected_text == ""
