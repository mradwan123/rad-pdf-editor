# Phase 6 — Editor: GUI overhaul plan

Extends `docs/SPEC.md` section 4's roadmap with a sixth phase. SPEC.md
stays the source of truth for the locked requirements and the frozen
interfaces; this document is the design record for Phase 6 only.

Status: **6a, 6b and 6c done**; 6d onward not started. Slice status is
in the §4 table.

---

## 0. The reframing this plan starts from

Phases 1–5 built 36 operations, and every one of them is a
*whole-document or whole-page transform*: merge, rotate, crop,
watermark, convert, OCR, repair. Not one edits the content **on** a
page.

The GUI matches that shape exactly. A tab is a `QListWidget` of
thumbnails and nothing else (`gui/document_tab.py`). There is no page
viewer, no text selection, no find, no annotation, no redaction. The
interaction model is: pick a tool from a menu, fill in a form, press
OK.

That is a batch-processing suite with a visual file picker. The
product is a **PDF editor**. Closing that gap is Phase 6, and it is
GUI work *plus* a new class of `Operation` — not GUI work alone.

### What does not change

- **The frozen interfaces are not touched.** `Operation`,
  `DocumentSession`, `Pipeline`, `ToolPlugin` keep their current
  signatures. Phase 6 adds one *optional* method (§3.4) and nothing
  else; that is additive and allowed under SPEC.md 6.1.
- **No network calls.** Every capability below is local: PyMuPDF and
  QtPdf, both already dependencies.
- **Everything is still an `Operation`.** Live editing is UI state
  only until it commits; the commit is an `Operation` (§3.3).
- Working copies still live in the private `SessionTempDir`; originals
  are never written.

---

## 1. Decisions locked in this session

| # | Question | Decision |
|---|---|---|
| 1 | Main document view | Real page viewer as the primary pane; thumbnails demote to a navigation sidebar |
| 2 | Tool interaction | Both — selection-aware dialogs *and* on-canvas direct manipulation |
| 3 | Long operations | Background worker with progress and cancel |
| 4 | Sequencing | Decompose `main_window.py` first, then build |
| 5 | Content-editing scope | Markup + redact, **plus** insert new content, **plus** edit existing text |
| 6 | Undo granularity | One `Operation` per committed edit |
| 7 | Viewer engine | Custom `QGraphicsView` canvas + QtPdf's model classes |
| 8 | Visual scope | Full design system — icons, panels, notifications, light/dark |
| 9 | Annotation editing | Fully re-editable after commit — select, move, restyle, delete |
| 10 | Redaction scope | Rect **and** search-and-redact document-wide, **plus** metadata/XMP/bookmark/attachment scrub |
| 11 | Progress reporting | Real per-page progress where the operation loops pages; indeterminate + elapsed time for opaque ones |
| 12 | Font fallback on text edit | Warn, preview the substitute, let the user accept / choose another / cancel |
| 13 | Session persistence | UI state **and** reopen last session's documents, behind a preference |

---

## 2. Two findings that shape the design

Both verified against the installed libraries, not assumed from docs.

### 2.1 QtPdf's useful parts do not require `QPdfView`

`QPdfSearchModel`, `QPdfDocument.getSelection()` /
`getSelectionAtIndex()` / `getAllText()`, `QPdfSelection` (including
`copyToClipboard()`), `QPdfBookmarkModel`, `QPdfLinkModel`,
`QPdfPageNavigator` and `QPdfPageRenderer` all operate on a
`QPdfDocument`. None of them need the `QPdfView` *widget*.

This removes the tradeoff that would otherwise have decided the
architecture. A custom `QGraphicsView` canvas gets editable overlays
**and** real text selection, find, an outline tree, and clickable
links — we do not have to choose between Qt's polished viewer and a
canvas we can draw on.

`QPdfPageRenderer` additionally provides **asynchronous, queued page
rendering**, which is the direct fix for the synchronous full re-render
in `MainWindow._render_thumbnails` (§3.5).

### 2.2 PyMuPDF already covers the whole annotation surface

