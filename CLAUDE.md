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
Reorder Pages, Rotate Pages, Delete Pages, Compress, Set Metadata
(including creation/modification dates), Rename, Protect, Unlock,
Watermark — each with unit tests, plus an integration test proving
`discover_and_load` wires them all up. A minimal CLI (`cli/main.py`)
exposes all 11 as subcommands.

`core/session/` and `core/security/` are also built now:
- `core/security/secure_delete.py` — multi-pass overwrite + delete.
- `core/security/sandbox.py` — `network_lockdown()` context manager,
  defense-in-depth blocking of non-loopback outbound sockets.
- `core/session/session_dir.py` — `SessionTempDir`, a private
  per-session working directory under `app_data_dir()`, securely wiped
  on close. The CLI uses this instead of the OS system temp dir.
- `core/session/audit_log.py` — append-only local JSONL trail. The CLI
  records every successful run.
- `core/session/autosave.py` — checkpoint-based crash recovery
  (restores the last working-file snapshot; does **not** replay a full
  undo/redo stack from serialized operations - see the module
  docstring for why). Not yet wired into the CLI (a single-shot batch
  tool has little use for it); relevant once the GUI's live editing
  session exists.

`core.logging_config.app_data_dir()` now honors a
`PDFEDITOR_APP_DATA_DIR` env var override, so tests (and anyone else)
can redirect all of the above away from the real per-OS location.

**GUI exists now** (`python -m gui.main`), closing out the last Phase 1
item ("basic thumbnail UI + undo/redo wired to the framework"):
- `gui/controller.py` — `AppController`, Qt-free session/document glue
  (registry, `SessionTempDir`, `AutosaveJournal`, `AuditLog`,
  `DocumentSession`). Unit-tested directly, no display server needed
  (`tests/unit/test_gui_controller.py`).
- `gui/main_window.py` — `MainWindow`: thumbnail grid (`QtPdf` render),
  File/Edit/Tools menus + toolbar, Undo/Redo, error dialogs.
- `gui/dialogs/base_tool_dialog.py` — `BaseToolDialog`, the shared
  dialog shell every tool dialog subclasses (SPEC.md 6.2). One
  dialog per tool_id in `gui/dialogs/`.
- `gui/main.py` — entry point; Qt Fusion style app-wide, wraps the
  whole app lifetime in `network_lockdown()`.
- `tests/integration/test_gui_smoke.py` — drives the real `MainWindow`
  headlessly (`QT_QPA_PLATFORM=offscreen`): open a PDF, apply an
  operation via the actual dialog flow (mocking only `QDialog.exec`
  itself, not the business logic), undo/redo, save, close, confirm
  secure session cleanup.

A `MergeOperation.apply()`-without-any-open-document edge case was
caught and fixed here: `allocate_working_path` (core/ops/common.py)
derives its output dir from `doc.working_path.parent`, so
`AppController.apply_operation` must point a fresh, empty
`DocumentSession.working_path` at the new session dir *before*
calling `apply()`, or Merge would silently fall back to the OS system
temp dir instead of the private session dir.

CI (`.github/workflows/ci.yml`) now runs `mypy core cli gui` and sets
`QT_QPA_PLATFORM=offscreen` for the pytest step on all 3 OSes.

**Branding/theme pass** (SPEC.md 6.2's `styles.qss` is built now):
- `gui/styles.qss` — dark silver/gray/black theme, applied app-wide.
- `gui/palette.py` — `build_dark_palette()`, a `QPalette` applied via
  `app.setPalette()` *before* the stylesheet. This is load-bearing,
  not decoration: QSS alone did not reliably drive
  `QListWidget`'s IconMode selection highlight (it fell back to the
  OS default blue) - the fix was setting `QPalette::Highlight` in
  code, the standard approach for theming Fusion. Don't try to move
  that color into `styles.qss` alone without re-checking this.
- `gui/resources.py` — the "Rad PDF Editor" mark (`build_app_icon`,
  `build_logo_pixmap`), drawn with `QPainter` rather than a checked-in
  binary asset, themed to match the palette.
