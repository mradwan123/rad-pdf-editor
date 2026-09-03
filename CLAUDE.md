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

## Phase 2 — Forms & layout (12 of 12 done)

Per `docs/SPEC.md` section 4's Phase 2 list, implemented: Crop,
Resize, N-up, Grayscale, Flip, Header/Footer, Bates/page numbering,
Flatten, Remove Annotations, Fill Form, Sign, Create Forms. All
registered via `discover_and_load`, exposed as CLI subcommands, and as
GUI Tools-menu dialogs (same `BaseToolDialog` pattern as Phase 1).
Phase 2 is complete.

**Flip** (`FlipOperation`, `core/ops/layout.py`) was originally missing
from the shipped Phase 2 batch despite being in `docs/SPEC.md` section
4's list alongside crop/resize/N-up/etc — the README/CLAUDE.md tracking
had silently dropped it rather than descoping it deliberately. Added
using the same content-stream-matrix-prepend technique
`ResizeOperation` already uses: a horizontal flip prepends `-1 0 0 1
{width} 0 cm`, a vertical flip prepends `1 0 0 -1 0 {height} cm`,
wrapped in `q`/`Q`. Unlike Crop/Resize, mediabox/cropbox are left
untouched — only orientation changes, not dimensions. Tested with a
real pixel check (a marker drawn in one corner of a rendered page must
end up in the mirrored corner after apply, and back after undo), not
just "didn't raise" — this project's established verification
convention (Grayscale's tests do the same for color).

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

## Phase 3 — Conversions (10 of 10 done)

Per `docs/SPEC.md` section 4's Phase 3 list: Word/PowerPoint/Excel/
HTML/JPG, both directions. `core/ops/convert_from.py` (PDF is the
source) and `core/ops/convert_to.py` (an external file is the source,
shaped like `MergeOperation` - works even with no document open, per
the same "external file(s) in" pattern) - all registered via
`discover_and_load`, exposed as CLI subcommands, and as GUI Tools-menu
dialogs (same `BaseToolDialog` pattern as Phase 1/2). New pip deps:
`python-docx`, `python-pptx`, `xhtml2pdf` (`openpyxl`/`reportlab`/
`pdfplumber`/`PyMuPDF` were already present). LibreOffice itself is an
**optional system prerequisite**, not a pip dependency.

**Engine strategy - not symmetric between the two directions**, and
this was discovered by hand-testing the real `soffice` binary, not
assumed from documentation:

- **External file -> PDF** (`convert_to.py`): LibreOffice headless
  (`soffice --headless --convert-to pdf`) is the primary engine for
  all four Office/HTML ops, confirmed working for docx/pptx/xlsx/html
  all export straight to PDF reliably. Falls back to pure-Python
  (python-docx/python-pptx/openpyxl -> reportlab platypus/canvas
  reconstruction, or xhtml2pdf for HTML) automatically when
  `soffice`/`libreoffice` isn't on `PATH`.
- **PDF -> external file** (`convert_from.py`): pure-Python only,
  *always*, no LibreOffice attempt at all - for both docx and pptx
  targets. Tried the natural-seeming assumption first ("Impress must
  have a PDF import filter") and it's wrong: a PDF always imports into
  LibreOffice as a *Draw* document, and Draw's export filter set only
  covers odg/pdf/image formats - `soffice --convert-to docx` (or
  `pptx`) from a PDF source fails outright with `Error: no export
  filter for ...sample.docx found, aborting.`, reproduced directly
  against the installed binary for both targets. Calc additionally has
  no PDF-import filter whatsoever (a separate, even more fundamental
  gap - it can't open a PDF at all). So unlike the reverse direction,
  there's no working LibreOffice path here to even attempt before
  falling back to pdfplumber/fitz-based extraction.
- `core/ops/convert_common.py` centralizes `libreoffice_binary()` (an
  availability check every op branches on) and
  `run_libreoffice_convert()` (the subprocess wrapper: a fresh,
  securely-wiped `-env:UserInstallation` profile per call so no state
  persists and the real user profile is never touched; raises the
  existing `ConversionError` on missing binary/timeout/nonzero
  exit/missing output).
- Every dual-engine operation records which engine actually ran in
  `describe()` (e.g. `"Converted Word document to PDF (LibreOffice)"`
  vs `"...(pure-Python fallback)"`), visible in the undo-stack UI and
  audit log - so fidelity expectations are traceable after the fact,
  not just "converted" with no indication of which path was taken.

**PDF -> PPTX fallback** renders each page as a full-slide image (via
fitz, at `dpi`) rather than attempting shape/text reconstruction -
there is no reliable pure-Python way to turn arbitrary PDF content
into editable slide shapes, so this deliberately trades editability
for visual fidelity instead of shipping a lossy guess silently.
**PDF -> XLSX** is pdfplumber table extraction only (`extract_tables()`
per page) - no attempt at capturing non-tabular page content.
**PDF -> DOCX/HTML** fallbacks are text-only (pdfplumber
`extract_text()` per page) - no layout, fonts, or images. All three
tradeoffs are documented in their operation's own docstring, same
transparency convention as Grayscale's rasterization tradeoff.

**HTML -> PDF fallback's security detail**: xhtml2pdf's
`link_callback` resolves every resource an HTML document references
(img src, link href, ...) to a local path it reads directly.
`_reject_remote_uri` in `convert_to.py` explicitly raises on anything
that isn't already a local path or a `data:` URI - defense in depth on
top of `network_lockdown()`, since a crafted HTML file's remote
`<img src>` is exactly the kind of attempted-outbound-fetch surface
the "no network calls anywhere" requirement (SPEC.md section 1) is
meant to close off, not something to rely on the socket-level lockdown
alone to catch.

**Real security gap found and fixed, not just theorized**:
`network_lockdown()`'s socket patch is process-local and does not
extend to the LibreOffice subprocess - confirmed by hand that this
wasn't hypothetical. A converted HTML file referencing a remote
`<img src>` made LibreOffice actually attempt an outbound TCP
connection: an 11s conversion vs. the ~1s local-only baseline,
timed against an RFC 5737 black-hole address
(`http://192.0.2.1:81/probe.png`) - a real, measurable leak, against
the app's own hard "no network calls anywhere" requirement (SPEC.md
section 1), and not caught by the `_reject_remote_uri` guard, which
only wraps the xhtml2pdf *fallback* path, not the LibreOffice primary
path used for all four Office/HTML conversions. Fixed centrally in
`run_libreoffice_convert` (`convert_common.py`): every proxy env var
(`http_proxy`/`https_proxy`/`ftp_proxy`/`all_proxy`, upper and lower
case, plus clearing `no_proxy` so nothing can exempt a host from it)
is forced to a dead loopback address (`http://127.0.0.1:1`) for the
subprocess's environment - confirmed by hand that this brings the same
malicious HTML back down to the ~1s baseline (LibreOffice's own
outbound attempts now fail closed immediately, never reaching a real
remote host). This is an environment-level mitigation, not a kernel-
enforced boundary - a component that ignores proxy env vars entirely
wouldn't be stopped by it - so it's still documented as the same class
of limitation `core/security/sandbox.py` already documents about its
own socket patch. True OS-level network isolation for subprocesses
remains the open item `docs/SPEC.md` section 5 already lists.

**Real bug found and fixed while wiring the CLI**: `cli/main.py`'s
`main()` left `DocumentSession(working_path=None, ...)` for
external-source tool_ids (this was already latent for `merge`, just
never triggered). `allocate_working_path` falls back to the bare OS
system temp dir root when `working_path` is `None` - and
`run_libreoffice_convert`'s profile-dir logic (`out_dir.parent /
"lo_profile_..."`) then climbs *one level above that*, landing on `/`
- a real `PermissionError` reproduced live via the CLI
(`html_to_pdf`), not just theorized. Fixed the same way CLAUDE.md
already documents for the GUI's `AppController.apply_operation`:
point a fresh, empty `working_path` at the session dir before calling
`apply()`. `_EXTERNAL_SOURCE_TOOL_IDS` in `cli/main.py` now covers
`merge` and all five external-source Phase 3 tool_ids with this fix
applied uniformly.

Verification matched Phase 1/2's approach: every op checked against
real output, not just "didn't raise" - opened produced `.docx` files
with `python-docx` and checked paragraph text round-trips, produced
`.xlsx` files with `openpyxl` and checked cell values, rendered PDFs
checked via `pikepdf` page counts, both engine paths exercised for
every dual-engine op (LibreOffice-available tests are
`pytest.mark.skipif`-guarded; fallback-path tests force it via
monkeypatching `libreoffice_binary` so the suite stays deterministic
on machines without `soffice` installed).

## Phase 4 — Scans (3 of 3 done)

Per `docs/SPEC.md` section 4: OCR, Deskew, Repair. `core/ops/
ocr_scan.py` (OCR, Deskew) and `core/ops/repair.py` (Repair) - the
exact module names SPEC.md's directory layout already specified.
Registered via `discover_and_load`, exposed as CLI subcommands and GUI
Tools-menu dialogs, same as every prior phase. 36 operations total.

**`tesseract` was not installed on the dev machine at the start of
this phase** - only `ocrmypdf` (the Python wrapper) and Ghostscript
were present. Installed with explicit approval so OCR could be run and
verified for real, not shipped blind - unlike Phase 3's LibreOffice,
there is no pure-Python fallback for real OCR, so `tesseract_available()`
(`shutil.which("tesseract")`) is checked up front and `OCROperation`
raises a clear `ConversionError` immediately if it's missing, rather
than degrading silently.

**Deskew is deliberately its own operation, not `ocrmypdf`'s bundled
`deskew=True` flag** - and this was a real, hand-verified finding, not
a stylistic preference. Tried the flag first: it has no standalone
mode (always runs the full OCR pipeline just to get a rotation
correction) and its angle detection silently reported `0.000°` - no
correction, no error - on a page hand-rotated by a real 8°, reproduced
on both a sparse and a denser/more realistic text fixture via debug
logging (`ocrmypdf._exec.tesseract`'s `Deskew angle: 0.000` log line).
The `deskew` package (Hough-transform-based, via `scikit-image`) was
verified instead: detected the same rotation as
`-7.999999999999986°` and, once actually applied with the correct
rotation sign (`image.rotate(angle, ...)`, not `-angle` - got this
backwards on the first attempt and caught it by actually looking at
the rendered output, not just trusting the numeric angle), produced a
genuinely level page - confirmed by rendering before/after PNGs and
looking, the same discipline `test_gui_resources.py`'s branding pass
already established for this project.

**A real mypy blocker and its fix**: `scikit-image`'s dependency chain
(`tifffile`, reached via `skimage`'s package-level imports even though
`deskew` itself only touches `skimage.color`/`feature`/`transform`)
ships source using Python 3.12-only syntax (`type X = ...`, PEP 695),
which chokes `mypy` at this project's `python_version = "3.11"` target
(matching the CI matrix). Version-pinning (`numpy<2.1`, `scikit-image<0.24`)
does work but is fragile - a future `pip install` could re-resolve
past it. The fix actually shipped: `follow_imports = "skip"` in a
`pyproject.toml` mypy override for `skimage.*`/`tifffile.*`/
`imageio.*`/`scipy.*` - tells mypy not to parse those modules' source
at all (treated as `Any`), robust to whatever version pip resolves. No
upper-bound pins needed with this in place.

**`OCROperation`** (`core/ops/ocr_scan.py`) wraps `ocrmypdf.ocr()`
directly - PDF-in/PDF-out, the same shape as every Phase 1/2 op, much
simpler than Phase 3's cross-format work since OCR never leaves the
PDF format. `skip_text=True` is the default (leaves pages that already
have real text untouched - the safer default for a confidential-
documents tool); `force_ocr` re-OCRs everything, replacing existing
text; the two are mutually exclusive (`__post_init__` rejects both).
Does **not** expose ocrmypdf's own `deskew` param, given the
unreliability above - that's `DeskewOperation`'s job.
`ocrmypdf.exceptions.ExitCodeException` (the shared base for
`PriorOcrFoundError`, `EncryptedPdfError`, `MissingDependencyError`,
etc.) is wrapped into `ConversionError`.

**`DeskewOperation`** rasterizes `pages` (1-indexed; empty means all)
via `fitz`, runs `deskew.determine_skew()` on each as a grayscale
`numpy` array, and only rewrites pages where a confident angle
(`abs(angle) >= 0.1°`) was actually found - pages already level, or
where no confident angle is detected, are left untouched (passed
through via `fitz.Document.insert_pdf`, not needlessly rasterized).
Same vector/text-selectability tradeoff `GrayscaleOperation` already
documents, applied only to corrected pages. `describe()` reports real
outcome (`"Deskewed 2 of 3 page(s)"`), not just "ran."

**A genuinely useful discovery while testing OCR against a skewed
fixture**: `tesseract` reported "Empty page!!" on a *rotated* 12pt/
300dpi text image even directly via the raw CLI (not an ocrmypdf
pipeline issue - confirmed by running `tesseract` standalone on both
the rotated and unrotated PNG). Small text degrades too much under
rotation-interpolation blur for tesseract's default recognition,
while the *unrotated* version of the exact same text read perfectly.
Composing the two new operations - `DeskewOperation` first, then
`OCROperation(force_ocr=True)` on its output - recovered **word-
perfect** text extraction from the same originally-skewed page. This
isn't a workaround; it validates the whole reason Deskew and OCR are
separate, composable operations rather than one bundled step, and is
worth remembering as the intended real-world usage pattern (deskew
scanned/skewed input before OCR-ing it) rather than something users
need to be told explicitly - just note it here for future reference.

**`RepairOperation`** (`core/ops/repair.py`) is shaped like
`MergeOperation`/`DocxToPdfOperation` - external `source_path`, not
`doc.working_path`, works with no document open (added to `cli/
main.py`'s `_EXTERNAL_SOURCE_TOOL_IDS`) - since the whole point is
recovering a file that might not open via the app's normal "Open" flow
at all. Two tiers, both confirmed against real corrupted fixtures
before being locked in:
1. `pikepdf.Pdf.open()` alone - confirmed: a file truncated mid-stream
   (missing its xref/trailer) opened and re-saved cleanly, no extra
   code needed (qpdf's own structural recovery).
2. Ghostscript's `-sDEVICE=pdfwrite` repair pass, for corruption
   pikepdf can't parse at all - confirmed: randomly mangled bytes
   mid-file made pikepdf raise a **plain `RuntimeError`**
   (`/Count is wrong after flattening pages tree`), not `PdfError`/
   `OSError` - the `except` clause in `repair.py` catches
   `RuntimeError` too specifically because of this, not defensively.
   Ghostscript's output is **always re-verified** by reopening with
   pikepdf before being accepted - confirmed by hand that `gs` can
   exit 0 while only partially recovering a file ("errors that were
   repaired or ignored" in its own stderr), so a clean exit code alone
   is not proof of a usable result.

No network-lockdown subprocess caveat for Repair, unlike Phase 3's
LibreOffice - Ghostscript's `pdfwrite` pass is purely local
file-format processing with no reason to touch the network.

Verification matched every prior phase: real fixtures (a real 300 DPI
scan - a lower-DPI fixture earlier in this investigation produced a
false "OCR failure" that turned out to be a bad fixture, not a real
bug - and real truncated/byte-mangled corrupt PDFs, built the same way
by hand before being turned into test fixtures), `pytest.mark.skipif`
guards on `tesseract_available()`/Ghostscript-availability for
portability to machines without those binaries (`deskew` needs no
system binary, so its tests always run), and a real end-to-end
`MainWindow` round trip confirming GUI wiring, not just unit-level
Operation tests.

## Phase 5 — Workflow builder + save/replay (done; plugin manifest docs and installers still open)

Per `docs/SPEC.md` section 4, Phase 5 is three things: Workflow
builder UI + save/replay, plugin manifest docs, installers. Only the
first is done - the other two are separate, deferred follow-ups, not
started.

**The actual gap "save/replay" needed closed**: `core/model/pipeline.py`'s
`Pipeline` (frozen since Phase 0) already had `run()` and
`serialize()`, but no way back from JSON into live `Operation`s.
Closing it needed zero per-operation code, because of a convention
that's held across all 36 operations built in Phases 1-4, verified by
grep across every `core/ops/*.py` file rather than assumed: every
`Operation.serialize()`'s `"type"` field exactly matches its
`ToolPlugin.tool_id` (the only exception, `restore_snapshot`, is the
internal undo-only helper in `core/ops/common.py` that's explicitly
documented as never persisted). So reconstruction in
`core/session/workflow_store.py`'s `deserialize_pipeline()` is just
`registry.get(data["type"]).build_operation(**kwargs)` per step.
Verified against real output, not just "didn't raise": built a real
3-step pipeline (rotate + flip + watermark, spanning Phase 1/2 ops),
saved it, reloaded it, applied both the original and the reconstructed
version to identical fixture PDFs, and diffed the real results
(rotation flag, watermark text extraction) rather than trusting that
deserialization merely succeeded.

**Where saved workflows live**: `app_data_dir() / "workflows" /
f"{name}.json"` - one file per workflow - **not** the repo's top-level
`/workflows` directory SPEC.md's architecture diagram names. That
reads as a conceptual placeholder, not an instruction to write
user-created runtime data into the source tree; SPEC.md section 6.4's
own policy ("local files under the OS-appropriate app-data directory,
never the user's working directory") already covers this, and
`recent_files.py`/`audit_log.py`/`autosave.py` all already follow it -
`workflow_store.py` is a direct sibling, same pattern, same
`PDFEDITOR_APP_DATA_DIR` test-isolation override.

**GUI: a real circular-import refactor, done properly rather than
worked around.** The Workflow builder needs to reuse every tool's
*existing* dialog (so building a step is configured with the exact
same UI as running that tool directly, not a second hand-rolled form)
via the `_TOOL_DIALOGS` dict that used to live inline in
`gui/main_window.py`. Importing it from a new `gui/dialogs/` module
back into `main_window.py` (which already imports every dialog module)
would be circular. Fixed by moving the dict itself - renamed public
`TOOL_DIALOGS`, no leading underscore, since it's now genuinely shared
- into a new `gui/dialogs/tool_dialog_registry.py`, with
`main_window.py` importing it from there instead. Pure move + rename;
the dict's contents and every dialog class are unchanged.

`WorkflowBuilderDialog` (`gui/dialogs/workflow_builder_dialog.py`) has
a non-standard constructor (`(registry, parent=None)`, needs the live
`Registry` to enumerate tools and build real `Operation`s), same kind
of deviation `FillFormDialog` already has from the plain `(parent=None)`
every ordinary tool dialog uses. Reuses `merge_dialog.py`'s exact
list-plus-Add/Remove/Move-Up/Move-Down shape for the step list.
"Add Step..." explicitly **excludes `fill_form`** from the tool picker
- it needs a live document's actual AcroForm field names, which a
workflow being built in the abstract (against no particular document)
can't supply, same reason `MainWindow._run_tool` already special-cases
it. Picking a tool opens that tool's real dialog from `TOOL_DIALOGS`,
and on accept immediately calls `registry.get(tool_id).build_operation(**values)`
- validates via the Operation's own `__post_init__` right away, not
deferred to run time, and gives a real `Operation` to display via
`describe()` (every operation in this codebase already produces a
sensible pre-apply description - confirmed during Phase 3/4 work,
where the dual-engine ops explicitly say `"...(pending)"` for exactly
this not-yet-applied case). `accept()` is overridden to reject an
empty name or zero steps via `QMessageBox.warning`, dialog stays open
rather than silently producing a broken/empty saved workflow.
`build_pipeline()` is the dialog's actual exit point, not `values()` -
a genuinely different shape than every other tool dialog, and that's
fine; `BaseToolDialog.values()` is a convention other dialogs follow,
not an enforced contract.

`RunWorkflowDialog` (`gui/dialogs/run_workflow_dialog.py`) picks a
saved workflow name + input/output PDF pair. `MainWindow._run_workflow`
runs it through a **throwaway `SessionTempDir`/`DocumentSession`,
never touching `self.controller`'s document or undo stack** -
deliberately not woven into the currently-open document's live undo
stack. This was a real design decision, not an oversight: SPEC.md's
own framing is "replayed against new input files unattended," and
integrating per-step into the live undo/redo stack would flood it with
N entries per run and conflate two different mental models (live
interactive editing vs. batch replay of an external saved sequence).
Verified by test: running a workflow against an external file while a
*different* document is open in the GUI leaves that open document's
`operation_log` completely unchanged
(`test_run_workflow_applies_saved_pipeline_without_touching_open_document`,
`tests/integration/test_gui_smoke.py`). (A later review pass did add
one `self.controller` touch here - see "Phase 5 review pass" below;
the document/undo-stack isolation this paragraph describes still
holds.)

**CLI**: `list-workflows` and `run-workflow <name> <input> -o <output>`
in `cli/main.py` aren't `ToolPlugin`s (they replay a saved *sequence*,
not apply one op), so they're intercepted in `main()` before the
`registry.get(args.tool_id)` plugin lookup, rather than fitting into
`_build_kwargs`'s per-tool_id branches like everything else.

**A real split-brain risk avoided during this phase, worth remembering
for next time multiple agents touch shared files concurrently**: the
GUI dialog work and this backend were built in parallel by two
different processes in the same working tree, and separately, a third
task was committing+pushing *unrelated* already-finished Phase 2/3/4
work from that same tree at the same time. The commit task was given
an explicit, itemized list of exactly which files/hunks belonged to
the already-finished work vs. the in-flight Phase 5 work, told to
re-check `git diff --stat` immediately before `git add`, and told to
stop and report rather than guess if it couldn't tell the two apart in
a shared file - it did have to navigate exactly that situation (`cli/main.py`,
`gui/main_window.py` were being edited by the Phase 5 work while it
ran) and got it right. Also hit: an agent launched with `isolation:
"worktree"` for a git task that needed to operate on this session's
own uncommitted working-tree changes fails structurally - a fresh
worktree is a clean branch off `origin/main`, none of the uncommitted
work is there. Worktree isolation is for tasks that need a disposable
sandbox copy, not for committing what's already sitting in the shared
checkout.

Verification matched every prior phase: real multi-step pipeline
save/load round-trip diffed against direct application (not just
"didn't raise"), `pytest.mark.skipif`-free (no external binary
involved), full CLI (`list-workflows`/`run-workflow` against a real
fixture) and GUI (`WorkflowBuilderDialog`'s add-step/exclude-fill_form/
move-up/validation paths, `RunWorkflowDialog`'s real end-to-end run)
smoke tests, 340/340 full suite passing.

## Phase 5, remainder — plugin manifest + installers (done, Phase 5 fully complete)

`docs/SPEC.md` section 5's manifest-format open item is resolved:
**`plugin.json` directory scan, not Python `entry_points`** -
`entry_points` needs a plugin to be a properly pip-installed package
just to register one tool, real friction against SPEC.md section 1's
"small team, local installs" distribution model that a folder dropped
into `/plugins` doesn't have. `core/registry/plugin_base.py`'s
`PLUGIN_MANIFEST_SCHEMA_VERSION = 1` had been sitting unused since
Phase 0, anticipating exactly this.

**`core/registry/registry.py`'s `_third_party_plugins()`**: scans
`plugins_dir/*/plugin.json`, dynamically imports each declared module
via `importlib.util.spec_from_file_location` (no `sys.path`
pollution, no requiring plugins to be real installed packages),
instantiates the declared class, hands it to the same `registry.register()`
every first-party plugin already uses. A malformed manifest or a
plugin that fails to load/instantiate/register is skipped with a
logged warning - never a crash. `discover_and_load()` gained an
optional `plugins_dir` parameter (defaults to the repo's `/plugins`,
mainly for tests) - purely additive, doesn't touch the frozen
`Operation`/`DocumentSession`/`Pipeline`/`ToolPlugin` interfaces.

**A real bug found and fixed, not a hypothetical**: the first working
version of `_load_plugin_class` used `importlib.util.module_from_spec`
+ `exec_module` without registering the module in `sys.modules`
first. Every dataclass-based `Operation` (i.e. every operation in this
codebase, first- and third-party) hit `AttributeError: 'NoneType'
object has no attribute '__dict__'` on load - Python's `dataclasses`
machinery looks itself up via `sys.modules[cls.__module__]` while
processing field annotations (this project's `from __future__ import
annotations` style guarantees every `Operation` hits that code path),
so a dynamically-loaded module that was never registered in
`sys.modules` breaks it. Fixed by adding `sys.modules[module_name] =
module` before `exec_module()` - the standard, documented pattern for
this exact situation. Also used a synthetic, collision-resistant
module name (`pdfeditor_plugin_{plugin_dir_name}_{module_stem}`, not
just the file stem) - two different plugins each naming their own
module `operation.py` (as this project's own example plugin does)
would otherwise silently clobber each other in the process-global
`sys.modules` dict.

**`plugins/example_plugin/`** ("Reverse Page Order") is a complete,
real, working plugin - not just illustrative markdown - exercising the
full contract end to end, reusing `core/ops/common.py`'s helpers
exactly like a first-party op (those are usable by external code, not
`core/ops`-private). Verified registered from the *actual* repo
`/plugins` directory (not a synthetic fixture) via
`test_the_real_shipped_example_plugin_loads_from_the_default_dir`
(`tests/integration/test_discover_and_load.py`), and its `Operation`
verified against real page reordering, not just "didn't raise."

**A second real gap found while making the example plugin actually
usable end-to-end, not just registrable**: `WorkflowBuilderDialog`'s
"Add Step..." picker (`gui/dialogs/workflow_builder_dialog.py`)
enumerates every registered plugin including third-party ones, but
unconditionally did `TOOL_DIALOGS[tool_id](self)` - a real `KeyError`
crash for any plugin with no matching dialog registered, which
`reverse_pages` (a parameterless tool with no dialog of its own) hit
immediately when actually exercised through the real picker flow, not
just imagined. Fixed: a tool_id absent from `TOOL_DIALOGS` is now
treated as "takes no configuration" and built directly with
`build_operation()` and no dialog shown - correct for `reverse_pages`,
and a reasonable default for any future third-party plugin that
doesn't ship its own dialog.

**Known, documented scope boundary** (`plugins/README.md`): a
third-party plugin is *not* automatically exposed as its own CLI
subcommand or Tools-menu entry - that's true of every tool, first-
party included, each needs its own hand-written `argparse` subparser
in `cli/main.py` and `TOOL_DIALOGS` entry, there's no generic dispatch
mechanism (yet). What *does* work transparently for any registered
plugin, third-party included: programmatic use
(`registry.get(tool_id).build_operation(**kwargs)`), and Workflows -
both the GUI builder (via the fallback above) and the CLI's
`run-workflow`, since `Pipeline.run()` doesn't care what's inside a
step.

**Packaging** (`packaging/`): PyInstaller, one `.spec` used on every
OS. Two real, hand-verified findings while building on Linux (not
assumed to work from reading PyInstaller's docs):
- **`SPECPATH`, not the invocation directory.** Paths inside a `.spec`
  file resolve relative to the spec file's own location, not wherever
  `pyinstaller` was actually run from - a plain `"gui/main.py"` failed
  with `script '.../packaging/gui/main.py' not found` even when
  correctly invoked from the repo root. Fixed with PyInstaller's
  `SPECPATH` builtin + `pathex=[_REPO_ROOT]` (needed separately, for
  `gui/main.py`'s own `from core... import ...` absolute imports to
  resolve).
- **Single-file output, not a one-folder bundle** - the spec's
  `EXE(...)` call is given `a.binaries`/`a.datas` directly rather than
  routed through a separate `COLLECT(...)` step, which is what
  produces PyInstaller's one-folder mode instead. Confirmed by the
  actual build output (`dist/rad-pdf-editor`, a single ELF binary, not
  a directory) - `packaging/build.sh`'s echoed instructions and
  `packaging/README.md` were written to match what the build actually
  produced, not the other way around.
- `collect_all(...)` per package (`pikepdf`, `fitz`, `ocrmypdf`,
  `reportlab`, `pdfplumber`, `deskew`, `skimage`, `pyhanko`) rather
  than hand-picking `hiddenimports` - PyInstaller's static analysis
  doesn't reliably follow everything a compiled-extension package
  loads dynamically.
- The built binary was actually launched (`QT_QPA_PLATFORM=offscreen`,
  stayed running 5+s with an empty log) to confirm the full app - every
  `core`/`gui` import, not just a minimal smoke script - genuinely
  initializes, not just that PyInstaller's own build step exited 0.
  Windows/macOS use the identical spec but are explicitly documented
  in `packaging/README.md` as **not yet verified** on real hardware -
  not claimed as tested when they weren't.

## Phase 5 review pass (done)

A follow-up review of the merged Phase 5 work (workflow builder,
plugin manifests, packaging) found and fixed two real bugs, not just
theoretical gaps:

- **`MainWindow._run_workflow` never recorded to the audit log.**
  Every other path that applies an `Operation` - tool dialogs via
  `AppController.apply_operation`, and the CLI's own `run-workflow`
  (which already had its own audit-log test) - records to the audit
  trail. The GUI's workflow-run path didn't, so a workflow run through
  the GUI silently modified a document with zero audit trail. Fixed by
  recording each step via `self.controller.audit_log` after a
  successful run - the one exception to "never touching
  `self.controller`" noted above; it still never touches
  `self.controller.doc` or the undo stack.
- **Pre-existing, unrelated `mypy` failure** in
  `core/ops/convert_from.py`'s `PdfToXlsxOperation` -
  `openpyxl.Workbook.active` has no type stubs, so mypy inferred the
  generic `_WorkbookChild` base (no `.append()`). Fixed by replacing a
  bare `assert default_sheet is not None` with
  `assert isinstance(default_sheet, Worksheet)` - a real runtime
  guarantee that also fixes the type narrowing.

Four tests were added covering the audit-log fix and gaps found while
reviewing `WorkflowBuilderDialog`/`workflow_store.py` test coverage
(`_remove_selected`, `_add_step`'s error-dialog path on an invalid
operation, and `deserialize_pipeline` raising a proper `PDFEditorError`
- not a raw `KeyError` - for an unknown plugin type).

**Tools menu grouped into submenus.** The Tools menu had grown into
one flat list of every registered tool_id across Phases 1-5 - too long
to scan. Split into eight submenus (Organize Pages, Edit and Design,
Forms and Signatures, Security, Document Properties, Convert from PDF,
Convert to PDF, Scans and Repair); every `TOOL_DIALOGS` tool_id must
appear in exactly one group, checked at menu-build time so a
newly-added tool can't silently go missing from the menu. Verified
live, not just read through: instantiated the real `MainWindow` under
`QT_QPA_PLATFORM=offscreen`, walked the actual `QMenu`/`QAction` tree,
and confirmed all tool_ids land in exactly one submenu. Third-party
plugin tool_ids (e.g. the example plugin's `reverse_pages`) correctly
stay out of the Tools menu entirely, unchanged from the scope boundary
already documented above - the "exactly one group" invariant is scoped
to `TOOL_DIALOGS` keys, not the full registry.

Full suite: **357 passed**, `ruff check .` clean, `mypy core cli gui`
clean - supersedes the "340/340" figure earlier in this doc, which
predates the plugin-manifest/installer and review-pass work.

## View menu (done)

A new `&View` menu in `MainWindow` (`gui/main_window.py`), alongside
File/Edit/Tools/Workflows - four items, scoped exactly to what was
asked for (no undo/redo involvement, no `Operation`, pure window/view
state):

1. **Thumbnail zoom** - Zoom In/Zoom Out/Reset Zoom, width-driven
   (`_THUMBNAIL_ZOOM_MIN_WIDTH = 60`, `_THUMBNAIL_ZOOM_MAX_WIDTH = 240`,
   `_THUMBNAIL_ZOOM_STEP = 20`, default `_THUMBNAIL_SIZE = QSize(120,
   160)`), standard `QKeySequence.StandardKey.ZoomIn`/`ZoomOut`
   shortcuts, `Ctrl+0` for reset. `MainWindow.thumbnail_size` (new
   instance attribute, replacing the old direct use of the module-level
   `_THUMBNAIL_SIZE` constant in both `setIconSize` and
   `_render_thumbnails`) holds the current zoom level; height is always
   derived from *`_THUMBNAIL_SIZE`'s original* aspect ratio
   (`round(width * _THUMBNAIL_SIZE.height() / _THUMBNAIL_SIZE.width())`)
   rather than compounded from the current size step-over-step, so
   repeated zoom in/out can't drift the aspect ratio. `_set_thumbnail_zoom`
   clamps to the min/max, updates `thumbnail_list.setIconSize(...)`,
   and calls `_refresh()` - genuinely re-rendering every thumbnail from
   the PDF at the new size via the existing `_render_thumbnails`, not
   stretching the old pixmaps blurrily (confirmed by test: the actual
   `QIcon.actualSize()` of a rendered thumbnail after zooming matches
   the new `thumbnail_size` exactly).
2. **Toggle Toolbar** - checkable action wired to `self.toolbar.setVisible()`.
   The toolbar previously lived only as a local variable inside
   `_build_actions` (never a `self` attribute, so nothing outside that
   method could reach it) - promoted to `self.toolbar` as part of this
   change, a small but real precondition for the toggle to have
   anything to call.
3. **Toggle Status Bar** - checkable action wired to
   `self.statusBar().setVisible()`.
4. **Full Screen** - checkable action wired to
   `showFullScreen()`/`showNormal()`.

**Full-screen behavior under `QT_QPA_PLATFORM=offscreen` was checked
by hand before writing the test, not assumed** - the task explicitly
flagged this as a possible headless limitation worth investigating
rather than papering over. It is **not** a no-op: a real `MainWindow`
instantiated under the offscreen platform, shown, and toggled via
`full_screen_action.trigger()` genuinely flips
`windowState() == Qt.WindowState.WindowFullScreen` and
`isFullScreen()` both ways (confirmed interactively before the test
was written). So `test_view_menu_full_screen_toggle_reflects_window_state`
asserts the real state transition, not a relaxed/skipped check.

All four items are plain `QAction`s built in a new
`MainWindow._build_view_menu()` (called from the end of
`_build_actions()`) - no `BaseToolDialog` subclass, since none of this
touches an `Operation`, `DocumentSession`, or the undo/redo stack; the
task was explicit that this is pure UI/window state, and CLAUDE.md's
own "everything is an `Operation`" rule is scoped to *tools*, not
view-state toggles like this.

Five new tests in `tests/integration/test_gui_smoke.py`, driving the
real `MainWindow` headlessly (matching the existing convention): zoom
in/out/reset actually resize `thumbnail_list.iconSize()` and
re-render a real thumbnail to the new pixel dimensions; zoom is
clamped at both ends after 50 repeated zoom-in/zoom-out triggers;
toolbar and status-bar visibility actually flip
(`isVisible()`/`isChecked()` both checked, not just the internal
`Qt.WA_WState_Hidden`-style flag); full-screen toggling actually
flips `isFullScreen()` both directions.

Full suite: **362 passed** (357 baseline + 5 new), `ruff check .`
clean, `mypy core cli gui` clean.
## Tool dialogs widened 25% (done)

Every tool dialog in `gui/dialogs/` got 25% wider. Confirmed before
touching anything, not assumed: `grep -rn "resize\|setFixedSize\|
setMinimumWidth\|setMinimumSize\|sizeHint\|setGeometry" gui/dialogs/*.py
gui/main_window.py` turned up exactly one explicit size call in all
of `gui/` - `MainWindow.resize(900, 700)`, unrelated to tool dialogs.
Every dialog's width - including `merge_dialog.py`'s list-plus-buttons
layout, `FillFormDialog`'s dynamically-built form, and
`WorkflowBuilderDialog`'s step list - is purely implicit, computed by
Qt's own layout system via `sizeHint()`, with nothing overriding it
anywhere.

That made the fix a single centralized override:
`BaseToolDialog.sizeHint()` (`gui/dialogs/base_tool_dialog.py`) now
returns `super().sizeHint()` with its width multiplied by
`_WIDTH_MULTIPLIER = 1.25` (height untouched). No per-dialog file was
touched - every subclass inherits it automatically, including the two
with non-standard constructors (`FillFormDialog(field_names, parent)`,
`WorkflowBuilderDialog(registry, parent)`), confirmed directly rather
than assumed to "probably still work": both were instantiated
headlessly and their `sizeHint()` came back at the expected ~1.25x
ratio same as every plain `(parent=None)` dialog.

Why the override is picked up automatically at dialog-open time, not
just returned by a getter nobody calls: Qt's own `show()`/`exec()`
path sizes a top-level widget via `sizeHint()` on first show when
nothing else has called `resize()`/`setGeometry()` on it - confirmed
by hand (`dialog.show()` then reading `dialog.width()` back) that the
live shown width matches the overridden `sizeHint()`, not just that
the method returns the right number in isolation.

Verified visually, same discipline as the branding pass documented
above ("a clean pytest run doesn't prove a UI actually looks right"):
rendered `RotateDialog`, `MergeDialog`, and `WatermarkDialog` to PNG
via `widget.grab()` under `QT_QPA_PLATFORM=offscreen` and looked at
them - labels, inputs, and button rows all scale sensibly into the
extra width, no dialog reads as too wide for its contents or has
broken-looking dead space.

`tests/unit/test_dialog_sizing.py` (new) asserts the ~1.25x ratio
against a real "before" value - not a hardcoded pixel baseline that
would silently rot if a dialog's contents changed - by calling the
*unmodified* `QDialog.sizeHint(dialog)` directly (unbound, on the same
live instance) as the "what it would have been without the override"
comparison point. Covers a plain-constructor dialog (`RotateDialog`),
`add_full_width`'s custom-widget shape (`MergeDialog`), and both
non-standard constructors (`FillFormDialog`, `WorkflowBuilderDialog`),
plus one test asserting `dialog.show()` on a live widget actually
picks up the widened size, not just `sizeHint()` in isolation. No
pre-existing test asserted a specific dialog width/size, so nothing
needed updating for the new expectation.

Full suite: **362 passed** (357 + 5 new), `ruff check .` clean,
`mypy core cli gui` clean.

## Multi-document tabs (done)

The largest structural change in the project so far, and deliberately
confined to `gui/` plus one additive `core/session/autosave.py`
extension - no frozen interface (`Operation`, `DocumentSession`,
`Pipeline`, `ToolPlugin`) was touched at all.

**The architecture: one `AppController` per tab.** `AppController`
(`gui/controller.py`) already owned exactly the right unit of state -
one `SessionTempDir`, one `DocumentSession`, one undo/redo stack, one
dirty flag, one `AutosaveJournal` - it was just documented as "one
instance per running GUI process." Making it per-*document* instead
required no restructuring of what it owns; the only real change is
that the two genuinely app-wide things it used to build privately are
now passed in and shared across all tabs:

- the plugin `Registry` (rebuilding it per tab would rescan `/plugins`
  and re-import every plugin module for nothing), and
- the `AuditLog` (one append-only trail for the whole app; each entry
  already carries its own `document_label`, so a shared trail stays
  unambiguous with several documents open - verified by
  `test_both_controllers_record_into_one_shared_audit_log`).

Both parameters default to a freshly built private instance, so a bare
`AppController()` - as `tests/unit/test_gui_controller.py` constructs -
behaves exactly as it did before. `RecentFiles` stays on `MainWindow`,
app-wide, unchanged.

`gui/document_tab.py`'s `DocumentTab` is the per-tab widget: an
`AppController` plus that document's own thumbnail `QListWidget`
(there's one grid per document now, not one per window).
`MainWindow.controller` / `.thumbnail_list` / `.current_tab` became
read-only properties returning the *active* tab's objects, **or None
when no tab is open** - that None case is real and every caller
handles it, since zero tabs is the empty-state welcome screen.
`MainWindow.tabs()` returns the tabs in current visual order (which
the user can change by dragging).

**`SessionTempDir` needed no change at all** to get the right wipe
granularity - it was already per-instance and idempotent-on-close, so
"close one tab, securely wipe that tab's working files now, leave
every other tab's alone" is just `tab.controller.close_session()` on
the tab being removed. Verified as a real filesystem check, not by
inspection (`test_closing_a_tab_wipes_only_that_tabs_session`: tab A's
session dir is gone, tab B's still exists, *and* tab B then applies a
further operation successfully afterwards - the wipe didn't take
anything the survivor needed).

**Locked product decisions, as implemented:**

1. **Opening always asks New Tab / Replace Current Tab / Cancel**
   (`gui/dialogs/tab_placement_dialog.py`), skipped entirely when zero
   tabs are open (nothing to replace, nothing ambiguous). Replace
   still runs the *target tab's* own dirty check first. `File > Open`
   and `File > Open Recent` both route through
   `MainWindow._open_document_path(path, placement)`.
2. **Full tab-bar scope**: closable tabs, `setMovable(True)`
   drag-to-reorder, a tab-bar context menu (Close / Close Others /
   Close All, each closed tab through its own dirty check), a leading
   `•` on the label of a tab with unsaved changes, `Ctrl+W`, and
   `Ctrl+Tab`/`Ctrl+Shift+Tab` wired explicitly (`QTabWidget` has no
   built-in cycling) - the two cycle actions live in the File menu so
   their shortcuts are actually registered with the window rather than
   just declared on a floating `QAction`.
3. **Crash recovery restores the most recently active tab only** - see
   the autosave section below.
4. **`Run Workflow` is untouched**: still a throwaway
   `SessionTempDir`/`DocumentSession`, still opens no tab and touches
   no tab's `AppController`, document or undo stack. It now reads the
   app-level `self.registry`/`self.audit_log` instead of
   `self.controller.registry`/`.audit_log`, which incidentally fixes a
   latent crash - with tabs, `self.controller` is None when nothing is
   open, and Run Workflow is explicitly usable with nothing open.
5. **View-menu zoom/toolbar/status bar/full screen stay window-level.**
   Zoom re-icon-sizes *every* tab's grid and a tab re-renders when
   activated, so a background tab can never show thumbnails left over
   from a different zoom level (`test_a_new_tab_uses_the_current_window_level_zoom`
   checks the real `QIcon.actualSize()` on both tabs, not just the
   internal size variable).

**`closeEvent` checks every tab, sequentially.** Each tab is made the
visible one before it's asked about (otherwise the prompt names a
document the user can't see), and Cancel on any single tab aborts the
whole window close, leaving the tabs not yet reached untouched.
`_confirm_discard_if_dirty(tab)` and `_save_as(tab=None)` both take
the specific tab, because during a multi-tab close the tab being asked
about is frequently not the one that was active when the close started.

**Drag-and-drop-to-open was checked for and is genuinely absent** from
`main_window.py` (no `dragEnterEvent`/`dropEvent` anywhere) - so it
stayed out of scope rather than being invented as part of this change.

### Autosave scoping

`core/session/autosave.py` gained a module-level "most recently active
session" pointer (`mark_active_session` / `active_session_id` /
`recover_active_session` / `discard_active_session`) - purely
additive, `AutosaveJournal` itself is unchanged. Every tab keeps
checkpointing its own journal exactly as before; the pointer just
records which one crash recovery should offer. `MainWindow` re-marks
it on every tab change, open and applied operation.

Two alternatives were considered and rejected: (a) only letting the
active tab checkpoint at all (switching tabs would then destroy the
other tab's recovery data, which is worse than not offering it), and
(b) purging every other session's journal at startup (that would wipe
a *concurrently running* second app instance's live journals - there's
no single-instance lock).

`AutosaveRecovery` also carries `source_path` now, read with `.get()`
so journals written before the field existed still parse - no schema
bump needed, per the additive-changes policy. Without it a restored
document would have taken its identity from the checkpoint file's own
name rather than the file the user was actually editing.

`MainWindow.restore_autosaved_session()` is called from `gui/main.py`
*after* `window.show()`, deliberately not from `MainWindow.__init__` -
a constructor that can block on a modal prompt is both bad practice
and would make every `MainWindow()` in the test suite hang.
`AppController.restore_from_checkpoint()` opens the checkpoint as a
normal private working copy but keeps the crashed document's identity
and starts **dirty** (the recovered state was, by definition, never
saved). The undo history is not restored - the journal stores
serialized operations, not re-appliable ones, which
`core/session/autosave.py`'s module docstring has documented since
Phase 0.

Known, pre-existing limitation left as-is (not a regression, but worth
recording): a crashed run's *session temp dirs* still linger on disk -
nothing has ever cleaned those up, only the journals. And with two app
instances running, the pointer is app-data-dir-global, so the later
instance's active tab wins.

### Real bugs and surprises found while building this

- **`QAction.triggered` carries a `checked` bool, and PySide6 will
  bind it to an optional first parameter.** Real, not theoretical:
  `_save_as(tab=None)` and `_close_other_tabs(index=None)` connected
  as bare bound methods would have received `False` as `tab`/`index` -
  `_save_as` would then have done `False.controller` and crashed the
  GUI on Ctrl+S. Caught while wiring the actions (the single-document
  `_save_as()` took no parameters, so this hazard simply didn't exist
  before). Fixed by connecting through `lambda: self._save_as()`.
  Worth remembering for any future action whose slot grows an optional
  parameter - it's a silent, type-checker-invisible break.
- **`QMessageBox` can't be used for the New Tab / Replace Current Tab
  prompt.** Qt 6 dropped `setButtonText`, so custom-labelled choices
  need a hand-built box - and a hand-built `QMessageBox` instance's
  `.exec()` is the exact compiled-method trap CLAUDE.md already
  documents for `QMenu.exec`: `patch.object(QMessageBox, "exec", fake)`
  does not intercept it, the real modal dialog runs, and a headless
  test hangs forever. Hence `TabPlacementDialog`, a plain Python
  `QDialog` subclass, which patches exactly like every
  `BaseToolDialog` in the project already does. (The static
  `QMessageBox.warning`/`.question`/`.critical` helpers stay
  patchable - it's only instance `.exec()` that isn't.)
- **A failed open in a new tab used to strand an empty tab.** Found by
  asking what `_open_document_path`'s error branch does now that it
  may have created a tab before the open was attempted; the new tab
  survived the error with no document in it. Fixed by discarding the
  just-created tab on failure (a *replaced* tab correctly keeps its
  existing document, since `open_document` already leaves the current
  one intact when it fails - the Phase-1 regression fix documented
  above). Covered by
  `test_a_failed_open_in_a_new_tab_does_not_strand_an_empty_tab`.
- **Merge-with-no-document had to move.** `_run_tool` creates the tab
  only *after* the dialog is accepted, so a cancelled Merge can't
  leave an empty tab behind either.
- **Theming the tab bar has one landmine**: adding *any*
  `QTabBar::close-button` rule to `gui/styles.qss` replaces that
  button's icon wholesale, so a rule without a valid `image:` silently
  removes the close button from every tab. `styles.qss` therefore
  styles `QTabWidget::pane` and `QTabBar::tab` only, and says so in a
  comment. Verified the same way the branding pass was (SPEC.md 6.2
  discipline): rendered the real three-tab window - one tab dirty - to
  PNG via `widget.grab()` under `QT_QPA_PLATFORM=offscreen` and looked
  at it, confirming the selected tab, the `•` marker and the close
  buttons all read correctly, since a green test run proves none of
  that.
- **Confirmation that the old test-hang warning is still live.** The
  first full run after the refactor, before the tests were adapted,
  hung exactly as CLAUDE.md's "UX polish batch" section warns: a
  `window.close()` with a dirty document reaching a real, unmocked
  modal `QMessageBox.warning`. Rather than fixing it case by case, the
  smoke tests now share a `_force_close(window)` helper that wipes
  every tab's session first (equivalent to Discard on each), so
  `closeEvent` has nothing left to ask about.

### How per-tab isolation was actually verified

Not "the UI looked right" - every isolation claim is checked against
real files and real state:

- `test_two_tabs_have_genuinely_independent_undo_stacks`: applies an
  operation in tab A and undoes it there, then asserts tab B's
  `operation_log`, `redo_stack`, dirty flag **and the actual bytes of
  its own working PDF** (page count + `/Rotate`) are all untouched -
  plus that the two tabs' working paths live in different session
  dirs, and that the Undo/Redo actions follow whichever tab is active.
- `test_closing_a_tab_wipes_only_that_tabs_session`: filesystem check
  on both session dirs, then a further real operation applied to the
  surviving tab.
- `test_close_other_tabs_dirty_checks_each_closed_tab`: three tabs,
  one dirty; asserts the unsaved-changes prompt fired *exactly once*
  (only for the dirty one) and both closed tabs' session dirs are gone.
- `test_close_all_tabs_cancelled_on_a_dirty_tab_keeps_it_open` and
  `test_window_close_checks_every_tab_not_only_the_active_one` (the
  latter dirties a *background* tab and leaves a clean one active -
  the single-document `closeEvent` would have seen nothing to lose).
- `test_tabs_can_be_reordered_and_keep_their_own_documents`: calls the
  real `tabBar().moveTab(...)` - the same call Qt makes at the end of
  a genuine drag - for the same reason
  `test_dragging_a_thumbnail_reorders_the_document` calls
  `model().moveRow(...)`: real drag gestures aren't reliably
  simulatable under `QT_QPA_PLATFORM=offscreen`.
- `test_autosave_restores_only_the_most_recently_active_tab`: two
  edited tabs are abandoned without ever closing a session (what a
  crash actually leaves behind), then a fresh `MainWindow` restores
  **one** tab - the last-*activated* one, deliberately not the
  last-opened one - and the restored working file really carries the
  unsaved 90° rotation while the original file on disk still doesn't.
- `test_run_workflow_touches_no_tab_and_opens_none`: the Phase 5
  isolation test, extended to the multi-tab world - two open tabs,
  both `operation_log`s empty and both working PDFs unrotated
  afterwards, and no third tab opened.
- Qt-free unit coverage underneath all of it
  (`tests/unit/test_gui_controller.py`): two `AppController`s are
  independent sessions, a shared `Registry`/`AuditLog` really is
  shared (and a bare `AppController()` still builds its own), both
  controllers' entries land in one audit trail correctly labelled per
  document, and `restore_from_checkpoint` keeps the crashed
  document's identity while starting dirty. Plus
  `tests/unit/test_autosave.py` for the pointer itself, including a
  corrupt pointer meaning "no recovery," not a crash.

Full suite: **398 passed** (362 baseline + 36 new), `ruff check .`
clean, `mypy core cli gui` clean.

## Thumbnail zoom max raised to 3x, plus a real Ctrl++ bug (done)

**Max zoom: 240px -> 720px** (`_THUMBNAIL_ZOOM_MAX_WIDTH`,
`gui/main_window.py`), exactly 3x, so a user can zoom in on fine page
detail (small print, thin diagram lines) rather than only fitting more
pages on screen at once. Min (60), step (20), and the min-side
clamping test were untouched - the clamp test already referenced
`_THUMBNAIL_ZOOM_MAX_WIDTH`/`_THUMBNAIL_ZOOM_MIN_WIDTH` symbolically
rather than hardcoding 240, so it needed no edit to cover the new
ceiling.

**No render-resolution bug existed, confirmed rather than assumed.**
`_render_thumbnails` calls `QPdfDocument.render(i, self.thumbnail_size)`
- there is no fixed-resolution intermediate cache to outrun, since
`render()` rasterizes the page directly at whatever `QSize` it's given
each call. Verified three ways, not just read: (1) a standalone script
rendering a 15-page fixture (fine 6pt/4pt text plus 0.3pt vertical
ruling lines, built via `fitz` - the kind of content that visibly
blurs if upscaled from a smaller source) at both 240px and 720px
confirmed the returned `QImage`'s actual pixel size matches the
request exactly (720x960) and the 720px PNG is genuinely crisp, not a
blown-up 240px render - both saved and looked at directly. (2) The
existing `QIcon.actualSize()` assertion in
`test_view_menu_zoom_in_out_and_reset_resize_the_icon_and_rerender`
already covers this class of bug and continues to pass unmodified at
the new ceiling. (3) A real `MainWindow`, opened against the same
15-page fixture, zoomed to the new 720px max via
`_set_thumbnail_zoom(_THUMBNAIL_ZOOM_MAX_WIDTH)`, and grabbed
(`widget.grab()`) under `QT_QPA_PLATFORM=offscreen` - the resulting
PNG shows sharp text/lines and a sensibly-scrolling single-column
`QListWidget` grid (IconMode wraps/scrolls for free, confirmed rather
than assumed).

**Performance, measured, not guessed**: the same 15-page fixture's
full thumbnail set re-rendered at the new 720px max in **0.112s**
(standalone `QPdfDocument.render` loop) / **0.188s** (through the real
`MainWindow._set_thumbnail_zoom`, including the white-backdrop
composite and `QListWidgetItem` construction per page) - both trivial,
nowhere near the "multi-minute hang" failure mode the task was
watching for. No intermediate-resolution cap was needed.

**A second, real bug found while exercising the View menu for this
task** (reported separately by the user as "Ctrl++ and the other
options aren't working," folded in here since it's the same code):
`zoom_in_action` was bound only to `QKeySequence.StandardKey.ZoomIn`,
which resolves to the literal `"Ctrl++"` on this platform (confirmed
via `QKeySequence.keyBindings(...)`) - but `+` isn't its own physical
key on most keyboard layouts, it's `Shift+=` on a US layout (and
varies further on non-US ones), so a user pressing the unshifted
`Ctrl+=` - the alternate every major app (browsers, editors) also
binds for exactly this reason - saw nothing happen. Reproduced for
real, not just reasoned about: a headless `MainWindow` with a real
`QTest.keyClick(window, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)`
(the unshifted combination) left `thumbnail_size` unchanged, while the
literal `Key_Plus` combination worked. Fixed by adding an explicit
`"Ctrl+="` alternate via `setShortcuts()` (plural) alongside every
binding `QKeySequence.keyBindings(StandardKey.ZoomIn)` already
provides, rather than replacing the standard binding.
`test_view_menu_zoom_in_keyboard_shortcut_actually_fires` (new) covers
this with real `QTest.keyClick` events through the actual Qt shortcut
-matching machinery, not `.trigger()` (which would pass even if no
shortcut were bound at all, since it calls the slot directly).

**A real, offscreen-platform-specific testing gotcha found while
building that test**: `QTest.keyClick`-driven shortcuts are silently
dropped unless the target window `isActiveWindow()` - under
`QT_QPA_PLATFORM=offscreen`, a bare `window.show()` does **not**
activate it (confirmed: `isActiveWindow()` was `False` immediately
after `show()`), so the new test calls `window.activateWindow()`
before sending key events. This is believed to be a headless-platform
testing artifact, not a real-app bug - a normal top-level window shown
on a real display server is ordinarily the active one - but it's worth
remembering for any future test that drives shortcuts via `QTest.keyClick`
rather than `.trigger()`, since every existing View-menu test up to
this point used `.trigger()` and so never hit it.

**Everything else the bug report asked to double-check was already
correct, verified live rather than assumed from reading the code**:
- `_set_thumbnail_zoom` iterates `self.tabs()` (every open tab) for
  `setIconSize`, and `_refresh()` re-renders only `self.current_tab` -
  both correctly resolve through the post-multi-tab `MainWindow.controller`
  /`.thumbnail_list` properties, not a stale single-document reference.
  All five existing `test_view_menu_*` tests plus the drag-reorder test
  already passed unmodified going into this task, confirming the
  wiring itself was never broken by the tabs merge.
- Toggle Toolbar / Toggle Status Bar / Full Screen were re-checked the
  same way (existing `test_view_menu_toggle_toolbar_visibility` /
  `test_view_menu_toggle_status_bar_visibility` /
  `test_view_menu_full_screen_toggle_reflects_window_state` all still
  pass) - no fix needed for any of the three.

**Drag-and-drop page reordering was also re-verified for this task**
(a related report: "add drag-and-drop reordering," which already
exists per the Phase 1 GUI section above) - genuinely still correct
after the multi-tab merge, not just assumed from a green test.
`_add_tab` connects each tab's `rowsMoved` signal with the tab bound
into the lambda's default argument (`t=tab`) at tab-creation time, so
a reorder's `QTimer.singleShot(0, ...)` deferral can't resolve against
"whichever tab happens to be active by the time it runs" - and
`_apply_thumbnail_reorder`/`_apply_to_tab` take that same `tab`
parameter throughout, never `self.controller`. Confirmed with a live,
two-tab check (not just re-running the existing single-tab
`test_dragging_a_thumbnail_reorders_the_document`): opened tabs A (4
pages) and B (3 pages), reordered tab A via `model().moveRow(...)`
while tab B was in the background, and confirmed tab A alone recorded
the `reorder_pages` operation while tab B's operation log and working
PDF page count were untouched. Added as a permanent regression test,
`test_reordering_thumbnails_only_affects_the_active_tab`, rather than
just a one-off manual check.

Full suite: **405 passed** (403 baseline + 2 new -
`test_view_menu_zoom_in_keyboard_shortcut_actually_fires` and
`test_reordering_thumbnails_only_affects_the_active_tab`), `ruff check .`
## Dialog button-truncation audit (done; nothing was actually broken)

Follow-up to "Tool dialogs widened 25%" above: that flat multiplier
was a heuristic, not a verified guarantee that no button in the app
ever renders narrower than its own text needs. This pass measured it
for real instead of trusting the ratio, across every popup window in
`gui/` - all 36 `TOOL_DIALOGS` entries (`gui/dialogs/tool_dialog_registry.py`),
the non-standard-constructor dialogs (`FillFormDialog` with real long
field names, `WorkflowBuilderDialog`, `RunWorkflowDialog`), and
`TabPlacementDialog` (the multi-tab feature's plain `QDialog` subclass
that does **not** inherit `BaseToolDialog`'s widened `sizeHint()` at
all - see "Multi-document tabs" above for why it can't be a
`QMessageBox`). Also spot-checked one `QMessageBox` (the Save/Discard/
Cancel unsaved-changes prompt) and the one `QInputDialog.getItem` call
(`WorkflowBuilderDialog._add_step`'s tool picker) - both are stock Qt
static dialogs, well-tested by Qt itself, and both measured clean.

**The finding: no dialog in the app actually truncates any button
text.** Not "close enough" - every `QPushButton` in every dialog
instantiated headlessly (`QT_QPA_PLATFORM=offscreen`), shown, and
measured rendered at least as wide as its own `QPushButton.sizeHint().width()`.

This is worth trusting specifically because of how it was measured,
not just asserted: the first attempt used the method the task
suggested at face value - `QFontMetrics(button.font()).horizontalAdvance(text)`
plus a flat guessed pixel padding (24px) - and it produced a **false
positive** on `MergeDialog`/`WorkflowBuilderDialog`'s "Remove Selected"
button (flagged as needing 120px, rendering at 112px). Checked by hand
before trusting it: `QPushButton.sizeHint()` for that exact text
computes 110px using the real active style's actual button padding
(confirmed separately: Fusion's real per-button padding for
longer-than-minimum-width text is ~14-20px, not the guessed 24px) - so
112px was never actually truncated, the flat-padding heuristic was
just wrong. Switched the real check to comparing a button's live
rendered width against its own `sizeHint().width()`, which is Qt's
own style-and-font-aware computation of "how wide this button needs to
be to show this text" - confirmed safe to call after the widget is
already placed in a layout and shown (it keeps returning the
widget's *unstretched* preferred size, not whatever the layout
assigned it, verified by reading it back on an already-shown button).

**Why this holds structurally, not by coincidence of current text
lengths**: every dialog in `gui/dialogs/` is purely layout-driven
(same grep-confirmed fact "Tool dialogs widened 25%" already
established - no `resize()`/`setFixedSize()`/`setMinimumWidth()`
anywhere). A `QVBoxLayout`'s width `sizeHint()` is the max of its rows'
own widths, so a dialog's natural top-level width can never come out
narrower than its widest row - including the button row - as long as
nothing later imposes a smaller explicit size. `MergeDialog`/
`WorkflowBuilderDialog`'s custom button row (a `QHBoxLayout` of four
`QPushButton`s, unlike every other dialog's plain `QDialogButtonBox`)
does redistribute the widened dialog's leftover width *evenly* across
all four buttons once shown - confirmed directly (all four came back
at the identical 112px, not their individual natural widths) - but
that redistribution only ever *adds* width beyond each button's own
`sizeHint()`, never subtracts, so it doesn't produce truncation
either; it was just the reason the flat-padding heuristic's false
positive landed on those two dialogs specifically. `TabPlacementDialog`
needed no fix either, for a related but distinct reason: it uses
`QDialogButtonBox.addButton()` (not a hand-rolled `QHBoxLayout`), and
`QDialogButtonBox` lays each button out at its own natural preferred
width rather than stretching/redistributing - so it was never at risk
even without inheriting `BaseToolDialog`'s 25% multiplier. Tested both
with and without a `document_name` (the longest-button case,
"Replace Current Tab", renders identically either way, since the
label's `wordWrap` never forces the dialog narrower than the button
row needs).

Per the task's own instruction not to manufacture a finding that isn't
real: **no per-dialog fix was made** - `BaseToolDialog.sizeHint()`'s
`_WIDTH_MULTIPLIER` and every dialog's layout are unchanged.
`TabPlacementDialog` was deliberately left as a plain `QDialog` too -
adopting `BaseToolDialog`'s override would have been solving a problem
it doesn't have, and would come with `BaseToolDialog`'s modal
OK/Cancel-button-box shape that this dialog intentionally doesn't use
(its three choices are three real `AcceptRole`/`RejectRole` buttons,
not an options-form-plus-OK/Cancel).

Verified visually too, same discipline as every prior sizing/branding
pass: rendered `MergeDialog`, `WorkflowBuilderDialog`,
`TabPlacementDialog` (the three flagged by the discarded false-positive
heuristic, for direct comparison) plus `RotateDialog`, `WatermarkDialog`,
`RunWorkflowDialog` (known-good contrast cases, the latter with a
deliberately long saved-workflow name in its combo box) to PNG via
`widget.grab()` under `QT_QPA_PLATFORM=offscreen` and looked at them -
every button's full label is visible with normal padding, no dialog
reads as cramped.

`tests/unit/test_dialog_sizing.py` gained the real per-dialog
button-width audit: a parametrized test over every `TOOL_DIALOGS`
tool_id (all 36, via the shared registry - not just the 3-4 dialogs
the original flat-25%-ratio tests happened to cover), plus dedicated
tests for `FillFormDialog` (with real, deliberately long field names,
not the placeholder empty-list factory `TOOL_DIALOGS["fill_form"]`
uses), `WorkflowBuilderDialog`, `RunWorkflowDialog`, and
`TabPlacementDialog` (parametrized over both a real document name and
`None`). Each asserts every `QPushButton`'s live rendered width is
`>=` its own `sizeHint().width()` after a real `show()` - the
authoritative, style-and-font-aware check, not a hand-rolled font-
metrics-plus-guessed-padding stand-in (that version is kept only as a
narrative example above of why it's the wrong tool for this, not
shipped as a test). The pre-existing 25%-ratio tests are untouched.

Full suite: **444 passed** (403 baseline + 41 new), `ruff check .`
clean, `mypy core cli gui` clean.

## Interactive signature placement (Sign) — done

`SignDialog` (`gui/dialogs/sign_dialog.py`) no longer requires typing
four raw numbers to say where a signature image goes: it now shows the
**real target page, rendered, with the chosen image draggable and
corner-resizable on top of it**. Scoped deliberately to Sign only -
Watermark, Header/Footer and Create Forms share the same
rect-placement shape and could reuse this, but were left untouched
(see "Reusability" below).

**Manual numeric entry was kept, not replaced** - a deliberate
decision, for three separate reasons, any one of which would have been
enough:

1. The Workflow builder constructs every tool dialog against *no
   document at all* (`TOOL_DIALOGS[tool_id](parent)`), so there is
   nothing to preview and nothing to drag on. Removing the spin boxes
   would have made `sign` unconfigurable as a workflow step.
2. Typing an exact value is genuinely better than dragging when you
   already know the coordinates (a house style, a template, a rect
   copied from another document).
3. They double as the canvas's live read-out - the numbers move while
   you drag, which is how you find out what a drag actually produced.

So the two inputs are two-way bound: a drag writes into the spin
boxes, editing a spin box moves the overlay, and `values()` always
reads the spin boxes. One source of truth for the rect regardless of
how it was set, and `build_operation(**values)` receives the identical
shape it always has. **`SignOperation` was not touched at all** - not
its signature, not its bottom-left-origin rect convention, not its
internal fitz conversion. The dialog's whole job is to produce the
same tuple a user would have typed.

**Wiring**: `MainWindow._run_tool` special-cases `"sign"` to pass the
open document's working path, exactly the way it already special-cases
`"fill_form"` (whose dialog needs the document's AcroForm field
names). `SignDialog.__init__` takes that path as an *optional* second
argument, so the plain `(parent)` call every other caller makes still
works and silently degrades to numeric-only. A document that fails to
render degrades the same way rather than blocking the tool.

### The interaction mechanism

`gui/placement_canvas.py` (new, ~330 lines) is a `QGraphicsView` +
`QGraphicsScene` holding two items: a non-interactive
`QGraphicsPixmapItem` of the rendered page, and a `PlacementItem`
(a hand-written `QGraphicsItem`) for the overlay. Rendering is the
project's existing QtPdf path - `QPdfDocument.render()`, including the
white-backdrop compositing `MainWindow._render_thumbnails` already
documents (QtPdf leaves unpainted areas fully transparent, so a blank
page would otherwise read as nothing at all). No second rasterisation
mechanism was introduced.

`PlacementItem` does its own mouse handling rather than leaning on
`ItemIsMovable`, because the flag only moves - it cannot resize:
`mousePressEvent` hit-tests the four corner handles *first* and falls
back to the body, `mouseMoveEvent` applies the delta from the
press-time rect (not incrementally, so a drag can't accumulate
rounding), `mouseReleaseEvent` clears the mode. Both operations are
clamped to the page: a body drag slides along the edge instead of
leaving the page, and a handle drag is clamped to `_MIN_SIZE` from the
opposite edge, so the canvas can never produce the degenerate
`x1 <= x0` rect `SignOperation` rejects.

**One deliberate deviation from the usual Qt idiom**: the item's
`pos()` is pinned at scene (0, 0) forever and the rectangle is stored
in *scene* coordinates, instead of moving `pos()` around a
local-origin rect. Item-local and scene coordinates are then always
identical, which removes a whole category of "applied the delta in the
wrong frame" bug from the drag code and means the rect conversion
never has to `mapToScene` anything. The cost is a `boundingRect()`
that sits far from the item's origin, which Qt handles fine.

The view `fitInView`s the whole page on resize, so the *widget's* size
never enters the maths - conversion is only ever between PDF points
and scene pixels. Resizing the dialog cannot move a placement.

### Coordinate conversion, worked through

Page pixels: the page's long edge is rendered to `_TARGET_LONG_EDGE`
= 800 scene px (a thumbnail is 120x160 - too small to place anything
precisely). For the 300x400 pt fixture page that is exactly scale 2.0,
scene 600x800.

Scene is top-left-origin with y growing *down*; PDF is
bottom-left-origin with y growing *up*. So the on-screen **top** edge
becomes **y1** and the on-screen **bottom** edge becomes **y0** -
which is precisely the swap a naive `y0 = height - top` conversion
gets backwards. For an overlay at scene (100, 120) sized 200x80:

    x0 = 100 / 2                =  50
    x1 = (100 + 200) / 2        = 150
    y1 = 400 - (120 / 2)        = 340   <- screen top
    y0 = 400 - ((120 + 80) / 2) = 300   <- screen bottom

giving `(50, 300, 150, 340)`. Confirmed against the live widget, then
pinned as an exact assertion (`test_scene_rect_converts_to_the_exact_
bottom_left_origin_pdf_rect`), with the inverse
(`set_pdf_rect` → the same scene rect) asserted separately so the two
conversions are proven to be genuine inverses rather than
individually plausible. `test_a_rect_at_the_page_origin_maps_to_the_
bottom_left_corner` pins the flip itself: PDF (0, 0) must land at
scene y = 800, the *bottom* of the page.

End-to-end, not just in isolation: applying the resulting
`SignOperation` to a real fixture and asking `fitz` where the image
went returns bbox `(50, 60, 150, 100)` - the same rect flipped into
fitz's own top-left frame on a 400 pt page, exactly as predicted
(`test_sign_dragged_on_the_canvas_lands_where_it_was_dropped`,
`tests/integration/test_gui_smoke.py`, driving the real `MainWindow`
through `_run_tool`).

### A real PyMuPDF quirk found while making the preview honest

The preview is only worth having if it shows what the file will
actually contain, so "does `insert_image` stretch or fit?" had to be
answered against the real library rather than its docstring. It fits:
`keep_proportion=True` is the default and a 200x80 image dropped into
a 100x100 pt rect really does render as 100x40, centred - so the
canvas letterboxes the same way.

**Except for exactly-square images, where PyMuPDF 1.28.2 ignores its
own `keep_proportion` default entirely.** A 100x100 px image in a
100x40 pt rect fills the whole rect, distorted - and passing
`keep_proportion=True` *explicitly* changes nothing, nor does passing
`False`. A 101x100 px image in the identical rect is correctly fitted
to 40.4x40. Found by measuring the blue-pixel bounding box in the
rasterised output page, not by trusting `get_image_info`'s reported
bbox (which reports the placement rect, and would have hidden it), and
characterised across square/near-square/wide/tall images before being
believed.

`PlacementItem._stretches_to_fill()` mirrors the quirk rather than
papering over it: if a user's signature is going to come out
stretched, the preview should show it stretched instead of quietly
disagreeing with the file it produces. `PagePlacementCanvas.image_pdf_
rect()` exposes where the *image* (as opposed to the placement box
around it) will land, and `test_the_preview_predicts_where_the_image_
really_lands` asserts that prediction against a real applied
`SignOperation`'s actual fitz bbox - parametrised over both the fitted
and the stretched case. If a future PyMuPDF fixes the quirk, that test
fails and names the reason.

### Testing note: driving a QGraphicsItem drag headlessly

Real mouse drags aren't reliably simulatable under
`QT_QPA_PLATFORM=offscreen` - already documented twice here (the
thumbnail-reorder test calls `model().moveRow(...)`, the tab-reorder
test calls `QTabBar.moveTab(...)`). The equivalent for a graphics item
is that **`QGraphicsSceneMouseEvent` is directly constructible in
PySide6** (`setPos`/`setScenePos`/`setButton`), so the tests build real
event objects and hand them to the item's own
`mousePressEvent`/`mouseMoveEvent`/`mouseReleaseEvent`. That exercises
the genuine handlers - hit-testing a corner handle over the body it
overlaps, the press-time-rect delta maths, the page clamping - rather
than a hand-rolled substitute for them, and without a pointer.

`tests/unit/test_sign_placement_canvas.py` (16 tests): the scale the
rest of the maths depends on, the exact hand-computed round trip and
its inverse, the origin flip, body drag, corner-handle resize, a press
outside the box grabbing nothing, both clamps (can't leave the page,
can't invert the rect), the WYSIWYG prediction vs. real output, and
the dialog wiring - canvas absent without a document, spin boxes
following a drag, a typed value moving the overlay without feeding
back into itself, a page change keeping the placement, and the default
rect being clamped on a page smaller than it.

### Reusability (noted, not implemented)

`PagePlacementCanvas` takes a PDF path, a page number and a pixmap and
returns a bottom-left-origin rect - nothing about it is Sign-specific,
and `WatermarkOperation`/`HeaderFooterOperation`/
`CreateFormFieldOperation` all take a rect in the same convention.
Watermark and Create Forms could reuse it as-is (a text watermark
would want a text-rendering overlay rather than a pixmap one, which is
a `PlacementItem` paint change, not a coordinate change). Deliberately
left for a separate change rather than done speculatively here.

Full suite: **420 passed** (403 baseline + 17 new), `ruff check .`
clean, `mypy core cli gui` clean.

## Black-empty-tab bug: fixed (root cause was in `_add_tab`, not tab-switching)

User report: "opening a new tab shows a black, empty tab - the PDF
content never appears." A correction narrowed it: the tab created for
a *second* document actually renders fine - the black tab was some
*other* tab. That pointed initial suspicion at tab-*switching*
(reactivating a backgrounded tab), not tab-*creation*.

**Tab-switching itself was checked exhaustively and is not the bug.**
Before touching any code: opened two/three tabs with distinctly-
colored fixture pages (real fitz-drawn rects, not blank pikepdf pages,
so pixel sampling could tell "genuinely rendered" from "stale/wrong"),
switched between them 20+ times via `setCurrentWidget`, via real
`QTest.mouseClick` on the tab bar, out of order, after closing a
background tab, and via the drag-reorder `QTimer.singleShot(0, ...)`
deferral racing against a tab switch (switch tabs *before* the
deferred reorder-apply callback fires, then let it run) - every one of
these, sampled by real pixel value via `grab()` and via
`icon().pixmap().toImage().pixelColor(...)`, rendered correctly every
time, both with and without the dark palette/stylesheet applied
(`gui/palette.py` / `gui/styles.qss` - not loaded by a bare `MainWindow()`
in a script, only by `gui/main.py`, so this had to be applied by hand
to match what a real run looks like). `_refresh()`/`_render_tab`
always fully clear-and-rebuild the *current* tab's list from disk on
every call, unconditionally, so any staleness from a background
mutation (e.g. the reorder race) self-heals the instant that tab
becomes current again - there's no "only renders once per tab
lifetime" guard anywhere to have gone wrong.

**Root cause, found by capturing the actual transient state, not by
reasoning about timing**: `MainWindow._add_tab` called
`self.tab_widget.setCurrentIndex(index)` right after `addTab`. Making
a tab current fires `currentChanged` *synchronously*, which
`_on_current_tab_changed` turns straight into a full `_refresh()` -
and every one of `_add_tab`'s three call sites (`_open_document_path`,
`restore_autosaved_session`, `_run_tool`'s Merge-with-no-document
case) only loads/builds the actual document *after* `_add_tab`
returns. So the brand-new tab gets rendered once *before* it has a
document at all. Confirmed by literally patching `MainWindow._add_tab`
to `grab()` the window the instant it returns, with the dark
stylesheet applied: the result was a real, on-screen "Rad PDF Editor -
Untitled" window with "0 page(s)" in the status bar and a plain dark
`#17181a` thumbnail grid (its own empty background - styles.qss's
`QListWidget` color) where the thumbnails should be. That screenshot
*is* the bug report, word for word - a real, `grab()`-capturable frame,
not a hypothetical.

Why a synchronous test script didn't stumble onto this on its own: the
real content-loading call and a second, correct `_refresh()` happen
immediately afterward in the same call stack, with no event-loop turn
in between - so end-to-end pixel checks that only look at the *final*
state always saw the correct, overwritten result. It took deliberately
capturing the *intermediate* state (patching `_add_tab` to grab
immediately on return, before its caller has done anything else) to
see it. In a real running app, anything that forces a repaint between
those two points - a slow file copy for a large PDF, a window-manager-
driven redraw - can expose this frame for real; the caller's own
`open_document()`/`apply_operation()`/`restore_from_checkpoint()` call
is exactly the kind of I/O that isn't guaranteed to be instant.

**Fix**: `_add_tab` gained an `activate: bool = True` parameter. All
three real call sites now pass `activate=False` and only activate the
tab - via `self.tab_widget.setCurrentWidget(tab)` - once its document
has actually loaded/built successfully; on failure the tab is
discarded exactly as before, now with the added guarantee that it was
never shown with content at all (not even the empty flash). One
subtlety, found by testing rather than assumed: adding the very
*first* tab to an empty `QTabWidget` makes Qt auto-select it (confirmed
directly: `addTab` alone, with no `setCurrentIndex` call at all, still
fires `currentChanged`) - so `activate=False` blocks the tab widget's
signals for the `addTab` call itself, not just skips the explicit
`setCurrentIndex`. That also means `setCurrentWidget(tab)` afterward
is a silent no-op for the very-first-tab case (already current, no
index change, no signal) - which is why every call site still keeps
its own explicit trailing `_refresh()` rather than relying on
`setCurrentWidget` alone to trigger it. Verified end-to-end after the
fix, same patched-`_add_tab`-grab() technique: mid-load, the window
now keeps showing the *previous* tab's real content (or, for the very
first tab, stays on the branded empty-state welcome screen) - never an
empty "Untitled" tab.

