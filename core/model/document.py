"""DocumentSession: wraps a working PDF plus its operation log.

The operation log (a list of applied Operations) is the source of
truth for undo/redo and, serialized, for autosave/crash-recovery and
the audit trail. See SPEC.md section 2.

FROZEN INTERFACE (as of Phase 0) — see core/model/operation.py header
for the versioning policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from core.errors import OperationError
from core.logging_config import get_logger

if TYPE_CHECKING:
    from core.model.operation import Operation

log = get_logger(__name__)

DOCUMENT_SCHEMA_VERSION = 1


@dataclass
class DocumentSession:
    """A single open document plus its full undo/redo history.

    `working_path` points at a private temp-directory copy, never the
    user's original file, so operations never mutate source documents
    in place and secure-delete cleanup (SPEC.md 6.4) has a clear
    boundary between "our scratch space" and "the user's files."
    """

    schema_version: int = DOCUMENT_SCHEMA_VERSION
    working_path: Path | None = None
    source_path: Path | None = None
    operation_log: list[Operation] = field(default_factory=list)
    redo_stack: list[Operation] = field(default_factory=list)
    #: User-facing output filename (no extension requirement enforced
    #: here). Additive field - see core/model/operation.py header for
    #: the frozen-interface versioning policy. None until a Rename
    #: operation (core/ops/metadata.py) sets it.
    display_name: str | None = None

    def apply(self, operation: Operation) -> DocumentSession:
        """Apply an operation, recording it in the log and clearing
        the redo stack (standard undo/redo semantics: a fresh action
        after an undo discards the old redo branch).
        """
        try:
            result = operation.apply(self)
        except Exception as exc:  # noqa: BLE001 - re-raised as OperationError
            log.error("Operation failed: %s", operation.describe())
            raise OperationError(str(exc)) from exc

        result.operation_log = [*self.operation_log, operation]
        result.redo_stack = []
        log.info("Applied operation", extra={"context": operation.describe()})
        return result

    def undo(self) -> DocumentSession:
        if not self.operation_log:
            raise OperationError("Nothing to undo.")
        *rest, last = self.operation_log
        inverse = last.invert()
        result = inverse.apply(self)
        result.operation_log = rest
        result.redo_stack = [*self.redo_stack, last]
        return result

    def redo(self) -> DocumentSession:
        if not self.redo_stack:
            raise OperationError("Nothing to redo.")
        *rest, next_op = self.redo_stack
        result = next_op.apply(self)
        result.operation_log = [*self.operation_log, next_op]
        result.redo_stack = rest
        return result

    def serialize_log(self) -> list[dict[str, object]]:
        """Used by autosave and the audit trail."""
        return [op.serialize() for op in self.operation_log]
