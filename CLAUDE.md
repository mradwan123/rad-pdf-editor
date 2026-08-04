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
