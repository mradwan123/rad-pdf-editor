"""ToolPlugin: the contract every tool (first-party or third-party)
implements to register itself with the app. Built-in tools in core/ops/
use this exact same interface a team-authored plugin in /plugins would
use — there is only ever one extensibility system, per SPEC.md
sections 2 and 6.1.

FROZEN INTERFACE (as of Phase 0) — see core/model/operation.py header
for the versioning policy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.model.operation import Operation

PLUGIN_MANIFEST_SCHEMA_VERSION = 1


class ToolPlugin(ABC):
    """One plugin = one tool category (e.g. 'merge', 'watermark', 'ocr').

    A plugin may expose more than one related Operation subclass (e.g.
    the 'split' plugin might build split-by-pages, split-by-bookmarks,
    or split-by-size Operations depending on parameters) but registers
    under one stable `tool_id`.
    """

    #: Stable identifier, e.g. "merge", "split_by_bookmarks". Never
    #: reused for a different tool once shipped — see SPEC.md 6.1.
    tool_id: str

    #: Human-readable name shown in the UI tool list.
    display_name: str

    #: Semver range of core this plugin is compatible with, e.g.
    #: ">=1.0,<2.0". Enforced by the registry at load time.
    compatible_core_version: str

    @abstractmethod
    def build_operation(self, **kwargs: Any) -> Operation:
        """Construct a configured Operation instance for this tool
        from user-supplied parameters (e.g. from a GUI dialog or a
        deserialized Workflow step).
        """

    @abstractmethod
    def operation_class(self) -> type[Operation]:
        """Return the Operation subclass this plugin registers, used
        by the registry to deserialize saved Workflow steps back into
        live Operation instances.
        """
