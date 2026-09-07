"""Unit tests for gui/controller.py - deliberately Qt-free so these
run without a display server."""

from __future__ import annotations

import shutil
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


def _make_docx(path: Path, *, body: str = "WORD BODY TEXT") -> Path:
    import docx

    document = docx.Document()
    document.add_paragraph(body)
    document.save(str(path))
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


def test_open_document_with_missing_path_raises_pdf_editor_error(tmp_path: Path) -> None:
    controller = AppController()
    with pytest.raises(OperationError):
        controller.open_document(tmp_path / "does-not-exist.pdf")


def test_failed_open_does_not_destroy_the_currently_open_document(tmp_path: Path) -> None:
    # Regression: open_document used to close the current session
    # unconditionally before even trying to read the new path, so a
    # failed Open (bad path) silently threw away whatever was open.
    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    controller.open_document(src)
    working_before = controller.doc.working_path

    with pytest.raises(OperationError):
        controller.open_document(tmp_path / "does-not-exist.pdf")

    assert controller.is_open
    assert controller.doc.working_path == working_before
    controller.close_session()


def test_open_document_via_conversion_produces_a_real_pdf_keeping_original_identity(
    tmp_path: Path,
) -> None:
    """File > Open on a .docx (see core.ops.common.CONVERTIBLE_OPEN_EXTENSIONS)
    runs it through this instead of open_document - the working file
    must actually be a PDF (open_document alone would have copied the
    .docx in verbatim, which every editing Operation and the
    QtPdf/fitz thumbnail renderer assume is a PDF), while source_path
    still names the real original file, not the converted copy."""
    src = _make_docx(tmp_path / "in.docx")
    controller = AppController()
    operation = controller.registry.get("docx_to_pdf").build_operation(source_path=src)

    controller.open_document_via_conversion(src, operation)

    assert controller.is_open
    assert controller.doc.source_path == src
    assert controller.doc.working_path is not None
    assert controller.doc.working_path.suffix == ".pdf"
    with pikepdf.Pdf.open(controller.doc.working_path) as pdf:
        assert len(pdf.pages) >= 1
    controller.close_session()


def test_open_document_via_conversion_records_no_undo_entry(tmp_path: Path) -> None:
    """The conversion produced the document - it isn't an edit made to
    it, same reasoning _export_document's throwaway-session
    conversions already use on the export side."""
    src = _make_docx(tmp_path / "in.docx")
    controller = AppController()
    operation = controller.registry.get("docx_to_pdf").build_operation(source_path=src)

    controller.open_document_via_conversion(src, operation)

    assert controller.doc.operation_log == []
    assert not controller.can_undo
    controller.close_session()


