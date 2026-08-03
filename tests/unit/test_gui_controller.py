"""Unit tests for gui/controller.py - deliberately Qt-free so these
run without a display server."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from core.errors import OperationError
from gui.controller import AppController


def _make_pdf(path: Path, num_pages: int) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(300, 400))
    pdf.save(path)
    return path


@pytest.fixture(autouse=True)
def _isolated_app_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path / "appdata"))


def test_starts_with_no_document_open() -> None:
    controller = AppController()
    assert not controller.is_open
    assert not controller.can_undo
    assert not controller.can_redo


def test_open_document_copies_into_private_session_dir(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 2)
    controller = AppController()

    controller.open_document(src)

    assert controller.is_open
    assert controller.doc.working_path != src
    assert controller.doc.source_path == src
    controller.close_session()


def test_apply_operation_updates_doc_and_undo_state(tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    controller.open_document(src)

    controller.apply_operation(RotatePagesOperation(angle=90))

    assert controller.can_undo
    assert not controller.can_redo
    with pikepdf.Pdf.open(controller.doc.working_path) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90
    controller.close_session()


def test_undo_redo_round_trip(tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    controller.open_document(src)
    controller.apply_operation(RotatePagesOperation(angle=90))

    controller.undo()
    assert not controller.can_undo
    assert controller.can_redo
    with pikepdf.Pdf.open(controller.doc.working_path) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 0

    controller.redo()
    assert controller.can_undo
    with pikepdf.Pdf.open(controller.doc.working_path) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90
    controller.close_session()


def test_save_as_copies_working_file(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 1)
    out = tmp_path / "out" / "result.pdf"
    controller = AppController()
    controller.open_document(src)

    controller.save_as(out)

    assert out.exists()
    with pikepdf.Pdf.open(out) as pdf:
        assert len(pdf.pages) == 1
    controller.close_session()


def test_save_as_with_no_document_raises() -> None:
    controller = AppController()
    with pytest.raises(OperationError):
        controller.save_as(Path("/tmp/whatever.pdf"))


def test_close_session_wipes_working_directory(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    controller.open_document(src)
    working_dir = controller.doc.working_path.parent

    controller.close_session()

    assert not working_dir.exists()
    assert not controller.is_open


def test_opening_a_second_document_closes_the_first(tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf", 1)
    b = _make_pdf(tmp_path / "b.pdf", 3)
    controller = AppController()
    controller.open_document(a)
    first_working_dir = controller.doc.working_path.parent

    controller.open_document(b)

    assert not first_working_dir.exists()
    assert controller.doc.source_path == b
    controller.close_session()


def test_apply_operation_records_to_audit_log(tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    controller.open_document(src)

    controller.apply_operation(RotatePagesOperation(angle=90))

    entries = controller.audit_log.read_all()
    assert len(entries) == 1
    assert entries[0]["operation"]["type"] == "rotate_pages"
    controller.close_session()


def test_get_plugin_returns_registered_plugin() -> None:
    controller = AppController()
    plugin = controller.get_plugin("watermark")
    assert plugin.tool_id == "watermark"


def test_merge_without_opening_a_document_first_creates_a_session(tmp_path: Path) -> None:
    from core.logging_config import app_data_dir
    from core.ops.merge_split import MergeOperation

    a = _make_pdf(tmp_path / "a.pdf", 1)
    b = _make_pdf(tmp_path / "b.pdf", 2)
    controller = AppController()
    assert not controller.is_open

    controller.apply_operation(MergeOperation(sources=[a, b]))

    assert controller.is_open
    with pikepdf.Pdf.open(controller.doc.working_path) as pdf:
        assert len(pdf.pages) == 3
    # the merged working copy must live under the private session dir,
    # not the OS system temp dir.
    assert controller.doc.working_path.is_relative_to(app_data_dir())
    controller.close_session()
