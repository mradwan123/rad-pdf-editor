"""Smoke test for the frozen ToolPlugin/Registry contract.

Uses a trivial fake plugin (no real tool behavior) to prove
register/get/all_plugins and compatibility-checking in
core/registry/registry.py, independent of any real first-party plugin
(those land in Phase 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from core.errors import PluginCompatibilityError, PluginLoadError
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.registry.plugin_base import ToolPlugin
from core.registry.registry import Registry


@dataclass
class _NoOp(Operation):
    def apply(self, doc: DocumentSession) -> DocumentSession:
        return doc

    def invert(self) -> Operation:
        return self

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "noop"}

    def describe(self) -> str:
        return "No-op"


class _FakePlugin(ToolPlugin):
    tool_id = "noop"
    display_name = "No-op"
    compatible_core_version = ">=1.0,<2.0"

    def build_operation(self, **kwargs: Any) -> Operation:
        return _NoOp()

    def operation_class(self) -> type[Operation]:
        return _NoOp


class _IncompatiblePlugin(_FakePlugin):
    tool_id = "incompatible"
    compatible_core_version = ""


def test_register_and_get() -> None:
    registry = Registry()
    plugin = _FakePlugin()
    registry.register(plugin)
    assert registry.get("noop") is plugin
    assert registry.all_plugins() == [plugin]


def test_register_duplicate_tool_id_raises() -> None:
    registry = Registry()
    registry.register(_FakePlugin())
    with pytest.raises(PluginLoadError):
        registry.register(_FakePlugin())


def test_get_unknown_tool_id_raises() -> None:
    registry = Registry()
    with pytest.raises(PluginLoadError):
        registry.get("does-not-exist")


def test_register_without_compatible_core_version_raises() -> None:
    registry = Registry()
    with pytest.raises(PluginCompatibilityError):
        registry.register(_IncompatiblePlugin())
