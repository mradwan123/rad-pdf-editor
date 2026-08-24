# PDF Editor

A local, fully offline, cross-platform PDF suite: merge/split, edit &
sign, security (encrypt/watermark/flatten), compression, conversion
(Word/Excel/PowerPoint/HTML/JPG in both directions), OCR & scan
cleanup, and automation via saved Workflows. The GUI ships as **Rad
PDF Editor**.

Built for handling confidential/regulated documents — **no network
calls anywhere in the codebase**.

## Status

Phases 1–5 of `docs/SPEC.md`'s roadmap are complete: **37 operations**
(36 built-in + the shipped example plugin), each registered via
`discover_and_load`, unit- and integration-tested, and exposed as both
a CLI subcommand (`python -m cli.main`) and a GUI Tools-menu dialog.
Phase 6 — turning the batch suite into a real editor — is planned in
full and started.

| Phase | Scope | State |
|---|---|---|
| 1 | MVP ops — Merge, Extract, Reorder, Rotate, Delete, Compress, Metadata, Rename, Protect/Unlock, Watermark | done (11) |
| 2 | Forms & layout — Crop, Resize, N-up, Grayscale, Flip, Header/Footer, Bates, Flatten, Remove Annotations, Fill Form, Sign, Create Forms | done (12) |
| 3 | Conversions — Word/PowerPoint/Excel/HTML/JPG, both directions | done (10) |
| 4 | Scans — OCR, Deskew, Repair | done (3) |
| 5 | Workflow builder + save/replay, plugin manifests, installers | done |
| 6 | Editor — page viewer, on-canvas editing, markup/redaction, background execution, design system | planned; slice 6a done |

Notable tradeoffs, kept deliberately and documented at the source
rather than buried here:

- **Fill Form / Sign are visual data operations, not cryptographic
  signing** (`core/ops/forms.py`). Create Forms authors brand-new
  fields; its `radio` type is an independent toggle, not a grouped
  mutually-exclusive set.
- **Grayscale and Deskew rasterize the pages they touch**
  (`core/ops/layout.py`, `core/ops/ocr_scan.py`) — those pages lose
  text selection.
- **Conversion is dual-engine, asymmetrically** (`core/ops/convert_common.py`).
  *Into* PDF, LibreOffice headless is primary with a pure-Python
  fallback. *Out of* PDF it is pure-Python always: LibreOffice has no
  working filter chain for PDF → docx/pptx/xlsx (a PDF always imports
  as a Draw document). Each operation records which engine actually
  ran in `describe()`, so fidelity is traceable in the undo stack and
  audit log.
- **OCR requires Tesseract** and says so up front instead of degrading
  silently — no pure-Python fallback for real text recognition exists.
  Deskew is a separate operation because `ocrmypdf`'s bundled
  `deskew=True` flag was tried and found unreliable.
- **Repair is two-tier** (`core/ops/repair.py`): `pikepdf`'s own
  structural recovery, falling back to a Ghostscript `pdfwrite` pass
  whose output is always re-verified by reopening it.

### Infrastructure

A private per-session temp directory (`core/session/session_dir.py`)
holds every working copy — originals are never written — and is
securely wiped, not just deleted (`core/security/secure_delete.py`).
Alongside it: an append-only audit log, checkpoint-based autosave/crash
recovery, recent files, and a defense-in-depth network lockdown
(`core/security/sandbox.py`). Both the CLI and GUI are wired to all of
them.

### Workflows and plugins

A saved Workflow is a named `Pipeline` serialized to JSON under
`app_data_dir()/workflows/`. Loading one back needs no per-operation
code: every `Operation.serialize()`'s `"type"` matches its
`ToolPlugin.tool_id`, so reconstruction is
`registry.get(type).build_operation(**kwargs)`. The GUI's Workflows
menu and the CLI's `list-workflows` / `run-workflow` are two surfaces
over the same store. Building a workflow reuses each tool's real
dialog; running one is an unattended batch pass over an external
input/output pair, deliberately not woven into the open document's
undo stack.

Third-party plugins are a `plugin.json` directory scan of `plugins/`
(chosen over Python `entry_points` — no pip-publish step to add one
tool). `plugins/example_plugin/` is a complete working example; a
malformed manifest is skipped with a warning, never a startup crash.
See [`plugins/README.md`](plugins/README.md).

Standalone builds via PyInstaller (`packaging/`) — built and launched
on Linux; the Windows/macOS specs are identical but unverified on real
hardware, see [`packaging/README.md`](packaging/README.md).

## The GUI

`python -m gui.main` — PySide6, Qt Fusion, a dark theme
(`gui/palette.py` + `gui/styles.qss`) and a programmatically-drawn
icon/logo (no binary assets checked in).

- **Multi-document tabs.** Each tab is a fully independent editing
  session — its own working copy, undo/redo stack and dirty marker
  (`•`) — so an operation in one document can never reach another.
  Closable, drag-reorderable, `Ctrl+W` to close, `Ctrl+Tab` /
  `Ctrl+Shift+Tab` to cycle. Closing a tab wipes that document's
  working files immediately. Opening asks new tab vs. replace current.
