"""Merge and Split/Extract operations (SPEC.md Phase 1 list).

Merge combines several source PDFs into the session's working
document. Extract keeps only the given page ranges of the current
working document — the single-document-in, single-document-out shape
that `Operation.apply` supports; producing many output files from one
split is a batch/export concern layered on top in the GUI/CLI, not an
Operation itself.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf

from core.errors import OperationError
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.ops.common import (
    allocate_working_path,
    next_session,
    open_pdf,
    read_working_bytes,
    snapshot_restore_invert,
)
from core.registry.plugin_base import ToolPlugin

CORE_VERSION_RANGE = ">=1.0,<2.0"


@dataclass
class MergeOperation(Operation):
    """Concatenates `sources`, in order, into a single working document."""

    sources: list[Path]
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if not self.sources:
            raise OperationError("Merge requires at least one source PDF.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with contextlib.ExitStack() as stack:
            merged = pikepdf.Pdf.new()
            for src in self.sources:
                merged.pages.extend(stack.enter_context(open_pdf(src)).pages)
            merged.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "merge",
            "sources": [str(p) for p in self.sources],
        }

    def describe(self) -> str:
        return f"Merged {len(self.sources)} file(s)"


@dataclass
class ExtractPagesOperation(Operation):
    """Keeps only `pages` (1-indexed, in the given order) of the
    working document, dropping the rest."""

    pages: list[int]
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if doc.working_path is None:
            raise OperationError("No document open to extract pages from.")
        if not self.pages:
            raise OperationError("Extract requires at least one page number.")

        self._pre_snapshot = read_working_bytes(doc)
        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as src:
            total = len(src.pages)
            for n in self.pages:
                if not (1 <= n <= total):
                    raise OperationError(f"Page {n} is out of range (document has {total} pages).")
            extracted = pikepdf.Pdf.new()
            extracted.pages.extend(src.pages[n - 1] for n in self.pages)
            extracted.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "extract_pages",
            "pages": list(self.pages),
        }

    def describe(self) -> str:
        return f"Extracted {len(self.pages)} page(s)"


class MergePlugin(ToolPlugin):
    tool_id = "merge"
    display_name = "Merge"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            sources = kwargs["sources"]
        except KeyError as exc:
            raise OperationError("Merge requires a 'sources' list of PDF paths.") from exc
        return MergeOperation(sources=list(sources))

    def operation_class(self) -> type[Operation]:
        return MergeOperation


class ExtractPagesPlugin(ToolPlugin):
    tool_id = "extract_pages"
    display_name = "Split / Extract Pages"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            pages = kwargs["pages"]
        except KeyError as exc:
            raise OperationError("Extract requires a 'pages' list of page numbers.") from exc
        return ExtractPagesOperation(pages=list(pages))

    def operation_class(self) -> type[Operation]:
        return ExtractPagesOperation
