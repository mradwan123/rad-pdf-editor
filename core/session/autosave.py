"""Autosave / crash-recovery journal (SPEC.md section 2: "periodically
persisting the operation log ... plus a checkpoint of the working
file").

Scope note: `recover()` returns the *raw* serialized data from the last
checkpoint (a working-file copy plus `Operation.serialize()` dicts),
not live, re-applicable `Operation` objects. Turning those dicts back
into a replayable undo/redo stack needs a type-name -> Operation-class
deserialization registry, which doesn't exist yet (Workflows,
SPEC.md's Phase 5 feature, needs the same machinery). Until then, the
practical crash-recovery guarantee this provides is "restore the last
known-good working file," not "replay my full undo history" - the
caller (GUI/CLI) decides what to do with the raw operation_log (e.g.
show it read-only, or feed it to a future replay mechanism once one
exists).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.logging_config import app_data_dir, get_logger
from core.model.document import DocumentSession
from core.security.secure_delete import secure_delete_dir

log = get_logger(__name__)

AUTOSAVE_SCHEMA_VERSION = 1


@dataclass
class AutosaveRecovery:
    """Raw data from the last checkpoint - see module docstring."""

    checkpoint_path: Path | None
    operation_log: list[dict[str, Any]]
    display_name: str | None
    timestamp: str


class AutosaveJournal:
    """One journal per editing session. Doesn't impose a save
    schedule - call `checkpoint()` as often as the caller wants (e.g.
    after every `DocumentSession.apply`)."""

    def __init__(self, session_id: str, root: Path | None = None) -> None:
        self.session_id = session_id
        base = root if root is not None else app_data_dir() / "autosave"
        self.dir = base / session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._journal_path = self.dir / "journal.json"
        self._checkpoint_path = self.dir / "checkpoint.pdf"

    def checkpoint(self, doc: DocumentSession) -> None:
        """Persist a recovery point: the current working file plus the
        serialized operation log. Overwrites the previous checkpoint -
        only the latest recovery point is kept, not full history
        (that's the audit log's job, core/session/audit_log.py)."""
        checkpoint_path: str | None = None
        if doc.working_path is not None and doc.working_path.exists():
            shutil.copyfile(doc.working_path, self._checkpoint_path)
            checkpoint_path = str(self._checkpoint_path)

        journal = {
            "schema_version": AUTOSAVE_SCHEMA_VERSION,
            "timestamp": datetime.now(UTC).isoformat(),
            "display_name": doc.display_name,
            "checkpoint_path": checkpoint_path,
            "operation_log": doc.serialize_log(),
        }
        self._journal_path.write_text(json.dumps(journal, ensure_ascii=False), encoding="utf-8")
        log.info("Autosave checkpoint written", extra={"context": self.session_id})

    def recover(self) -> AutosaveRecovery | None:
        """Return the last checkpoint's raw data, or None if this
        session never checkpointed (or was already discarded)."""
        if not self._journal_path.exists():
            return None
        journal = json.loads(self._journal_path.read_text(encoding="utf-8"))
        checkpoint = Path(journal["checkpoint_path"]) if journal["checkpoint_path"] else None
        return AutosaveRecovery(
            checkpoint_path=checkpoint,
            operation_log=journal["operation_log"],
            display_name=journal["display_name"],
            timestamp=journal["timestamp"],
        )

    def discard(self) -> None:
        """Securely wipe this session's autosave data - once a session
        closes normally, there's nothing left to recover, and
        confidential data shouldn't linger past its useful life
        (SPEC.md 6.4)."""
        secure_delete_dir(self.dir)
        log.info("Autosave journal discarded", extra={"context": self.session_id})
