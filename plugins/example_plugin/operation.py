"""Example third-party plugin: Reverse Page Order.

Demonstrates the full third-party plugin contract end to end - a
real, working `Operation` + `ToolPlugin`, not just illustrative code
(see `plugins/README.md` for the walkthrough this plugin is built to
match). Discovered via `plugin.json` in this same directory, loaded by
`core/registry/registry.py`'s manifest scan - never imported directly
by anything in `core/` or `gui/`.

Reuses `core/ops/common.py`'s shared helpers exactly like every
first-party operation does - those are usable by external code, not
`core/ops`-private, so a third-party plugin gets the same undo/audit/
Workflow-replay behavior for free without reimplementing any of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pikepdf

from core.errors import OperationError
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.ops.common import (
    allocate_working_path,
    next_session,
    open_pdf,
    read_working_bytes,
    snapshot_restore_invert,
)
from core.registry.plugin_base import ToolPlugin

CORE_VERSION_RANGE = ">=1.0,<2.0"


def _require_working_pdf(doc: DocumentSession) -> None:
    if doc.working_path is None:
        raise OperationError("No document open.")


@dataclass
class ReversePagesOperation(Operation):
    """Reverses the working document's page order end-to-end."""

    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            total = len(pdf.pages)
            if total < 2:
                raise OperationError("Reversing page order needs at least 2 pages.")
            reversed_pdf = pikepdf.Pdf.new()
            reversed_pdf.pages.extend(pdf.pages[n] for n in reversed(range(total)))
            reversed_pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "reverse_pages"}

    def describe(self) -> str:
        return "Reversed page order"


class ReversePagesPlugin(ToolPlugin):
    tool_id = "reverse_pages"
    display_name = "Reverse Page Order"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return ReversePagesOperation()

    def operation_class(self) -> type[Operation]:
        return ReversePagesOperation
