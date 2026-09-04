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

Create Forms (`CreateFormFieldOperation`) is the remaining Phase 2
item: authoring a brand-new field, a different feature from Fill Form
(which only edits values of fields that already exist). Same
explicit-page-and-rect approach as Sign, same reasoning. Uses
`fitz.Widget` rather than hand-built pikepdf annotation dictionaries -
pikepdf has no "add an annotation/field" helper (confirmed while
building Flatten), and PyMuPDF's `Page.add_widget()` handles the
/AcroForm bookkeeping (creating it if absent, registering the field)
automatically. Text fields and checkboxes work reliably; true radio
button *groups* (multiple widgets sharing one field name, mutually
exclusive) do not - `Widget.update()` validates a shared field name
against an already-existing `/Parent /Kids` structure and raises "bad
xref" for a freshly-created one (confirmed empirically, not assumed).
So "radio" fields are each their own independent field_name - a
round-styled toggle that behaves like a checkbox, not a grouped radio
button. Documented here and in the operation's docstring rather than
silently shipped as if grouping worked.
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
    resolve_page_targets,
    snapshot_restore_invert,
)
from core.registry.plugin_base import ToolPlugin

CORE_VERSION_RANGE = ">=1.0,<2.0"


def _require_working_pdf(doc: DocumentSession) -> None:
    if doc.working_path is None:
        raise OperationError("No document open.")


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
            targets = resolve_page_targets(self.pages, total)
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
            targets = resolve_page_targets(self.pages, total)
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


def _widget_field_name(annot: pikepdf.Object) -> str | None:
    """Reconstructs a widget annotation's fully-qualified field name by
    walking its `/Parent` chain and joining each `/T` segment with
    "." - the same convention `pikepdf.Field.fully_qualified_name`
    uses, so names line up with `list_form_field_names`'s output."""
    parts: list[str] = []
    obj: pikepdf.Object | None = annot
    for _ in range(32):  # generous ceiling on /Parent chain depth
        if obj is None:
            break
        t = obj.get("/T")
        if t is not None:
            parts.append(str(t))
        obj = obj.get("/Parent")
    return ".".join(reversed(parts)) if parts else None


def _visual_field_order(pdf: pikepdf.Pdf) -> dict[str, tuple[int, float, float]]:
    """Maps each field's fully-qualified name to a (page index, top,
    left) sort key taken from its own widget annotation's page and
    `/Rect` - reading order, not the order `af.fields` happens to walk
    the `/AcroForm /Fields` array/tree in, which only reflects
    creation order and need not match where a field actually sits on
    the page."""
    order: dict[str, tuple[int, float, float]] = {}
    for page_index, page in enumerate(pdf.pages):
        annots = page.obj.get("/Annots")
        if annots is None:
            continue
        for annot in annots:
            if str(annot.get("/Subtype")) != "/Widget":
                continue
            rect = annot.get("/Rect")
            name = _widget_field_name(annot)
            if rect is None or name is None or name in order:
                continue
            x0, y0, x1, y1 = (float(v) for v in rect)
            # PDF's origin is bottom-left with y growing upward, so the
            # top of the page is the *larger* y - negate it to sort
            # top-to-bottom with a plain ascending sort.
            order[name] = (page_index, -max(y0, y1), min(x0, x1))
    return order


def list_form_field_names(path: Path) -> list[str]:
    """Every fillable field's fully-qualified name in `path`'s
    AcroForm, in reading order (top-to-bottom, left-to-right per page,
    pages in document order) - for GUI/CLI discovery before building a
    `FillFormOperation`. Empty if the document has no form.

    A field with no matching widget annotation (so its position can't
    be determined - not expected in practice, but not assumed
    impossible either) sorts after every positioned field, in its
    original `af.fields` order."""
    with pikepdf.Pdf.open(path) as pdf:
        af = pdf.acroform
        if not af.exists:
            return []
        names = [str(f.fully_qualified_name) for f in af.fields]
        order = _visual_field_order(pdf)
        unpositioned = len(order)
        indexed = list(enumerate(names))
        indexed.sort(key=lambda item: order.get(item[1], (unpositioned, 0.0, float(item[0]))))
        return [name for _, name in indexed]


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


_FIELD_TYPES = ("text", "checkbox", "radio")


@dataclass
class CreateFormFieldOperation(Operation):
    """Adds one new AcroForm field to `page` (1-indexed) at `rect`
    (x0, y0, x1, y1 - PDF-native points, origin bottom-left, consistent
    with every other op's coordinates in this package).

    `field_type` is "text", "checkbox", or "radio". For "text",
    `default_value` seeds the field's initial text. For "checkbox" and
    "radio", `checked` seeds its initial state.

    Known limitation: "radio" is one independent toggle field, not a
    member of a mutually-exclusive group - see this module's docstring
    for why grouped radio buttons aren't supported yet.
    """

    page: int
    field_name: str
    field_type: str
    rect: tuple[float, float, float, float]
    default_value: str = ""
    checked: bool = False
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        if self.field_type not in _FIELD_TYPES:
            raise OperationError(
                f"field_type must be one of {_FIELD_TYPES}, got '{self.field_type}'."
            )
        if not self.field_name.strip():
            raise OperationError("field_name must not be empty.")
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

                widget = fitz.Widget()
                widget.field_name = self.field_name
                widget.rect = placement
                if self.field_type == "text":
                    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
                    widget.field_value = self.default_value
                else:
                    widget.field_type = (
                        fitz.PDF_WIDGET_TYPE_CHECKBOX
                        if self.field_type == "checkbox"
                        else fitz.PDF_WIDGET_TYPE_RADIOBUTTON
                    )
                    widget.field_value = self.checked
                target_page.add_widget(widget)
                pdf.save(out_path)
        except (RuntimeError, ValueError) as exc:
            raise OperationError(f"Could not create form field: {exc}") from exc

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "create_form_field",
            "page": self.page,
            "field_name": self.field_name,
            "field_type": self.field_type,
            "rect": list(self.rect),
            "default_value": self.default_value,
            "checked": self.checked,
        }

    def describe(self) -> str:
        return f"Added {self.field_type} field '{self.field_name}' on page {self.page}"


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


class CreateFormFieldPlugin(ToolPlugin):
    tool_id = "create_form_field"
    display_name = "Create Form Field"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            page = kwargs["page"]
            field_name = kwargs["field_name"]
            field_type = kwargs["field_type"]
            rect = kwargs["rect"]
        except KeyError as exc:
            raise OperationError(
                "Create Form Field requires 'page', 'field_name', 'field_type', and 'rect'."
            ) from exc
        return CreateFormFieldOperation(
            page=page,
            field_name=field_name,
            field_type=field_type,
            rect=tuple(rect),
            default_value=str(kwargs.get("default_value", "")),
            checked=bool(kwargs.get("checked", False)),
        )

    def operation_class(self) -> type[Operation]:
        return CreateFormFieldOperation
