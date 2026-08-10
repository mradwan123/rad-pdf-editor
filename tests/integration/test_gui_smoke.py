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
from PySide6.QtCore import QModelIndex, QPoint, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

from gui.dialogs.bates_numbering_dialog import BatesNumberingDialog
from gui.dialogs.create_form_field_dialog import CreateFormFieldDialog
from gui.dialogs.crop_dialog import CropDialog
from gui.dialogs.fill_form_dialog import FillFormDialog
from gui.dialogs.merge_dialog import MergeDialog
from gui.dialogs.rotate_dialog import RotateDialog
from gui.dialogs.run_workflow_dialog import RunWorkflowDialog
from gui.dialogs.sign_dialog import SignDialog
from gui.dialogs.workflow_builder_dialog import WorkflowBuilderDialog
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
        "create_form_field",
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


def test_create_form_field_via_tools_menu_adds_a_text_field(
    qapp: QApplication, tmp_path: Path
) -> None:
    import fitz

    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    def fake_create(self: CreateFormFieldDialog) -> QDialog.DialogCode:
        self.field_name.setText("full_name")
        self.field_type.setCurrentText("text")
        self.page.setValue(1)
        self.x0.setValue(50)
        self.y0.setValue(300)
        self.x1.setValue(250)
        self.y1.setValue(320)
        self.default_value.setText("Jane Doe")
        return QDialog.DialogCode.Accepted

    with patch.object(CreateFormFieldDialog, "exec", fake_create):
        window._run_tool("create_form_field", CreateFormFieldDialog)

    with fitz.open(window.controller.doc.working_path) as pdf:
        widgets = list(pdf[0].widgets())
    assert len(widgets) == 1
    assert widgets[0].field_name == "full_name"
    assert widgets[0].field_value == "Jane Doe"
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


# --- recent files -----------------------------------------------------------


def test_opening_a_document_adds_it_to_recent_files(qapp: QApplication, tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()

    window._open_document_path(src)

    assert window.recent_files.list() == [src]
    window.controller.close_session()
    window.close()


def test_recent_files_menu_lists_most_recent_first_and_reopens_on_click(
    qapp: QApplication, tmp_path: Path
) -> None:
    a = _make_pdf(tmp_path / "a.pdf", 1)
    b = _make_pdf(tmp_path / "b.pdf", 2)
    window = MainWindow()
    window._open_document_path(a)
    window._open_document_path(b)

    window._populate_recent_files_menu()
    actions = window.recent_files_menu.actions()
    # newest first, then a separator, then "Clear Recent Files"
    assert [a.text() for a in actions[:2]] == ["b.pdf", "a.pdf"]

    actions[1].trigger()  # reopen a.pdf

    assert window.controller.doc.source_path == a
    window.controller.close_session()
    window.close()


def test_recent_files_menu_shows_placeholder_when_empty(qapp: QApplication) -> None:
    window = MainWindow()

    window._populate_recent_files_menu()

    actions = window.recent_files_menu.actions()
    assert len(actions) == 1
    assert not actions[0].isEnabled()
    window.close()


def test_opening_a_stale_recent_file_shows_error_and_drops_it_from_the_list(
    qapp: QApplication, tmp_path: Path
) -> None:
    missing = tmp_path / "gone.pdf"
    window = MainWindow()
    window.recent_files.add(missing)

    with patch.object(QMessageBox, "critical") as mock_critical:
        window._open_recent_file(missing)

    mock_critical.assert_called_once()
    assert window.recent_files.list() == []
    window.close()


def test_clear_recent_files_empties_the_list(qapp: QApplication, tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window._open_document_path(src)
    assert window.recent_files.list() == [src]

    window.recent_files.clear()

    assert window.recent_files.list() == []
    window.controller.close_session()
    window.close()


def test_opening_a_recent_file_over_a_dirty_document_prompts_first(
    qapp: QApplication, tmp_path: Path
) -> None:
    from core.ops.organize import RotatePagesOperation

    a = _make_pdf(tmp_path / "a.pdf", 1)
    b = _make_pdf(tmp_path / "b.pdf", 1)
    window = MainWindow()
    window._open_document_path(a)
    window.controller.apply_operation(RotatePagesOperation(angle=90))
    window.recent_files.add(b)

    with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel):
        window._open_recent_file(b)

    assert window.controller.doc.source_path == a
    window.controller.close_session()
    window.close()


# --- thumbnail context menu --------------------------------------------------
#
# QMenu.exec() is a native/compiled PySide6 method - unlike the
# project's own BaseToolDialog subclasses (a real Python class, so
# `patch.object(SomeDialog, "exec", fake)` genuinely overrides it),
# patching it the same way does NOT intercept the call: menu.exec(pos)
# still runs the real blocking modal popup, which hangs forever with
# no headless UI to click through (confirmed - hung pytest, had to
# kill -9 the stuck process). So these tests exercise the actual
# operation-applying logic directly (_apply_thumbnail_rotate /
# _apply_thumbnail_delete) rather than trying to drive it through a
# faked context-menu popup.


def test_thumbnail_context_menu_rotate_right_rotates_selected_pages_only(
    qapp: QApplication, tmp_path: Path
) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 3)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    window._apply_thumbnail_rotate([2], angle=90)

    with pikepdf.Pdf.open(window.controller.doc.working_path) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 0
        assert int(pdf.pages[1].get("/Rotate", 0)) == 90
        assert int(pdf.pages[2].get("/Rotate", 0)) == 0
    window.controller.close_session()
    window.close()


