"""Non-Qt session/document glue for the GUI.

Kept separate from QWidget code so it's testable without a display
server (see tests/unit/test_gui_controller.py) and so gui/ stays a
thin Qt layer over core/, per SPEC.md's module split - MainWindow
drives this, it doesn't duplicate it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from core.errors import OperationError
from core.logging_config import get_logger
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.registry.plugin_base import ToolPlugin
from core.registry.registry import Registry, discover_and_load
from core.session.audit_log import AuditLog
from core.session.autosave import AutosaveJournal
from core.session.session_dir import SessionTempDir

log = get_logger(__name__)


class AppController:
    """Owns one open document: its session temp dir, autosave journal,
    live `DocumentSession`, undo/redo stack and dirty flag.

    **One instance per open document tab**, not per process (that was
    true up to the multi-tab work - see CLAUDE.md). Everything that is
    genuinely app-wide rather than per-document is passed in and
    shared: the plugin `Registry` (rebuilding it per tab would rescan
    and re-import every plugin for nothing) and the `AuditLog` (one
    append-only trail for the whole app, with each entry already
    carrying its own `document_label`). Both default to a
    freshly-built private instance so a standalone `AppController()` -
    as the unit tests construct - still works exactly as before.
    """

    def __init__(self, registry: Registry | None = None, audit_log: AuditLog | None = None) -> None:
        if registry is None:
            registry = Registry()
            discover_and_load(registry)
        self.registry = registry
        self.audit_log = audit_log if audit_log is not None else AuditLog()
        self._session: SessionTempDir | None = None
        self._autosave: AutosaveJournal | None = None
        self.doc = DocumentSession(working_path=None, source_path=None)
        self._dirty = False

    @property
    def is_open(self) -> bool:
        return self.doc.working_path is not None

    @property
    def session_id(self) -> str | None:
        """This document's session id, or None before any session temp
        dir exists. Used by the GUI to record which tab was most
        recently active for crash recovery (core/session/autosave.py's
        `mark_active_session`)."""
        return self._session.session_id if self._session is not None else None

    @property
    def is_dirty(self) -> bool:
        """True if the current document state may not match what's on
        disk anywhere yet (opening a file, or a successful Save As,
        clears this; applying/undoing/redoing an operation sets it -
        deliberately simple/conservative rather than precisely
        tracking "is this exact state byte-identical to the last
        save": a false "you have unsaved changes" prompt is an
        annoyance, a false "nothing to lose" is a data-loss risk)."""
        return self._dirty

    @property
    def can_undo(self) -> bool:
        return bool(self.doc.operation_log)

    @property
    def can_redo(self) -> bool:
        return bool(self.doc.redo_stack)

    def get_plugin(self, tool_id: str) -> ToolPlugin:
        return self.registry.get(tool_id)

    def open_document(self, path: Path) -> None:
        """Start a fresh session with a private working copy of `path`
        - the original is never touched (SPEC.md section 1).

        The new file is copied in *before* the previously open session
        (if any) is closed, and any failure is raised as a
        `PDFEditorError`. Found in review: the old version closed the
        current session unconditionally, before even trying to read
        the new file - and a missing/unreadable path raised a raw,
        uncaught `OSError` instead of a normal app error. Together
        that meant a failed "Open" (bad path) both crashed the caller
        *and* silently destroyed whatever document was already open.
        """
        new_session = SessionTempDir()
        try:
            working = new_session.path / f"working{path.suffix or '.pdf'}"
            shutil.copyfile(path, working)
        except OSError as exc:
            new_session.close()
            raise OperationError(f"Could not open '{path.name}': {exc}") from exc

        self.close_session()
        self._session = new_session
        self._autosave = AutosaveJournal(new_session.session_id)
        self.doc = DocumentSession(working_path=working, source_path=path)
        self._dirty = False
        self._checkpoint()

    def restore_from_checkpoint(
        self,
        checkpoint: Path,
        *,
        source_path: Path | None = None,
        display_name: str | None = None,
    ) -> None:
        """Open an autosave checkpoint as the current document.

        Same private-working-copy path as `open_document` (the
        checkpoint file itself is left alone), but the restored session
        keeps the *original* document's identity - `source_path` /
        `display_name` come from the journal, not from the checkpoint
        file's own name - and starts **dirty**, because by definition
        the recovered state was never saved anywhere. The undo history
        is not restored: the autosave journal stores serialized
        operations, not re-appliable ones (see core/session/autosave.py's
        module docstring).
        """
        self.open_document(checkpoint)
        self.doc = DocumentSession(
            working_path=self.doc.working_path,
            source_path=source_path,
            display_name=display_name,
        )
        self._dirty = True
        self._checkpoint()

    def apply_operation(self, operation: Operation) -> None:
        """Applies `operation` to the current document. A session temp
        dir is created lazily if none exists yet - Merge, unlike every
        other tool, is meaningful with no document open (it builds one
        from scratch). `allocate_working_path` (core/ops/common.py)
        derives its output directory from `doc.working_path.parent`,
        so an empty `doc` needs `working_path` pointed at the new
        session *before* apply() runs, or it'd fall back to the OS
        system temp dir - the placeholder path itself need not exist."""
        self._ensure_session()
        assert self._session is not None
        if self.doc.working_path is None:
            self.doc = DocumentSession(
                working_path=self._session.path / "empty.pdf",
                source_path=self.doc.source_path,
                display_name=self.doc.display_name,
            )
        self.doc = self.doc.apply(operation)
        self._dirty = True
        label = str(self.doc.source_path) if self.doc.source_path else self.doc.display_name
        self.audit_log.record_operation(operation, document_label=label)
        self._checkpoint()

    def undo(self) -> None:
        self.doc = self.doc.undo()
        self._dirty = True
        self._checkpoint()

    def redo(self) -> None:
        self.doc = self.doc.redo()
        self._dirty = True
        self._checkpoint()

    def save_as(self, path: Path) -> None:
        if self.doc.working_path is None:
            raise OperationError("No document open.")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.doc.working_path, path)
        self._dirty = False
        log.info("Saved document", extra={"context": str(path)})

    def close_session(self) -> None:
        """Discard autosave data and securely wipe the session temp
        dir (SPEC.md 6.4) - call when closing a document or exiting."""
        if self._autosave is not None:
            self._autosave.discard()
            self._autosave = None
        if self._session is not None:
            self._session.close()
            self._session = None
        self.doc = DocumentSession(working_path=None, source_path=None)
        self._dirty = False

    def _checkpoint(self) -> None:
        if self._autosave is not None:
            self._autosave.checkpoint(self.doc)

    def _ensure_session(self) -> None:
        if self._session is None:
            self._session = SessionTempDir()
            self._autosave = AutosaveJournal(self._session.session_id)
