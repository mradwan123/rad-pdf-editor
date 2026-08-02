"""The Operation abstraction: the single mechanism that undo/redo,
autosave/crash-recovery, the audit trail, and Workflows automation are
all built on top of. See SPEC.md sections 2 and 6.1.

FROZEN INTERFACE (as of Phase 0). Changes after Phase 0 must be
additive only (new optional methods/fields) — see SPEC.md 6.1 for the
versioning policy. Do not change these method signatures without a
schema_version bump and a CHANGELOG.md migration note.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.model.document import DocumentSession

OPERATION_SCHEMA_VERSION = 1


class Operation(ABC):
    """Base class every tool (merge, watermark, OCR, ...) implements.

    Concrete subclasses live in core/ops/ (first-party) or plugins/
    (third-party), registered via core/registry. See
    core/registry/plugin_base.py for how a tool exposes its
    Operation(s) to the app.
    """

    #: Bump only via an additive migration; see SPEC.md 6.1.
    schema_version: int = OPERATION_SCHEMA_VERSION

    @abstractmethod
    def apply(self, doc: DocumentSession) -> DocumentSession:
        """Apply this operation, returning the resulting session state.

        Must be side-effect-free with respect to `doc` where practical
        (return a new/updated session rather than mutating in place)
        so undo/redo and autosave snapshots stay predictable.
        """

    @abstractmethod
    def invert(self) -> Operation:
        """Return an Operation that undoes this one.

        Enables undo without re-deriving prior document state from
        scratch. Some operations are not cleanly invertible (e.g. OCR
        text-layer addition) — in that case, invert() should return an
        Operation that restores the pre-apply snapshot rather than
        raising, so undo always works from the user's perspective.
        """

    @abstractmethod
    def serialize(self) -> dict[str, Any]:
        """Return a JSON-serializable dict representing this operation.

        Must include `schema_version` and enough information to fully
        reconstruct the operation via the registry. This is what
        autosave, the audit log, and saved Workflows all persist.
        """

    @abstractmethod
    def describe(self) -> str:
        """Human-readable one-line description, e.g. 'Merged 3 files'.

        Used in the undo-stack UI and the audit log — must not leak
        full file paths or document contents if avoidable, given the
        confidential-document requirement (SPEC.md section 1).
        """