def test_thumbnail_context_menu_rotate_left_uses_negative_angle(
    qapp: QApplication, tmp_path: Path
) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    window._apply_thumbnail_rotate([1], angle=-90)

    with pikepdf.Pdf.open(window.controller.doc.working_path) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 270
    window.controller.close_session()
    window.close()


def test_thumbnail_context_menu_delete_removes_selected_pages(
    qapp: QApplication, tmp_path: Path
) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 3)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    window._apply_thumbnail_delete([1, 3])

    with pikepdf.Pdf.open(window.controller.doc.working_path) as pdf:
        assert len(pdf.pages) == 1
    window.controller.close_session()
    window.close()


def test_thumbnail_context_menu_does_nothing_without_a_selection(
    qapp: QApplication, tmp_path: Path
) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()
    assert window.thumbnail_list.selectedItems() == []

    with patch.object(QMenu, "exec") as mock_exec:
        window._show_thumbnail_context_menu(QPoint(0, 0))

    mock_exec.assert_not_called()
    window.controller.close_session()
    window.close()


# --- busy-cursor feedback ----------------------------------------------------


def test_busy_cursor_sets_wait_cursor_and_restores_it_after(qapp: QApplication) -> None:
    window = MainWindow()
    assert QApplication.overrideCursor() is None

    with window._busy_cursor():
        assert QApplication.overrideCursor() is not None
        assert QApplication.overrideCursor().shape() == Qt.CursorShape.WaitCursor

    assert QApplication.overrideCursor() is None
    window.close()


def test_busy_cursor_restores_cursor_even_on_exception(qapp: QApplication) -> None:
    window = MainWindow()

    with pytest.raises(RuntimeError), window._busy_cursor():
        raise RuntimeError("boom")

    assert QApplication.overrideCursor() is None
    window.close()


def test_running_a_tool_leaves_no_override_cursor_set_afterward(
    qapp: QApplication, tmp_path: Path
) -> None:
    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    def fake_exec(self: RotateDialog) -> QDialog.DialogCode:
        self.angle.setCurrentText("180")
        return QDialog.DialogCode.Accepted

    with patch.object(RotateDialog, "exec", fake_exec):
        window._run_tool("rotate_pages", RotateDialog)

    assert QApplication.overrideCursor() is None
    window.controller.close_session()
    window.close()


# --- Phase 5: Workflow builder + run --------------------------------------


def test_workflows_menu_actions_exist_and_are_enabled_without_a_document(
    qapp: QApplication,
) -> None:
    # Building/running a workflow is document-independent by design -
    # unlike every tool in tool_actions, these two aren't gated on
    # is_open.
    window = MainWindow()
    assert window.build_workflow_action.isEnabled()
    assert window.run_workflow_action.isEnabled()
    window.close()


def test_build_workflow_saves_a_real_pipeline(qapp: QApplication) -> None:
    from core.registry.registry import Registry, discover_and_load
    from core.session.workflow_store import WorkflowStore

    window = MainWindow()
    registry = Registry()
    discover_and_load(registry)
    rotate_op = registry.get("rotate_pages").build_operation(angle=90, pages=[])

    def fake_exec(self: WorkflowBuilderDialog) -> QDialog.DialogCode:
        self.name_edit.setText("gui_test_workflow")
        self._operations.append(rotate_op)
        self.step_list.addItem(rotate_op.describe())
        return QDialog.DialogCode.Accepted

    with patch.object(WorkflowBuilderDialog, "exec", fake_exec):
        window._build_workflow()

    assert "gui_test_workflow" in WorkflowStore().list_workflows()
    window.close()


