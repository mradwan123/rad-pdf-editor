"""Persists named Workflows (saved Pipelines) so they can be replayed
against new input files unattended (SPEC.md's Workflows automation
feature, section 2, and Phase 5 of the roadmap).

One JSON file per workflow under `app_data_dir() / "workflows"` -
respects `PDFEDITOR_APP_DATA_DIR` for test isolation, the same
convention `recent_files.py`/`audit_log.py`/`autosave.py` already
follow. Deliberately **not** the repo's top-level `/workflows`
directory named in SPEC.md's architecture diagram - that reads as a
conceptual placeholder, not an instruction to write user-created
runtime data into the source tree. SPEC.md section 6.4's own policy
("local files under the OS-appropriate app-data directory, never the
user's working directory") already covers exactly this case, same as
every other piece of session data in this package.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from core.errors import OperationError, SchemaVersionError
from core.logging_config import app_data_dir, get_logger
from core.model.pipeline import PIPELINE_SCHEMA_VERSION, Pipeline
from core.registry.registry import Registry

log = get_logger(__name__)

_WORKFLOWS_DIR_NAME = "workflows"


def deserialize_pipeline(data: dict[str, Any], registry: Registry) -> Pipeline:
    """Reconstruct a Pipeline (with live Operation instances) from a
    `Pipeline.serialize()`'d dict.

    Relies on a convention checked by hand across every operation
    registered so far (not assumed from documentation): every
    `Operation.serialize()`'s `"type"` field exactly matches its
    `ToolPlugin.tool_id`. That means reconstruction is just
    `registry.get(type).build_operation(**kwargs)` for each step - no
    per-operation deserialization code needed anywhere.
    """
    schema_version = data.get("schema_version")
    if schema_version != PIPELINE_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Workflow schema_version {schema_version!r} is not supported "
            f"(expected {PIPELINE_SCHEMA_VERSION})."
        )

    operations = []
    for op_data in data.get("operations", []):
        try:
            tool_id = op_data["type"]
        except KeyError as exc:
            raise SchemaVersionError("Workflow step is missing a 'type' field.") from exc
        plugin = registry.get(tool_id)
        op_schema_version = op_data.get("schema_version")
        if op_schema_version != plugin.operation_class().schema_version:
            raise SchemaVersionError(
                f"Workflow step '{tool_id}' has unsupported schema_version "
                f"{op_schema_version!r}."
            )
        kwargs = {k: v for k, v in op_data.items() if k not in ("schema_version", "type")}
        operations.append(plugin.build_operation(**kwargs))

    return Pipeline(name=data.get("name", ""), operations=operations)


def _validate_name(name: str) -> None:
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise OperationError(f"Invalid workflow name: {name!r}")


class WorkflowStore:
    """Saved Workflows (named Pipelines), one JSON file per workflow."""

    def __init__(self) -> None:
        self._dir = app_data_dir() / _WORKFLOWS_DIR_NAME
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, name: str) -> Path:
        _validate_name(name)
        return self._dir / f"{name}.json"

    def save(self, pipeline: Pipeline) -> Path:
        path = self._path_for(pipeline.name)
        path.write_text(
            json.dumps(pipeline.serialize(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Saved workflow", extra={"context": pipeline.name})
        return path

    def load(self, name: str, registry: Registry) -> Pipeline:
        path = self._path_for(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise OperationError(f"No workflow named '{name}'.") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise OperationError(f"Could not read workflow '{name}': {exc}") from exc
        return deserialize_pipeline(data, registry)

    def list_workflows(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.json"))

    def delete(self, name: str) -> None:
        path = self._path_for(name)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
