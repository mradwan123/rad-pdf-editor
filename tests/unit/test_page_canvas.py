"""Phase 6c: the page viewer.

Covers the properties 6c actually claims - a readable continuous page
view, zoom that re-renders rather than magnifies, fit modes derived
from the viewport, and rendering limited to what is on screen - plus
regressions for the two rendering bugs found while building it, both of
which produced a silently *blank* viewer that still reported success.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from gui.page_canvas import _LOOKAHEAD, _MAX_ZOOM, _MIN_ZOOM, PageCanvas

PAGE_W, PAGE_H = 400.0, 600.0


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_pdf(path: Path, pages: int = 10) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text((40, 100), f"page {i + 1}", fontsize=24)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def canvas(qapp: QApplication) -> Iterator[PageCanvas]:
    view = PageCanvas()
    view.resize(800, 600)
    yield view
    view.release()


def test_shows_every_page_stacked_in_order(canvas: PageCanvas, tmp_path: Path) -> None:
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 6))
    assert canvas.page_count == 6
    tops = [item.pos().y() for item in canvas._items]
    assert tops == sorted(tops), "pages must be laid out top to bottom"
    assert [item.page_number for item in canvas._items] == [1, 2, 3, 4, 5, 6]


def test_zoom_changes_the_rendered_pixel_size_not_a_view_transform(
    canvas: PageCanvas, tmp_path: Path
) -> None:
    """Scene units are device pixels, so zooming in must make the page
    genuinely bigger in pixels - that is what makes it sharper rather
    than magnified."""
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 3))
    canvas.set_zoom(1.0)
    before = canvas._items[0].boundingRect().width()
    assert before == pytest.approx(PAGE_W)

    canvas.set_zoom(2.0)
    assert canvas._items[0].boundingRect().width() == pytest.approx(PAGE_W * 2)
    # The view itself is never scaled; the pages are.
    assert canvas.transform().m11() == pytest.approx(1.0)


def test_zoom_is_clamped(canvas: PageCanvas, tmp_path: Path) -> None:
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 2))
    for _ in range(50):
        canvas.zoom_in()
    assert canvas.zoom == pytest.approx(_MAX_ZOOM)
    for _ in range(80):
        canvas.zoom_out()
    assert canvas.zoom == pytest.approx(_MIN_ZOOM)


def test_fit_width_derives_zoom_from_the_viewport(canvas: PageCanvas, tmp_path: Path) -> None:
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 3))
    canvas.fit_width()
    expected = canvas.viewport().size().width() / PAGE_W
    assert canvas.zoom == pytest.approx(expected, rel=0.02)


def test_fit_page_fits_the_whole_page(canvas: PageCanvas, tmp_path: Path) -> None:
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 3))
    canvas.fit_page()
    viewport = canvas.viewport().size()
    assert canvas._items[0].boundingRect().height() <= viewport.height() + 1
    assert canvas._items[0].boundingRect().width() <= viewport.width() + 1


def test_only_visible_pages_are_rendered(canvas: PageCanvas, tmp_path: Path) -> None:
    """The reason 6c does not inherit 6b's eager rendering: a full-size
    page costs ~2 MB against a thumbnail's 75 KB, so a long document
    must not render every page."""
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 40))
    canvas.set_zoom(1.0)
    assert canvas.wait_until_idle()

    rendered = canvas.rendered_page_count
    assert rendered > 0, "the visible pages must actually render"
    assert rendered < 40, "a 40-page document must not render every page"
    # Two 600px pages fit an 600px-tall viewport at zoom 1.0, plus the
    # lookahead in each direction.
    assert rendered <= 3 + 2 * _LOOKAHEAD


def test_scrolling_renders_the_pages_scrolled_to(canvas: PageCanvas, tmp_path: Path) -> None:
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 40))
    canvas.set_zoom(1.0)
    assert canvas.wait_until_idle()
    assert not canvas._items[29].has_pixmap

    canvas.scroll_to_page(30)
    assert canvas.wait_until_idle()

    assert canvas.current_page == 30
    assert canvas._items[29].has_pixmap, "the page scrolled to must be rendered"


def test_a_zoom_change_re_renders_rather_than_going_blank(
    canvas: PageCanvas, tmp_path: Path
) -> None:
    """Regression: _pending was keyed by page only, so after a zoom the
    still-pending old-size request made _request_visible skip the page
    as "already requested". Its result was then correctly dropped for
    being the wrong size and nothing ever re-requested it - a silently
    blank viewer that reported itself idle."""
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 5))
    canvas.set_zoom(1.0)
    assert canvas.wait_until_idle()
    assert canvas.rendered_page_count > 0

    canvas.set_zoom(1.5)
    assert canvas.wait_until_idle()

    assert canvas.rendered_page_count > 0, "pages went blank after zooming"
    assert canvas._items[0].boundingRect().width() == pytest.approx(PAGE_W * 1.5)


def test_two_rapid_zooms_still_render(canvas: PageCanvas, tmp_path: Path) -> None:
    """Regression: _on_page_rendered discarded from _pending *before*
    checking the delivered size, so a late result for a superseded zoom
    cleared the entry belonging to the current request. wait_until_idle
    then returned early with nothing drawn - which only showed up when
    two zooms happened without waiting in between."""
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 5))
    canvas.set_zoom(1.0)
    assert canvas.wait_until_idle()

    canvas.zoom_in()
    canvas.zoom_in()  # no wait in between - this is the race
    assert canvas.wait_until_idle()

    assert canvas.rendered_page_count > 0, "rapid zooming left the viewer blank"


def test_invalidate_drops_rendered_pages_and_re_renders(
    canvas: PageCanvas, tmp_path: Path
) -> None:
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 4))
    canvas.set_zoom(1.0)
    assert canvas.wait_until_idle()
    assert canvas._items[0].has_pixmap

    canvas.invalidate([1])
    assert canvas.wait_until_idle()
    assert canvas._items[0].has_pixmap, "an invalidated visible page must re-render"


def test_release_drops_the_document_handle(canvas: PageCanvas, tmp_path: Path) -> None:
    """The canvas keeps the working file open; Windows refuses to
    unlink an open file, so the secure wipe depends on this."""
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 2))
    assert canvas._pdf is not None
    canvas.release()
    assert canvas._pdf is None
    canvas.release()  # idempotent


def test_an_unloadable_file_leaves_an_empty_canvas(canvas: PageCanvas, tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    assert canvas.set_document(broken) is False
    assert canvas.page_count == 0


def test_current_page_follows_the_scroll_position(canvas: PageCanvas, tmp_path: Path) -> None:
    canvas.set_document(_make_pdf(tmp_path / "a.pdf", 10))
    canvas.set_zoom(1.0)
    assert canvas.current_page == 1

    seen: list[int] = []
    canvas.current_page_changed.connect(seen.append)
    canvas.scroll_to_page(7)

    assert canvas.current_page == 7
    assert seen and seen[-1] == 7, "current_page_changed must actually fire"


def _red_fraction(image: object) -> float:
    """Fraction of sampled pixels that are strongly red."""
    from PySide6.QtGui import QImage

    assert isinstance(image, QImage)
    reds = total = 0
    for y in range(0, image.height(), 3):
        for x in range(0, image.width(), 3):
            colour = image.pixelColor(x, y)
            total += 1
            if colour.red() > 150 and colour.green() < 120 and colour.blue() < 120:
                reds += 1
    return reds / max(total, 1)


def _make_annotated_pdf(path: Path) -> Path:
    import fitz

    doc = fitz.open()
    doc.new_page(width=200, height=200)
    annot = doc[0].add_rect_annot(fitz.Rect(20, 20, 180, 180))
    annot.set_colors(stroke=(1, 0, 0), fill=(1, 0, 0))
    annot.set_opacity(1.0)
    annot.update()
    doc.save(str(path))
    doc.close()
    return path


def test_the_page_view_actually_draws_annotations(canvas: PageCanvas, tmp_path: Path) -> None:
    """Regression: QtPdf omits annotations from render() unless
    RenderFlag.Annotations is passed. Without it, highlights, notes and
    shapes were created correctly in the file and were simply invisible
    - which no assertion about the *document* would ever catch."""
    canvas.set_document(_make_annotated_pdf(tmp_path / "annot.pdf"))
    canvas.set_zoom(1.0)
    assert canvas.wait_until_idle()

    item = canvas._items[0]
    assert item.has_pixmap
    assert _red_fraction(item._pixmap.toImage()) > 0.3, "the annotation was not rendered"


def test_thumbnails_also_draw_annotations(qapp: QApplication, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QListWidget

    from gui.rendering import ThumbnailRenderer

    widget = QListWidget()
    renderer = ThumbnailRenderer(widget)
    try:
        renderer.render(_make_annotated_pdf(tmp_path / "annot.pdf"), QSize(120, 120))
        assert renderer.wait_until_idle()
        icon = widget.item(0).icon()
        assert _red_fraction(icon.pixmap(QSize(120, 120)).toImage()) > 0.3
    finally:
        renderer.release()