def test_workflow_builder_add_step_excludes_fill_form(qapp: QApplication) -> None:
    from core.registry.registry import Registry, discover_and_load

    registry = Registry()
    discover_and_load(registry)
    dialog = WorkflowBuilderDialog(registry)

    with patch(
        "gui.dialogs.workflow_builder_dialog.QInputDialog.getItem", return_value=("", False)
    ) as mock_get_item:
        dialog._add_step()

    offered_labels = mock_get_item.call_args[0][3]
    fill_form_display_name = registry.get("fill_form").display_name
    assert fill_form_display_name not in offered_labels
    dialog.close()


def test_workflow_builder_add_step_builds_and_lists_a_real_operation(qapp: QApplication) -> None:
    from core.registry.registry import Registry, discover_and_load

    registry = Registry()
    discover_and_load(registry)
    dialog = WorkflowBuilderDialog(registry)
    rotate_display_name = registry.get("rotate_pages").display_name

    def fake_rotate_exec(self: RotateDialog) -> QDialog.DialogCode:
        self.angle.setCurrentText("90")
        return QDialog.DialogCode.Accepted

    with (
        patch(
            "gui.dialogs.workflow_builder_dialog.QInputDialog.getItem",
            return_value=(rotate_display_name, True),
        ),
        patch.object(RotateDialog, "exec", fake_rotate_exec),
    ):
        dialog._add_step()

    assert dialog.step_list.count() == 1
    assert len(dialog._operations) == 1
    assert dialog._operations[0].describe() == "Rotated all pages by 90 degrees"
    dialog.close()


def test_workflow_builder_move_up_keeps_list_and_operations_in_sync(qapp: QApplication) -> None:
    from core.registry.registry import Registry, discover_and_load

    registry = Registry()
    discover_and_load(registry)
    dialog = WorkflowBuilderDialog(registry)

    op_a = registry.get("rotate_pages").build_operation(angle=90, pages=[])
    op_b = registry.get("flip").build_operation(direction="horizontal", pages=[])
    dialog._operations.extend([op_a, op_b])
    dialog.step_list.addItem(op_a.describe())
    dialog.step_list.addItem(op_b.describe())

    dialog.step_list.setCurrentRow(1)
    dialog._move(-1)

    assert dialog._operations == [op_b, op_a]
    assert [dialog.step_list.item(i).text() for i in range(2)] == [
        op_b.describe(),
        op_a.describe(),
    ]
    dialog.close()


def test_workflow_builder_remove_selected_keeps_list_and_operations_in_sync(
    qapp: QApplication,
) -> None:
    from core.registry.registry import Registry, discover_and_load

    registry = Registry()
    discover_and_load(registry)
    dialog = WorkflowBuilderDialog(registry)

    op_a = registry.get("rotate_pages").build_operation(angle=90, pages=[])
    op_b = registry.get("flip").build_operation(direction="horizontal", pages=[])
    dialog._operations.extend([op_a, op_b])
    dialog.step_list.addItem(op_a.describe())
    dialog.step_list.addItem(op_b.describe())

    dialog.step_list.setCurrentRow(0)
    dialog._remove_selected()

    assert dialog._operations == [op_b]
    assert dialog.step_list.count() == 1
    assert dialog.step_list.item(0).text() == op_b.describe()
    dialog.close()


def test_workflow_builder_add_step_shows_error_and_does_not_add_on_invalid_operation(
    qapp: QApplication,
) -> None:
    # Regression coverage: _add_step catches PDFEditorError from
    # build_operation (e.g. a dialog that accepted invalid input) and
    # shows an error dialog rather than appending a broken step or
    # crashing. Exercised via "compress", whose real dialog takes no
    # input but whose build_operation is stubbed here to fail, so the
    # error path is proven without depending on any one tool's own
    # validation quirks.
    from core.errors import OperationError
    from core.registry.registry import Registry, discover_and_load
    from gui.dialogs.compress_dialog import CompressDialog

    registry = Registry()
    discover_and_load(registry)
    dialog = WorkflowBuilderDialog(registry)
    compress_display_name = registry.get("compress").display_name

    def fake_compress_exec(self: CompressDialog) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    with (
        patch(
            "gui.dialogs.workflow_builder_dialog.QInputDialog.getItem",
            return_value=(compress_display_name, True),
        ),
        patch.object(CompressDialog, "exec", fake_compress_exec),
        patch.object(
            type(registry.get("compress")),
            "build_operation",
            side_effect=OperationError("boom"),
        ),
        patch("gui.dialogs.workflow_builder_dialog.QMessageBox.critical") as mock_critical,
    ):
        dialog._add_step()

    mock_critical.assert_called_once()
    assert dialog.step_list.count() == 0
    assert dialog._operations == []
    dialog.close()


