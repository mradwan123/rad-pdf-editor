"""Phase 6c: document outline and find-in-document.

Both are QtPdf model classes driven off a `QPdfDocument` with no
`QPdfView` involved (docs/GUI_PLAN.md §2.1). The search model is
**asynchronous** - a single `processEvents()` after `setSearchString`
reports zero hits on a document that plainly contains the term - so
every search assertion here settles first rather than reading the count
straight back.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication

from gui.find_bar import FindBar
from gui.outline_panel import OutlinePanel
from gui.page_canvas import PageCanvas

PAGE_W, PAGE_H = 400.0, 600.0


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_pdf(path: Path, *, with_outline: bool = True) -> Path:
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text((50, 100), f"Section {i + 1}", fontsize=20)
        page.insert_text((50, 200), "the needle appears here", fontsize=14)
        page.insert_text((50, 300), "and some filler text", fontsize=14)
    if with_outline:
        doc.set_toc([[1, "First", 1], [2, "First child", 2], [1, "Second", 3]])
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def canvas(qapp: QApplication, tmp_path: Path) -> Iterator[PageCanvas]:
    view = PageCanvas()
    view.resize(700, 500)
    view.set_document(_make_pdf(tmp_path / "doc.pdf"))
    yield view
    view.release()


# --- outline ---------------------------------------------------------------


def test_outline_lists_the_documents_bookmarks(canvas: PageCanvas, qapp: QApplication) -> None:
    panel = OutlinePanel()
    panel.set_document(canvas.document)

    assert panel.has_outline
    model = panel.tree.model()
    assert model.rowCount(model.index(-1, -1)) == 2  # two top-level entries
    panel.set_document(None)


def test_activating_a_bookmark_emits_a_one_based_page(
    canvas: PageCanvas, qapp: QApplication
) -> None:
    """QtPdf reports 0-based pages; everything user-facing in this
    project is 1-based, so the panel converts."""
    panel = OutlinePanel()
    panel.set_document(canvas.document)
    seen: list[int] = []
    panel.page_requested.connect(seen.append)

    model = panel.tree.model()
    panel.tree.clicked.emit(model.index(1, 0))  # "Second" -> page 3

    assert seen == [3]
    panel.set_document(None)


def test_a_document_without_an_outline_reports_none(
    qapp: QApplication, tmp_path: Path
) -> None:
    view = PageCanvas()
    view.set_document(_make_pdf(tmp_path / "plain.pdf", with_outline=False))
    panel = OutlinePanel()
    panel.set_document(view.document)

    assert not panel.has_outline

    panel.set_document(None)
    view.release()


# --- find ------------------------------------------------------------------


def test_find_locates_every_occurrence(canvas: PageCanvas, qapp: QApplication) -> None:
    bar = FindBar()
    bar.set_document(canvas.document)
    bar.input.setText("needle")

    total = bar.wait_until_settled()

    assert total == 4, "one hit on each of the four pages"
    by_page = bar.results_by_page()
    assert sorted(by_page) == [1, 2, 3, 4]
    bar.set_document(None)


def test_find_results_are_in_top_left_origin_pdf_points(
    canvas: PageCanvas, qapp: QApplication
) -> None:
    """The convention QPdfSearchModel shares with getSelectionAtIndex,
    verified against text drawn at a known baseline: "the needle
    appears here" sits at y=200 from the top, so its hit rect must be
    just above that - not near y=400, which is where a
    bottom-left-origin reading would put it."""
    bar = FindBar()
    bar.set_document(canvas.document)
    bar.input.setText("needle")
    bar.wait_until_settled()

    rect = bar.results_by_page()[1][0]
    assert 180 < rect.y() < 205, f"unexpected y for a top-left origin: {rect.y()}"
    assert 50 < rect.x() < 200
    bar.set_document(None)


def test_find_next_cycles_through_hits(canvas: PageCanvas, qapp: QApplication) -> None:
    bar = FindBar()
    bar.set_document(canvas.document)
    bar.input.setText("needle")
    bar.wait_until_settled()

    pages: list[int] = []
    bar.result_activated.connect(lambda page, _rect: pages.append(page))
    for _ in range(5):
        bar.find_next()

    assert pages == [1, 2, 3, 4, 1], "next must advance and then wrap"
    bar.set_document(None)


def test_a_search_with_no_hits_reports_none(canvas: PageCanvas, qapp: QApplication) -> None:
    bar = FindBar()
    bar.set_document(canvas.document)
    bar.input.setText("haystack")
    assert bar.wait_until_settled() == 0
    assert "No results" in bar.status.text()
    bar.set_document(None)


# --- highlights ------------------------------------------------------------


def test_highlights_are_stored_in_points_and_scale_with_zoom(canvas: PageCanvas) -> None:
    """Kept in PDF points rather than pixels so a zoom change cannot
    leave them stale - there is one source of truth for where a hit is."""
    canvas.set_zoom(1.0)
    canvas.set_highlights({1: [QRectF(50, 190, 80, 14)]})
    item = canvas._items[0]
    assert item._highlights == [QRectF(50, 190, 80, 14)]
    assert item._scale == pytest.approx(1.0)

    canvas.set_zoom(2.0)
    assert item._highlights == [QRectF(50, 190, 80, 14)], "rects stay in points"
    assert item._scale == pytest.approx(2.0), "only the scale changes"