**A second, related gap fixed in the same pass**: Merge-with-no-
document already avoided stranding a tab for a *cancelled* dialog
(existing behavior), but not for an *accepted* dialog whose build then
fails (e.g. every selected input file is missing/invalid) -
`apply_operation` raising left the freshly-created, permanently
content-less tab sitting in the tab bar. Fixed alongside the
`activate=False` change: the tab is discarded in that `except` branch
too, matching the pattern `_open_document_path`'s failure branch
already used.

**Regression tests** (`tests/integration/test_gui_smoke.py`, 7 new):
two target the root cause directly, by patching
`AppController.open_document` to assert *mid-call* that the window is
still showing the old tab (or the empty-state screen, for the very
first tab) rather than the new one - proving activation is genuinely
deferred, not just that the end state happens to look right. The rest
are the pixel-level symptom checks the task asked for specifically
because "wrong but non-empty" is a failure mode `count() > 0` alone
would miss: opening a second tab, switching back and forth between two
tabs five times each direction, a third tab with out-of-order
switching, replacing the current tab, and the Merge-failure tab-
stranding case - all via `_make_colored_pdf` (real fitz-drawn colored
rects) and `_thumbnail_center_pixel` (samples the actual built
`QIcon`/`QPixmap`), new small helpers alongside the existing
`_make_pdf`/`_open_tab`.

