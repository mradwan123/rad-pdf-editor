# PDF Editor

A local, fully offline, cross-platform PDF editing suite: merge/split,
edit & sign, security (encrypt/watermark/flatten), compression,
conversion (Word/Excel/PowerPoint/HTML/JPG in both directions), OCR
& scan cleanup, and automation via saved Workflows.

Built for handling confidential/regulated documents — no network calls
anywhere in the codebase.

## Status

**Phase 1 — MVP ops: done.** Core interfaces (`Operation`,
`DocumentSession`, `Pipeline`, plugin `Registry`) are frozen and
tested. First-party operations: Merge, Split/Extract, Organize
(reorder), Rotate, Delete Pages, Compress, Metadata (incl. creation/mod
dates), Rename, Protect/Unlock, Watermark.

**Phase 2 — Forms & layout ops: done (12 of 12).** Crop, Resize, N-up,
Grayscale (rasterizes affected pages — see `core/ops/layout.py`'s
module docstring for the tradeoff), Flip, Header/Footer, Bates/page
numbering, Flatten, Remove Annotations, Fill Form, Sign, Create Forms.
Fill/Sign are visual/data operations (set AcroForm field values; place
a signature image at a page/rect), not cryptographic signing — see
`core/ops/forms.py`'s module docstring. Create Forms authors brand-new
fields (a different feature from Fill Form, which only edits values of
fields that already exist); text fields and checkboxes are fully
supported, radio fields are independent toggles rather than a grouped
mutually-exclusive set — see `core/ops/forms.py`'s module docstring
for why grouping isn't supported yet.

**Phase 3 — Conversions: done (10 of 10).** Word/PowerPoint/Excel/
HTML/JPG, both directions. Dual-engine: LibreOffice headless
(`soffice --convert-to`, if installed) is the primary engine wherever
it has a real filter for the pair involved, with a pure-Python
fallback (python-docx, python-pptx, openpyxl+reportlab, xhtml2pdf)
used automatically otherwise. The "PDF -> Office format" direction is
pure-Python only in every case — confirmed by hand against the real
`soffice` binary that LibreOffice has no working filter chain for
PDF -> docx/pptx/xlsx at all (a PDF always imports as a Draw document,
whose export filters don't cover Office formats) — see
`core/ops/convert_common.py`'s module docstring for the full detail
and the "external file -> PDF" direction, where LibreOffice genuinely
is the primary engine. `core/ops/convert_from.py` and
`core/ops/convert_to.py` split the two directions; each operation
records which engine actually ran in `describe()`, visible in the
undo-stack UI and audit log.

**Phase 4 — Scans: done (3 of 3).** OCR, Deskew, Repair. OCR
(`core/ops/ocr_scan.py`) wraps `ocrmypdf`/Tesseract directly - no
pure-Python fallback exists for real text recognition, so it's a
required system prerequisite (unlike LibreOffice in Phase 3), and the
operation raises a clear error up front if `tesseract` isn't found.
Deskew is deliberately its own operation, not `ocrmypdf`'s bundled
`deskew=True` flag - that flag was tried first and found unreliable by
hand (no standalone mode, and its angle detection silently reported
`0.000°` on a page hand-rotated by a real 8°, no error). The `deskew`
package (Hough-transform-based) was verified instead: detected the
same rotation as `-7.999999999999986°` and, once applied, produced a
genuinely level page. Repair (`core/ops/repair.py`) is two-tier:
`pikepdf`'s own structural recovery first (handles common corruption -
e.g. a truncated file - for free), falling back to Ghostscript's
`-sDEVICE=pdfwrite` repair pass for corruption pikepdf can't parse at
all, with Ghostscript's output always re-verified by reopening via
pikepdf before being trusted (confirmed by hand that `gs` can exit 0
while only partially recovering a file).

