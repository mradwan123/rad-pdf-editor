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

Multi-document scope note: one `AutosaveJournal` exists per editing
session, and since the GUI became multi-tab there can be several live
at once (one per open tab). Crash recovery is deliberately scoped to
*one* of them - the most recently active tab - via the module-level
`mark_active_session()` / `recover_active_session()` pointer below,
rather than restoring every tab that happened to be open. Restoring N
documents unattended at startup is a much bigger UX question (which
window? which order? what if one of them fails?) than restoring the
one the user was actually looking at when the app died.
"""

from __future__ import annotations

import contextlib
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

#: Name of the "which session was most recently active" pointer file,
#: written alongside (not inside) the per-session journal directories
#: so `AutosaveJournal.discard()` - which wipes only its own dir -
#: never takes the pointer with it.
ACTIVE_SESSION_POINTER = "active_session.json"


@dataclass
class AutosaveRecovery:
    """Raw data from the last checkpoint - see module docstring."""

    checkpoint_path: Path | None
    operation_log: list[dict[str, Any]]
    display_name: str | None
    timestamp: str
    #: Original file the crashed session was editing, when it had one
    #: (None for a document built from scratch, e.g. by Merge).
    #: Additive since the multi-tab work - journals written before it
    #: existed simply read back as None, so no schema bump is needed.
    source_path: Path | None = None


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
            "source_path": str(doc.source_path) if doc.source_path else None,
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
        # .get(), not [...]: journals written before source_path was
        # recorded stay readable rather than raising a KeyError.
        raw_source = journal.get("source_path")
        return AutosaveRecovery(
            checkpoint_path=checkpoint,
            operation_log=journal["operation_log"],
            display_name=journal["display_name"],
            timestamp=journal["timestamp"],
            source_path=Path(raw_source) if raw_source else None,
        )

    def discard(self) -> None:
        """Securely wipe this session's autosave data - once a session
        closes normally, there's nothing left to recover, and
        confidential data shouldn't linger past its useful life
        (SPEC.md 6.4)."""
        secure_delete_dir(self.dir)
        log.info("Autosave journal discarded", extra={"context": self.session_id})


# --- "most recently active session" pointer ---------------------------------
#
# With one document per tab there are N live journals at once. These
# module-level helpers record which one belongs to the tab the user
# was last looking at, so crash recovery can offer exactly that one
# (see the module docstring for why it's deliberately not all N).


def autosave_root(root: Path | None = None) -> Path:
    """The directory holding every session's journal directory plus the
    active-session pointer."""
    base = root if root is not None else app_data_dir() / "autosave"
    base.mkdir(parents=True, exist_ok=True)
    return base


def mark_active_session(session_id: str | None, root: Path | None = None) -> None:
    """Record `session_id` as the most recently active editing session,
    or clear the pointer entirely when passed None (a clean shutdown -
    nothing left to recover)."""
    pointer = autosave_root(root) / ACTIVE_SESSION_POINTER
    if session_id is None:
        with contextlib.suppress(OSError):
            pointer.unlink()
        return
    pointer.write_text(
        json.dumps({"schema_version": AUTOSAVE_SCHEMA_VERSION, "session_id": session_id}),
        encoding="utf-8",
    )


def active_session_id(root: Path | None = None) -> str | None:
    """The session id recorded by `mark_active_session`, or None if
    there isn't one (or the pointer is unreadable/corrupt - a bad
    pointer means "no recovery available," never a crash)."""
    pointer = autosave_root(root) / ACTIVE_SESSION_POINTER
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read active session pointer: %s", exc)
        return None
    session_id = data.get("session_id")
    return session_id if isinstance(session_id, str) else None


def recover_active_session(root: Path | None = None) -> AutosaveRecovery | None:
    """The last checkpoint of the most recently active session, if one
    survived (i.e. the app died before that session was closed
    normally). None when there's nothing to recover."""
    session_id = active_session_id(root)
    if session_id is None:
        return None
    base = autosave_root(root)
    # Checked before constructing the journal: AutosaveJournal.__init__
    # mkdirs its own directory, so instantiating one for a session
    # whose data was already discarded would recreate an empty orphan.
    if not (base / session_id / "journal.json").exists():
        return None
    recovery = AutosaveJournal(session_id, root=base).recover()
    if recovery is None or recovery.checkpoint_path is None:
        return None
    if not recovery.checkpoint_path.exists():
        return None
    return recovery


def discard_active_session(root: Path | None = None) -> None:
    """Wipe the most recently active session's journal and clear the
    pointer - called once its recovery offer has been accepted or
    declined, so the same offer can't reappear on the next launch."""
    session_id = active_session_id(root)
    base = autosave_root(root)
    if session_id is not None and (base / session_id).exists():
        AutosaveJournal(session_id, root=base).discard()
    mark_active_session(None, root)