All three tab-creation paths were re-checked against the fix, not just
the one that turned out to be broken: **New Tab and the very-first-
tab-with-zero-tabs-open case were the ones exhibiting the transient
black frame** (both go through `_add_tab`); **Replace Current Tab
never did** (it re-uses the already-current tab and never calls
`_add_tab` at all - confirmed both before and after the fix, screenshot-
verified, still correct and unchanged by this fix).

Full suite: **470 passed** (463 baseline + 7 new), `ruff check .`
clean, `mypy core cli gui` clean.

## CI: all runs failed - three distinct root causes, all now fixed (real logs obtained)

The previous entry here recorded an investigation that could not read
the job logs (`GET .../actions/jobs/{id}/logs` 403'd without admin
rights) and therefore could not confirm a cause. `gh` is authenticated
now, the logs from run `31582525113` were read directly, and they
settle it: **the earlier investigation's leading hypothesis was right
for Linux, and its assumption that Windows failed the same way was
wrong.** Windows failed for two entirely unrelated reasons, one of
them a genuine product bug. macOS passed cleanly all along.

Keeping the earlier entry's correct groundwork, since it saved time
here and is still true: pytest exit code 2 does mean a *collection*
error (verified by repro, not assumed), so the Linux failure was never
an assertion failure; and the `skipif`-guard and `tifffile`/PEP-695
theories really were dead ends, ruled out by local reproduction under
a genuine Python 3.11 build with the optional binaries hidden.

