"""Integration test for the cli.main scripting entry point (SPEC.md's
/cli row - "reuses /core directly")."""

from __future__ import annotations

from pathlib import Path

import fitz
import pikepdf
import pytest

from cli.main import main


@pytest.fixture(autouse=True)
def _isolated_app_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # cli.main writes session temp dirs and the audit log under
    # core.logging_config.app_data_dir() - redirect that to a tmp dir
    # so these tests never touch the real per-OS app-data location.
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path / "appdata"))


def _make_pdf(path: Path, num_pages: int) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(300, 400))
    pdf.save(path)
    return path


def test_merge_then_rotate_then_watermark_roundtrip(tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf", 1)
    b = _make_pdf(tmp_path / "b.pdf", 2)
    merged = tmp_path / "merged.pdf"
    rotated = tmp_path / "rotated.pdf"
    watermarked = tmp_path / "watermarked.pdf"

    assert main(["merge", str(a), str(b), "-o", str(merged)]) == 0
    assert main(["rotate_pages", str(merged), "-o", str(rotated), "--angle", "90"]) == 0
    assert main(["watermark", str(rotated), "-o", str(watermarked), "--text", "DRAFT"]) == 0

    with pikepdf.Pdf.open(watermarked) as pdf:
        assert len(pdf.pages) == 3
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90


def test_protect_then_unlock_roundtrip(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 1)
    protected = tmp_path / "protected.pdf"
    unlocked = tmp_path / "unlocked.pdf"

    assert main(["protect", str(src), "-o", str(protected), "--user-password", "secret"]) == 0
    with pikepdf.Pdf.open(protected, password="secret") as pdf:
        assert pdf.is_encrypted

    assert main(["unlock", str(protected), "-o", str(unlocked), "--password", "secret"]) == 0
    with pikepdf.Pdf.open(unlocked) as pdf:
        assert not pdf.is_encrypted


def test_operating_on_encrypted_document_without_password_fails_cleanly(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 1)
    protected = tmp_path / "protected.pdf"
    out = tmp_path / "out.pdf"

    assert main(["protect", str(src), "-o", str(protected), "--user-password", "secret"]) == 0
    exit_code = main(["set_metadata", str(protected), "-o", str(out), "--title", "X"])
    assert exit_code == 1


def test_successful_run_is_recorded_in_the_audit_log(tmp_path: Path) -> None:
    from core.session.audit_log import AuditLog

    src = _make_pdf(tmp_path / "in.pdf", 1)
    out = tmp_path / "out.pdf"

    assert main(["rotate_pages", str(src), "-o", str(out), "--angle", "90"]) == 0

    entries = AuditLog().read_all()
    assert len(entries) == 1
    assert entries[0]["operation"]["type"] == "rotate_pages"
    assert entries[0]["document"] == str(out)


def test_session_temp_dir_is_cleaned_up_after_run(tmp_path: Path) -> None:
    from core.logging_config import app_data_dir

    src = _make_pdf(tmp_path / "in.pdf", 1)
    out = tmp_path / "out.pdf"

    assert main(["rotate_pages", str(src), "-o", str(out), "--angle", "90"]) == 0

    sessions_dir = app_data_dir() / "sessions"
    assert not sessions_dir.exists() or list(sessions_dir.iterdir()) == []


def test_missing_input_file_fails_cleanly_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Regression: a nonexistent --input path used to propagate an
    # unhandled FileNotFoundError (shutil.copyfile isn't a
    # PDFEditorError) instead of the CLI's normal "Error: ..." message.
    out = tmp_path / "out.pdf"
    exit_code = main(
        ["rotate_pages", str(tmp_path / "does-not-exist.pdf"), "-o", str(out), "--angle", "90"]
    )
    assert exit_code == 1
    assert "Error:" in capsys.readouterr().err


def test_malformed_pages_argument_fails_cleanly_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Regression: a non-numeric --pages value used to propagate an
    # unhandled ValueError from _parse_int_list instead of a clean
    # CLI error.
    src = _make_pdf(tmp_path / "in.pdf", 1)
    out = tmp_path / "out.pdf"
    exit_code = main(
        ["rotate_pages", str(src), "-o", str(out), "--angle", "90", "--pages", "abc"]
    )
    assert exit_code == 1
    assert "Error:" in capsys.readouterr().err


def test_list_workflows_reports_none_when_empty(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list-workflows"]) == 0
    assert "No saved workflows" in capsys.readouterr().out


def test_run_workflow_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from core.model.pipeline import Pipeline
    from core.registry.registry import Registry, discover_and_load
    from core.session.workflow_store import WorkflowStore

    registry = Registry()
    discover_and_load(registry)
    rotate = registry.get("rotate_pages").build_operation(angle=90, pages=[])
    watermark = registry.get("watermark").build_operation(text="DRAFT", opacity=0.3, font_size=40)
    WorkflowStore().save(Pipeline(name="cli_wf", operations=[rotate, watermark]))

    assert main(["list-workflows"]) == 0
    assert "cli_wf" in capsys.readouterr().out

    src = _make_pdf(tmp_path / "in.pdf", 1)
    out = tmp_path / "out.pdf"
    exit_code = main(["run-workflow", "cli_wf", str(src), "-o", str(out)])
    assert exit_code == 0

    with pikepdf.Pdf.open(out) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90
    with fitz.open(out) as pdf:
        assert "DRAFT" in pdf[0].get_text()


def test_run_workflow_records_every_step_in_the_audit_log(tmp_path: Path) -> None:
    from core.model.pipeline import Pipeline
    from core.registry.registry import Registry, discover_and_load
    from core.session.audit_log import AuditLog
    from core.session.workflow_store import WorkflowStore

    registry = Registry()
    discover_and_load(registry)
    rotate = registry.get("rotate_pages").build_operation(angle=90, pages=[])
    watermark = registry.get("watermark").build_operation(text="DRAFT", opacity=0.3, font_size=40)
    WorkflowStore().save(Pipeline(name="cli_wf", operations=[rotate, watermark]))

    src = _make_pdf(tmp_path / "in.pdf", 1)
    out = tmp_path / "out.pdf"
    assert main(["run-workflow", "cli_wf", str(src), "-o", str(out)]) == 0

    entries = AuditLog().read_all()
    assert [e["operation"]["type"] for e in entries] == ["rotate_pages", "watermark"]


def test_run_workflow_missing_name_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 1)
    out = tmp_path / "out.pdf"
    exit_code = main(["run-workflow", "does_not_exist", str(src), "-o", str(out)])
    assert exit_code == 1
    assert "Error:" in capsys.readouterr().err


def test_create_form_field_adds_a_text_field(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 1)
    out = tmp_path / "out.pdf"

    exit_code = main(
        [
            "create_form_field",
            str(src),
            "-o",
            str(out),
            "--page",
            "1",
            "--field-name",
            "full_name",
            "--field-type",
            "text",
            "--rect",
            "50,300,250,320",
            "--default-value",
            "Jane Doe",
        ]
    )

    assert exit_code == 0
    with fitz.open(out) as pdf:
        widgets = list(pdf[0].widgets())
    assert len(widgets) == 1
    assert widgets[0].field_name == "full_name"
    assert widgets[0].field_value == "Jane Doe"
