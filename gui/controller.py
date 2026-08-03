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
    """Owns the registry, the current session's temp dir/autosave
    journal, and the live `DocumentSession`. One instance per running
    GUI process."""

    def __init__(self) -> None:
        self.registry = Registry()
        discover_and_load(self.registry)
        self.audit_log = AuditLog()
        self._session: SessionTempDir | None = None
        self._autosave: AutosaveJournal | None = None
        self.doc = DocumentSession(working_path=None, source_path=None)

    @property
    def is_open(self) -> bool:
        return self.doc.working_path is not None

    @property
    def can_undo(self) -> bool:
        return bool(self.doc.operation_log)

    @property
    def can_redo(self) -> bool:
        return bool(self.doc.redo_stack)

    def get_plugin(self, tool_id: str) -> ToolPlugin:
        return self.registry.get(tool_id)

    def open_document(self, path: Path) -> None:
        """Close whatever's currently open, then start a fresh session
        with a private working copy of `path` - the original is never
        touched (SPEC.md section 1)."""
        self.close_session()
        self._ensure_session()
        assert self._session is not None
        working = self._session.path / f"working{path.suffix or '.pdf'}"
        shutil.copyfile(path, working)
        self.doc = DocumentSession(working_path=working, source_path=path)
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
        label = str(self.doc.source_path) if self.doc.source_path else self.doc.display_name
        self.audit_log.record_operation(operation, document_label=label)
        self._checkpoint()

    def undo(self) -> None:
        self.doc = self.doc.undo()
        self._checkpoint()

    def redo(self) -> None:
        self.doc = self.doc.redo()
        self._checkpoint()

    def save_as(self, path: Path) -> None:
        if self.doc.working_path is None:
            raise OperationError("No document open.")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.doc.working_path, path)
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

    def _checkpoint(self) -> None:
        if self._autosave is not None:
            self._autosave.checkpoint(self.doc)

    def _ensure_session(self) -> None:
        if self._session is None:
            self._session = SessionTempDir()
            self._autosave = AutosaveJournal(self._session.session_id)
