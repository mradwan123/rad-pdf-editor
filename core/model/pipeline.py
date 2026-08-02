"""Pipeline: an ordered, named sequence of Operations that can run
unattended against an input document. This is the mechanism behind
both simple multi-step edits and the Workflows automation feature
(SPEC.md sections 2 and 4).

FROZEN INTERFACE (as of Phase 0) — see core/model/operation.py header
for the versioning policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.errors import OperationError
from core.logging_config import get_logger
from core.model.document import DocumentSession
from core.model.operation import Operation

log = get_logger(__name__)

PIPELINE_SCHEMA_VERSION = 1


@dataclass
class Pipeline:
    """A named, ordered list of Operations, replayable against new input.

    Saved/loaded via `serialize` / the registry's deserialization so it
    can be persisted as a Workflow file (workflows/*.json) and reused
    across sessions and input files.
    """

    schema_version: int = PIPELINE_SCHEMA_VERSION
    name: str = ""
    operations: list[Operation] = field(default_factory=list)

    def run(self, doc: DocumentSession) -> DocumentSession:
        """Execute every operation in order against `doc`.

        Stops and raises on the first failure rather than silently
        skipping steps — an unattended pipeline that partially applies
        without telling anyone is worse than one that stops loudly and
        leaves a clear audit trail of how far it got.
        """
        result = doc
        for i, operation in enumerate(self.operations):
            try:
                result = result.apply(operation)
            except OperationError:
                log.error(
                    "Pipeline '%s' failed at step %d/%d: %s",
                    self.name,
                    i + 1,
                    len(self.operations),
                    operation.describe(),
                )
                raise
        return result

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "operations": [op.serialize() for op in self.operations],
        }
