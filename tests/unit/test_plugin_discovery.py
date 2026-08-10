"""Unit tests for core/registry/registry.py's third-party `plugin.json`
discovery mechanism (SPEC.md section 5's resolved manifest format
decision - see registry.py's module docstring and plugins/README.md).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pikepdf

from core.model.document import DocumentSession
from core.registry.registry import Registry, _third_party_plugins, discover_and_load

_PLUGIN_MODULE_A = '''
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.registry.plugin_base import ToolPlugin


@dataclass
class NoOpTestOperation(Operation):
    def apply(self, doc: DocumentSession) -> DocumentSession:
        return doc

    def invert(self) -> Operation:
        return self

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "test_noop_plugin"}

    def describe(self) -> str:
        return "Test no-op A"


class NoOpTestPlugin(ToolPlugin):
    tool_id = "test_noop_plugin"
    display_name = "Test No-op Plugin"
    compatible_core_version = ">=1.0,<2.0"

    def build_operation(self, **kwargs: Any) -> Operation:
        return NoOpTestOperation()

    def operation_class(self) -> type[Operation]:
        return NoOpTestOperation
'''

# Deliberately reuses the exact same class/field names as _PLUGIN_MODULE_A
# but a different tool_id - a regression fixture for the real bug found
# while building this: two plugins each naming their own module file
# "operation.py" (as this project's own example plugin does) must not
# clobber each other via sys.modules.
_PLUGIN_MODULE_B = '''
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.registry.plugin_base import ToolPlugin


@dataclass
class NoOpTestOperation(Operation):
    def apply(self, doc: DocumentSession) -> DocumentSession:
        return doc

    def invert(self) -> Operation:
        return self

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "test_noop_plugin_b"}

    def describe(self) -> str:
        return "Test no-op B"


class NoOpTestPlugin(ToolPlugin):
    tool_id = "test_noop_plugin_b"
    display_name = "Test No-op Plugin B"
    compatible_core_version = ">=1.0,<2.0"

    def build_operation(self, **kwargs: Any) -> Operation:
        return NoOpTestOperation()

    def operation_class(self) -> type[Operation]:
        return NoOpTestOperation
'''

_COLLIDING_TOOL_ID_MODULE = '''
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.registry.plugin_base import ToolPlugin


@dataclass
class CollidingOperation(Operation):
    def apply(self, doc: DocumentSession) -> DocumentSession:
        return doc

    def invert(self) -> Operation:
        return self

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "flip"}

    def describe(self) -> str:
        return "Colliding with first-party flip"


class CollidingPlugin(ToolPlugin):
    tool_id = "flip"
    display_name = "Colliding Plugin"
    compatible_core_version = ">=1.0,<2.0"

    def build_operation(self, **kwargs: Any) -> Operation:
        return CollidingOperation()

    def operation_class(self) -> type[Operation]:
        return CollidingOperation
'''


def _write_plugin(
    plugins_dir: Path,
    dir_name: str,
    manifest: dict[str, Any],
    module_source: str = _PLUGIN_MODULE_A,
    module_filename: str = "operation.py",
) -> None:
    plugin_dir = plugins_dir / dir_name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / module_filename).write_text(module_source, encoding="utf-8")


def _valid_manifest(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "tool_id": "test_noop_plugin",
        "display_name": "Test No-op Plugin",
        "module": "operation.py",
        "plugin_class": "NoOpTestPlugin",
        "compatible_core_version": ">=1.0,<2.0",
    }
    manifest.update(overrides)
    return manifest


# --- happy path -------------------------------------------------------


def test_loads_a_real_third_party_plugin(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "my_plugin", _valid_manifest())
    plugins = _third_party_plugins(tmp_path)
    assert len(plugins) == 1
    assert plugins[0].tool_id == "test_noop_plugin"


def test_discover_and_load_registers_third_party_alongside_first_party(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "my_plugin", _valid_manifest())
    registry = Registry()
    discover_and_load(registry, plugins_dir=tmp_path)
    tool_ids = {p.tool_id for p in registry.all_plugins()}
    assert "test_noop_plugin" in tool_ids
    assert "flip" in tool_ids  # first-party plugins are unaffected


def test_third_party_operation_actually_runs_against_a_real_pdf(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "my_plugin", _valid_manifest())
    registry = Registry()
    discover_and_load(registry, plugins_dir=tmp_path)

    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    working = tmp_path / "working.pdf"
    pdf.save(working)
    doc = DocumentSession(working_path=working, source_path=None)

    plugin = registry.get("test_noop_plugin")
    result = doc.apply(plugin.build_operation())
    assert result.working_path == working


def test_the_real_shipped_example_plugin_loads_from_the_default_dir() -> None:
    # No plugins_dir override - exercises the actual repo /plugins
    # directory and the real plugins/example_plugin shipped with the
    # project, not a synthetic fixture.
    registry = Registry()
    discover_and_load(registry)
    assert "reverse_pages" in {p.tool_id for p in registry.all_plugins()}


def test_two_plugins_with_the_same_module_filename_dont_collide(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "plugin_a", _valid_manifest(), module_source=_PLUGIN_MODULE_A)
    _write_plugin(
        tmp_path,
        "plugin_b",
        _valid_manifest(tool_id="test_noop_plugin_b", plugin_class="NoOpTestPlugin"),
        module_source=_PLUGIN_MODULE_B,
    )
    plugins = _third_party_plugins(tmp_path)
    tool_ids = sorted(p.tool_id for p in plugins)
    assert tool_ids == ["test_noop_plugin", "test_noop_plugin_b"]


# --- failure handling: skip with a warning, never crash -----------------


def test_missing_plugins_directory_returns_no_plugins(tmp_path: Path) -> None:
    assert _third_party_plugins(tmp_path / "does_not_exist") == []


def test_malformed_json_manifest_is_skipped_not_raised(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "broken"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text("{not valid json", encoding="utf-8")
    assert _third_party_plugins(tmp_path) == []


def test_manifest_missing_a_required_field_is_skipped_not_raised(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "broken", {"schema_version": 1, "module": "operation.py"})
    assert _third_party_plugins(tmp_path) == []


def test_unsupported_schema_version_is_skipped_not_raised(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "broken", _valid_manifest(schema_version=999))
    assert _third_party_plugins(tmp_path) == []


def test_missing_plugin_class_is_skipped_not_raised(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "broken", _valid_manifest(plugin_class="DoesNotExist"))
    assert _third_party_plugins(tmp_path) == []


def test_class_not_a_toolplugin_subclass_is_skipped_not_raised(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        "broken",
        _valid_manifest(plugin_class="NotAPlugin"),
        module_source="class NotAPlugin:\n    pass\n",
    )
    assert _third_party_plugins(tmp_path) == []


def test_broken_plugin_does_not_prevent_first_party_registration(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "broken", _valid_manifest(schema_version=999))
    registry = Registry()
    discover_and_load(registry, plugins_dir=tmp_path)
    tool_ids = {p.tool_id for p in registry.all_plugins()}
    assert "flip" in tool_ids
    assert "test_noop_plugin" not in tool_ids


def test_third_party_tool_id_colliding_with_first_party_is_skipped_not_raised(
    tmp_path: Path,
) -> None:
    _write_plugin(
        tmp_path,
        "colliding",
        _valid_manifest(tool_id="flip", plugin_class="CollidingPlugin"),
        module_source=_COLLIDING_TOOL_ID_MODULE,
    )
    registry = Registry()
    discover_and_load(registry, plugins_dir=tmp_path)  # must not raise
    # first-party "flip" registered first, wins - the colliding
    # third-party plugin is skipped, not silently overwriting it.
    flip_plugin = registry.get("flip")
    assert type(flip_plugin).__module__.startswith("core.ops")