def test_failed_open_via_conversion_does_not_destroy_the_currently_open_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same ordering guarantee open_document already gives plain PDFs:
    a source that fails to convert must leave whatever was already
    open untouched, not just fail cleanly in isolation.

    The fallback engine is forced because LibreOffice is permissive
    enough to convert a plain text file with a .docx name quite
    happily - python-docx is the engine that rejects it (see
    tests/integration/test_gui_smoke.py's identical note on the
    Tools-menu path)."""
    monkeypatch.setattr("core.ops.convert_to.libreoffice_binary", lambda: None)
    good = _make_pdf(tmp_path / "good.pdf", 1)
    controller = AppController()
    controller.open_document(good)
    working_before = controller.doc.working_path

    broken = tmp_path / "broken.docx"
    broken.write_text("this is plainly not a Word document")
    operation = controller.registry.get("docx_to_pdf").build_operation(source_path=broken)

    with pytest.raises(OperationError):
        controller.open_document_via_conversion(broken, operation)

    assert controller.is_open
    assert controller.doc.working_path == working_before
    assert controller.doc.source_path == good
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


# --- dirty-state tracking -------------------------------------------------


def test_starts_clean() -> None:
    controller = AppController()
    assert not controller.is_dirty


def test_opening_a_document_is_clean(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    controller.open_document(src)
    assert not controller.is_dirty
    controller.close_session()


def test_apply_operation_marks_dirty(tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    controller.open_document(src)

    controller.apply_operation(RotatePagesOperation(angle=90))

    assert controller.is_dirty
    controller.close_session()


def test_undo_marks_dirty(tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    controller.open_document(src)
    controller.apply_operation(RotatePagesOperation(angle=90))

    controller.save_as(tmp_path / "out.pdf")
    assert not controller.is_dirty

    controller.undo()
    assert controller.is_dirty
    controller.close_session()


def test_save_as_clears_dirty(tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    controller.open_document(src)
    controller.apply_operation(RotatePagesOperation(angle=90))
    assert controller.is_dirty

    controller.save_as(tmp_path / "out.pdf")

    assert not controller.is_dirty
    controller.close_session()


def test_close_session_clears_dirty(tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    controller.open_document(src)
    controller.apply_operation(RotatePagesOperation(angle=90))
    assert controller.is_dirty

    controller.close_session()

    assert not controller.is_dirty


def test_two_controllers_are_fully_independent_sessions(tmp_path: Path) -> None:
    # The whole basis of multi-tab documents: one AppController per
    # open document, sharing nothing mutable. Checked here, Qt-free,
    # before any tab UI is involved.
    from core.ops.organize import RotatePagesOperation

    a = _make_pdf(tmp_path / "a.pdf", 1)
    b = _make_pdf(tmp_path / "b.pdf", 2)
    first = AppController()
    second = AppController()
    first.open_document(a)
    second.open_document(b)

    assert first.doc.working_path.parent != second.doc.working_path.parent
    assert first.session_id != second.session_id

    first.apply_operation(RotatePagesOperation(angle=90))

    assert first.is_dirty
    assert not second.is_dirty
    assert second.doc.operation_log == []
    assert not second.can_undo
    with pikepdf.Pdf.open(second.doc.working_path) as pdf:
        assert len(pdf.pages) == 2
        assert int(pdf.pages[0].get("/Rotate", 0)) == 0

    second_working_dir = second.doc.working_path.parent
    first.close_session()

    # Closing one document wipes only its own scratch space.
    assert second_working_dir.exists()
    assert second.is_open
    second.close_session()


def test_registry_and_audit_log_can_be_shared_between_controllers() -> None:
    from core.registry.registry import Registry, discover_and_load
    from core.session.audit_log import AuditLog

    registry = Registry()
    discover_and_load(registry)
    audit_log = AuditLog()

    first = AppController(registry, audit_log)
    second = AppController(registry, audit_log)

    assert first.registry is registry
    assert second.registry is registry
    assert first.audit_log is audit_log
    assert second.audit_log is audit_log
    # ...and a plain AppController() still builds its own, unchanged.
    assert AppController().registry is not registry


def test_both_controllers_record_into_one_shared_audit_log(tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation
    from core.registry.registry import Registry, discover_and_load
    from core.session.audit_log import AuditLog

    registry = Registry()
    discover_and_load(registry)
    audit_log = AuditLog()
    a = _make_pdf(tmp_path / "a.pdf", 1)
    b = _make_pdf(tmp_path / "b.pdf", 1)
    first = AppController(registry, audit_log)
    second = AppController(registry, audit_log)
    first.open_document(a)
    second.open_document(b)

    first.apply_operation(RotatePagesOperation(angle=90))
    second.apply_operation(RotatePagesOperation(angle=180))

    entries = audit_log.read_all()
    assert len(entries) == 2
    # Each entry names the document it actually belongs to, so one
    # shared trail stays unambiguous across concurrent documents.
    assert {e["document"] for e in entries} == {str(a), str(b)}
    first.close_session()
    second.close_session()


def test_session_id_is_none_until_a_document_is_open(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 1)
    controller = AppController()
    assert controller.session_id is None

    controller.open_document(src)
    assert controller.session_id is not None

    controller.close_session()
    assert controller.session_id is None


def test_restore_from_checkpoint_keeps_identity_and_starts_dirty(tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    src = _make_pdf(tmp_path / "in.pdf", 1)
    crashed = AppController()
    crashed.open_document(src)
    crashed.apply_operation(RotatePagesOperation(angle=90))
    checkpoint = tmp_path / "checkpoint.pdf"
    shutil.copyfile(crashed.doc.working_path, checkpoint)

    restored = AppController()
    restored.restore_from_checkpoint(checkpoint, source_path=src, display_name="in.pdf")

    assert restored.is_open
    # Identity comes from the crashed document, not the checkpoint file.
    assert restored.doc.source_path == src
    assert restored.doc.display_name == "in.pdf"
    # The recovered state was never saved anywhere, so it's dirty...
    assert restored.is_dirty
    # ...and it really is the edited state, in its own private copy.
    assert restored.doc.working_path != checkpoint
    with pikepdf.Pdf.open(restored.doc.working_path) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90
    with pikepdf.Pdf.open(src) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 0
    crashed.close_session()
    restored.close_session()


def test_opening_a_new_document_over_a_dirty_one_resets_dirty(tmp_path: Path) -> None:
    from core.ops.organize import RotatePagesOperation

    a = _make_pdf(tmp_path / "a.pdf", 1)
    b = _make_pdf(tmp_path / "b.pdf", 1)
    controller = AppController()
    controller.open_document(a)
    controller.apply_operation(RotatePagesOperation(angle=90))
    assert controller.is_dirty

    controller.open_document(b)

    assert not controller.is_dirty
    controller.close_session()
