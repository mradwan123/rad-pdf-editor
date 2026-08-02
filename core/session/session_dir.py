"""Private per-session temp directory (CLAUDE.md: "Working copies live
in a private session temp dir ... never the user's original files or
working directory. Wipe on close, not just delete.").

Lives under `core.logging_config.app_data_dir()` (not the OS system
temp dir and never the user's own folders), so it's alongside the app
log, audit log, and autosave journal - all local-only, all under one
OS-appropriate root (SPEC.md 6.4).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import TracebackType

from core.logging_config import app_data_dir, get_logger
from core.security.secure_delete import secure_delete_dir

log = get_logger(__name__)


class SessionTempDir:
    """A private working directory for one editing session.

    Working copies of the user's document, and any operation
    intermediates (core/ops/common.py's `allocate_working_path`), live
    under `.path`. Call `close()` (or use as a context manager) when
    the session ends to securely wipe it - not a plain delete, per the
    confidential-document requirement (SPEC.md section 1).
    """

    def __init__(self, session_id: str | None = None, root: Path | None = None) -> None:
        self.session_id = session_id or uuid.uuid4().hex
        base = root if root is not None else app_data_dir() / "sessions"
        self.path = base / self.session_id
        self.path.mkdir(parents=True, exist_ok=False)
        self._closed = False
        log.info("Opened session temp dir", extra={"context": str(self.path)})

    def close(self) -> None:
        """Securely wipe and remove this session's temp directory.
        Idempotent - safe to call more than once."""
        if self._closed:
            return
        secure_delete_dir(self.path)
        self._closed = True
        log.info("Closed session temp dir", extra={"context": str(self.path)})

    def __enter__(self) -> SessionTempDir:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