PyMuPDF 1.28.2 (already used by six modules) provides
`add_highlight_annot`, `add_underline_annot`, `add_strikeout_annot`,
`add_squiggly_annot`, `add_ink_annot` (freehand), `add_rect_annot`,
`add_circle_annot`, `add_line_annot`, `add_polygon_annot`,
`add_polyline_annot`, `add_freetext_annot`, `add_text_annot` (sticky
note), `add_stamp_annot`, `add_caret_annot` — plus `add_redact_annot`
and `apply_redactions`.

That last pair matters: it gives **true redaction** that removes the
underlying content, not a black rectangle drawn over still-extractable
text. For a tool whose stated purpose is confidential and regulated
documents, shipping the cosmetic version would be worse than shipping
nothing.

No new dependency is required for any of §3.3.

---

## 3. Architecture

### 3.1 Step zero — decompose `gui/main_window.py`

1163 lines currently handling menus, actions, tab lifecycle, thumbnail
rendering, tool dispatch, workflows, autosave recovery, dirty checks
and error reporting. Phase 6 roughly doubles that surface. Split
before building:

| New module | Takes over |
|---|---|
| `gui/main_window.py` | Window shell, layout, panel docking only |
| `gui/actions.py` | `QAction` construction, menus, toolbar, shortcuts |
| `gui/tab_manager.py` | Tab lifecycle, dirty checks, close/replace logic |
| `gui/rendering.py` | Page + thumbnail rendering, the render cache |
| `gui/tool_runner.py` | `_run_tool`, workflow runs, background execution |
| `gui/panels/` | History, properties, outline, search-results panels |

**Test-compatibility constraint:** `MainWindow.controller`,
`.thumbnail_list`, `.current_tab` and `.tabs()` are load-bearing for
roughly 470 existing tests. `.thumbnail_list` keeps its name and
meaning — it becomes the sidebar's list rather than the tab's only
widget — so the decomposition is a move, not a rename. Any test that
must change should change because behaviour changed, not because a
property moved.

### 3.2 The viewer

`gui/page_canvas.py` — a `QGraphicsView` over a `QGraphicsScene` laid
out as a continuous vertical strip of `PageItem`s, one per page.

- Zoom is a view transform; fit-width and fit-page are computed from
  the viewport. Page geometry stays in PDF points, exactly as
  `PagePlacementCanvas` already does, so no coordinate convention is
  introduced or changed.
- Rendering is delegated to `gui/rendering.py` (§3.5): pages are
  rendered on demand at the current zoom, cached, and only visible
  pages plus a small lookahead are ever requested.
- Text selection, find and links come from the QtPdf models in §2.1,
  overlaid as scene items.
- `gui/placement_canvas.py`'s `PlacementItem` — its corner-handle
  hit-testing, press-time-delta drag maths and page clamping are all
  proven and tested — is generalised into the shared base for every
  on-canvas tool rather than duplicated. `PagePlacementCanvas` becomes
  one caller of that base, not a parallel implementation.

Layout: outline/thumbnail sidebar | page canvas | inspector panel,
in `QSplitter`s, with History and Properties as dockable panels.

### 3.3 Content-editing operations

New modules under `core/ops/`, following every existing convention
(dataclass, `apply`/`invert`/`serialize`/`describe`, registered via a
`ToolPlugin`, errors from `core/errors.py`):

| Module | Operations |
|---|---|
| `core/ops/annotate.py` | Highlight, Underline, Strikeout, Squiggly, StickyNote, Ink, Shape (rect/circle/line/polygon) |
| `core/ops/redact.py` | Redact — `add_redact_annot` + `apply_redactions`, content genuinely removed |
| `core/ops/content.py` | InsertTextBox, InsertImage, InsertStamp |
| `core/ops/text_edit.py` | EditTextSpan (§5.1 — experimental) |

Each takes explicit, serialisable parameters (page, rect or point
list, colour, text) in the project's existing bottom-left-origin PDF
coordinate convention, converted internally to fitz's top-left origin
— identical to `SignOperation`. That is what makes every one of them
usable from the CLI and from a saved Workflow, not just from the
canvas.

