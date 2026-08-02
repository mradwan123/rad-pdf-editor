"""Multi-pass secure delete for confidential working files (SPEC.md
sections 1 and 6.4: "wipe on close, not just delete").

A plain `os.remove` only unlinks the directory entry - the underlying
disk blocks are typically still recoverable until overwritten. These
functions overwrite file contents with random bytes before unlinking,
which is what CLAUDE.md's "secure temp-file handling" constraint means
in practice for a local, offline app handling regulated documents.

Not a defense against forensic recovery on wear-leveling flash/SSDs
(the OS/drive controller may relocate blocks silently) - that would
need OS/filesystem-specific TRIM-aware tooling, out of scope here.
This is the standard, portable best-effort mitigation.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.errors import SecurityError
from core.logging_config import get_logger

log = get_logger(__name__)

DEFAULT_PASSES = 3


def secure_delete_file(path: Path, passes: int = DEFAULT_PASSES) -> None:
    """Overwrite `path` with random bytes `passes` times, then remove
    it. A no-op if `path` doesn't exist (idempotent cleanup)."""
    if not path.exists():
        return
    if passes < 1:
        raise SecurityError(f"passes must be >= 1, got {passes}.")

    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            for _ in range(passes):
                f.seek(0)
                f.write(os.urandom(size))
                f.flush()
                os.fsync(f.fileno())
        path.unlink()
    except OSError as exc:
        raise SecurityError(f"Could not securely delete '{path}': {exc}") from exc
    log.info("Securely deleted file", extra={"context": str(path)})


def secure_delete_dir(path: Path, passes: int = DEFAULT_PASSES) -> None:
    """Securely delete every file under `path`, then remove the now-empty
    directory tree. A no-op if `path` doesn't exist."""
    if not path.exists():
        return

    for root, _dirs, files in os.walk(path, topdown=False):
        root_path = Path(root)
        for name in files:
            secure_delete_file(root_path / name, passes=passes)
        try:
            root_path.rmdir()
        except OSError as exc:
            raise SecurityError(f"Could not remove directory '{root_path}': {exc}") from exc
    log.info("Securely deleted directory tree", extra={"context": str(path)})