**Phase 5 — Workflow builder + save/replay, plugin manifest docs,
installers: done.** A saved Workflow is just a named `Pipeline`
(`core/model/pipeline.py`, frozen since Phase 0) serialized to JSON.
Loading one back into live `Operation`s
(`core/session/workflow_store.py`'s `deserialize_pipeline`) turned out
to need no per-operation code at all, because of a convention verified
by hand across all 36 built-in operations: every
`Operation.serialize()`'s `"type"` field exactly matches its
`ToolPlugin.tool_id`, so reconstruction is just
`registry.get(type).build_operation(**kwargs)`. Saved workflows live
under `app_data_dir()/workflows/` (one JSON file each) - user data,
not the source tree, same convention as recent files/audit log/
autosave. The GUI's Workflows menu ("Build Workflow...", "Run
Workflow...") and the CLI's `list-workflows`/`run-workflow`
subcommands are two independent surfaces over the same store. Building
a workflow reuses every tool's *existing* dialog unchanged (picking a
step just opens that tool's real dialog); running one is a deliberate
batch/unattended operation against an external input/output file pair,
not woven into the currently-open document's live undo stack.

Third-party plugins are real now, not just planned: `plugins/` is
scanned at startup for `plugin.json` manifests
(`core/registry/registry.py`) - a simple directory-scan format chosen
over Python `entry_points` to match this project's small-team/local-
install distribution model (no pip-publish step needed to add one
tool). `plugins/example_plugin/` is a complete, real, working example
("Reverse Page Order") demonstrating the full contract, not just
illustrative markdown - see `plugins/README.md` for the schema and a
walkthrough. A malformed manifest or a plugin that fails to load is
skipped with a logged warning, never a startup crash.

Standalone builds via PyInstaller (`packaging/`) - real build, real
launch, verified on Linux (`packaging/build.sh`); Windows
(`packaging/build.ps1`) uses the identical spec but is not yet
verified on real hardware, see `packaging/README.md`.

All 37 operations (36 built-in + the shipped example plugin) are
registered via `discover_and_load`, covered by unit + integration
tests, and exposed as both CLI subcommands (`python -m cli.main`) and
GUI Tools-menu dialogs.

Session/security infrastructure is in place: a private per-session
temp directory (`core/session/session_dir.py`), secure multi-pass
delete (`core/security/secure_delete.py`), an append-only audit log
(`core/session/audit_log.py`), a checkpoint-based autosave/crash-
recovery journal (`core/session/autosave.py`), and a defense-in-depth
network lockdown (`core/security/sandbox.py`). Both the CLI and GUI
are wired to the session dir, network lockdown, and audit log.

A first working GUI exists (`python -m gui.main`), branded as **Rad
PDF Editor**: PySide6 + Qt Fusion style, a dark silver/gray/black theme
(`gui/palette.py` + `gui/styles.qss` — SPEC.md 6.2's shared stylesheet),
a programmatically-drawn app icon/logo (`gui/resources.py`, no binary
image assets checked in), a branded empty-state welcome screen, a
thumbnail page grid (rendered via `QtPdf`), Open/Save As/Close,
Undo/Redo, and a Tools menu with a dialog for each of the 36 built-in
operations, all subclassing a shared `BaseToolDialog` (SPEC.md 6.2).
`gui/controller.py` holds the Qt-free session/document glue so it's
unit-testable without a display server; `tests/integration/test_gui_smoke.py`
drives the whole window headlessly (open, apply an op, undo/redo,
save, close) via `QT_QPA_PLATFORM=offscreen`.

Pages can be reordered by dragging thumbnails directly in the grid
(applies a real `ReorderPagesOperation`, undoable like everything
else), in addition to the typed-permutation dialog under Tools.

All five phases from `docs/SPEC.md`'s roadmap are now complete.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

**Optional**: install [LibreOffice](https://www.libreoffice.org/) for
higher-fidelity Word/PowerPoint/Excel/HTML <-> PDF conversion (Phase
3). It's a system-level install, not a pip dependency — its absence
just means every conversion op automatically uses its pure-Python
fallback instead (see the Phase 3 status note above).

**Required for OCR**: install `tesseract-ocr` (e.g. `apt install
tesseract-ocr` on Debian/Ubuntu) for the OCR tool (Phase 4). Unlike
LibreOffice, there is no pure-Python fallback for real text
recognition — the OCR operation raises a clear error if it's missing,
rather than degrading silently. Deskew and Repair don't need it.

**Optional for Repair**: Ghostscript (usually already present on
Linux/macOS; `gs` on `PATH`) is used as Repair's fallback engine for
corruption `pikepdf`'s own structural recovery can't handle. Most
corrupt PDFs are recovered by `pikepdf` alone without needing it.

## Development

```bash
ruff check .          # lint
mypy core cli gui     # strict type-check on core/cli, relaxed on gui
pytest                # tests (set QT_QPA_PLATFORM=offscreen if headless)
```

## Documentation

- [`docs/SPEC.md`](docs/SPEC.md) — full technical specification,
  architecture, agent/workstream breakdown, and roadmap.
- [`CLAUDE.md`](CLAUDE.md) — quick-reference conventions for Claude
  Code sessions working in this repo.
- [`plugins/README.md`](plugins/README.md) — third-party plugin
  manifest format and a walkthrough building one.
- [`packaging/README.md`](packaging/README.md) — building a standalone
  installer, and its per-OS verification status.
