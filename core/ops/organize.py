"""Organize, Rotate, Delete Pages, and Compress operations (SPEC.md
Phase 1 list).

All four act on the current working document in place (producing a new
working-file snapshot, per the Operation contract) rather than pulling
in outside sources - that's Merge's job (core/ops/merge_split.py).

Compress uses pikepdf's own stream/object-stream optimization only -
no Ghostscript/image-recompression pass. That's real, measurable
shrinkage for PDFs that don't already use object streams (common for
Word/PowerPoint exports and older scans), but not the "recompress
every image at 70% JPEG quality" style of the Sejda-equivalent tool.
Image recompression needs an external binary and belongs to the
Conversion agent's sandboxed territory (SPEC.md section 3), not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    resolve_page_targets,
    snapshot_restore_invert,
)
from core.registry.plugin_base import ToolPlugin

CORE_VERSION_RANGE = ">=1.0,<2.0"


def _require_working_pdf(doc: DocumentSession) -> None:
    if doc.working_path is None:
        raise OperationError("No document open.")


@dataclass
class ReorderPagesOperation(Operation):
    """Reorders the working document's pages to `page_order`
    (1-indexed permutation of every existing page - use
    ExtractPagesOperation instead if you also want to drop pages)."""

    page_order: list[int]
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            total = len(pdf.pages)
            if sorted(self.page_order) != list(range(1, total + 1)):
                raise OperationError(
                    f"page_order must be a permutation of all {total} page(s); got {self.page_order}."
                )
            reordered = pikepdf.Pdf.new()
            reordered.pages.extend(pdf.pages[n - 1] for n in self.page_order)
            reordered.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "reorder_pages",
            "page_order": list(self.page_order),
        }

    def describe(self) -> str:
        return "Reordered pages"


@dataclass
class RotatePagesOperation(Operation):
    """Rotates `pages` (1-indexed; empty means all pages) by `angle`
    degrees, relative to their current orientation. `angle` must be a
    multiple of 90."""

    angle: int
    pages: list[int] = field(default_factory=list)
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        if self.angle % 90 != 0:
            raise OperationError(f"Rotation angle must be a multiple of 90, got {self.angle}.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            total = len(pdf.pages)
            targets = resolve_page_targets(self.pages, total)
            for n in targets:
                pdf.pages[n - 1].rotate(self.angle, relative=True)
            pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "rotate_pages",
            "angle": self.angle,
            "pages": list(self.pages),
        }

    def describe(self) -> str:
        target = "all pages" if not self.pages else f"{len(self.pages)} page(s)"
        return f"Rotated {target} by {self.angle} degrees"


@dataclass
class DeletePagesOperation(Operation):
    """Removes `pages` (1-indexed) from the working document."""

    pages: list[int]
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        if not self.pages:
            raise OperationError("Delete requires at least one page number.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            total = len(pdf.pages)
            to_delete = set(self.pages)
            for n in to_delete:
                if not (1 <= n <= total):
                    raise OperationError(f"Page {n} is out of range (document has {total} pages).")
            if len(to_delete) == total:
                raise OperationError("Cannot delete every page of a document.")
            kept = pikepdf.Pdf.new()
            kept.pages.extend(pdf.pages[i] for i in range(total) if (i + 1) not in to_delete)
            kept.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "delete_pages",
            "pages": list(self.pages),
        }

    def describe(self) -> str:
        return f"Deleted {len(self.pages)} page(s)"


@dataclass
class CompressOperation(Operation):
    """Re-saves the working document with pikepdf's stream/object-stream
    optimization enabled - see module docstring for what this does and
    does not cover."""

    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)
    _pre_size: int = field(default=0, init=False, repr=False)
    _post_size: int = field(default=0, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        self._pre_snapshot = read_working_bytes(doc)
        self._pre_size = doc.working_path.stat().st_size

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            pdf.save(
                out_path,
                compress_streams=True,
                recompress_flate=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
            )
        self._post_size = out_path.stat().st_size

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "compress"}

    def describe(self) -> str:
        if self._pre_size and self._post_size:
            saved_pct = round(100 * (1 - self._post_size / self._pre_size), 1)
            return f"Compressed ({saved_pct}% smaller)"
        return "Compressed"


class ReorderPagesPlugin(ToolPlugin):
    tool_id = "reorder_pages"
    display_name = "Organize / Reorder Pages"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            page_order = kwargs["page_order"]
        except KeyError as exc:
            raise OperationError("Reorder requires a 'page_order' permutation.") from exc
        return ReorderPagesOperation(page_order=list(page_order))

    def operation_class(self) -> type[Operation]:
        return ReorderPagesOperation


class RotatePagesPlugin(ToolPlugin):
    tool_id = "rotate_pages"
    display_name = "Rotate"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            angle = kwargs["angle"]
        except KeyError as exc:
            raise OperationError("Rotate requires an 'angle'.") from exc
        return RotatePagesOperation(angle=angle, pages=list(kwargs.get("pages", [])))

    def operation_class(self) -> type[Operation]:
        return RotatePagesOperation


class DeletePagesPlugin(ToolPlugin):
    tool_id = "delete_pages"
    display_name = "Delete Pages"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            pages = kwargs["pages"]
        except KeyError as exc:
            raise OperationError("Delete requires a 'pages' list of page numbers.") from exc
        return DeletePagesOperation(pages=list(pages))

    def operation_class(self) -> type[Operation]:
        return DeletePagesOperation


class CompressPlugin(ToolPlugin):
    tool_id = "compress"
    display_name = "Compress"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return CompressOperation()

    def operation_class(self) -> type[Operation]:
        return CompressOperation