def test_workflow_builder_accept_rejects_empty_name(qapp: QApplication) -> None:
    from core.registry.registry import Registry, discover_and_load

    registry = Registry()
    discover_and_load(registry)
    dialog = WorkflowBuilderDialog(registry)
    op = registry.get("rotate_pages").build_operation(angle=90, pages=[])
    dialog._operations.append(op)
    dialog.step_list.addItem(op.describe())

    with patch("gui.dialogs.workflow_builder_dialog.QMessageBox.warning") as mock_warning:
        dialog.accept()
    mock_warning.assert_called_once()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.close()


def test_workflow_builder_accept_rejects_zero_steps(qapp: QApplication) -> None:
    from core.registry.registry import Registry, discover_and_load

    registry = Registry()
    discover_and_load(registry)
    dialog = WorkflowBuilderDialog(registry)
    dialog.name_edit.setText("has_a_name")

    with patch("gui.dialogs.workflow_builder_dialog.QMessageBox.warning") as mock_warning:
        dialog.accept()
    mock_warning.assert_called_once()
    dialog.close()


def test_run_workflow_shows_info_when_no_workflows_saved(qapp: QApplication) -> None:
    window = MainWindow()
    with patch("gui.main_window.QMessageBox.information") as mock_info:
        window._run_workflow()
    mock_info.assert_called_once()
    window.close()


def test_run_workflow_applies_saved_pipeline_without_touching_open_document(
    qapp: QApplication, tmp_path: Path
) -> None:
    from core.model.pipeline import Pipeline
    from core.registry.registry import Registry, discover_and_load
    from core.session.workflow_store import WorkflowStore

    registry = Registry()
    discover_and_load(registry)
    rotate = registry.get("rotate_pages").build_operation(angle=90, pages=[])
    WorkflowStore().save(Pipeline(name="gui_run_test", operations=[rotate]))

    src = _make_pdf(tmp_path / "src.pdf", 1)
    out = tmp_path / "out.pdf"

    window = MainWindow()
    # a currently-open, unrelated document - Run Workflow's batch
    # semantics must leave it completely untouched.
    other_open = _make_pdf(tmp_path / "other.pdf", 1)
    window.controller.open_document(other_open)
    window._refresh()
    ops_before = len(window.controller.doc.operation_log)

    def fake_exec(self: RunWorkflowDialog) -> QDialog.DialogCode:
        self.workflow_combo.setCurrentText("gui_run_test")
        self._input_path = src
        self._output_path = out
        return QDialog.DialogCode.Accepted

    with patch.object(RunWorkflowDialog, "exec", fake_exec):
        window._run_workflow()

    assert out.exists()
    with pikepdf.Pdf.open(out) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90
    assert len(window.controller.doc.operation_log) == ops_before
    window.controller.close_session()
    window.close()


def test_view_menu_zoom_in_out_and_reset_resize_the_icon_and_rerender(
    qapp: QApplication, tmp_path: Path
) -> None:
    from gui.main_window import _THUMBNAIL_SIZE

    src = _make_pdf(tmp_path / "src.pdf", 1)
    window = MainWindow()
    window.controller.open_document(src)
    window._refresh()

    assert window.thumbnail_size == _THUMBNAIL_SIZE
    assert window.thumbnail_list.iconSize() == _THUMBNAIL_SIZE

    window.zoom_in_action.trigger()

    assert window.thumbnail_size.width() > _THUMBNAIL_SIZE.width()
    assert window.thumbnail_list.iconSize() == window.thumbnail_size
    # Not just an internal size variable - the actual rendered icon
    # pixmap must be re-rendered at the new size, not stretched.
    item = window.thumbnail_list.item(0)
    assert item.icon().actualSize(window.thumbnail_size) == window.thumbnail_size

    window.zoom_out_action.trigger()
    window.zoom_out_action.trigger()

    assert window.thumbnail_size.width() < _THUMBNAIL_SIZE.width()
    assert window.thumbnail_list.iconSize() == window.thumbnail_size
    item = window.thumbnail_list.item(0)
    assert item.icon().actualSize(window.thumbnail_size) == window.thumbnail_size

    window.reset_zoom_action.trigger()

    assert window.thumbnail_size == _THUMBNAIL_SIZE
    assert window.thumbnail_list.iconSize() == _THUMBNAIL_SIZE

    window.controller.close_session()
    window.close()


