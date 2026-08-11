"""Unit tests for the interactive signature-placement canvas
(`gui/placement_canvas.py`) and `SignDialog`'s use of it.

What actually matters here is the coordinate round trip. The canvas
works in scene pixels with a top-left origin (Qt's convention) while
`SignOperation` - like Crop/Resize/Watermark/HeaderFooter - takes PDF
points with a *bottom-left* origin, so every placement crosses a y-flip
and a render-scale division. These tests pin the exact numbers for a
page of known size rather than asserting "some plausible rect".

Real mouse drag gestures aren't reliably simulatable under
QT_QPA_PLATFORM=offscreen (documented twice in CLAUDE.md - the
thumbnail-reorder test calls `model().moveRow(...)` and the tab-reorder
test calls `QTabBar.moveTab(...)` for the same reason). So the drag
tests here construct real `QGraphicsSceneMouseEvent`s and hand them to
the item's own press/move/release handlers: that exercises the genuine
hit-testing and drag maths, just without a synthetic pointer.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pikepdf
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsSceneMouseEvent

from gui.dialogs.sign_dialog import SignDialog
from gui.placement_canvas import PagePlacementCanvas, PlacementItem

_PAGE_WIDTH_PT = 300.0
_PAGE_HEIGHT_PT = 400.0


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_pdf(path: Path, num_pages: int = 1) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(_PAGE_WIDTH_PT, _PAGE_HEIGHT_PT))
    pdf.save(path)
    return path


def _canvas(path: Path) -> PagePlacementCanvas:
    canvas = PagePlacementCanvas()
    assert canvas.load_document(path)
    canvas.show_page(1)
    return canvas


def _mouse_event(
    event_type: QGraphicsSceneMouseEvent.Type, pos: QPointF
) -> QGraphicsSceneMouseEvent:
    event = QGraphicsSceneMouseEvent(event_type)
    # Item and scene coordinates are identical for a PlacementItem: it
    # keeps pos() pinned at scene (0, 0) by design.
    event.setPos(pos)
    event.setScenePos(pos)
    event.setButton(Qt.MouseButton.LeftButton)
    return event


def _drag(item: PlacementItem, start: QPointF, end: QPointF) -> None:
    types = QGraphicsSceneMouseEvent.Type
    item.mousePressEvent(_mouse_event(types.GraphicsSceneMousePress, start))
    item.mouseMoveEvent(_mouse_event(types.GraphicsSceneMouseMove, end))
    item.mouseReleaseEvent(_mouse_event(types.GraphicsSceneMouseRelease, end))


# --- the render scale the rest of the maths depends on -------------------


def test_page_renders_at_the_expected_scale(qapp: QApplication, tmp_path: Path) -> None:
    """A 300x400 pt page has a 400 pt long edge and the canvas targets
    800 scene px on the long edge, so the scale is exactly 2.0 and the
    scene is 600x800 px. Every hand-computed number below depends on
    this, so it's asserted first rather than assumed."""
    canvas = _canvas(_make_pdf(tmp_path / "src.pdf"))
    assert canvas.page_size_points().width() == _PAGE_WIDTH_PT
    assert canvas.page_size_points().height() == _PAGE_HEIGHT_PT
    assert canvas.scene().sceneRect() == QRectF(0, 0, 600, 800)


# --- coordinate round trip ------------------------------------------------


