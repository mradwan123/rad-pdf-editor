"""Shared helpers for first-party Operation implementations.

Not a plugin category itself (see SPEC.md section 2 for the module
layout) — just the "snapshot the working file, restore-on-undo"
plumbing that every op in this package reuses so operations without a
cheap true inverse (merge, split, delete pages, protect, ...) still get
correct undo, per the fallback described in
core/model/operation.py's `invert()` docstring.
"""

from __future__ import annotations

import base64
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pikepdf

from core.errors import CorruptDocumentError, OperationError
from core.model.document import DocumentSession
from core.model.operation import Operation

#: Tool ids whose source is one or more *external* files rather than
#: the currently-open document - MergeOperation's shape (see
#: core/ops/merge_split.py and core/ops/convert_to.py). Two callers
#: need this same membership and must not drift apart: the CLI points
#: a fresh working_path at the session dir before apply() for these
#: (allocate_working_path would otherwise fall back to the OS temp
#: root), and the GUI must not gate them behind "Open a document
#: first" - they are usable, by design, with nothing open at all.
EXTERNAL_SOURCE_TOOL_IDS = frozenset(
    {
        "merge",
        "docx_to_pdf",
        "pptx_to_pdf",
        "xlsx_to_pdf",
        "html_to_pdf",
        "jpg_to_pdf",
        "repair",
    }
)

#: Extension -> tool_id, for files File > Open can bring in by running
#: them through the matching external-source conversion above first,
#: rather than trying to open them as a PDF directly - every editing
#: Operation, and the QtPdf/fitz thumbnail renderer, assume the
#: working file already is one. jpg_to_pdf takes `sources` (a list,
#: shared with its Tools-menu "combine several images" form) rather
#: than the singular `source_path` the other four take - a caller
#: building its kwargs from a single opened path needs to wrap it in a
#: one-element list.
CONVERTIBLE_OPEN_EXTENSIONS: dict[str, str] = {
    ".docx": "docx_to_pdf",
    ".pptx": "pptx_to_pdf",
    ".xlsx": "xlsx_to_pdf",
    ".html": "html_to_pdf",
    ".htm": "html_to_pdf",
    ".jpg": "jpg_to_pdf",
    ".jpeg": "jpg_to_pdf",
    ".png": "jpg_to_pdf",
}


def open_pdf(path: Path) -> pikepdf.Pdf:
    """Open `path` with pikepdf, translating library/OS errors into the
    shared `CorruptDocumentError` hierarchy (core/errors.py) instead of
    letting pikepdf/OSError leak past the ops layer."""
    try:
        return pikepdf.Pdf.open(path)
    except (pikepdf.PdfError, OSError) as exc:
        raise CorruptDocumentError(f"Could not open '{path.name}': {exc}") from exc


def read_working_bytes(doc: DocumentSession) -> bytes | None:
    """Snapshot the current working file's bytes, or None if the
    session has no document open yet."""
    if doc.working_path is None or not doc.working_path.exists():
        return None
    return doc.working_path.read_bytes()


def allocate_working_path(doc: DocumentSession, suffix: str = ".pdf") -> Path:
    """Reserve a fresh, empty file path in the session's temp directory
    for an op to write its output into (e.g. via `pikepdf.Pdf.save`).
    Never touches the user's original source file — `doc.working_path`'s
    parent is always a private session temp dir (see
    core/model/document.py), so new files land there too.
    """
    tmp_dir = doc.working_path.parent if doc.working_path is not None else Path(tempfile.gettempdir())
    fd, name = tempfile.mkstemp(prefix="op_", suffix=suffix, dir=tmp_dir)
    os.close(fd)
    return Path(name)


def next_session(doc: DocumentSession, working_path: Path | None) -> DocumentSession:
    """Build the DocumentSession an op's `apply()` should return: same
    `source_path`/`display_name` as `doc`, pointing at a new
    `working_path`. Centralizing this construction means adding a new
    DocumentSession field later (an additive change per SPEC.md 6.1)
    doesn't silently get dropped by every op that forgot to carry it
    forward.
    """
    return DocumentSession(
        working_path=working_path,
        source_path=doc.source_path,
        display_name=doc.display_name,
    )


def resolve_page_targets(pages: list[int], total: int) -> list[int]:
    """Resolve a user-supplied 1-indexed page list against a document
    of `total` pages: empty means "every page", every entry is range-
    checked, and the result is deduplicated and returned in ascending
    order.

    The dedup matters: without it, `pages=[1, 1]` would silently apply
    a per-page operation (rotate, crop, watermark stamp, page number,
    ...) to page 1 twice - a real bug found in review, not a
    hypothetical one (confirmed: RotatePagesOperation with
    pages=[1, 1] and angle=90 produced a 180-degree rotation). Every
    op that takes a `pages` list should resolve it through this
    function rather than rolling its own - four separate near-identical
    copies of this logic already existed before this fix, each with
    the same gap.
    """
    if not pages:
        return list(range(1, total + 1))
    for n in pages:
        if not (1 <= n <= total):
            raise OperationError(f"Page {n} is out of range (document has {total} pages).")
    return sorted(set(pages))


def new_working_copy(doc: DocumentSession, data: bytes, suffix: str = ".pdf") -> Path:
    """Write `data` to a fresh file in the session's temp directory and
    return its path. See `allocate_working_path` for the no-content
    variant used when an op writes its own output (e.g. `pdf.save`).
    """
    path = allocate_working_path(doc, suffix=suffix)
    path.write_bytes(data)
    return path


@dataclass
class RestoreSnapshotOperation(Operation):
    """Generic undo fallback: restores the exact byte snapshot taken
    just before some other operation was applied.

    Never appears in a persisted operation log — `DocumentSession.undo`
    applies it transiently and keeps the *original* operation on the
    redo stack (see core/model/document.py), so `serialize`/`invert`
    here only need to be non-crashing, not meaningful.
    """

    snapshot: bytes | None
    label: str

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if self.snapshot is None:
            return next_session(doc, None)
        path = new_working_copy(doc, self.snapshot)
        return next_session(doc, path)

    def invert(self) -> Operation:
        raise OperationError("RestoreSnapshotOperation is transient and not itself invertible.")

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "restore_snapshot",
            "label": self.label,
            "snapshot_b64": (
                base64.b64encode(self.snapshot).decode("ascii") if self.snapshot is not None else None
            ),
        }

    def describe(self) -> str:
        return f"Restore state before: {self.label}"


def snapshot_restore_invert(doc_snapshot: bytes | None, label: str) -> Operation:
    """Convenience for op modules: `invert()` implementations that use
    the snapshot fallback just return `snapshot_restore_invert(self._pre_snapshot, self.describe())`.
    """
    return RestoreSnapshotOperation(snapshot=doc_snapshot, label=label)
