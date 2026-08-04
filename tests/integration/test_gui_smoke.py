"""Headless smoke tests for the GUI (SPEC.md Phase 1: "basic thumbnail
UI + undo/redo wired to the framework").

Runs under QT_QPA_PLATFORM=offscreen so it works without a display
server (set here, defensively, in case the environment hasn't already
- doesn't override a real display if one's configured).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pikepdf
import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from gui.dialogs.bates_numbering_dialog import BatesNumberingDialog
from gui.dialogs.crop_dialog import CropDialog
from gui.dialogs.fill_form_dialog import FillFormDialog
from gui.dialogs.merge_dialog import MergeDialog
from gui.dialogs.rotate_dialog import RotateDialog
from gui.dialogs.sign_dialog import SignDialog
from gui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    # Qt allows exactly one QApplication per process - shared across
    # every test in this module/session.
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_app_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path / "appdata"))


def _make_pdf(path: Path, num_pages: int) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(300, 400))
    pdf.save(path)
    return path


def test_starts_on_empty_state_with_branded_title(qapp: QApplication) -> None:
    window = MainWindow()
    assert window.windowTitle() == "Rad PDF Editor"
    assert window.stack.currentWidget() is window.empty_state
    window.close()


def test_opening_a_document_switches_to_thumbnail_view(qapp: QApplication, tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 2)
    window = MainWindow()

    window.controller.open_document(src)
    window._refresh()

    assert window.stack.currentWidget() is window.thumbnail_list
    window.close()


def test_closing_document_returns_to_empty_state(qapp: QApplication, tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    window.controller.close_session()
    window._refresh()

    assert window.stack.currentWidget() is window.empty_state
    assert window.windowTitle() == "Rad PDF Editor"
    window.close()


def test_open_render_undo_redo_save_close(qapp: QApplication, tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "src.pdf", 3)
    window = MainWindow()

    assert window.thumbnail_list.count() == 0
    assert not window.undo_action.isEnabled()

    window.controller.open_document(src)
    window._refresh()
    assert window.thumbnail_list.count() == 3
    assert "src.pdf" in window.windowTitle()

    window.controller.apply_operation(RotatePagesOperation(angle=90))
    window._refresh()
    assert window.undo_action.isEnabled()

    out = tmp_path / "out.pdf"
    window.controller.save_as(out)
    with pikepdf.Pdf.open(out) as pdf:
        assert len(pdf.pages) == 3
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90

    window.controller.undo()
    window._refresh()
    assert not window.undo_action.isEnabled()
    assert window.redo_action.isEnabled()

    working_dir = window.controller.doc.working_path.parent
    window.controller.close_session()
    window.close()
    assert not working_dir.exists()


def test_dragging_a_thumbnail_reorders_the_document(qapp: QApplication, tmp_path: Path) -> None:
    # model().moveRow(...) triggers the exact same rowsMoved signal a
    # real mouse drag-and-drop would - InternalMove drag gestures
    # aren't reliably simulatable headlessly, but this exercises the
    # real signal-handling code path, not a hand-rolled substitute.
    src = _make_pdf(tmp_path / "src.pdf", 4)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    moved = window.thumbnail_list.model().moveRow(QModelIndex(), 3, QModelIndex(), 0)
    assert moved
    QTest.qWait(50)  # let the QTimer.singleShot(0, ...) deferral run

    assert len(window.controller.doc.operation_log) == 1
    applied = window.controller.doc.operation_log[-1].serialize()
    assert applied["type"] == "reorder_pages"
    assert applied["page_order"] == [4, 1, 2, 3]
    assert window.undo_action.isEnabled()
    with pikepdf.Pdf.open(window.controller.doc.working_path) as pdf:
        assert len(pdf.pages) == 4
    window.controller.close_session()
    window.close()


def test_tool_actions_disabled_without_open_document_except_merge(qapp: QApplication) -> None:
    window = MainWindow()
    assert not window.tool_actions["rotate_pages"].isEnabled()
    assert not window.tool_actions["watermark"].isEnabled()
    assert window.tool_actions["merge"].isEnabled()
    window.close()


def test_merge_from_tools_menu_opens_a_document(qapp: QApplication, tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf", 1)
    b = _make_pdf(tmp_path / "b.pdf", 2)
    window = MainWindow()

    def fake_exec(self: MergeDialog) -> QDialog.DialogCode:
        self.file_list.addItems([str(a), str(b)])
        return QDialog.DialogCode.Accepted

    with patch.object(MergeDialog, "exec", fake_exec):
        window._run_tool("merge", MergeDialog)

    assert window.controller.is_open
    assert window.thumbnail_list.count() == 3
    assert window.tool_actions["rotate_pages"].isEnabled()
    window.controller.close_session()
    window.close()


def test_run_tool_applies_operation_via_dialog_values(qapp: QApplication, tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    def fake_exec(self: RotateDialog) -> QDialog.DialogCode:
        self.angle.setCurrentText("180")
        return QDialog.DialogCode.Accepted

    with patch.object(RotateDialog, "exec", fake_exec):
        window._run_tool("rotate_pages", RotateDialog)

    with pikepdf.Pdf.open(window.controller.doc.working_path) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 180
    window.controller.close_session()
    window.close()


def test_cancelling_a_tool_dialog_leaves_document_unchanged(qapp: QApplication, tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()
    ops_before = len(window.controller.doc.operation_log)

    def fake_cancel(self: RotateDialog) -> QDialog.DialogCode:
        return QDialog.DialogCode.Rejected

    with patch.object(RotateDialog, "exec", fake_cancel):
        window._run_tool("rotate_pages", RotateDialog)

    assert len(window.controller.doc.operation_log) == ops_before
    window.close()


def test_running_a_tool_without_open_document_shows_error_not_crash(
    qapp: QApplication,
) -> None:
    window = MainWindow()
    with patch("gui.main_window.QMessageBox.critical") as mock_critical:
        window._run_tool("rotate_pages", RotateDialog)
    mock_critical.assert_called_once()
    window.close()


def test_phase2_tools_are_all_registered_in_the_menu(qapp: QApplication) -> None:
    window = MainWindow()
    for tool_id in (
        "crop",
        "resize",
        "n_up",
        "grayscale",
        "header_footer",
        "bates_numbering",
        "flatten",
        "remove_annotations",
        "fill_form",
        "sign",
    ):
        assert tool_id in window.tool_actions
    window.close()


def test_crop_then_bates_numbering_via_tools_menu(qapp: QApplication, tmp_path: Path) -> None:
    import pdfplumber

    src = _make_pdf(tmp_path / "src.pdf", 2)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    def fake_crop(self: CropDialog) -> QDialog.DialogCode:
        self.margin_top.setValue(20)
        self.margin_left.setValue(10)
        return QDialog.DialogCode.Accepted

    with patch.object(CropDialog, "exec", fake_crop):
        window._run_tool("crop", CropDialog)

    with pikepdf.Pdf.open(window.controller.doc.working_path) as pdf:
        assert [float(x) for x in pdf.pages[0].mediabox] == [10.0, 0.0, 300.0, 380.0]

    def fake_bates(self: BatesNumberingDialog) -> QDialog.DialogCode:
        self.prefix.setText("DOC-")
        return QDialog.DialogCode.Accepted

    with patch.object(BatesNumberingDialog, "exec", fake_bates):
        window._run_tool("bates_numbering", BatesNumberingDialog)

    with pdfplumber.open(window.controller.doc.working_path) as pdf:
        assert pdf.pages[0].extract_text() == "DOC-00001"
        assert pdf.pages[1].extract_text() == "DOC-00002"

    assert len(window.controller.doc.operation_log) == 2
    assert window.undo_action.isEnabled()
    window.controller.close_session()
    window.close()


def _make_pdf_with_text_field(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(300, 400))
    field = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/FT": pikepdf.Name("/Tx"),
                "/T": pikepdf.String("name"),
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


def test_fill_form_via_tools_menu_uses_detected_field_names(
    qapp: QApplication, tmp_path: Path
) -> None:
    import fitz

    src = _make_pdf_with_text_field(tmp_path / "form.pdf")
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    def fake_fill(self: FillFormDialog) -> QDialog.DialogCode:
        assert "name" in self._inputs
        self._inputs["name"].setText("Jane Smith")
        return QDialog.DialogCode.Accepted

    with patch.object(FillFormDialog, "exec", fake_fill):
        window._run_tool("fill_form", None)

    with fitz.open(window.controller.doc.working_path) as pdf:
        assert "Jane Smith" in pdf[0].get_text()
    window.controller.close_session()
    window.close()


def test_sign_via_tools_menu_places_image(qapp: QApplication, tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    src = _make_pdf(tmp_path / "src.pdf", 1)
    sig = tmp_path / "sig.png"
    img = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    ImageDraw.Draw(img).line((10, 60, 190, 20), fill=(0, 0, 200, 255), width=6)
    img.save(sig)

    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    def fake_sign(self: SignDialog) -> QDialog.DialogCode:
        self._image_path = sig
        self.page.setValue(1)
        self.x0.setValue(50)
        self.y0.setValue(50)
        self.x1.setValue(250)
        self.y1.setValue(130)
        return QDialog.DialogCode.Accepted

    with patch.object(SignDialog, "exec", fake_sign):
        window._run_tool("sign", SignDialog)

    with pikepdf.Pdf.open(window.controller.doc.working_path) as pdf:
        assert len(pdf.pages) == 1
    assert window.undo_action.isEnabled()
    window.controller.close_session()
    window.close()


def test_refresh_does_not_leak_qpdfdocument_instances(qapp: QApplication, tmp_path: Path) -> None:
    # Regression: _render_thumbnails used to parent its throwaway
    # QPdfDocument to `self` (MainWindow), so every _refresh() (every
    # applied operation, undo, or redo) leaked one instance for the
    # life of the window instead of being freed after rendering.
    from PySide6.QtPdf import QPdfDocument

    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    before = len([c for c in window.children() if isinstance(c, QPdfDocument)])
    for _ in range(5):
        window.controller.apply_operation(RotatePagesOperation(angle=90))
        window._refresh()
    after = len([c for c in window.children() if isinstance(c, QPdfDocument)])

    assert after == before
    window.controller.close_session()
    window.close()


def test_reordering_to_the_same_order_does_not_record_a_no_op_operation(
    qapp: QApplication, tmp_path: Path
) -> None:
    # Regression: dragging (or a duplicate rowsMoved signal for the
    # same gesture) that results in the identity order previously
    # still pushed a no-op ReorderPagesOperation onto the undo stack.
    src = _make_pdf(tmp_path / "src.pdf", 3)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()
    ops_before = len(window.controller.doc.operation_log)

    for i in range(window.thumbnail_list.count()):
        window.thumbnail_list.item(i).setData(Qt.ItemDataRole.UserRole, i + 1)
    window._apply_thumbnail_reorder()

    assert len(window.controller.doc.operation_log) == ops_before
    window.close()


def test_closing_a_clean_document_does_not_prompt(qapp: QApplication, tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    with patch.object(QMessageBox, "warning") as mock_warning:
        window._close_document()

    mock_warning.assert_not_called()
    assert not window.controller.is_open
    window.close()


def test_closing_a_dirty_document_prompts_and_cancel_keeps_it_open(
    qapp: QApplication, tmp_path: Path
) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window.controller.apply_operation(RotatePagesOperation(angle=90))
    window._refresh()

    with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel):
        window._close_document()

    assert window.controller.is_open
    # window.close() alone would hang here: the document is still
    # (correctly) dirty, so it'd trigger a second, unmocked closeEvent
    # -> a real modal QMessageBox.warning() blocking forever
    # headlessly. Clear the session directly first, then close() is
    # safe (nothing left to prompt about).
    window.controller.close_session()
    window.close()


def test_closing_a_dirty_document_discard_closes_it(qapp: QApplication, tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window.controller.apply_operation(RotatePagesOperation(angle=90))
    window._refresh()

    with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Discard):
        window._close_document()

    assert not window.controller.is_open
    window.close()


def test_window_close_event_is_ignored_when_user_cancels_unsaved_prompt(
    qapp: QApplication, tmp_path: Path
) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window.controller.apply_operation(RotatePagesOperation(angle=90))
    window._refresh()

    event = QCloseEvent()
    with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel):
        window.closeEvent(event)

    assert not event.isAccepted()
    assert window.controller.is_open
    # See the comment in test_closing_a_dirty_document_prompts_and_cancel_keeps_it_open
    # - window.close() alone here would hang on a real, unmocked prompt.
    window.controller.close_session()
    window.close()