def test_view_menu_zoom_is_clamped_to_min_and_max(qapp: QApplication) -> None:
    from gui.main_window import _THUMBNAIL_ZOOM_MAX_WIDTH, _THUMBNAIL_ZOOM_MIN_WIDTH

    window = MainWindow()

    for _ in range(50):
        window.zoom_in_action.trigger()
    assert window.thumbnail_size.width() == _THUMBNAIL_ZOOM_MAX_WIDTH

    for _ in range(50):
        window.zoom_out_action.trigger()
    assert window.thumbnail_size.width() == _THUMBNAIL_ZOOM_MIN_WIDTH

    window.close()


def test_view_menu_toggle_toolbar_visibility(qapp: QApplication) -> None:
    window = MainWindow()
    window.show()
    assert window.toolbar.isVisible()
    assert window.toggle_toolbar_action.isChecked()

    window.toggle_toolbar_action.trigger()
    assert not window.toolbar.isVisible()
    assert not window.toggle_toolbar_action.isChecked()

    window.toggle_toolbar_action.trigger()
    assert window.toolbar.isVisible()
    window.close()


def test_view_menu_toggle_status_bar_visibility(qapp: QApplication) -> None:
    window = MainWindow()
    window.show()
    assert window.statusBar().isVisible()
    assert window.toggle_statusbar_action.isChecked()

    window.toggle_statusbar_action.trigger()
    assert not window.statusBar().isVisible()
    assert not window.toggle_statusbar_action.isChecked()

    window.toggle_statusbar_action.trigger()
    assert window.statusBar().isVisible()
    window.close()


def test_view_menu_full_screen_toggle_reflects_window_state(qapp: QApplication) -> None:
    # Under QT_QPA_PLATFORM=offscreen, showFullScreen()/showNormal()
    # were confirmed by hand to actually flip Qt.WindowState.WindowFullScreen
    # (not a headless no-op) - this test exercises the real toggle, not
    # a stand-in for one.
    window = MainWindow()
    window.show()
    assert not window.isFullScreen()

    window.full_screen_action.trigger()
    assert window.isFullScreen()
    assert window.full_screen_action.isChecked()

    window.full_screen_action.trigger()
    assert not window.isFullScreen()
    assert not window.full_screen_action.isChecked()
    window.close()


def test_run_workflow_records_every_step_in_the_audit_log(
    qapp: QApplication, tmp_path: Path
) -> None:
    # Regression: unlike every other path that applies an Operation
    # (AppController.apply_operation, and the CLI's own run-workflow,
    # see test_run_workflow_records_every_step_in_the_audit_log in
    # tests/integration/test_cli.py), MainWindow._run_workflow used to
    # not record anything to the audit trail at all - a GUI-driven
    # workflow run was invisible to it despite genuinely modifying a
    # document.
    from core.model.pipeline import Pipeline
    from core.registry.registry import Registry, discover_and_load
    from core.session.audit_log import AuditLog
    from core.session.workflow_store import WorkflowStore

    registry = Registry()
    discover_and_load(registry)
    rotate = registry.get("rotate_pages").build_operation(angle=90, pages=[])
    watermark = registry.get("watermark").build_operation(
        text="DRAFT", opacity=0.3, font_size=40
    )
    WorkflowStore().save(Pipeline(name="gui_audit_test", operations=[rotate, watermark]))

    src = _make_pdf(tmp_path / "src.pdf", 1)
    out = tmp_path / "out.pdf"

    window = MainWindow()

    def fake_exec(self: RunWorkflowDialog) -> QDialog.DialogCode:
        self.workflow_combo.setCurrentText("gui_audit_test")
        self._input_path = src
        self._output_path = out
        return QDialog.DialogCode.Accepted

    with patch.object(RunWorkflowDialog, "exec", fake_exec):
        window._run_workflow()

    entries = AuditLog().read_all()
    assert [e["operation"]["type"] for e in entries] == ["rotate_pages", "watermark"]
    assert all(e["document"] == str(out) for e in entries)
    window.close()
