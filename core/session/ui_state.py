"""Window and session state that survives a restart.

Phase 6g (docs/GUI_PLAN.md §3.7, decision 13). Qt-free and honouring
`PDFEDITOR_APP_DATA_DIR`, matching `recent_files.py`, `audit_log.py`
and `autosave.py` - deliberately **not** `QSettings`, which writes to
the real per-OS registry/config location even under tests.

Reopening documents is a genuine privacy tradeoff for an app whose
premise is confidential material: launching it in a meeting room should
not redisplay whatever was last open. So it is governed by
`reopen_documents`, which the user can turn off while keeping panel
layout, and `clear_session()` forgets the document list on demand.
Restored documents are reopened from their **original paths** through
the normal open flow - session temp dirs are never resurrected.

A corrupt or unreadable state file means "no saved state", never a
crash: this is a convenience, and it must not stop the app starting.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.logging_config import app_data_dir, get_logger

log = get_logger(__name__)

_STATE_FILENAME = "ui_state.json"
#: Bumped only if the shape changes incompatibly; an unknown version is
#: discarded rather than guessed at.
_STATE_VERSION = 1


@dataclass
class UiState:
    """Everything the window remembers between runs."""

    theme: str = "dark"
    window_width: int = 0
    window_height: int = 0
    sidebar_width: int = 0
    show_sidebar: bool = True
    show_toolbar: bool = True
    show_statusbar: bool = True
    show_history: bool = False
    thumbnail_width: int = 0
    zoom: float = 0.0
    #: Absolute paths of the documents open at the last clean shutdown.
    open_documents: list[str] = field(default_factory=list)
    #: The privacy switch - see the module docstring.
    reopen_documents: bool = True


def state_path() -> Path:
    return app_data_dir() / _STATE_FILENAME


def load_ui_state() -> UiState:
    """The saved state, or defaults if there is none or it is unusable."""
    path = state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return UiState()
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable UI state at %s: %s", path, exc)
        return UiState()
    if not isinstance(raw, dict) or raw.get("version") != _STATE_VERSION:
        log.warning("Ignoring UI state with unexpected version at %s", path)
        return UiState()
    known = {f for f in UiState.__dataclass_fields__}
    # Unknown keys are dropped rather than passed to the constructor, so
    # a state file written by a newer build cannot crash an older one.
    return UiState(**{k: v for k, v in raw.get("state", {}).items() if k in known})


def save_ui_state(state: UiState) -> None:
    path = state_path()
    payload: dict[str, Any] = {"version": _STATE_VERSION, "state": asdict(state)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not save UI state to %s: %s", path, exc)


def clear_session_documents() -> None:
    """Forget which documents were open, keeping the rest of the layout.

    The action behind "Clear saved session" - the point is that a user
    can drop the document trail without losing their panel setup.
    """
    state = load_ui_state()
    state.open_documents = []
    save_ui_state(state)
