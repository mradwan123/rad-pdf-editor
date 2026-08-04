"""Persists the list of recently-opened document paths across app
restarts (the GUI's File > Open Recent menu).

Stored as JSON under `core.logging_config.app_data_dir()` rather than
a Qt-native mechanism (e.g. QSettings) so it stays Qt-free - testable
without a display server, like the rest of core/session/ - and so it
respects the same `PDFEDITOR_APP_DATA_DIR` test-isolation override as
the audit log and autosave journal, instead of writing into the real
per-OS registry/config location during tests.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from core.logging_config import app_data_dir, get_logger

log = get_logger(__name__)

_FILE_NAME = "recent_files.json"
_MAX_ENTRIES = 10

#: Alias so annotations elsewhere in this class don't resolve `list`
#: against the `RecentFiles.list` method instead of the builtin type -
#: mypy treats a method name shadowing a builtin used in later
#: same-class annotations as the method, not the type.
_PathList = list[Path]


class RecentFiles:
    """Most-recently-opened-first list of document paths, deduplicated
    and capped at `_MAX_ENTRIES`."""

    def __init__(self) -> None:
        self._path = app_data_dir() / _FILE_NAME

    def list(self) -> _PathList:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read recent files list: %s", exc)
            return []
        return [Path(p) for p in raw]

    def add(self, path: Path) -> None:
        entries = [p for p in self.list() if p != path]
        entries.insert(0, path)
        self._write(entries[:_MAX_ENTRIES])

    def remove(self, path: Path) -> None:
        """Drop a path that's turned out to be stale (moved/deleted) -
        called after a recent-file open attempt fails, so a dead entry
        doesn't keep reappearing in the menu."""
        self._write([p for p in self.list() if p != path])

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()

    def _write(self, entries: _PathList) -> None:
        self._path.write_text(
            json.dumps([str(p) for p in entries]), encoding="utf-8"
        )