- **Thumbnail grid** per tab, with drag-to-reorder pages (a real
  undoable `ReorderPagesOperation`) and a right-click menu for
  rotate/delete.
- **View menu**: thumbnail zoom from 60px up to 720px (3×, re-rendered
  from the PDF at each step, not upscaled), toolbar/status-bar
  toggles, full screen.
- **Document Properties** (`File > Properties...`, `Ctrl+D`): a
  read-only report on the active tab's document — its metadata, the
  file on disk (with an explicit unsaved-changes line, since
  everything else describes the in-memory working copy), page
  count/size/orientation, calling out mixed page sizes rather than
  reporting page 1's as the whole document's, and PDF
  version/fast-web-view/tagged/encryption with the permission bits.
  Copies to the clipboard as plain text; "Edit Metadata..." hands off
  to the ordinary Metadata tool so any real edit stays undoable and
  audited. The reading is Qt-free in `core/document_info.py`;
  creation time comes from `core/file_times.py`, which calls `statx()`
  on Linux because `os.stat()` exposes `st_birthtime` only on
  macOS/Windows.
- **Interactive signature placement**: the Sign dialog renders the
  target page and lets you drag and corner-resize the signature image
  on it, two-way bound to the numeric rect fields. The preview mirrors
  what PyMuPDF will actually produce, including its
  square-image aspect-ratio quirk.
- **Crash recovery**: the next launch offers to restore the tab you
  were last working in.
- 36 tool dialogs, grouped into eight Tools submenus, all sharing
  `BaseToolDialog`. `gui/controller.py` keeps the session/document
  glue Qt-free and unit-testable without a display server;
  `tests/integration/test_gui_smoke.py` drives the real window
  headlessly under `QT_QPA_PLATFORM=offscreen`.

## Phase 6 — Editor (in progress)

Phases 1–5 built 36 whole-document/whole-page *transforms* behind a
thumbnail grid. There is still no page viewer, no text selection, no
find, no annotation and no redaction — that gap is Phase 6, and it is
GUI work **plus a new class of `Operation`**. The frozen interfaces
(`Operation`, `DocumentSession`, `Pipeline`, `ToolPlugin`) are not
touched; Phase 6 adds one optional additive method.

[`docs/GUI_PLAN.md`](docs/GUI_PLAN.md) is the design record: thirteen
locked decisions, the sequencing (6a–6h), and the risks. Two library
findings shaped it, both verified against the installed versions:
QtPdf's search/selection/outline/async-render model classes all work
without the `QPdfView` widget (so a custom `QGraphicsView` canvas gets
editable overlays *and* real text selection), and PyMuPDF already
covers every annotation type plus `apply_redactions` — true redaction
that removes content, not a black rectangle over extractable text.

**6a (decompose `gui/main_window.py`) is done**: 1174 → 541 lines as a
pure move into `gui/actions.py`, `tab_manager.py`, `tool_runner.py`,
`rendering.py` and `window_parts.py`, with zero test changes. It lives
on the `worktree-gui-editor-plan` branch and is **not yet merged to
`main`**. Slices 6b–6h (async rendering, page viewer, background
execution, markup/insert, redaction, design system, text editing) are
not started.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

System prerequisites, all optional except where noted:

| Tool | Needed for | If missing |
|---|---|---|
| [LibreOffice](https://www.libreoffice.org/) | higher-fidelity Office/HTML → PDF conversion | pure-Python fallback runs automatically |
| `tesseract-ocr` | **required** by the OCR tool | OCR raises a clear error; Deskew and Repair are unaffected |
| Ghostscript (`gs`) | Repair's second-tier engine | most corrupt PDFs are recovered by `pikepdf` alone |

## Development

```bash
ruff check .          # lint
mypy core cli gui     # strict on core/cli, relaxed on gui
pytest                # set QT_QPA_PLATFORM=offscreen if headless
```

Full suite: **478 passing**. CI (`.github/workflows/ci.yml`) runs those
three commands on Linux, macOS and Windows; the Linux leg installs PySide6's
native library dependencies (`libegl1`, `libgl1`, `libxkbcommon0`,
`libfontconfig1`, `libdbus-1-3`) — the offscreen platform plugin needs
those five, and nothing from the xcb stack.

## Documentation

- [`docs/SPEC.md`](docs/SPEC.md) — technical specification,
  architecture, workstream breakdown, roadmap.
- [`docs/GUI_PLAN.md`](docs/GUI_PLAN.md) — the Phase 6 design record
  (lives on the `worktree-gui-editor-plan` branch until it merges).
- [`CLAUDE.md`](CLAUDE.md) — conventions and the full engineering
  log: every non-obvious finding, tradeoff and bug behind the summary
  above.
- [`plugins/README.md`](plugins/README.md) — plugin manifest format
  and a walkthrough.
- [`packaging/README.md`](packaging/README.md) — standalone builds and
  their per-OS verification status.
