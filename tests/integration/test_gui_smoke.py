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
from PySide6.QtWidgets import QApplication, QDialog

from gui.dialogs.merge_dialog import MergeDialog
from gui.dialogs.rotate_dialog import RotateDialog
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
    window.close()
    assert not working_dir.exists()


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