### Linux: PySide6's native library dependencies aren't on the runner

```
ImportError while importing test module '.../tests/integration/test_gui_smoke.py'
tests/integration/test_gui_smoke.py:21: in <module>
    from PySide6.QtGui import QCloseEvent
E   ImportError: libEGL.so.1: cannot open shared object file: No such file or directory
```

Identical for all four GUI-importing test modules
(`test_gui_smoke.py`, `test_dialog_sizing.py`, `test_gui_resources.py`,
`test_sign_placement_canvas.py`), hence `Interrupted: 4 errors during
collection` / exit code 2. This is exactly the community-known
PySide6-on-bare-Ubuntu problem the previous investigation identified as
its leading theory but declined to ship a fix for without evidence -
the right call at the time, and the evidence has now arrived. It also
explains why *every* run since #1 failed: `test_gui_resources.py` has
existed since the branding commit, so the Linux leg has never once got
past collection.

Fixed in `.github/workflows/ci.yml` with an `apt-get` step guarded by
`if: runner.os == 'Linux'`. The package list was **derived, not
guessed one-at-a-time**: `objdump -p` on the actual libraries this app
loads gives their direct `NEEDED` entries, and only five name
something Ubuntu's base system doesn't already guarantee:

| library | needed by | package |
| --- | --- | --- |
| `libEGL.so.1` | `libQt6Gui.so.6` | `libegl1` |
| `libGL.so.1` | `libQt6Gui.so.6`, `platforms/libqoffscreen.so` | `libgl1` |
| `libxkbcommon.so.0` | `libQt6Gui.so.6`, `libqoffscreen.so` | `libxkbcommon0` |
| `libfontconfig.so.1` | `libQt6Gui.so.6` | `libfontconfig1` |
| `libdbus-1.so.3` | `libQt6DBus.so.6` (a `NEEDED` of QtGui) | `libdbus-1-3` |

