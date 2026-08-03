"""Flatten, Remove Annotations, Fill Form, and Sign operations
(SPEC.md Phase 2 list).

Flatten composites each annotation's visual appearance onto the page's
own content (so it prints/exports identically but is no longer an
interactive object) and drops it from /Annots. Remove Annotations just
drops matching annotations outright, no visual trace kept. Together
they cover "make this non-interactive" (keep the look) vs. "get rid
of this" (comments, highlights, stray form widgets, ...).

Scope note (Flatten): only annotations with a normal appearance
stream that's directly a Form XObject are flattened. Annotations
whose /AP /N is a sub-state dictionary (e.g. a checkbox's On/Off
appearances) are flattened only if /AS names a present sub-state;
otherwise that single annotation is left as-is rather than guessing -
this only skips that one annotation, it doesn't fail the operation.

Fill Form and Sign are the "Fill & Sign" pair from SPEC.md's Phase 2
list. Both are visual/data operations, not cryptographic signing (a
digital-signature op using pyhanko - already a project dependency -
would be a distinct future feature). Neither has an interactive
click-to-place canvas yet (the GUI's thumbnail grid isn't a page
editor); Fill takes explicit field name/value pairs (see
`list_form_field_names` for discovering names) and Sign takes an
explicit page + rect, both things a future interactive canvas would
compute for the user rather than replace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
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


def _require_working_pdf(doc: DocumentSession) -> None:
    if doc.working_path is None:
        raise OperationError("No document open.")


def _resolve_targets(pages: list[int], total: int) -> list[int]:
    targets = pages or list(range(1, total + 1))
    for n in targets:
        if not (1 <= n <= total):
            raise OperationError(f"Page {n} is out of range (document has {total} pages).")
    return targets


def _appearance_stream(annot: pikepdf.Object) -> pikepdf.Object | None:
    ap = annot.get("/AP")
    if ap is None or "/N" not in ap:
        return None
    normal = ap.get("/N")
    if isinstance(normal, pikepdf.Stream):
        return normal
    if normal is None:
        return None
    # Sub-state dictionary (e.g. checkbox On/Off) - use the active state.
    state = annot.get("/AS")
    if state is not None and str(state) in normal:
        candidate = normal.get(str(state))
        if isinstance(candidate, pikepdf.Stream):
            return candidate
    return None


@dataclass
class FlattenOperation(Operation):
    """Bakes annotation appearances into page content on `pages`
    (1-indexed; empty means all), removing the flattened annotations
    from /Annots."""

    pages: list[int] = field(default_factory=list)
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            total = len(pdf.pages)
            targets = _resolve_targets(self.pages, total)
            for n in targets:
                page = pdf.pages[n - 1]
                annots = page.obj.get("/Annots")
                if annots is None:
                    continue
                remaining = []
                for annot in annots:
                    stream = _appearance_stream(annot)
                    rect = annot.get("/Rect")
                    if stream is None or rect is None:
                        remaining.append(annot)
                        continue
                    box = [float(x) for x in rect]
                    page.add_overlay(stream, rect=pikepdf.Rectangle(box[0], box[1], box[2], box[3]))
                if remaining:
                    page.obj["/Annots"] = pikepdf.Array(remaining)
                elif "/Annots" in page.obj:
                    del page.obj["/Annots"]
            pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "flatten", "pages": list(self.pages)}

    def describe(self) -> str:
        return "Flattened annotations"


@dataclass
class RemoveAnnotationsOperation(Operation):
    """Removes annotations from `pages` (1-indexed; empty means all).
    `subtypes` (e.g. ["Highlight", "Text"]) restricts which annotation
    subtypes are removed; empty means remove every annotation."""

    pages: list[int] = field(default_factory=list)
    subtypes: list[str] = field(default_factory=list)
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        self._pre_snapshot = read_working_bytes(doc)

        wanted = {f"/{s.lstrip('/')}" for s in self.subtypes}
        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            total = len(pdf.pages)
            targets = _resolve_targets(self.pages, total)
            for n in targets:
                page = pdf.pages[n - 1]
                annots = page.obj.get("/Annots")
                if annots is None:
                    continue
                if not wanted:
                    del page.obj["/Annots"]
                    continue
                remaining = [a for a in annots if str(a.get("/Subtype")) not in wanted]
                if remaining:
                    page.obj["/Annots"] = pikepdf.Array(remaining)
                else:
                    del page.obj["/Annots"]
            pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "remove_annotations",
            "pages": list(self.pages),
            "subtypes": list(self.subtypes),
        }

    def describe(self) -> str:
        return "Removed annotations"


def list_form_field_names(path: Path) -> list[str]:
    """Every fillable field's fully-qualified name in `path`'s
    AcroForm - for GUI/CLI discovery before building a
    `FillFormOperation`. Empty if the document has no form."""
    with pikepdf.Pdf.open(path) as pdf:
        af = pdf.acroform
        if not af.exists:
            return []
        return [str(f.fully_qualified_name) for f in af.fields]


@dataclass
class FillFormOperation(Operation):
    """Sets AcroForm field values from `field_values` (name -> value)
    and regenerates their appearance streams so the values are visible
    without relying on the viewer to do it."""

    field_values: dict[str, str]
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.field_values:
            raise OperationError("field_values must not be empty.")

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            af = pdf.acroform
            if not af.exists:
                raise OperationError("Document has no fillable form fields.")
            by_name = {str(f.fully_qualified_name): f for f in af.fields}
            for name, value in self.field_values.items():
                if name not in by_name:
                    raise OperationError(f"No form field named '{name}'.")
                by_name[name].set_value(str(value), True)
            af.generate_appearances_if_needed()
            pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "fill_form",
            "field_values": dict(self.field_values),
        }

    def describe(self) -> str:
        return f"Filled {len(self.field_values)} form field(s)"


@dataclass
class SignOperation(Operation):
    """Places the image at `image_path` on `page` (1-indexed) within
    `rect` (x0, y0, x1, y1 - PDF-native points, origin bottom-left,
    consistent with every other op's coordinates in this package)."""

    image_path: Path
    page: int
    rect: tuple[float, float, float, float]
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        if not self.image_path.exists():
            raise OperationError(f"Signature image not found: {self.image_path}")
        x0, y0, x1, y1 = self.rect
        if x1 <= x0 or y1 <= y0:
            raise OperationError("rect must have x1 > x0 and y1 > y0.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        try:
            with fitz.open(doc.working_path) as pdf:
                total = pdf.page_count
                if not (1 <= self.page <= total):
                    raise OperationError(
                        f"Page {self.page} is out of range (document has {total} pages)."
                    )
                target_page = pdf[self.page - 1]
                page_height = target_page.rect.height
                # fitz's own Rect is top-left-origin (y grows downward) -
                # convert from this package's bottom-left convention.
                placement = fitz.Rect(x0, page_height - y1, x1, page_height - y0)
                target_page.insert_image(placement, filename=str(self.image_path))
                pdf.save(out_path)
        except (RuntimeError, ValueError) as exc:
            raise OperationError(f"Could not place signature image: {exc}") from exc

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "sign",
            "image_path": str(self.image_path),
            "page": self.page,
            "rect": list(self.rect),
        }

    def describe(self) -> str:
        return f"Placed signature on page {self.page}"


class FlattenPlugin(ToolPlugin):
    tool_id = "flatten"
    display_name = "Flatten"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return FlattenOperation(pages=list(kwargs.get("pages", [])))

    def operation_class(self) -> type[Operation]:
        return FlattenOperation


class RemoveAnnotationsPlugin(ToolPlugin):
    tool_id = "remove_annotations"
    display_name = "Remove Annotations"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return RemoveAnnotationsOperation(
            pages=list(kwargs.get("pages", [])),
            subtypes=list(kwargs.get("subtypes", [])),
        )

    def operation_class(self) -> type[Operation]:
        return RemoveAnnotationsOperation


class FillFormPlugin(ToolPlugin):
    tool_id = "fill_form"
    display_name = "Fill Form"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            field_values = kwargs["field_values"]
        except KeyError as exc:
            raise OperationError("Fill Form requires 'field_values'.") from exc
        return FillFormOperation(field_values=dict(field_values))

    def operation_class(self) -> type[Operation]:
        return FillFormOperation


class SignPlugin(ToolPlugin):
    tool_id = "sign"
    display_name = "Sign"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            image_path = kwargs["image_path"]
            page = kwargs["page"]
            rect = kwargs["rect"]
        except KeyError as exc:
            raise OperationError("Sign requires 'image_path', 'page', and 'rect'.") from exc
        return SignOperation(image_path=Path(image_path), page=page, rect=tuple(rect))

    def operation_class(self) -> type[Operation]:
        return SignOperation
