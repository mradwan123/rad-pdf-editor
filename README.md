# PDF Editor

A local, fully offline, cross-platform PDF editing suite: merge/split,
edit & sign, security (encrypt/watermark/flatten), compression,
conversion (Word/Excel/PowerPoint/HTML/JPG in both directions), OCR
& scan cleanup, and automation via saved Workflows.

Built for handling confidential/regulated documents — no network calls
anywhere in the codebase.

## Status

**Phase 1 — MVP ops (in progress).** Core interfaces (`Operation`,
`DocumentSession`, `Pipeline`, plugin `Registry`) are frozen and
tested. First-party operations implemented so far: Merge,
Split/Extract, Organize (reorder), Rotate, Delete Pages, Compress,
Metadata (incl. creation/mod dates), Rename, Protect/Unlock,
Watermark — all registered via `discover_and_load` and covered by
unit + integration tests. A minimal CLI (`python -m cli.main`) exposes
all of them.

Session/security infrastructure is in place: a private per-session
temp directory (`core/session/session_dir.py`), secure multi-pass
delete (`core/security/secure_delete.py`), an append-only audit log
(`core/session/audit_log.py`), a checkpoint-based autosave/crash-
recovery journal (`core/session/autosave.py`), and a defense-in-depth
network lockdown (`core/security/sandbox.py`). Both the CLI and GUI
are wired to the session dir, network lockdown, and audit log.

A first working GUI exists (`python -m gui.main`): PySide6 + Qt Fusion
style, a thumbnail page grid (rendered via `QtPdf`), Open/Save As/Close,
Undo/Redo, and a Tools menu with a dialog for each of the 11
operations, all subclassing a shared `BaseToolDialog` (SPEC.md 6.2).
`gui/controller.py` holds the Qt-free session/document glue so it's
unit-testable without a display server; `tests/integration/test_gui_smoke.py`
drives the whole window headlessly (open, apply an op, undo/redo,
save, close) via `QT_QPA_PLATFORM=offscreen`.

Not yet built: drag-and-drop page reordering in the thumbnail grid
(reorder currently takes a typed permutation), a pipeline/Workflow
builder UI, and `gui/styles.qss` (SPEC.md 6.2's shared stylesheet -
currently just the bare Fusion style).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

## Development

```bash
ruff check .      # lint
mypy core         # strict type-check on core/
pytest            # tests
```

## Documentation

- [`docs/SPEC.md`](docs/SPEC.md) — full technical specification,
  architecture, agent/workstream breakdown, and roadmap.
- [`CLAUDE.md`](CLAUDE.md) — quick-reference conventions for Claude
  Code sessions working in this repo.