Everything else in the full `ldd` closure (`libglvnd0`, `libglx0`,
`libx11-6`, `libxcb1`, `libfreetype6`, `libexpat1`, `libsystemd0`, ...)
arrives as an apt dependency of those five - verified with `apt-cache
depends`, so naming them would be redundant, not safer. Deliberately
**not** installed: `libxcb-cursor0`/`-icccm4`/`-keysyms1`/`-randr0`/
`-render-util0`/`-shape0`/`-xinerama0`, which appear in most
"PySide6 headless CI" snippets on the web. Those are dependencies of
the **xcb** platform plugin, which this job never loads
(`QT_QPA_PLATFORM=offscreen`); `libqoffscreen.so` links only
`libxcb.so.1`, which comes in via `libx11-6` anyway. Every plugin
directory PySide6 ships was swept the same way to make sure nothing
dlopen'd at runtime needs more: `imageformats`/`styles`/`iconengines`/
`generic`/`platforminputcontexts` need nothing beyond the set above,
and the only plugin that does (`platformthemes/libqgtk3.so`, which
wants the whole GTK3 stack) is never loaded under offscreen.

This is the one fix that cannot be validated locally - this dev machine
is Ubuntu 24.04 (the same as `ubuntu-latest`) and already has all of
these installed from normal desktop use, which is precisely why the
bug was invisible here. It'll be proven by the next real CI run.

