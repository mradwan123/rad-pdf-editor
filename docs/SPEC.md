# Local PDF Editor — Technical Specification (v0.1)

## 1. Locked Requirements

| Decision | Answer | Architectural Consequence |
|---|---|---|
| Platforms | Windows, macOS, Linux | PySide6 + PyInstaller per-OS builds; avoid platform-specific paths in core |
| Stack | Python, PySide6 | Native widgets, `QtPdf` for rendering, `QThread`/`QThreadPool` for background work |
| Distribution | Small team, local installs | No server component; no shared license/auth system needed |
| File scale | Up to a few hundred pages | No need for streaming/chunked page-by-page processing; can hold a document's working state in memory, but must stream *output* generation for large batch jobs |
| Multi-file handling | Full pipeline — chain ops, run unattended | Core needs a pipeline/executor abstraction, not just single-shot functions |
| Data sensitivity | Confidential/regulated docs | No network calls anywhere in the binary; secure temp-file deletion; audit trail required |
| Persistence | Autosave + full undo/redo across session | Command-pattern engine with an operation log, not direct mutation |
| Extensibility | Plugin architecture | Core exposes a registry/entry-point system; built-in tools are "first-party plugins" |
| Testing | Rigorous — full regression corpus, CI-gated | Dedicated synthetic fixture corpus + CI matrix across 3 OSes |

Derived (from "no network calls"):
- **No auto-updater.** Updates ship as new installers.
- **No telemetry/crash reporting.** Logging is local-only, user-controlled, included in the audit trail.
- **No shared backend.** Each team member's copy is fully independent.

## 2. Architecture Overview

```
/core
  /model
    document.py        # DocumentSession: wraps a PDF + its operation log
    operation.py        # Operation base class (do/undo/redo, serializable)
    pipeline.py          # Pipeline: ordered list of Operations, executes unattended
  /registry
    plugin_base.py       # ToolPlugin interface all operations implement
    registry.py           # discovers first-party + third-party plugins (entry_points)
  /ops                    # first-party plugins, one module per Sejda-list category
    merge_split.py
    organize.py
    security.py
    watermark.py
    metadata.py
    convert_from.py
    convert_to.py
    ocr_scan.py
    numbering.py
    layout.py
    forms.py
    repair.py
  /session
    autosave.py           # periodic snapshot + crash-recovery journal
    audit_log.py            # append-only local audit trail (who/what/when, no network)
  /security
    secure_delete.py        # multi-pass temp file wipe
    sandbox.py                # enforce no-network at the process level (defense in depth)
/plugins                     # third-party/team-authored plugins live here, auto-discovered
/workflows                   # saved pipeline definitions (JSON), Workflows feature
/gui                          # PySide6: main window, thumbnail grid, tool panels, pipeline builder UI
/workers                      # QThreadPool wrappers, progress signals, cancellation
/cli                            # scripting entry point, reuses /core directly
/tests
  /fixtures                     # synthetic PDFs: encrypted, malformed, huge, scanned, form-bearing
  /unit                           # per-op tests
  /integration                     # full pipeline tests
  /regression                        # golden-file byte/structure comparison
/packaging                            # PyInstaller specs, per-OS build scripts
```

### Core design: everything is an `Operation`

Every action in the tool list (merge, watermark, OCR, etc.) is a class implementing:

```python
class Operation(ABC):
    def apply(self, doc: DocumentSession) -> DocumentSession: ...
    def invert(self) -> "Operation": ...       # enables undo without re-deriving state
    def serialize(self) -> dict: ...             # enables autosave, audit log, and Workflows
    def describe(self) -> str: ...                 # human-readable, for undo stack UI + audit log
```

This single abstraction is what makes undo/redo, autosave, the audit trail, and the Workflows automation feature fall out of the same mechanism instead of four separate systems:

