"""Unit tests for core/session/workflow_store.py."""

from __future__ import annotations

import shutil
from pathlib import Path

import fitz
import pikepdf
import pytest

from core.errors import OperationError, PluginLoadError, SchemaVersionError
from core.model.document import DocumentSession
from core.model.pipeline import Pipeline
from core.registry.registry import Registry, discover_and_load
from core.session.workflow_store import WorkflowStore, deserialize_pipeline


@pytest.fixture(autouse=True)
def _isolated_app_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path / "appdata"))


@pytest.fixture
def registry() -> Registry:
    reg = Registry()
    discover_and_load(reg)
    return reg


def _make_fixture_pdf(path: Path) -> Path:
    doc = fitz.open()
    doc.new_page(width=300, height=400)
    doc.save(path)
    doc.close()
    return path


def _multi_step_pipeline(registry: Registry, name: str = "my_workflow") -> Pipeline:
    rotate = registry.get("rotate_pages").build_operation(angle=90, pages=[])
    flip = registry.get("flip").build_operation(direction="horizontal", pages=[])
    watermark = registry.get("watermark").build_operation(
        text="CONFIDENTIAL", opacity=0.3, font_size=40
    )
    return Pipeline(name=name, operations=[rotate, flip, watermark])


# --- save/load round-trip ------------------------------------------------


def test_save_creates_a_json_file(tmp_path: Path, registry: Registry) -> None:
    pipeline = _multi_step_pipeline(registry)
    store = WorkflowStore()
    path = store.save(pipeline)
    assert path.exists()
    assert path.name == "my_workflow.json"


def test_reconstructed_pipeline_produces_identical_real_output(
    tmp_path: Path, registry: Registry
) -> None:
    pipeline = _multi_step_pipeline(registry)
    store = WorkflowStore()
    store.save(pipeline)
    reloaded = store.load("my_workflow", registry)

    fixture = _make_fixture_pdf(tmp_path / "fixture.pdf")
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    w1 = session_dir / "w1.pdf"
    shutil.copyfile(fixture, w1)
    result_original = pipeline.run(DocumentSession(working_path=w1, source_path=None))

    w2 = session_dir / "w2.pdf"
    shutil.copyfile(fixture, w2)
    result_reloaded = reloaded.run(DocumentSession(working_path=w2, source_path=None))

    assert result_original.working_path is not None
    assert result_reloaded.working_path is not None
    with (
        pikepdf.Pdf.open(result_original.working_path) as p1,
        pikepdf.Pdf.open(result_reloaded.working_path) as p2,
    ):
        assert len(p1.pages) == len(p2.pages)
        assert int(p1.pages[0].get("/Rotate", 0)) == int(p2.pages[0].get("/Rotate", 0)) == 90

    # both should have the watermark stamped - verify via extracted text
    with (
        fitz.open(result_original.working_path) as f1,
        fitz.open(result_reloaded.working_path) as f2,
    ):
        assert "CONFIDENTIAL" in f1[0].get_text()
        assert "CONFIDENTIAL" in f2[0].get_text()


def test_reloaded_pipeline_preserves_name_and_step_order(registry: Registry) -> None:
    pipeline = _multi_step_pipeline(registry)
    store = WorkflowStore()
    store.save(pipeline)
    reloaded = store.load("my_workflow", registry)

    assert reloaded.name == "my_workflow"
    assert [op.describe() for op in reloaded.operations] == [
        op.describe() for op in pipeline.operations
    ]


# --- list / delete ---------------------------------------------------------


def test_list_workflows_starts_empty() -> None:
    assert WorkflowStore().list_workflows() == []


def test_list_workflows_returns_sorted_names(registry: Registry) -> None:
    store = WorkflowStore()
    store.save(_multi_step_pipeline(registry, name="zebra"))
    store.save(_multi_step_pipeline(registry, name="alpha"))
    assert store.list_workflows() == ["alpha", "zebra"]


def test_delete_removes_a_workflow(registry: Registry) -> None:
    store = WorkflowStore()
    store.save(_multi_step_pipeline(registry))
    assert store.list_workflows() == ["my_workflow"]
    store.delete("my_workflow")
    assert store.list_workflows() == []


def test_delete_nonexistent_workflow_is_a_noop() -> None:
    WorkflowStore().delete("does_not_exist")


def test_load_nonexistent_workflow_raises(registry: Registry) -> None:
    with pytest.raises(OperationError):
        WorkflowStore().load("does_not_exist", registry)


# --- validation --------------------------------------------------------------


def test_save_rejects_invalid_names(registry: Registry) -> None:
    for bad_name in ("", "a/b", "a\\b", "."):
        pipeline = _multi_step_pipeline(registry, name=bad_name)
        with pytest.raises(OperationError):
            WorkflowStore().save(pipeline)


# --- deserialize_pipeline directly ---------------------------------------------


def test_deserialize_rejects_unsupported_pipeline_schema_version(registry: Registry) -> None:
    with pytest.raises(SchemaVersionError):
        deserialize_pipeline({"schema_version": 999, "name": "x", "operations": []}, registry)


def test_deserialize_rejects_unsupported_operation_schema_version(registry: Registry) -> None:
    data = {
        "schema_version": 1,
        "name": "x",
        "operations": [
            {"schema_version": 999, "type": "flip", "direction": "horizontal", "pages": []}
        ],
    }
    with pytest.raises(SchemaVersionError):
        deserialize_pipeline(data, registry)


def test_deserialize_rejects_missing_type_field(registry: Registry) -> None:
    data = {"schema_version": 1, "name": "x", "operations": [{"schema_version": 1}]}
    with pytest.raises(SchemaVersionError):
        deserialize_pipeline(data, registry)


def test_deserialize_raises_a_pdfeditor_error_for_an_unknown_plugin(registry: Registry) -> None:
    # A workflow file can reference a tool_id that no longer exists in
    # this registry - e.g. a third-party plugin that's since been
    # uninstalled, or a hand-edited file. registry.get() raises
    # PluginLoadError (a PDFEditorError subclass, not a raw KeyError),
    # so both the CLI's and GUI's `except PDFEditorError` handlers
    # around run-workflow show a clean message instead of crashing.
    data = {
        "schema_version": 1,
        "name": "x",
        "operations": [{"schema_version": 1, "type": "does_not_exist"}],
    }
    with pytest.raises(PluginLoadError):
        deserialize_pipeline(data, registry)


def test_deserialize_empty_pipeline(registry: Registry) -> None:
    pipeline = deserialize_pipeline(
        {"schema_version": 1, "name": "empty", "operations": []}, registry
    )
    assert pipeline.name == "empty"
    assert pipeline.operations == []