### Windows, part 1: a real handle leak that silently defeated secure wipe

```
tests/integration/test_gui_smoke.py::test_sign_via_tools_menu_places_image
tests/integration/test_gui_smoke.py::test_sign_dragged_on_the_canvas_lands_where_it_was_dropped
E  PermissionError: [WinError 32] The process cannot access the file because it is
   being used by another process: '...\appdata\sessions\<hex>\working.pdf'
E  core.errors.SecurityError: Could not securely delete '...\working.pdf': [WinError 32]
```

**Not a test artefact - a security-relevant product defect.** Windows
refuses to overwrite or unlink a file any handle still has open, so
`core/security/secure_delete.py` couldn't wipe the session working
copy: on Windows the app's core promise (SPEC.md section 1, "wipe on
close, not just delete") was failing outright for any session where a
signature had been placed. Linux and macOS unlink an open file happily,
which is why this cost nothing on two of the three legs and why no
existing test caught it.

The leak was found, not guessed at, by reading `/proc/self/fd` around a
real `MainWindow._run_tool("sign", ...)` run: the working copy is open
before the call and still open after it, and still open across
`close_session()` (there as `working.pdf (deleted)` - the Linux
"unlinked but a handle still holds it" state, which is exactly what
Windows refuses to allow in the first place). The holder is
`PagePlacementCanvas`'s `QPdfDocument` (`gui/placement_canvas.py`),
which previews the working copy: the canvas is parented to `SignDialog`
which is parented to `MainWindow`, so it outlives `exec()` indefinitely
and nothing ever released it. `MainWindow._render_thumbnails`' own
`QPdfDocument` does *not* leak - it's a local, unparented, throwaway
document destroyed by refcount at function exit - which is also why
only the two signature tests failed and not every GUI test on Windows.

**The genuinely surprising part, worth remembering: `QPdfDocument.close()`
does not close the file.** The first fix written here was
`close()` + `deleteLater()`, which looks obviously correct and is
wrong: verified against Qt 6.11 via `/proc/self/fd`, the fd survives
`close()` and only disappears when the object is *destroyed*
(`deleteLater()` doesn't help either unless the deferred-delete event
is actually delivered - `QApplication.processEvents()` by design does
not deliver those). So the fix that shipped:

- `PagePlacementCanvas` now creates its `QPdfDocument` **unparented**,
  with `self._pdf` as the sole owner - the same throwaway pattern
  `_render_thumbnails` already used - so clearing that attribute
  destroys it there and then under CPython refcounting.
- `PagePlacementCanvas.release_document()` closes and drops it,
  idempotently; `load_document()` calls it first, so reloading can't
  stack handles either.
- `BaseToolDialog.release_resources()` (a documented no-op) /
  `SignDialog.release_resources()` give every tool dialog a
  deterministic "give back your OS handles" hook, and
  `MainWindow._run_tool` calls it in a `finally` around the whole
  dialog flow - accepted, cancelled or errored.
  `WorkflowBuilderDialog._add_step` does the same for the dialogs it
  exec()s, so the contract is "whoever exec()s a tool dialog releases
  it", not a one-off at a single call site.
- Safe to release before `values()` is read (and documented as such):
  `SignDialog`'s rect always comes from the spin boxes, never from the
  canvas.