def test_scene_rect_converts_to_the_exact_bottom_left_origin_pdf_rect(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Worked through by hand for a 300x400 pt page at scale 2.0.

    Overlay at scene (100, 120), 200x80 px, y measured downward from
    the page's top edge:

        x0 = 100 / 2                    =  50
        x1 = (100 + 200) / 2            = 150
        y1 = 400 - (120 / 2)            = 340   (top edge, flipped)
        y0 = 400 - ((120 + 80) / 2)     = 300   (bottom edge, flipped)

    so the PDF rect must be exactly (50, 300, 150, 340) - note the
    *top* of the on-screen box becomes y1 and the *bottom* becomes y0,
    which is the flip a naive conversion gets backwards.
    """
    canvas = _canvas(_make_pdf(tmp_path / "src.pdf"))
    item = canvas.placement_item()
    assert item is not None

    item.set_rect(QRectF(100, 120, 200, 80))

    assert canvas.pdf_rect() == (50.0, 300.0, 150.0, 340.0)


def test_pdf_rect_round_trips_back_through_set_pdf_rect(
    qapp: QApplication, tmp_path: Path
) -> None:
    canvas = _canvas(_make_pdf(tmp_path / "src.pdf"))
    item = canvas.placement_item()
    assert item is not None

    canvas.set_pdf_rect((50.0, 300.0, 150.0, 340.0))

    # The same scene rect the previous test started from - the two
    # conversions are genuine inverses, not just individually plausible.
    assert item.rect() == QRectF(100, 120, 200, 80)
    assert canvas.pdf_rect() == (50.0, 300.0, 150.0, 340.0)


def test_a_rect_at_the_page_origin_maps_to_the_bottom_left_corner(
    qapp: QApplication, tmp_path: Path
) -> None:
    """The single most likely place for a flip bug: PDF (0, 0) is the
    *bottom*-left of the page, so it must land at the *bottom* of the
    scene (y = 800), not the top."""
    canvas = _canvas(_make_pdf(tmp_path / "src.pdf"))
    item = canvas.placement_item()
    assert item is not None

    canvas.set_pdf_rect((0.0, 0.0, 100.0, 50.0))

    assert item.rect() == QRectF(0, 700, 200, 100)


# --- real interaction -----------------------------------------------------


def test_dragging_the_body_moves_the_placement(qapp: QApplication, tmp_path: Path) -> None:
    canvas = _canvas(_make_pdf(tmp_path / "src.pdf"))
    item = canvas.placement_item()
    assert item is not None
    item.set_rect(QRectF(100, 120, 200, 80))

    # Grab the middle of the box (not a handle) and move it 20 px right
    # and 20 px *up* the screen.
    _drag(item, QPointF(200, 160), QPointF(220, 140))

    assert item.rect() == QRectF(120, 100, 200, 80)
    # Screen-up is PDF-y-up too, so both y values grow by 20/2 = 10 pt.
    assert canvas.pdf_rect() == (60.0, 310.0, 160.0, 350.0)


def test_dragging_a_corner_handle_resizes_the_placement(
    qapp: QApplication, tmp_path: Path
) -> None:
    canvas = _canvas(_make_pdf(tmp_path / "src.pdf"))
    item = canvas.placement_item()
    assert item is not None
    item.set_rect(QRectF(100, 120, 200, 80))

    # The bottom-right handle is centred on the box's bottom-right
    # corner - hit-testing must pick it over the body it overlaps.
    assert item.handle_at(QPointF(300, 200)) == "br"
    _drag(item, QPointF(300, 200), QPointF(340, 240))

    assert item.rect() == QRectF(100, 120, 240, 120)
    # Only x1 and y0 move: the on-screen bottom edge is the PDF y0.
    assert canvas.pdf_rect() == (50.0, 280.0, 170.0, 340.0)


def test_the_body_is_not_grabbed_from_outside_the_box(
    qapp: QApplication, tmp_path: Path
) -> None:
    canvas = _canvas(_make_pdf(tmp_path / "src.pdf"))
    item = canvas.placement_item()
    assert item is not None
    item.set_rect(QRectF(100, 120, 200, 80))

    _drag(item, QPointF(500, 500), QPointF(520, 520))

    assert item.rect() == QRectF(100, 120, 200, 80)


def test_a_drag_cannot_push_the_placement_off_the_page(
    qapp: QApplication, tmp_path: Path
) -> None:
    canvas = _canvas(_make_pdf(tmp_path / "src.pdf"))
    item = canvas.placement_item()
    assert item is not None
    item.set_rect(QRectF(100, 120, 200, 80))

    _drag(item, QPointF(200, 160), QPointF(5000, 5000))

    # Pinned against the page's bottom-right corner, same size.
    assert item.rect() == QRectF(400, 720, 200, 80)
    x0, y0, x1, y1 = canvas.pdf_rect() or (0.0, 0.0, 0.0, 0.0)
    assert (x1, y0) == (_PAGE_WIDTH_PT, 0.0)


def test_a_handle_drag_cannot_invert_the_rect(qapp: QApplication, tmp_path: Path) -> None:
    canvas = _canvas(_make_pdf(tmp_path / "src.pdf"))
    item = canvas.placement_item()
    assert item is not None
    item.set_rect(QRectF(100, 120, 200, 80))

    # Yank the bottom-right handle far past the top-left corner.
    _drag(item, QPointF(300, 200), QPointF(0, 0))

    rect = item.rect()
    assert rect.width() > 0 and rect.height() > 0
    x0, y0, x1, y1 = canvas.pdf_rect() or (0.0, 0.0, 0.0, 0.0)
    # SignOperation rejects a degenerate rect outright, so the canvas
    # must never be able to produce one.
    assert x1 > x0 and y1 > y0


# --- dialog wiring --------------------------------------------------------


def test_dialog_without_a_document_has_no_canvas(qapp: QApplication) -> None:
    """The Workflow builder constructs SignDialog with a parent only -
    there is no document to preview, and manual numeric entry has to
    keep working on its own."""
    dialog = SignDialog()
    assert dialog.canvas is None
    assert dialog._rect_values() == (0.0, 0.0, 200.0, 80.0)


def test_dialog_values_follow_a_canvas_drag(qapp: QApplication, tmp_path: Path) -> None:
    dialog = SignDialog(None, _make_pdf(tmp_path / "src.pdf"))
    dialog.set_image_path(_make_signature_image(tmp_path / "sig.png"))
    canvas = dialog.canvas
    assert canvas is not None
    item = canvas.placement_item()
    assert item is not None

    item.set_rect(QRectF(100, 120, 200, 80))
    canvas.rect_changed.emit()

    assert dialog._rect_values() == (50.0, 300.0, 150.0, 340.0)
    assert dialog.values()["rect"] == (50.0, 300.0, 150.0, 340.0)


def test_typing_into_the_spin_boxes_moves_the_canvas_overlay(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Manual entry is a first-class input, not a vestige: editing a
    spin box has to drive the preview, and must not feed back into
    itself."""
    dialog = SignDialog(None, _make_pdf(tmp_path / "src.pdf"))
    canvas = dialog.canvas
    assert canvas is not None
    item = canvas.placement_item()
    assert item is not None

    dialog.x0.setValue(50)
    dialog.y0.setValue(300)
    dialog.x1.setValue(150)
    dialog.y1.setValue(340)

    assert item.rect() == QRectF(100, 120, 200, 80)
    assert dialog._rect_values() == (50.0, 300.0, 150.0, 340.0)


def test_switching_page_keeps_the_placement_and_rerenders(
    qapp: QApplication, tmp_path: Path
) -> None:
    dialog = SignDialog(None, _make_pdf(tmp_path / "src.pdf", num_pages=3))
    canvas = dialog.canvas
    assert canvas is not None
    assert dialog.page.maximum() == 3

    first_item = canvas.placement_item()
    assert first_item is not None
    first_item.set_rect(QRectF(100, 120, 200, 80))
    canvas.rect_changed.emit()

    dialog.page.setValue(3)

    second_item = canvas.placement_item()
    assert second_item is not None
    # A genuinely new item on a freshly rendered page, carrying the
    # same placement in PDF terms.
    assert second_item is not first_item
    assert canvas.pdf_rect() == (50.0, 300.0, 150.0, 340.0)
    assert dialog.page.value() == 3


def test_defaults_are_clamped_to_a_page_smaller_than_the_default_rect(
    qapp: QApplication, tmp_path: Path
) -> None:
    """The numeric defaults (0, 0, 200, 80) are wider than a tiny page,
    and a rect hanging off the page would render half-missing."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(120, 120))
    path = tmp_path / "small.pdf"
    pdf.save(path)

    dialog = SignDialog(None, path)
    assert dialog.canvas is not None
    x0, y0, x1, y1 = dialog._rect_values()
    assert (x0, y0, x1, y1) == (0.0, 0.0, 120.0, 80.0)


def _make_signature_image(path: Path, size: tuple[int, int] = (200, 80)) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).line(
        (5, size[1] - 10, size[0] - 5, 10), fill=(0, 0, 200, 255), width=6
    )
    img.save(path)
    return path


# --- the preview matches the file it produces -----------------------------


def _actual_image_bbox(pdf_path: Path, rect: tuple[float, float, float, float]) -> tuple:
    """Apply a real SignOperation and report where fitz says the image
    actually landed (fitz's own bbox is top-left origin)."""
    import fitz

    from core.model.document import DocumentSession
    from core.ops.forms import SignOperation

    doc = DocumentSession(working_path=pdf_path, source_path=None)
    image_path = pdf_path.with_name("sig_for_bbox.png")
    result = doc.apply(SignOperation(image_path=image_path, page=1, rect=rect))
    with fitz.open(result.working_path) as out:
        info = out[0].get_image_info()
        assert len(info) == 1
        return tuple(info[0]["bbox"])


@pytest.mark.parametrize(
    ("image_size", "expect_stretched"),
    [
        ((200, 80), False),
        # PyMuPDF ignores its own keep_proportion default for an exactly
        # square image - see PlacementItem._stretches_to_fill.
        ((100, 100), True),
    ],
)
def test_the_preview_predicts_where_the_image_really_lands(
    qapp: QApplication,
    tmp_path: Path,
    image_size: tuple[int, int],
    expect_stretched: bool,
) -> None:
    """The canvas claims to be WYSIWYG, so its prediction is checked
    against the actual output file, not merely against itself."""
    path = _make_pdf(tmp_path / "src.pdf")
    _make_signature_image(tmp_path / "sig_for_bbox.png", image_size)

    canvas = _canvas(path)
    item = canvas.placement_item()
    assert item is not None
    item.set_pixmap(QPixmap(str(tmp_path / "sig_for_bbox.png")))
    # A deliberately wrong-shaped box: 200x160 scene px is 100x80 pt,
    # aspect 1.25, which matches neither test image - so a correctly
    # fitted image must end up *smaller* than the box it's placed in.
    item.set_rect(QRectF(100, 120, 200, 160))

    placement = canvas.pdf_rect()
    predicted = canvas.image_pdf_rect()
    assert placement is not None and predicted is not None
    assert (predicted == placement) is expect_stretched

    # Convert the canvas's bottom-left-origin prediction into fitz's
    # top-left-origin frame to compare with what fitz reports.
    px0, py0, px1, py1 = predicted
    expected_fitz_bbox = (
        px0,
        _PAGE_HEIGHT_PT - py1,
        px1,
        _PAGE_HEIGHT_PT - py0,
    )
    assert _actual_image_bbox(path, placement) == pytest.approx(expected_fitz_bbox, abs=0.05)