- App name is "Rad PDF Editor" (`gui/main_window.py`'s `_APP_NAME`) -
  the window title, taskbar icon, and the empty-state welcome screen
  (`MainWindow._build_empty_state`, shown via a `QStackedWidget`
  instead of the thumbnail grid when no document is open) all use it.
  The underlying project/package name is unchanged (this was scoped
  as a GUI branding request, not a project rename).
- Also fixed in this pass: `QPdfDocument.render()` leaves any
  unpainted area of a page fully transparent (alpha=0) rather than
  opaque white, invisible on blank/near-empty pages regardless of
  theme. `MainWindow._render_thumbnails` now composites onto a white
  backdrop before building the `QIcon`.
- Verified visually, not just by test pass/fail: rendered the real
  `MainWindow`/dialogs to PNG via `widget.grab()` under
  `QT_QPA_PLATFORM=offscreen` and viewed them, since a clean pytest
  run doesn't prove a UI actually looks right (it caught the
  transparent-thumbnail bug above, which no assertion had covered).

**Drag-and-drop page reordering** is implemented: `thumbnail_list` uses
`QListWidget.DragDropMode.InternalMove`; `MainWindow._on_thumbnails_reordered`
is connected to the model's `rowsMoved` signal and defers to
`_apply_thumbnail_reorder` via `QTimer.singleShot(0, ...)` (applying an
operation - which rebuilds the list via `_refresh()` - synchronously
from inside `rowsMoved` itself would fight Qt's own post-move
bookkeeping for that same signal). Each thumbnail's `Qt.ItemDataRole.UserRole`
holds which page it represents in the *current* document; reading that
back in visual order after a drop gives `ReorderPagesOperation`'s
`page_order`. Headless test note: real mouse drag-and-drop gestures
aren't reliably simulatable under `offscreen` - the test
(`test_dragging_a_thumbnail_reorders_the_document`) instead calls
`model().moveRow(...)` directly, which emits the identical `rowsMoved`
signal a real drag would, exercising the actual handler rather than a
hand-rolled substitute.