Regression tests assert **the handle is explicitly released**, not that
deleting the file works - the latter passes on Linux whether the bug is
present or not, which is the whole reason this survived to CI in the
first place. `tests/unit/test_sign_placement_canvas.py` covers the
canvas/dialog release contract, including one Linux-only
(`/proc/self/fd`) test that pins the real OS-level behaviour *and* the
`close()`-isn't-enough discovery above; `tests/integration/test_gui_smoke.py::
test_sign_dialog_releases_the_working_file_before_the_session_is_wiped`
covers the real `_run_tool` path for both the accepted and the
cancelled dialog.

### Windows, part 2: an over-constrained test, not a broken widening

```
tests/unit/test_dialog_sizing.py::test_shown_dialog_actually_uses_the_widened_size
E  assert 533 == 582.5 ± 17.475
```

**Determination: the test was wrong, the 25%-wider override is fine on
Windows** - and this was settled by reproducing the exact number
locally rather than by reasoning about Windows. `QWidget::adjustSize()`,
which sizes a top-level widget on its first `show()`, does not use
`sizeHint()` raw:

    shown = max(min(sizeHint, screen_width * 2 / 3), layout_minimum)

Under `QT_QPA_PLATFORM=offscreen` the virtual screen is 800x800, so
that cap is exactly **533** - the number Windows reported. Windows'
native style makes `RotateDialog`'s natural width ~466 (vs. 238 under
Fusion on Linux, a plain font/style-metrics difference), the override
asks for ~582, and Qt caps it at 533. Reproduced on Linux by giving a
`BaseToolDialog` a wide enough label: natural 469, widened 586, shown
**533**, the same three numbers. The formula above was then checked
against six dialogs spanning all three branches (uncapped,
screen-capped, layout-minimum-dominated) and predicted the shown width
to the pixel every time.

So the old assertion compared a *pre-`show()`* baseline against
*post-`show()`* geometry and assumed Qt applies a hint verbatim, which
it doesn't. Nothing user-visible is wrong: the cap only bites because
the headless virtual screen is 800px wide, and the audit that would
catch a genuinely too-narrow dialog
(`test_every_tool_dialog_button_fits_its_own_text`, which measures
every button against its own `sizeHint()`) passes on Windows CI.

Fixed by rewriting that single test - the module's other coverage is
untouched - to assert the two things that are true on every platform:
the overridden hint is still 1.25x the unmodified one *measured in the
same post-`show()` state*, and the shown geometry is exactly what Qt's
own rule derives from it. A new
`test_qt_clamps_a_dialog_wider_than_two_thirds_of_the_screen` pins the
clamp behaviour deliberately, so the next person to see a "too narrow"
dialog width has the explanation in the suite rather than only in this
file.

### Verification and status

`ruff check .` clean, `mypy core cli gui` clean, full suite **478
passed** (470 baseline + 8 new tests; the `/proc/self/fd` one skips off
Linux). The Sign dialog was also re-grabbed to PNG and looked at, per
this project's usual convention, to confirm the unparented
`QPdfDocument` still renders the page preview identically.

Not pushed: CI config changes are the human's call to validate against
a real GitHub run, which is the only place the Linux fix can be proven.

## Phase 6 — Editor (planned in full; 6a done)

`docs/GUI_PLAN.md` is the design record; `docs/SPEC.md` section 4 has
the roadmap entry. The reframing that drove it: Phases 1-5 built 36
whole-document/whole-page *transforms* behind a thumbnail-grid UI, so
there is no page viewer, no text selection, no find, no annotation and
no redaction. Closing that is GUI work **plus a new class of
`Operation`**, not GUI work alone.

Thirteen decisions are locked in the plan's §1 table. The two library
findings that shaped the architecture, both verified against the
installed versions rather than assumed:

- **QtPdf's model classes don't need `QPdfView`.** `QPdfSearchModel`,
  `QPdfDocument.getSelection()`/`getAllText()`, `QPdfSelection`,
  `QPdfBookmarkModel`, `QPdfLinkModel`, `QPdfPageNavigator` and
  `QPdfPageRenderer` all work off a `QPdfDocument`. So a custom
  `QGraphicsView` canvas can have editable overlays *and* real text
  selection/find/outline - the tradeoff that would otherwise have
  decided the viewer architecture doesn't exist. `QPdfPageRenderer`
  also gives async, queued rendering.
- **PyMuPDF 1.28.2 already covers every annotation type** plus
  `add_redact_annot`/`apply_redactions` for redaction that genuinely
  removes content. No new dependency for markup/redact/insert.

Design consequences worth remembering (plan §3.7): annotation identity
uses a UUID in `/NM`, **not** `xref` - every commit writes a new
working file, so xrefs aren't stable across edits. Progress reporting
uses an opt-in `SupportsProgress` mixin rather than an optional param
on `Operation.apply`, because existing subclasses declare
`apply(self, doc)` and would raise `TypeError` when called with it - a
breaking change wearing an additive costume.

### 6a — `main_window.py` decomposed (done)

1174 -> 541 lines, a **pure move**: full suite **478 passed**, byte-for
-byte the same count as the pre-refactor baseline, with **zero test
changes**. `ruff` and `mypy core cli gui` clean.

- `gui/actions.py` - actions, menus, toolbar. Free functions taking
  the window, not a mixin: nothing here is called after construction,
  so there is no behaviour to inherit, only wiring to perform.
- `gui/tab_manager.py` - `TabManagementMixin` (tab lifecycle).
- `gui/tool_runner.py` - `ToolRunnerMixin` (tool dialogs, thumbnail
  context/reorder operations, workflows, `_busy_cursor`).
- `gui/rendering.py` - `render_thumbnails()`, free function. Phase 6b
  turns this into the async/cached path.
- `gui/window_parts.py` - `WindowPart`, the typing base.

**Mixins, not collaborator objects, and that was forced by the test
suite** - roughly twenty of these methods are called directly on the
window (`window._close_tab(...)`) and three are patched on the class
(`patch.object(MainWindow, "_add_tab")`). A mixin keeps every one
resolving on `MainWindow` unchanged; a collaborator would have needed
a delegating shim per method.

`gui/window_parts.py`'s `WindowPart` is **only a type-checking
construct**: to mypy it's a `QMainWindow` declaring every shared
attribute and cross-part method signature, and at runtime it is an
empty plain class. Two real reasons, not style:
1. PySide6 does not support inheriting from two QObject-derived
   classes, so the mixins must not themselves be `QMainWindow`
   subclasses at runtime.
2. A stub with a real body would sit *ahead of* `QMainWindow` in the
   MRO (confirmed live: `MainWindow -> TabManagementMixin ->
   ToolRunnerMixin -> WindowPart -> QMainWindow -> QWidget`) and could
   silently shadow a genuine Qt method. Guarding the whole class
   behind `TYPE_CHECKING` makes that impossible.

Only methods a mixin calls on *another* part are declared there - a
name both declared in `WindowPart` and defined in a mixin is checked
as an override, so signatures must match exactly.

**Things that had to stay put, found by grepping the tests first
rather than discovered by a red suite:**
- `_THUMBNAIL_SIZE`, `_THUMBNAIL_ZOOM_MIN_WIDTH`/`_MAX_WIDTH`/`_STEP`
  are imported from `gui.main_window` by the View-menu tests.
- `patch("gui.main_window.QMessageBox.critical")` and friends patch a
  *class attribute*, so they keep intercepting even though
  `_run_workflow`'s `QMessageBox.information` call now lives in
  `tool_runner.py`. `gui.main_window` still has to import
  `QMessageBox`/`QFileDialog` for the patch target to resolve, which
  it does (`_show_error_message`, `_save_as`, `restore_autosaved_session`).
- Every `QAction` is still assigned onto the window
  (`window.undo_action = ...`) from `actions.py`. mypy can only infer
  an attribute from an assignment *inside* the class, so `MainWindow`
  now carries class-level annotations for all 17 actions plus
  `recent_files_menu`/`toolbar` - otherwise every later use is an
  `attr-defined` error.

One behaviour change was made and then reverted deliberately: an
added `log.error` in `_show_error`. 6a is a pure move; an improvement,
however small, doesn't belong in it.

Verified beyond the suite, per this project's convention: a live
`MainWindow` under `QT_QPA_PLATFORM=offscreen` with the real
palette/stylesheet applied, walking the actual menu tree - 5 top-level
menus, 8 Tools submenus with the same counts, all 36 `tool_actions`
matching `TOOL_DIALOGS`, the `Ctrl+=` zoom alternate still bound - and
`grab()`ed to PNG and looked at (branded empty state, toolbar with
Save As/Undo/Redo correctly disabled).

### 6b — rendering: async, cached, page-targeted (done)

`gui/rendering.py`'s `ThumbnailRenderer` replaces the synchronous
`render_thumbnails()` free function (deleted - it had no callers left).
One renderer per `DocumentTab`, so each document owns its own cache and
its own `QPdfDocument`.

**Measured on a real 500-page document, not extrapolated** (the plan's
original "~6 s" came from scaling a 15-page sample; the real figures
are lower, and measuring beat scaling):

| | UI blocked, pre-6b | UI blocked, 6b | after editing 1 page |
| --- | --- | --- | --- |
| default zoom 120x160 | 1065 ms | **10 ms** | 1 of 500 re-rendered, 2 ms |
| max zoom 720x960 | 2292 ms | **10 ms** | 428 of 500, 2005 ms (background) |

**Two wins, deliberately not conflated.** Async delivery is universal -
the UI never blocks more than ~10 ms at any length or zoom - but it
buys *responsiveness, not throughput*: total rasterisation time is
unchanged (measured 18 ms sync vs 19 ms async for the same 40 pages,
with a fresh `QPdfDocument` per run so no run inherited another's warm
MuPDF cache; an earlier unfair benchmark appeared to show async 16x
faster and was wrong). The cache win is what removes the work, and it
is complete only while the document fits the budget.

**The cache is keyed by `(page, width, height)` and lives on the tab -
deliberately *not* by working-file path.** `allocate_working_path`
mints a fresh `mkstemp` name for every operation, so a path-keyed cache
would miss 100% of the time after any edit. Invalidation is explicit
instead, via the new `Operation.affected_pages()`.

**`Operation.affected_pages()`** (`core/model/operation.py`) - the one
additive change to a frozen interface, non-abstract and defaulting to
`None` ("unknown, assume all"), so nothing written before it existed
had to change. Overridden on the ten operations that preserve page
**count and order** (Rotate, Crop, Resize, Grayscale, Flip, Flatten,
RemoveAnnotations, HeaderFooter, BatesNumbering, Deskew) as
`list(self.pages) or None` - the empty list already means "all pages"
throughout this codebase. Deliberately *not* overridden on Delete /
Extract / N-up / Merge / Reorder: those shift every later page's
identity, so cached thumbnails for pages they never touched are wrong
too. Undo/redo also invalidate everything, since the inverse of most
operations is a snapshot restore.

**A real limitation, measured rather than glossed:** at 720x960 a page
costs 2.7 MB, so only 72 of 500 fit in the 192 MB budget and LRU
eviction makes an edit re-render ~428 pages anyway - off the UI thread,
but still real work. A bound is not optional (unbounded would be
~1.4 GB), and raising it only moves the cliff since page cost grows
with the square of the zoom. The real fix is requesting only the pages
actually on screen plus a lookahead, which is already specified for the
6c viewer; thumbnails render eagerly today. Not done here because a
widget that has never been shown has no viewport, so "visible" is empty
in a headless test - that needs its own handling, in its own slice.

**Items appear synchronously with a correctly-sized blank placeholder**
and each real page replaces its own as it arrives. That is what keeps
`count()`, the `UserRole` page numbers and `QIcon.actualSize()` correct
the instant `render()` returns - the properties the window and the
existing tests rely on - so all 478 pre-existing tests passed unchanged
with only one test-helper edit: `_thumbnail_center_pixel` now waits via
`renderer.wait_until_idle()` before sampling, or it would read the
placeholder's white and report a rendering failure.

**Windows handle discipline carried over.** The renderer's
`QPdfDocument` is unparented and held only by `self._pdf`, so clearing
it destroys it under refcounting; `release()` does that and
`_discard_tab` calls it **before** `close_session()`. Same trap
`gui/placement_canvas.py` already hit: `QPdfDocument.close()` does not
release the file descriptor, only destruction does, and Windows refuses
to overwrite or unlink an open file - which would silently defeat the
secure wipe. The regression test asserts the *release*, not that
deletion succeeded, because deletion succeeds on Linux either way -
exactly how that bug reached CI last time.

Stale async results are dropped by comparing the delivered size against
the current one, so a result from a superseded zoom or a rebuilt grid
can never write into an item that no longer exists.

**A real bug this introduced, found by re-reading the diff rather than
by the suite** (no existing test replaced a tab's document with a
*visually different* one): the cache is keyed by `(page, size)` and not
by path, so **Replace Current Tab** served the previous document's
thumbnails - a correctly-sized, correctly-counted grid showing the
wrong pages, which no `count()` assertion can see. Reproduced first
against real colour-sampled pages (red document replaced by a green one
of the same page count still sampled `(255, 0, 0)`), then fixed by
declaring the identity change explicitly in `_open_document_path` -
the renderer cannot distinguish "next revision of this document" from
"a different document" by path alone, and that is inherent to the
cache key, not an oversight in it. Regression test:
`test_replacing_a_tabs_document_does_not_show_the_old_pages`.

Verified visually per convention: grabbed the real window mid-render
(correctly-sized white placeholders in a proper grid - importantly not
the "black empty tab" failure mode) and settled (all 12 pages rendered,
correct order, "12 page(s)" in the status bar).

### 6c — page viewer + sidebar (in progress)

`gui/page_canvas.py`'s `PageCanvas` is the first slice that visibly
changes the product: a tab is no longer a thumbnail grid alone. The
grid becomes a navigation sidebar (`DocumentTab` now holds a
`QSplitter` of `thumbnail_list` + `canvas`) and a continuous,
scrollable, zoomable page view becomes the primary pane.
`thumbnail_list` keeps its name, its API and every behaviour the suite
drives (selection, context menu, drag-reorder) - only where it sits on
screen changed.

Done: continuous scroll, zoom, fit width/page, viewport-limited
rendering, click-a-thumbnail-to-scroll. Still to come in 6c: text
selection, find, outline, links.

**Scene units are device pixels, not points under a view transform.**
Each page is rendered at exactly the pixel size it occupies and
`transform()` stays at identity, so zooming in gives a genuinely
sharper page instead of a magnified blurry one; fit modes compute a
zoom factor from the viewport rather than calling `fitInView`.

**Viewport-limited rendering lands here**, which is the fix
`docs/GUI_PLAN.md` §3.5.1 flagged for 6b's eviction cliff. Only pages
intersecting the viewport plus a two-page lookahead are requested - it
matters far more here than for thumbnails, since a full-size page costs
~2 MB against a thumbnail's 75 KB. **Placeholders are painted, never
allocated**: `PageItem.paint()` draws a white rect and the page number
when it has no pixmap, because a real placeholder `QPixmap` per page
would cost ~1 GB for 500 unrendered A4 pages at 100%.

**Zoom is two separate controls now.** The page view took the standard
shortcuts (Ctrl+= / Ctrl+- / Ctrl+0, plus Ctrl+1 fit width, Ctrl+2 fit
page) because it is the primary pane; thumbnail sizing kept its exact
behaviour under Ctrl+Shift and gained its own
`larger_thumbnails_action` / `smaller_thumbnails_action` /
`reset_thumbnails_action`. Three existing tests moved to those actions
- same coverage, different action driving it.

**Two real bugs found here, both of which produced a silently *blank*
viewer that still reported itself idle** - and both found by printing
`rendered_page_count`, not by looking at a screenshot, which is worth
remembering: a blank white page view looks plausible enough to pass a
glance.

1. `_pending` was keyed by page number only. After a zoom, the
   still-in-flight old-size request made `_request_visible` skip the
   page as "already requested"; that result then arrived, was correctly
   dropped for being the wrong size, and *nothing re-requested it*.
   Every zoom - including the fit-width that runs on first show - left
   the viewer blank.
2. `_on_page_rendered` discarded from `_pending` **before** checking the
   delivered size, so a late result for a superseded zoom cleared the
   entry belonging to the *current* request. `wait_until_idle` then
   returned early with nothing drawn. Only reproducible with two zooms
   in a row and no wait between them - a single-step debug script
   showed everything working.

Both fixed by making `_pending` a `dict[page, (width, height)]`:
requests are re-issued when the size differs, and a delivered result
only clears the entry when its size is the one still wanted. Regression
tests: `test_a_zoom_change_re_renders_rather_than_going_blank` and
`test_two_rapid_zooms_still_render`.

**`QPdfDocument.getSelection()` is unusable in PySide6 6.11.1** -
verified, not assumed: it returns an invalid, empty `QPdfSelection` for
every point range tried, including ranges squarely over text that
`getAllText()` happily reports on the same page. `getSelectionAtIndex()`
*does* work and returns correct top-left-origin PDF-point rects
(`"ALPHA"` -> `(50, 86, 65, 14)` on a page whose baselines are at
y=100/300/550). So text selection has to be built from
`getAllText().bounds()` - one polygon per line, confirmed: 3 lines ->
3 polygons, a dense page -> 45 - mapped to the `\r\n`-separated lines of
`getAllText().text()`, then a binary search within the line. Walking a
page character by character is not an option: `getSelectionAtIndex` costs
~906 us per call, 2.6 s for a 2923-character page.

`QPdfSearchModel` works but is **asynchronous** (timer-driven; one
`processEvents()` is not enough - it needed ~0.14 s and repeated turns
before `count()` became non-zero), and `QPdfBookmarkModel` works
directly, giving Title and Page per row.

The canvas holds the working file open exactly as the thumbnail
renderer does, so `_discard_tab` now releases both before
`close_session()` - same Windows secure-wipe discipline.

**Outline and find landed next** (`gui/outline_panel.py`,
`gui/find_bar.py`). The sidebar became a `QTabWidget` of Pages +
Outline; the find bar sits above the canvas, hidden until Ctrl+F.
Search hits are highlighted on the page and Next/Previous scrolls to
them. Verified visually: highlights land exactly on every occurrence of
the search term, which is what proves the top-left-origin PDF-point
mapping is right at the current zoom.

Three real defects found while wiring these, none of which a green test
would have surfaced on its own:

- **The outline and find models share the canvas's `QPdfDocument`, and
  destroying it out from under them segfaults the process** - not
  raises. Both `QPdfBookmarkModel` and `QPdfSearchModel` keep a raw
  pointer, so `PageCanvas.release()` freed memory they still held.
  Fixed with `DocumentTab.detach_document()` / `.release()`, which own
  the ordering (detach models, then release handles, then wipe the
  session) and are now what `_discard_tab` and the tests' `_force_close`
  both call. Sharing one document per tab is still right - one OS
  handle, one release point - but the ordering is load-bearing.
- **`Signal(dict)` does not work in PySide6.** Emitting a Python dict
  through a typed signal fails at emit time with
  `_pythonToCppCopy: Cannot copy-convert (dict) to C++` on stderr and
  silently delivers nothing, so the find bar's highlights never reached
  the canvas. `Signal(object)` passes the value through untouched.
  Found by reading stderr on a run that otherwise looked successful.
- **`QPdfSearchModel` settles in wall-clock time, not event-loop
  turns.** A `wait_until_settled` that counted three unchanged
  `processEvents()` calls returned 0 hits instantly on a document with
  72 of them - those turns elapse in microseconds while the search
  needs ~0.1 s. It now measures elapsed quiet time.

Highlight rects are stored on `PageItem` in **PDF points** with a
separate scale factor, not pre-multiplied into pixels, so a zoom change
cannot leave them stale - one source of truth for where a hit is.

One Qt testing note worth keeping: `isVisible()` is False for any child
widget whose window has never been shown, so a "did the find bar
appear?" assertion has to use `isHidden()` - the flag `setVisible()`
actually toggles.

**Text selection and links finished 6c** (`gui/text_selection.py`).

**`QPdfDocument.getSelection()` is unusable in PySide6 6.11.1**, so the
obvious API for drag-selection is simply not available - verified, not
assumed: it returns an invalid, empty `QPdfSelection` for every point
range tried, including ranges squarely over text `getAllText()` reports
on the same page. `PageTextIndex` rebuilds the capability from the
parts that work:

- `getAllText(page)` gives the text plus `bounds()`, one polygon per
  line, in top-left-origin PDF points.
- The text separates lines with `\r\n`, so line *k* matches polygon
  *k*. **That correspondence is the whole mechanism**, and a page where
  the two counts disagree is treated as unselectable rather than
  mis-mapped - a wrong mapping would silently select the wrong text.
- A point resolves to a line by y, then to a character by binary
  searching that line's index range with `getSelectionAtIndex`,
  comparing against each glyph's *midpoint* so clicking the right half
  of a character selects past it, as a caret does.

Walking a page character by character is not viable at ~906 us per
`getSelectionAtIndex` call (2.6 s for a 2923-character page); the
binary search is ~log2(line length) calls and probed rects are cached.

Verified against text at known positions rather than "a selection
happened": a drag from x=50 to x=175 on a line reading
"ALPHA BETA GAMMA" selects exactly `"ALPHA BETA"`, and the painted
rects land on the text (confirmed by grabbing the real window).

**Selection is within one page**, deliberately. A drag that wanders
onto another page keeps extending on the page it started on -
cross-page selection is separate work, and silently selecting the wrong
page's text would be worse than not extending. Selection is also
dropped when an operation invalidates the page it is on, since the
indices no longer refer to the same text.

**External links are shown, not opened.** `QPdfLinkModel` reports an
internal link's target page (0-based) and an external link's URL with
page **-1**; the canvas normalises that to page 0 so callers can tell
them apart. Internal links navigate. External ones only surface the
address in the status bar - SPEC.md section 1 forbids network access
anywhere in this app, and a click on an untrusted document's link is
exactly the wrong trigger for outbound traffic from a
confidential-documents tool. Deciding this either way was a product
call, not an oversight.

Interaction note: the canvas is `NoDrag`, not `ScrollHandDrag`, because
the left button now selects text; scrolling is the wheel and the
scrollbars, as in any PDF reader's select mode. Selection rects are
held on `PageItem` in PDF points with a separate scale factor, the same
as search highlights, so zoom cannot leave them stale.

Testing note: real drags are not simulatable under
`QT_QPA_PLATFORM=offscreen`, so the handlers are driven with real
`QMouseEvent` objects - the same technique the sign-placement tests use
for `QGraphicsSceneMouseEvent`. Use the overload that takes both a
local *and* a global position; the shorter one is deprecated and warns.

**Phase 6c is complete.** Full suite 543 passed, ruff and
`mypy core cli gui` clean.

### 6d — background execution with progress and cancel (done)

Applied operations now run on a `QThreadPool` worker behind a
window-modal, cancellable `QProgressDialog`
(`gui/operation_runner.py`). **Operations are unchanged** - they stay
synchronous and know nothing about threads. `AppController` is already
Qt-free, so `apply_operation` off-thread touches no widgets, and the
modal dialog stops the user driving the same document from two
directions while the event loop keeps turning.

**`SupportsProgress` (`core/model/progress.py`) is a mixin, not a
parameter.** Adding `apply(self, doc, progress=None)` to the frozen
base would look additive and is not: every existing subclass declares
`apply(self, doc)` and would raise `TypeError` when called with it. The
runner feature-detects with `isinstance` and shows an indeterminate bar
for everything else, which is honest - most operations here are one
opaque call into pikepdf/LibreOffice/ocrmypdf/Ghostscript and genuinely
cannot report a percentage. Instrumented so far: Rotate, Grayscale,
Deskew, Header/Footer, Bates.

**Cancellation is cooperative and safe because of the existing document
model**, not because of anything added here: an operation writes to a
*new* working file and the session only adopts it on success, so
cancelling discards a file the document never referenced. The progress
callback raises `OperationCancelledError` (new in `core/errors.py` -
named with the `Error` suffix because ruff's N818 requires it, matching
every other name there) and the operation unwinds through its normal
error path. An *opaque* operation cannot be interrupted at all: Cancel
stops the wait and the result is discarded when it arrives, and the
dialog says "Cancelling..." rather than pretending the work stopped.

Progress is reported at the *top* of each page loop with the count
completed so far. That covers both the do-this-page and skip-this-page
branches of the fitz loops without duplicating the call, and gives the
callback a chance to cancel *before* each page rather than only after.

The runner blocks its caller on a nested `QEventLoop` rather than
handing back a future: every call site is written as "apply, then
refresh", and making them all asynchronous would be far larger than 6d
needs - the point is that the *event loop* keeps running, not that the
call site stops being sequential.

`_busy_cursor` survives for exactly two things that are not a single
`Operation`: undo/redo (a snapshot restore - a file copy, where a
progress bar would be noise) and running a saved workflow (a whole
`Pipeline` against a throwaway session). Both are candidates for the
worker later; neither is the freeze 6d existed to fix.

One mypy note: `isinstance(op, SupportsProgress)` stored in a *bool*
does not narrow the object, so the runner holds the narrowed value
(`reporter = op if isinstance(...) else None`) instead of casting.

### 6e — annotations: markup, shapes, ink, notes (done)

`core/ops/annotate.py` adds the first operations in this codebase that
edit content *on* a page rather than transforming the document:
`AddAnnotationOperation`, `EditAnnotationOperation`,
`DeleteAnnotationOperation`, registered as `add_annotation` /
`edit_annotation` / `delete_annotation`.

**One Add operation with a `kind` discriminator**, not nine
near-identical classes: highlight/underline/strikeout/squiggly, rect/
circle/line, ink and note all take the same parameters (a page, a
region, a colour) and differ only in which PyMuPDF call runs.

**Identity is a UUID in `/NM`, proven not assumed.** A pikepdf round
trip renumbered the same three annotations from xrefs `[7, 9, 13]` to
`[4, 5, 6]` while their `/NM` values survived - which is exactly the
situation every operation creates, since each writes a new working
file. PyMuPDF exposes `/NM` as `annot.info["id"]` for *reading* but has
no setter (`set_info(id=...)` raises `TypeError`), so it is stamped
with `doc.xref_set_key(annot.xref, "NM", "(...)")`. Note
`xref_get_key` returns the **unwrapped** string - comparing against
`"(uuid)"` silently matches nothing, which quietly made the first
edit/delete probe a no-op that still looked successful.

**Text markup acts on the text selection, not a dragged rect** - the
familiar gesture, and it reuses 6c's selection machinery. A selection
spanning several lines becomes **one** annotation over several rects
(PyMuPDF's markup helpers accept a list), so it is one undo step and
one audit entry rather than one per line.

**Undo is precise where it can be.** Undoing an *add* is a
`DeleteAnnotationOperation` addressing the id the add assigned - not a
snapshot restore. Undoing a *delete* is a snapshot restore, because
deletion discards the annotation's full definition (appearance stream,
quad points, author, dates) which no add could faithfully rebuild from
an id - the same honest choice OCR and the other lossy operations here
already make.

**The bug that would have shipped invisible: QtPdf does not render
annotations by default.** Everything worked - the operations created
correct annotations, `fitz` confirmed their type and geometry, the
tests passed - and the page view showed a clean, unmarked page. Caught
only by looking at a screenshot. `QPdfDocumentRenderOptions` needs
`RenderFlag.Annotations`; measured on a page whose sole content is a
solid red square annotation, the red fraction goes from **0.000 to
0.672** with the flag. Both renderers now pass
`gui/rendering.annotation_render_options()`, with pixel-level
regression tests for the page view and the thumbnails, since no
assertion about the *document* could ever catch this.

**Re-editable annotations (decision 9)** are hit-tested from the
document itself: `PageCanvas._page_annotations` reads rects and ids via
fitz, so clicking picks the topmost annotation, dragging moves it
(committing an `EditAnnotationOperation`), and Delete removes it. The
index is dropped per page on invalidation, along with any selection on
an edited page.

**Selection-aware dialogs**: `BaseToolDialog.set_page_selection()`
fills the `pages` field that twelve tool dialogs share by convention,
and `_run_tool` passes the sidebar's selection. It only fills an
*empty* field - a value the dialog set for itself, or one the user
typed, is not ours to overwrite. Selecting pages 2, 5 and 9 and opening
Rotate no longer asks the user to type "2,5,9".

Also: `add_ink_annot` wants plain `(x, y)` float pairs and rejects
`fitz.Point` outright ("arg must be seq of seq of float pairs").

Still to come in 6e: insert-content operations (text box, image).

### 6f — redaction (done)

`core/ops/redact.py`. Split from 6e deliberately: this is the one
feature where a shortcut is a security failure rather than a UX
compromise.

**The finding that matters: `apply_redactions()` alone is not enough -
the save matters.** After redacting "Jane Doe" from every page,
`page.get_text()` came back clean and the string was **still present in
the raw file bytes**. A plain `Document.save()` leaves the superseded
content stream in the file as an unreferenced object, trivially
recoverable. Measured on the same fixture:

    plain save            "Jane Doe" in raw bytes: True   (3463 bytes)
    garbage=1             False  (1939)
    garbage=4, clean      False  (1242)

So the operation saves with `garbage=4, clean=True, deflate=True`, and
`tests/unit/test_redact.py` asserts against `read_bytes()` rather than
extracted text - **a text-only test passes the leaking version**, which
is exactly the failure mode this feature cannot afford.

**Page content is not the only leak path.** Document metadata, the XMP
packet, bookmark titles and embedded attachments all survive a content
redaction untouched. `scan_for_text()` reports them separately from
page hits, and the redact dialog shows them under "Also found outside
the page content" - they are the occurrences a user does not think to
check. The `scrub_*` flags only act when a `search_text` is given:
without a term there is nothing to match metadata against, and blanking
it unasked would be a surprise.

`RedactOperation` takes explicit rects *and* an optional search term,
so a CLI or Workflow run is exactly as thorough as an interactive one -
the GUI's review step narrows what happens, it adds no capability. Undo
is a snapshot restore, the only honest inverse for something
destructive by design.

**Redaction is confirmed, not applied on a drag.** "redact" is in the
canvas's `DRAW_TOOLS` because the *gesture* is the same as drawing a
shape, but the handler tells them apart: a redact drag prompts first,
since unlike an annotation it cannot be recovered from the saved file -
only undone from the session snapshot.

### 6g — the design system (done)

**Icons, at last.** `gui/icons.py` draws 20 glyphs with `QPainter`, the
same technique `gui/resources.py` already used for the app mark. Before
this the app had **no iconography at all** - the toolbar was four text
labels. Drawing rather than shipping means no binary assets, nothing
for PyInstaller to bundle, and - the reason it matters here - icons
that re-theme by being *redrawn* rather than needing a second set of
files. The toolbar is now grouped by what the user is doing (file,
undo, zoom, find, annotate, panels) rather than by which menu an action
came from.

**The History panel** (`gui/history_panel.py`) makes the undo stack
visible for the first time. Every `Operation` has had `describe()`
since Phase 0 - it feeds the audit log - and the GUI showed none of it.
Clicking an entry steps undo/redo *to* that point one operation at a
time rather than jumping: `DocumentSession` has no notion of a history
position, and inventing one here would put a second idea of "current
state" beside the one it already owns.

**Light theme, derived rather than duplicated.** The palette is one
table with two columns, so a role cannot be themed in dark and
forgotten in light. `styles.qss` was the harder half: 31 hardcoded
colours, and a palette swap alone left a light palette under a dark
stylesheet - visibly broken. `build_stylesheet()` mirrors each colour's
*lightness* while keeping hue and saturation, which is a mechanical
transformation with nothing to keep in sync. **This works because the
design is greyscale**; a saturated brand colour would invert into
something unintended and would need naming explicitly. Also caught by
looking: `PageCanvas` hardcoded its background, so the viewer stayed
dark while every other surface switched - it now takes a shade off
`Window` and re-applies on `QEvent.Type.PaletteChange`.

**Command palette** (`gui/dialogs/command_palette.py`, Ctrl+Shift+P)
over all 52 commands. A plain `QDialog` subclass, not a `QMenu` - a
testing constraint as much as a design one, since `QMenu.exec` is a
compiled method `patch.object` silently fails to intercept. Matching
requires *every* whitespace-separated term rather than fuzzy
subsequence, which with 40 similarly-named tools returns almost
everything for a short query. A tool's `tool_id` is searchable but not
shown: display names are written for users ("Word to PDF" contains no
"conv" and no "docx"), and the id is often what someone
half-remembers - `"conv pdf"` found nothing until the ids were indexed.

**UI and session state** (`core/session/ui_state.py`) is Qt-free JSON
under `app_data_dir()`, matching `recent_files.py` rather than using
`QSettings` (which writes to the real per-OS location even under
tests). Reopening documents is the privacy tradeoff decision 13 chose,
so it is governed by a `reopen_documents` preference and a
clear-session action; restored documents reopen from their **original
paths** through the normal open flow - session temp dirs are never
resurrected. A corrupt file, an unknown version or an unknown key all
mean "no saved state", never a crash.

Two Qt notes worth keeping:
- State is captured in `closeEvent` **before** `_close_all_tabs()`;
  closing first clears the document identity and would record an empty
  list every time. The tests' `_force_close` helper closes sessions
  first, so a test of this must call `window.close()` directly.
- `_refresh` keys the history update off
  `toggle_history_action.isChecked()`, not `history_dock.isVisible()`:
  a child of a never-shown window always reports invisible, so
  visibility meant the panel never populated under test - and the
  action is the user's intent anyway. Same trap as the find bar.

### 6h — editing existing text (done; experimental by design)

`core/ops/text_edit.py`. The feature users most associate with "PDF
editor" and the one most likely to disappoint, so its limits are stated
rather than discovered.

**There is no "change this text run" API.** The technique, verified end
to end: read the span from `get_text("dict")` (text, font, size,
colour, bbox, baseline origin), decide whether the font can be
reproduced, redact the old bbox, re-insert at the same origin.

**Font extractability is measured, not guessed from the name.**
`doc.extract_font(xref)` returns `ext='n/a'` and a **0-byte** buffer
for a base-14 font, and `ext='ttf'` with **759 KB** for a genuinely
embedded one - so buffer length is the test. Where the buffer exists it
is written into the session temp dir and re-embedded, and the
replacement really does render in the original typeface (confirmed: the
edited span came back reporting `DejaVuSans`).

**A name match is not a name match.** `get_fonts()` reported the same
face as `"DejaVu Sans Book"` while the span called it `"DejaVuSans"`.
Comparing the raw strings found nothing, so *every* font looked
non-embedded and the re-embedding path was silently dead - the tests
passed for the base-14 case and only the embedded-font test caught it.
`_normalise_font_name` strips the subset prefix and all non-alphanumerics
and matches in either direction.

**Decision 12 is enforced in two places, not one.** `EditTextDialog`
shows the warning and a preview *before* anything is written, and
`describe()` appends "(substituted font)" so the fact survives into the
undo stack and the audit log - the failure mode to avoid is not
imperfection but *undisclosed* imperfection.

**Scope, deliberately narrow:** one span, one line. Reflowing a
paragraph across line breaks, around figures or across a page boundary
is materially harder and is not attempted.

Undo is a snapshot restore: redact-and-reinsert destroys the original
glyphs, so nothing else would be honest. The save uses
`garbage=4, clean=True` for the same reason redaction does - otherwise
the replaced text stays recoverable in the raw bytes.

---

## Phase 6 complete

All eight slices are built, tested and pushed. The suite went from
**478** at the start of Phase 6 to **660+**, with `ruff` and
`mypy core cli gui` clean throughout, and every slice verified against
real output and a `widget.grab()` screenshot rather than a green test
run alone - which is what caught the two blank-viewer bugs, the
invisible annotations, and the half-applied light theme, none of which
any assertion was covering.