`invert()` for an added annotation removes it by its stable
identifier; for redaction and text editing, which destroy content,
`invert()` restores the pre-apply snapshot, exactly as OCR already
does.

These compose with what already exists: the existing Flatten and
Remove Annotations operations now have something to act on.

### 3.4 Interaction: canvas tools and selection-aware dialogs

**On-canvas.** `gui/canvas_tools/` — a `CanvasTool` base handling
press/move/release on the canvas and producing a committed
`Operation`. One subclass per tool. The active tool is window state,
shown in the toolbar. Committed on mouse release (decision #6): one
highlight, one shape, one redaction = one `Operation`, one undo step,
one audit entry.

Also extends the canvas to Crop, Watermark, Header/Footer and Create
Forms — all four already take a rect in the same convention and all
four currently ask the user to type four numbers.

**Selection-aware dialogs.** `BaseToolDialog` gains an optional
`set_page_selection(pages: list[int])`; `_run_tool` passes the
sidebar's current selection. Every dialog with a page-range field
prefills from it. Small change, touches all 36 tools, removes the
absurdity of selecting pages 2, 5 and 9 and then being asked to type
"2,5,9".

**The one additive interface change.** `Operation` gains an optional
`affected_pages(self) -> list[int] | None` defaulting to `None`
("unknown — assume all"). Purely a rendering hint: it lets the canvas
invalidate one page instead of re-rendering the document after every
edit. Additive, non-abstract, no `schema_version` bump, per SPEC.md
6.1.

### 3.5 Rendering and responsiveness

Today `_refresh()` re-renders **every page from disk, synchronously**,
after every operation, undo, redo and zoom step. Measured directly on
a real 500-page document rather than extrapolated: **1065 ms** at the
default zoom and **2292 ms** at the 720 px ceiling, blocking the UI
thread, on every single click. (An earlier draft of this plan
extrapolated ~6 s from a 15-page sample; the real figure is lower, and
measuring beat scaling.) With per-edit commits (decision #6) that
becomes the cost of every mark.

Three changes together:

1. **Async rendering** via `QPdfPageRenderer` — requests are queued
   and delivered by signal; the UI never blocks on rasterisation.
2. **A render cache** keyed by (document revision, page, size), so a
   zoom step or an unrelated edit does not re-rasterise untouched
   pages.
3. **Targeted invalidation** using `affected_pages()` (§3.4) — a
   highlight on page 3 invalidates page 3.

**Background execution.** `gui/tool_runner.py` runs each `Operation`
on a `QThreadPool` worker with a progress dialog and a Cancel button.
Operations stay synchronous and unchanged — the worker wraps them.

Cancellation is cooperative and safe *because of how the existing
model already works*: an operation writes to a **new** working path
and the session only swaps to it on success. Cancelling therefore
means "discard the in-progress result and securely wipe the partial
file" — never a half-written document replacing a good one.

### 3.5.1 What 6b actually delivered (measured)

Built and measured on a real 500-page document:

| | UI blocked, pre-6b | UI blocked, 6b | After editing one page |
| --- | --- | --- | --- |
| default zoom 120x160 | 1065 ms | **10 ms** | 1 of 500 re-rendered, 2 ms |
| max zoom 720x960 | 2292 ms | **10 ms** | 428 of 500, 2005 ms (background) |

Two separate wins, worth not conflating:

- **Async delivery is a universal win.** The UI never blocks for more
  than ~10 ms regardless of document length or zoom, because
  `render()` returns as soon as the items exist. It buys
  *responsiveness, not throughput* — total rasterisation time is
  unchanged (measured 18 ms sync vs 19 ms async for the same 40
  pages), the event loop simply stops waiting on it.
- **The cache win is complete only while the document fits its
  budget.** At 120x160 a page costs 75 KB, so all 500 fit in the
  192 MB budget and an edit re-renders exactly the page it touched. At
  720x960 a page costs 2.7 MB, only 72 fit, and LRU eviction means an
  edit re-renders ~428 pages again — off the UI thread, but still real
  work.

**Known limitation, deliberately not fixed here.** Thumbnails are
rendered eagerly for every page. The right fix is to request only the
pages actually on screen plus a lookahead, which §3.2 already
specifies for the 6c viewer; doing it for thumbnails now would need
its own handling for a widget that has never been shown (a headless
test has no viewport, so "visible" is empty). Raising the cache budget
instead would only move the cliff, since page cost grows with the
square of the zoom.

### 3.2.1 Viewer notes from building it

**Scene units are device pixels, not points scaled by a view
transform.** Each page is rendered at exactly the pixel size it
occupies and the view transform stays at identity, so zooming in
produces a genuinely sharper page rather than a magnified blurry one.
Fit modes compute a zoom factor from the viewport rather than calling
`fitInView`.

**Viewport-limited rendering lands here, not in 6b.** Only pages
intersecting the viewport (plus a two-page lookahead) are requested.
That is what §3.5.1 flagged as the fix for 6b's eviction cliff, and it
matters far more here: a full-size page costs ~2 MB against a
thumbnail's 75 KB. Placeholders are *painted*, never allocated - a real
placeholder `QPixmap` per page would cost ~1 GB for 500 unrendered A4
pages at 100%.

**Zoom is now two separate controls.** The page view takes the standard
shortcuts (Ctrl+= / Ctrl+- / Ctrl+0, plus Ctrl+1 fit width and Ctrl+2
fit page) because it is the primary pane; thumbnail sizing keeps its
behaviour under Ctrl+Shift as sidebar navigation. Three existing tests
moved to the renamed thumbnail actions - the coverage is unchanged,
only which action drives it.

**`getSelection()` is unusable in this Qt build** (PySide6 6.11.1,
verified): it returns an invalid, empty `QPdfSelection` for every point
range tried, including ranges squarely over text that `getAllText()`
reports. Text selection therefore cannot use the obvious API and will
be built from `getAllText().bounds()` - which returns one polygon per
line, in top-left-origin PDF points - plus `getSelectionAtIndex()`,
which does work correctly. Per-character rects cost ~906 us each
(2.6 s for a dense page), so a point maps to a character by locating
the line and binary-searching within it, never by walking the page.

### 3.2.2 Text selection, without the API that was meant to do it

`QPdfDocument.getSelection()` - point-range selection, exactly what a
drag needs - is unusable in PySide6 6.11.1. It returns an invalid,
empty `QPdfSelection` for every range tried, including ranges squarely
over text `getAllText()` reports on the same page.

`gui/text_selection.py`'s `PageTextIndex` rebuilds the capability from
the parts that do work:

- `getAllText(page)` gives the page text plus `bounds()`, which is one
  polygon per line in top-left-origin PDF points.
- The text separates lines with `\r\n`, so line *k* of the text maps
  to polygon *k* of `bounds()` - that correspondence is the whole
  mechanism, and a page where the two counts disagree is treated as
  unselectable rather than mis-mapped.
- A point resolves to a line by its y, then to a character by binary
  searching that line's index range with `getSelectionAtIndex`,
  comparing against each glyph's midpoint so clicking the right half of
  a character selects past it.

Walking a page character by character is not viable:
`getSelectionAtIndex` costs ~906 us per call, 2.6 s for a
2923-character page. A binary search is ~log2(line length) calls, and
probed rects are cached.

**Selection is within one page.** A drag that wanders onto another page
keeps extending on the page it started on. Cross-page selection is a
separate piece of work; silently selecting the wrong page's text would
be worse than not extending.

**External links are shown, not opened.** `QPdfLinkModel` reports an
internal link's target page and an external link's URL (with page -1).
Internal links navigate. External ones are surfaced in the status bar
and never handed to a browser: SPEC.md section 1 forbids network access
anywhere in this app, and a click on an untrusted document's link is
exactly the wrong trigger for outbound traffic from a
confidential-documents tool.

### 3.6 Design system

- **Icons.** The app currently has none — the toolbar is four text
  labels. A hand-drawn `QPainter` icon set, same technique as
  `gui/resources.py`'s logo: no binary assets, no PyInstaller `datas`
  entry, and it re-themes with the palette automatically.
- **Toolbar.** Grouped by function (navigate | select | markup | draw
  | redact | insert), with the active tool visibly active.
- **Panels.** History (the undo stack, which every `Operation`
  already describes via `describe()` and which is currently invisible),
  Properties (page size, encryption, form fields, metadata), Outline,
  Search results.
- **Notifications.** Non-blocking inline banners replace modal
  `QMessageBox.critical` for recoverable errors. Blocking prompts stay
  blocking only where a decision is genuinely required (unsaved
  changes).
- **Light/dark.** `styles.qss` is dark-only and hardcoded. Tokenise
  the palette and add a toggle, keeping the `QPalette`-before-
  stylesheet ordering that CLAUDE.md documents as load-bearing for
  `QListWidget` selection highlighting.
- **Command palette.** 36 tools across 8 submenus with no search.
  Must be a real Python `QDialog` subclass — instance `QMessageBox.exec`
  and `QMenu.exec` are compiled and unpatchable, which has hung this
  project's headless tests before.

### 3.7 Consequences of decisions 9–13

**Annotation identity must survive a rewrite (dec. 9).** Every commit
writes a *new* working file, so PyMuPDF `xref` values are not stable
across edits. Each annotation this app creates is therefore stamped
with a UUID in its `/NM` (annotation name) entry — the PDF spec's own
per-page unique identifier — and every annotation-editing `Operation`
addresses its target by that name, not by index or xref. The canvas
keeps a live annotation layer built from `page.annots()`, hit-testable
and handle-draggable, reusing the same `PlacementItem` base as every
other on-canvas tool (§3.2).

**Redaction is a two-stage tool (dec. 10).** Stage one *finds*:
either a dragged rect, or a search string resolved to every occurrence
document-wide plus every hit in metadata, XMP, bookmarks, attachments
and annotation contents. Stage two *reviews and applies*: a checklist
the user can deselect from, then `add_redact_annot` +
`apply_redactions` for page content, and explicit removal for the
non-content hits — which is where "redacted" PDFs most often still
leak. `RedactOperation` therefore takes an explicit list of targets,
so a CLI or Workflow run is exactly as thorough as a GUI one and never
depends on an interactive step.

**Progress is opt-in per operation (dec. 11).** A `SupportsProgress`
mixin in `core/model/` exposes `set_progress_callback(cb)` with a
no-op default; page-looping operations call it. `Operation.apply`'s
frozen signature is untouched — the runner feature-detects the mixin
and shows an indeterminate bar with elapsed time for everything else.
Adding an optional parameter to `apply()` was rejected: existing
subclasses declare `apply(self, doc)` and would raise `TypeError` when
called with it, which is a breaking change wearing an additive
costume.

**Font substitution is surfaced before commit (dec. 12).** The text
edit tool resolves the span's font via `pdf.extract_font(xref)` first;
if it is not extractable, the commit is held and a comparison preview
is shown (original vs. substitute, rendered) with accept / choose
another / cancel. Nothing is written until the user chooses.

**Session restore is a preference, not a default assumption (dec.
13).** `core/session/ui_state.py` persists panel geometry, visibility,
zoom/fit mode, theme, active tool and annotation defaults, plus the
open document set, to `app_data_dir()` as JSON — Qt-free and honouring
`PDFEDITOR_APP_DATA_DIR`, matching `recent_files.py` exactly rather
than using `QSettings`. Because this app's whole premise is
confidential documents, reopening is governed by an explicit
**"Reopen documents on launch"** preference and a **"Clear saved
session"** action, so a shared or presented-from machine can turn it
off without losing panel layout. Restored documents are reopened from
their original paths through the normal open path — session temp dirs
are never resurrected.

---

## 4. Sequencing

Each slice ships green — `ruff check .`, `mypy core cli gui`, full
pytest — before the next begins.

| Slice | Contents |
|---|---|
| **6a** | ✅ **Done.** Decomposed `main_window.py` 1174 → 541 lines (§3.1). Pure move: same 478 tests passing, zero test changes. |
| **6b** | ✅ **Done.** Rendering layer: async, cached, targeted invalidation (§3.5). UI blocking on a 500-page document went 1065 ms → 10 ms; an edit at the default zoom now re-renders 1 page instead of 500. |
| **6c** | ✅ **Done.** Page viewer + sidebar layout (§3.2): continuous scroll, zoom, fit width/page, viewport-limited rendering, outline panel, find-in-document with highlights, drag-to-select text with copy, and links. |
| **6d** | Background execution with progress and cancel (§3.5). |
| **6e** | Markup and insert operations (§3.3) with their canvas tools (§3.4), including the re-editable annotation layer (§3.7); selection-aware dialogs; existing rect tools moved on-canvas. |
| **6f** | Redaction: rect, document-wide search-and-redact, and the metadata/XMP/bookmark/attachment scrub with its review step (§3.7). Split from 6e because it is security-critical and deserves its own verification pass. |
| **6g** | Design system: icons, toolbar, panels, notifications, light/dark, command palette (§3.6); session restore and its preference (§3.7). |
| **6h** | Text editing (§5.1), explicitly experimental, with the font-substitution preview of §3.7 gating every commit. |

6a–6d are infrastructure with no new user-facing tools; 6c is the
first slice that visibly changes the product.

---

## 5. Risks and open questions

### 5.1 Editing existing text is the hard one

There is no "change this text run" API in PyMuPDF, or in any library
we have. The workable technique:

1. `page.get_text("dict")` gives spans with bbox, font name, size,
   colour and flags.
2. Try `pdf.extract_font(xref)` for the span's font. If the font is
   embedded and extractable, re-embed it with
   `page.insert_font(fontbuffer=...)` and the replacement renders in
   the original typeface.
3. If it is not extractable, fall back to a metric-compatible base-14
   substitute — and **say so in the UI before committing**, rather
   than silently changing how the document looks.
4. Redact the original span's bbox, then insert the new text.

Scope honestly: **single-span, single-line edits first.** Reflowing a
paragraph across line breaks, around figures, or across a page
boundary is a materially harder problem and is not in 6h. Acrobat
itself does this imperfectly; the failure mode to avoid is not
imperfection but *undisclosed* imperfection.

This is why 6h is last, and why decision 12 holds every commit behind
a rendered comparison of the original and the substitute.

### 5.2 Per-edit commit cost

Decision #6 gives the best undo granularity and the most useful audit
trail, at the cost of a full working-file rewrite per mark. §3.5's
three rendering changes make the *display* cost negligible, but the
file write remains. If a heavy markup session proves slow in practice,
the fallback is decision #6's second option (batch per tool session)
for annotation tools only — a change to when the commit fires, not to
the architecture. Measure before optimising.

### 5.3 CI has failed all 22 runs

Sequencing decision #4 chose decomposition over fixing CI first, so
Phase 6 will be verified **locally only** for its duration: full
suite, `ruff`, `mypy --strict`, plus the visual `widget.grab()` check
this project already requires for UI work. That is a deliberate,
accepted risk, and it means the Linux and Windows legs stay unverified
across a large refactor. The leading unconfirmed theory
(`libqoffscreen.so`'s native dependency chain on a bare CI VM) is
recorded in CLAUDE.md; it is worth revisiting before 6c lands, because
a viewer is exactly the kind of change that would benefit from
cross-OS verification.

### 5.4 Testing under `offscreen`

Established constraints that apply to everything above: real mouse
drags are not reliably simulatable, so canvas tools are tested by
constructing `QGraphicsSceneMouseEvent`s directly (as
`tests/unit/test_sign_placement_canvas.py` already does); `QMenu.exec`
and instance `QMessageBox.exec` cannot be patched; `QTest.keyClick`
needs `activateWindow()` first. Every new widget also gets the
`widget.grab()`-and-look check — a green suite has repeatedly failed
to prove this project's UI actually renders correctly.

---

## 6. What "done" looks like

A user opens a PDF, reads it at a legible size, finds a phrase,
selects and copies text, highlights a paragraph, adds a comment,
redacts a name so it is genuinely gone from the file, drops in a text
box, drags a crop rectangle directly on the page, sees every one of
those as a named step in a History panel they can undo one at a time,
and watches a 300-page OCR run with a progress bar they can cancel —
without the window ever freezing.

None of that is possible today.
