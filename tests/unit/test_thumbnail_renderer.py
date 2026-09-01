"""Phase 6b: the asynchronous, cached, page-targeted thumbnail renderer.

These assert the three properties 6b actually claims - that rendering
is deferred rather than blocking, that unchanged pages are served from
cache instead of re-rasterised, and that an edit only invalidates the
pages it touched - rather than the weaker "a thumbnail eventually
appears", which was already true before 6b.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QListWidget

from core.ops.organize import DeletePagesOperation, RotatePagesOperation
from gui.rendering import ThumbnailRenderer

SIZE = QSize(60, 80)


def _make_pdf(path: Path, pages: int = 4) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 200), f"page {i + 1}", fontsize=24)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def renderer(qapp: QApplication) -> Iterator[ThumbnailRenderer]:
    widget = QListWidget()
    r = ThumbnailRenderer(widget)
    yield r
    r.release()


def test_items_exist_immediately_but_pixels_arrive_later(
    renderer: ThumbnailRenderer, tmp_path: Path
) -> None:
    """The core of 6b: render() returns without blocking on
    rasterisation, yet the grid is already structurally correct."""
    pdf = _make_pdf(tmp_path / "a.pdf", 4)
    renderer.render(pdf, SIZE)

    # Structure is correct synchronously - this is what the window and
    # the rest of the suite depend on.
    assert renderer._list.count() == 4
    assert [renderer._list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(4)] == [1, 2, 3, 4]
    assert renderer._list.item(0).icon().actualSize(SIZE) == SIZE

    # ...but the pixels genuinely had not been rendered yet.
    assert not renderer.is_idle
    assert renderer.wait_until_idle()
    assert renderer.is_idle


def test_a_second_render_of_the_same_document_needs_no_rasterisation(
    renderer: ThumbnailRenderer, tmp_path: Path
) -> None:
    pdf = _make_pdf(tmp_path / "a.pdf", 4)
    renderer.render(pdf, SIZE)
    assert renderer.wait_until_idle()
    assert renderer.cache_size == 4

    # Every page is cached, so this pass requests nothing at all and is
    # idle the instant it returns.
    renderer.render(pdf, SIZE)
    assert renderer.is_idle
    assert renderer._list.count() == 4


def test_invalidating_one_page_re_renders_only_that_page(
    renderer: ThumbnailRenderer, tmp_path: Path
) -> None:
    pdf = _make_pdf(tmp_path / "a.pdf", 6)
    renderer.render(pdf, SIZE)
    assert renderer.wait_until_idle()
    assert renderer.cache_size == 6

    renderer.invalidate([3])
    assert renderer.cache_size == 5

    renderer.render(pdf, SIZE)
    # Exactly one page had to be requested - the whole point of
    # Operation.affected_pages().
    assert renderer._pending == {3}
    assert renderer.wait_until_idle()


def test_invalidating_everything_drops_the_whole_cache(
    renderer: ThumbnailRenderer, tmp_path: Path
) -> None:
    pdf = _make_pdf(tmp_path / "a.pdf", 3)
    renderer.render(pdf, SIZE)
    assert renderer.wait_until_idle()
    assert renderer.cache_size == 3

    renderer.invalidate(None)
    assert renderer.cache_size == 0
    renderer.render(pdf, SIZE)
    assert renderer._pending == {1, 2, 3}
    assert renderer.wait_until_idle()


def test_a_different_zoom_is_a_separate_cache_entry(
    renderer: ThumbnailRenderer, tmp_path: Path
) -> None:
    """Cache keys include the size, so zooming re-renders at the new
    size rather than upscaling a stale pixmap - and zooming back is
    then free."""
    pdf = _make_pdf(tmp_path / "a.pdf", 2)
    renderer.render(pdf, SIZE)
    assert renderer.wait_until_idle()

    bigger = QSize(120, 160)
    renderer.render(pdf, bigger)
    assert renderer._pending == {1, 2}
    assert renderer.wait_until_idle()
    assert renderer.cache_size == 4  # two pages at two sizes
    assert renderer._list.item(0).icon().actualSize(bigger) == bigger

    renderer.render(pdf, SIZE)  # back to the first zoom: served from cache
    assert renderer.is_idle


def test_the_cache_is_bounded(qapp: QApplication) -> None:
    """Without a byte budget a 500-page document at the 720x960 zoom
    ceiling would hold ~1.4 GB of pixmaps. Exercised against PixmapCache
    directly - it is the unit that owns eviction, and constructing it
    with a real budget is honest about what is under test."""
    from gui.rendering import PixmapCache

    page = QPixmap(QSize(60, 80))  # 60*80*4 = 19_200 bytes
    cache = PixmapCache(budget_bytes=48_000)  # room for 2 pages, not 3
    for n in range(1, 6):
        cache.put((n, 60, 80), page)

    assert len(cache) == 2, "cache grew past its budget"
    # LRU: the most recent survivors, the oldest evicted.
    assert cache.get((5, 60, 80)) is not None
    assert cache.get((1, 60, 80)) is None


def test_the_cache_evicts_least_recently_used_first(qapp: QApplication) -> None:
    from gui.rendering import PixmapCache

    page = QPixmap(QSize(60, 80))
    cache = PixmapCache(budget_bytes=48_000)
    cache.put((1, 60, 80), page)
    cache.put((2, 60, 80), page)
    cache.get((1, 60, 80))  # touch 1, making 2 the least recently used
    cache.put((3, 60, 80), page)

    assert cache.get((1, 60, 80)) is not None
    assert cache.get((2, 60, 80)) is None


def test_release_is_idempotent_and_drops_the_document(
    renderer: ThumbnailRenderer, tmp_path: Path
) -> None:
    """The renderer holds an open QPdfDocument on the working file;
    Windows refuses to overwrite or unlink an open file, so the secure
    wipe depends on this being released first."""
    pdf = _make_pdf(tmp_path / "a.pdf", 2)
    renderer.render(pdf, SIZE)
    assert renderer.wait_until_idle()
    assert renderer._pdf is not None

    renderer.release()
    assert renderer._pdf is None
    assert renderer._renderer is None
    renderer.release()  # idempotent
    assert renderer._pdf is None


def test_releasing_with_a_render_in_flight_does_not_crash(
    renderer: ThumbnailRenderer, tmp_path: Path
) -> None:
    """A tab can be closed while its pages are still rendering on the
    renderer's worker thread."""
    pdf = _make_pdf(tmp_path / "a.pdf", 12)
    renderer.render(pdf, SIZE)
    assert not renderer.is_idle  # genuinely still pending
    renderer.release()
    QApplication.processEvents()
    assert renderer._pdf is None


def test_an_unloadable_file_leaves_an_empty_grid(
    renderer: ThumbnailRenderer, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    renderer.render(broken, SIZE)
    assert renderer._list.count() == 0
    assert renderer.is_idle


# --- Operation.affected_pages() -------------------------------------------


def test_page_preserving_operations_report_only_their_target_pages() -> None:
    assert RotatePagesOperation(angle=90, pages=[2, 5]).affected_pages() == [2, 5]


def test_an_empty_pages_list_means_every_page() -> None:
    """Empty means "all pages" throughout this codebase, which is
    exactly the base class's None ("unknown - assume all")."""
    assert RotatePagesOperation(angle=90).affected_pages() is None


def test_operations_that_change_page_count_report_unknown() -> None:
    """Deleting a page shifts every later page's identity, so cached
    thumbnails for pages it never touched are wrong too."""
    assert DeletePagesOperation(pages=[1]).affected_pages() is None