Not yet built from the Phase 1 GUI wishlist: a pipeline/Workflow
builder UI (that's Phase 5).

## Phase 2 — Forms & layout (11 of 11 done)

Per `docs/SPEC.md` section 4's Phase 2 list, implemented: Crop,
Resize, N-up, Grayscale, Header/Footer, Bates/page numbering, Flatten,
Remove Annotations, Fill Form, Sign, Create Forms. All registered via
`discover_and_load`, exposed as CLI subcommands, and as GUI Tools-menu
dialogs (same `BaseToolDialog` pattern as Phase 1). Phase 2 is
complete.

**Create Forms** (`CreateFormFieldOperation`, `core/ops/forms.py`) —
authoring a *new* field, distinct from Fill Form (which only edits
values of fields that already exist). Same explicit page+rect approach
as Sign (no click-to-place canvas yet), and same bottom-left-origin
rect convention, converted internally to `fitz`'s top-left origin -
verified against a real PDF (page height 400, rect `(50,300,250,320)`
came back as `fitz.Rect(50, 80, 250, 100)` exactly as expected, not
just "didn't raise").
- Uses `fitz.Widget`/`Page.add_widget()` rather than hand-built pikepdf
  annotation dictionaries - pikepdf has no "add a field" helper
  (confirmed while building Flatten), and PyMuPDF's `add_widget()`
  handles the `/AcroForm` bookkeeping (creating it if absent,
  registering the field) automatically. Text fields and checkboxes
  work reliably and are fully tested.
- **Known, documented limitation**: `field_type="radio"` creates one
  independent toggle widget, not a member of a mutually-exclusive
  *group*. Tried building a real shared-field-name radio group first
  (not assumed to be impossible) - `Widget.update()` validates a
  shared field name against an already-existing `/Parent /Kids`
  structure and raises `ValueError: bad xref` for one freshly created
  from scratch via this PyMuPDF version's `add_widget()`. Rather than
  ship broken grouping silently, "radio" is documented (module
  docstring + operation docstring + CLAUDE.md here) as a round-styled
  independent toggle, same mechanism as a checkbox. Revisit if a
  future PyMuPDF version supports creating grouped kids directly, or
  if it's worth hand-building the `/Parent /Kids` structure via
  pikepdf post-save.

Fill Form / Sign notes:
- `core/ops/forms.py`'s `FillFormOperation`/`SignOperation` are visual
  data operations, not cryptographic signing - a digital-signature op
  using `pyhanko` (already a project dependency, unused so far) would
  be a distinct future feature, not what "Sign" means here.
- Key API discovery: `pikepdf.Pdf.acroform` gives a
  `QPDFAcroFormDocumentHelper`; `field.set_value(value, True)` sets
  `/V` but does **not** immediately generate an appearance stream (its
  `need_appearance` bool just flags `/NeedAppearances` for the
  *viewer* to handle) - `af.generate_appearances_if_needed()` must be
  called afterward for the value to actually render without relying on
  the viewer. Verified via `fitz`, which renders/extracts widget text
  directly (`page.get_text()` picked up the filled value); `pdfplumber`
  does **not** render annotation/widget appearance streams as part of
  normal text extraction, so it's the wrong tool to verify form fills
  or unflattened annotations with (it *is* right for Flatten's output,
  since that composites into page content, not a widget AP).
- `SignOperation` uses `fitz`/PyMuPDF (like Grayscale) for
  `page.insert_image(rect, filename=...)`. Its `rect` is exposed to
  callers in this package's usual PDF-native bottom-left-origin
  coordinates (matching Crop/Resize/Watermark/HeaderFooter), converted
  internally to fitz's own top-left-origin `Rect` - kept deliberately
  so switching between tools doesn't mean switching coordinate
  conventions.
- `gui/dialogs/fill_form_dialog.py`'s `FillFormDialog` is the one
  dialog whose `__init__` isn't `(parent=None)` - it needs the open
  document's actual field names (from `list_form_field_names()`)
  before it can lay out inputs, so `MainWindow._run_tool` special-cases
  `tool_id == "fill_form"` to construct it directly rather than via the
  generic `_TOOL_DIALOGS` factory dict.
- Bug caught before it shipped (by the "does `_run_tool` actually
  catch this?" question, not by running it): `SignDialog.values()`
  originally raised bare `ValueError` when no image was chosen, but
  `_run_tool`'s `try/except` only catches `PDFEditorError` - would have
  crashed the GUI instead of showing a clean error. Fixed to raise
  `OperationError`.

- `core/ops/layout.py` — Crop, Resize, N-up, Grayscale. Grayscale
  rasterizes affected pages via PyMuPDF (`fitz`) rather than remapping
  color operators in the content stream - a real, working tradeoff
  (loses text selection on converted pages), documented in the module
  docstring, not a shortcut taken silently. N-up reuses `add_overlay`
  with `page.as_form_xobject()` + `pdf.copy_foreign(...)`, the same
  mechanism Watermark/HeaderFooter use for a single page, just called
  per grid cell.
- `core/ops/numbering.py` — Header/Footer and Bates numbering. Unlike
  Watermark's one-overlay-reused-everywhere, each page gets its own
  reportlab-rendered stamp (Bates text differs per page; header/footer
  must match each page's own size for correct positioning).
- `core/ops/forms.py` — Flatten and Remove Annotations. Flatten's key
  discovery: `page.add_overlay()` accepts a raw appearance-stream
  `Object` directly (an annotation's `/AP` `/N`), not just a `Page` or
  `Page.as_form_xobject()` - no need to hand-roll BBox/Matrix/Rect
  placement math. Verified against a manually-constructed annotation
  (pikepdf has no built-in "add an annotation" helper) with `pdfplumber`
  confirming the composited rect landed at the exact `/Rect` position.
- Verification approach matched Phase 1: every op checked against real
  pikepdf/pdfplumber/fitz output (not just "didn't raise") before
  writing the pytest suite - e.g. Grayscale's test samples actual
  pixel values to confirm r==g==b, not just that the file still opens.
- One real bug caught by mypy during this batch, not by a human
  reading it twice: `gui/dialogs/resize_dialog.py` originally named
  its width/height spinbox attributes `self.width`/`self.height`,
  silently shadowing `QWidget.width()`/`height()` (the widget's own
  geometry methods) - `mypy --strict` flagged "cannot assign to a
  method" immediately. Renamed to `page_width`/`page_height`. Worth
  remembering when naming QWidget subclass attributes generally.

## UX polish batch (done)

Four items, each committed/pushed separately after its own full
ruff/mypy --strict/pytest pass:

1. **Dirty-state tracking + unsaved-changes warning.** `AppController`
   (`gui/controller.py`) tracks a conservative `is_dirty` flag - set on
   any `apply_operation`/`undo`/`redo`, cleared on open/save/close.
   `MainWindow._confirm_discard_if_dirty()` gates Open, Close Document,
   and window-close (`closeEvent`) behind a Save/Discard/Cancel prompt,
   only when there's actually something to lose. `_save_as()` now
   returns `bool` so the prompt handler knows whether "Save" actually
   completed before treating the discard-guard as satisfied.
2. **Recent Files.** `core/session/recent_files.py`'s `RecentFiles`
   persists up to 10 recently-opened paths as JSON under
   `app_data_dir()` (Qt-free, respects `PDFEDITOR_APP_DATA_DIR` like
   the audit log/autosave - deliberately not `QSettings`, which would
   write to the real per-OS registry/config location even under
   tests). File > Open Recent rebuilds on `aboutToShow`; a stale entry
   (moved/deleted since last time) shows the normal error and is
   dropped from the list automatically. Reopening through this menu
   goes through the same dirty-check guard as File > Open.
3. **Thumbnail right-click context menu.** Rotate Left/Right, Delete
   Selected, operating on `thumbnail_list.selectedItems()`'s
   `Qt.ItemDataRole.UserRole` page numbers directly - no dialog.
4. **Busy-cursor feedback.** `MainWindow._busy_cursor()` (a status-bar
   "Working..." message + `QApplication.setOverrideCursor(WaitCursor)`
   /`restoreOverrideCursor()`) wraps every `apply_operation`/`undo`/
   `redo` call site. Deliberately not a `QThreadPool` background-
   execution rewrite - operations stay synchronous, this just makes an
   otherwise-unresponsive-looking wait visible.

Three testing gotchas worth remembering for future GUI test-writing in
this project:

- **A `MagicMock()` is not a safe stand-in for a real Qt event.**
  Passing one to `closeEvent()` where the code path can trigger further
  real Qt machinery (a second, real `closeEvent` from the test's own
  cleanup `window.close()` call, hitting an unmocked, blocking
  `QMessageBox.warning()` with no headless UI to click through) hung
  pytest indefinitely - required `kill -9` on the stuck process to
  recover, twice, before root-causing it. Fixed by using a real
  `QCloseEvent()` instance and checking `event.isAccepted()`, and by
  making sure any smoke test that leaves the document dirty calls
  `window.controller.close_session()` before its cleanup `window.close()`
  rather than relying on a bare `close()` to be harmless.
- **`patch.object(QMenu, "exec", fake)` does not actually intercept the
  call**, unlike patching a real Python class's method (every
  `BaseToolDialog` subclass's `.exec` patches work fine, since those
  are genuine Python classes). `QMenu.exec` is a native/compiled
  PySide6 method - the "patched" version is silently bypassed and the
  real blocking modal popup still runs, hanging headlessly the same
  way. Don't try to test a `QMenu`-`.exec()`-driven flow end-to-end;
  test the underlying handler methods directly instead.
- Naming a method `list()` inside a class whose other methods annotate
  parameters as `list[Path]` etc. makes `mypy --strict` resolve those
  later annotations against the method, not the builtin type
  (`core/session/recent_files.py` hit this) - fixed with a
  module-level type alias (`_PathList = list[Path]`) rather than
  renaming the public method.
