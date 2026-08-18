# Phase 6 — Editor: GUI overhaul plan

Extends `docs/SPEC.md` section 4's roadmap with a sixth phase. SPEC.md
stays the source of truth for the locked requirements and the frozen
interfaces; this document is the design record for Phase 6 only.

Status: **planned, not started.** Nothing here has been implemented.

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
after every operation, undo, redo and zoom step. Measured at 0.188s
for 15 pages, which extrapolates to ~6s for a 500-page document after
every single click. With per-edit commits (decision #6) that becomes
unusable during markup.

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

---

## 4. Sequencing

Each slice ships green — `ruff check .`, `mypy core cli gui`, full
pytest — before the next begins.

| Slice | Contents |
|---|---|
| **6a** | Decompose `main_window.py` (§3.1). Pure move; behaviour and tests unchanged. |
| **6b** | Rendering layer: async, cached, targeted invalidation (§3.5). Still thumbnails-only — the win is measurable before any new UI exists. |
| **6c** | Page viewer + sidebar layout (§3.2), read-only: scroll, zoom, fit modes, text selection, find, outline, links. |
| **6d** | Background execution with progress and cancel (§3.5). |
| **6e** | Markup, redaction and insert operations (§3.3) with their canvas tools (§3.4); selection-aware dialogs; existing rect tools moved on-canvas. |
| **6f** | Design system: icons, toolbar, panels, notifications, light/dark, command palette (§3.6). |
| **6g** | Text editing (§5.1), explicitly experimental and gated behind a warning. |

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
boundary is a materially harder problem and is not in 6g. Acrobat
itself does this imperfectly; the failure mode to avoid is not
imperfection but *undisclosed* imperfection.

This is why 6g is last and gated.

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