- **Undo/redo** = walking the operation log backward/forward.
- **Autosave/crash recovery** = periodically persisting the operation log (cheap — it's a list of small serialized ops, not full document copies) plus a checkpoint of the working file.
- **Audit trail** = the operation log *is* the audit trail; each entry gets a timestamp and description.
- **Workflows** = a saved, named sequence of serialized operations that can be replayed against new input files unattended.

### Plugin system

`ToolPlugin` is the contract; each first-party tool in `/core/ops` registers itself the same way a future team-authored plugin in `/plugins` would (via Python entry points or a simple manifest scan at startup). This means "architect for extensibility" doesn't require a second system later — it's the only system, from day one.

### Security layer

- All external processes (LibreOffice, Ghostscript, Tesseract) run with no network access at the OS process level where the platform allows it (e.g., Windows Firewall rule per subprocess, or a wrapper that fails closed if a socket call is attempted) — defense in depth beyond "we just don't call requests.get()".
- Temp files (conversion intermediates, OCR scratch files) are written to a per-session temp directory and wiped with multi-pass overwrite + delete on session close or crash-recovery cleanup, not just `os.remove`.
- Audit log is append-only, stored locally (SQLite or JSONL), never transmitted.

## 3. Agent/Workstream Split (updated)

1. **Core Engine agent** — `Operation`/`DocumentSession`/`Pipeline` framework first, then first-party ops one category at a time.
2. **Plugin/Registry agent** — the extensibility system itself: discovery, manifest format, versioning/compatibility checks for third-party plugins.
3. **Conversion agent** — LibreOffice/Ghostscript/Tesseract integration, isolated because of binary discovery + sandboxing complexity.
4. **Session/Persistence agent** — autosave, crash recovery, undo/redo stack UI hooks, audit log.
5. **Security agent** — secure delete, network-lockdown enforcement, audit log integrity.
6. **UI/UX agent** — main window, thumbnail/reorder view, pipeline builder (drag-and-drop op chaining), tool dialogs.
7. **Testing/QA agent** — synthetic fixture corpus, regression suite, 3-OS CI matrix, owns the "gate every merge" policy.
8. **Packaging agent** — PyInstaller specs, bundling Ghostscript/Tesseract/LibreOffice or clean dependency-detection UX per OS.

## 4. Phased Roadmap

- **Phase 0 — Foundation.** `Operation`/`DocumentSession`/`Pipeline`/registry framework. This is now the critical path everything else depends on — nothing else should start until this interface is stable.
- **Phase 1 — MVP ops.** Merge, Split/Extract, Organize, Rotate, Delete Pages, Rename, Metadata, Compress, Protect/Unlock, Watermark. Basic thumbnail UI + undo/redo wired to the framework.
- **Phase 2 — Forms & layout.** Fill & Sign, Create Forms, Flatten, Crop, Resize, Bates/page numbers, header/footer, grayscale, N-up, flip, remove annotations.
- **Phase 3 — Conversions.** Word/PPT/Excel/JPG/HTML both directions — hand fully to Conversion agent, sandboxed.
- **Phase 4 — Scans.** OCR, Deskew, Repair.
- **Phase 5 — Automation & polish.** Workflows UI (pipeline builder + save/replay), plugin manifest docs for the team, installers.

## 5. Open items for later (not blocking Phase 0)

- Exact plugin manifest format (entry_points vs. simple `plugin.json` scan).
- Audit log storage format (SQLite vs. JSONL) — leaning JSONL for simplicity + easy diffing.
- Whether the network-lockdown enforcement needs to be OS-firewall-based or just process-argument/env-based (affects installer complexity/permissions prompts).

## 6. Coordination Conventions (locked, applies to all agents)

These exist specifically to prevent parallel agents from silently diverging.

### 6.1 Interface freeze policy
The `Operation`, `DocumentSession`, `Pipeline`, and `ToolPlugin` shapes are frozen at the end of Phase 0. After that:
- **Additive changes only** — new optional fields/methods are fine; changing or removing existing signatures requires a version bump and a migration note in `CHANGELOG.md`.
- Every serializable object (`Operation.serialize()`, Workflow files, plugin manifests) carries a `schema_version: int` field from its first commit.
- Core library follows semver. Plugins declare a `compatible_core_version` range in their manifest; the registry refuses to load a plugin outside that range instead of failing at runtime mid-pipeline.

### 6.2 UI/design system
Standard is **PySide6's native widget set with the Qt Fusion style** applied consistently — no custom component library to design or maintain. Consistency across the 40 tool dialogs comes from:
- A single `BaseToolDialog` class other dialogs subclass (consistent layout: preview pane, options panel, action buttons in the same position every time).
- A shared `styles.qss` stylesheet for spacing/typography, not per-dialog styling.
- All user-facing strings wrapped in `tr()` from the start (i18n-ready, English-only for now).
- Accessible names and full keyboard navigation set on every interactive widget as it's built — not a later pass.

### 6.3 Engineering standards (CI-enforced, blocks merge)
- `mypy --strict` on `/core`, `/registry`, `/plugins`; `/gui` may relax strictness slightly where Qt's stubs are incomplete.
- `ruff` for linting + formatting.
- Every PR must pass the full unit + integration suite on all 3 OSes before merge (ties into the Testing agent's CI matrix from Section 3).
- One shared exception hierarchy in `/core/errors.py`: `PDFEditorError` as base, with `ConversionError`, `SecurityError`, `CorruptDocumentError`, `PluginCompatibilityError`, etc. No module defines its own ad-hoc exceptions.
- One shared structured logging setup in `/core/logging_config.py` (JSON-lines, local file only, feeds the audit trail) — no module calls `print()` or configures its own logger.

### 6.4 Config & persistence conventions
- User preferences via `QSettings` (native per-OS store: registry/plist/ini) — no custom config file format to design.
- Autosave/crash-recovery journal and audit log are local files under the OS-appropriate app-data directory, never the user's working directory (avoids polluting folders containing confidential source documents).

### 6.5 Git workflow
Trunk-based development. Each agent works in short-lived branches scoped to one module/feature, opens a PR against `main`, and merges only after CI (tests + mypy + lint) passes. No long-lived per-agent branches — those are exactly what causes silent interface drift.
