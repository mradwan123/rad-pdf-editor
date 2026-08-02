# CLAUDE.md — Project Context for Claude Code

Read `docs/SPEC.md` in full before making architectural changes. It is
the source of truth for requirements, module layout, and the
conventions below. This file is the quick-reference; SPEC.md has the
reasoning.

## What this is

A local, fully offline, cross-platform (Windows/macOS/Linux) PDF
editing suite covering merge, split, edit/sign, security, conversion,
scanning, and automation — see `docs/SPEC.md` section 1 for the full
tool list and locked requirements.

## Non-negotiable constraints

- **No network calls anywhere in the codebase.** Not analytics, not
  update checks, not "just checking a CDN." Confidential/regulated
  documents pass through this app.
- **Secure temp-file handling.** Working copies live in a private
  session temp dir (see `core/logging_config.app_data_dir`-adjacent
  session dir, `core/session/` — not yet built), never the user's
  original files or working directory. Wipe on close, not just delete.
- **Everything is an `Operation`.** Don't build a one-off function for
  a new tool. Subclass `core.model.operation.Operation`, register it
  via a `ToolPlugin` (`core/registry/plugin_base.py`). This is what
  makes undo/redo, autosave, the audit log, and Workflows all work for
  free — see `docs/SPEC.md` section 2.

## Frozen interfaces — do not change signatures without reading SPEC.md 6.1

- `core/model/operation.py` — `Operation`
- `core/model/document.py` — `DocumentSession`
- `core/model/pipeline.py` — `Pipeline`
- `core/registry/plugin_base.py` — `ToolPlugin`

Additive changes only (new optional params/methods). Breaking changes
require a `schema_version` bump and a `CHANGELOG.md` entry.

## Conventions

- **Errors:** raise from `core/errors.py`'s hierarchy only. Don't
  define new exception classes in feature modules — add to
  `core/errors.py` if a new category is genuinely needed.
- **Logging:** `from core.logging_config import get_logger; log =
  get_logger(__name__)`. Never `print()`.
- **Typing:** `mypy --strict` on `core/`, `registry/`; relaxed only in
  `gui/` for incomplete Qt stubs. Every function signature typed.
- **Linting/formatting:** `ruff check .` — must pass before commit.
- **Strings in the GUI:** wrap in `tr()` even though only English
  ships now (i18n-readiness, see SPEC.md 6.2).
- **Widgets:** subclass `BaseToolDialog` (not yet built — Phase 1) for
  any new tool dialog rather than laying out a dialog from scratch.

## Commands

```bash
# install
python -m pip install -e ".[dev]"

# lint / type-check / test (same as CI)
ruff check .
mypy core
pytest

# run the app (once gui/main.py exists)
python -m gui.main
```

## Git workflow

Trunk-based. Short-lived branch per module/feature, PR into `main`,
merge only after CI (ruff + mypy + pytest, all 3 OSes) passes. No
long-lived per-agent branches — see SPEC.md 6.5.

## Where things go

See `docs/SPEC.md` section 2 for the full directory layout and section
3 for which agent owns which directory. When in doubt about whether
something is Core Engine, Conversion, Session/Persistence, Security,
or UI/UX territory, check that table before creating a new module in
the wrong place.

## Current phase

**Phase 1 — MVP ops (in progress)**, per `docs/SPEC.md` section 4.
Phase 0 foundation is merged to `main`. `discover_and_load()` now
registers 11 first-party plugins in `core/ops/`: Merge, Extract Pages,
Reorder Pages, Rotate Pages, Delete Pages, Compress, Set Metadata,
Rename, Protect, Unlock, Watermark — each with unit tests, plus an
integration test proving `discover_and_load` wires them all up.

Still open from the Phase 1 list (SPEC.md section 4): the basic
thumbnail UI + undo/redo wired to the framework in `gui/`. Also still
stubs: `core/session/` (autosave, audit log) and `core/security/`
(secure delete, network sandbox) - both empty packages, not yet
started.
