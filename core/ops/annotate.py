"""Annotation operations: markup, shapes, freehand ink and sticky notes.

Phase 6e (docs/GUI_PLAN.md §3.3). Phases 1-5 built 36 operations, every
one of them a whole-document or whole-page *transform*. These are the
first that edit content *on* a page, which is what the reframing in the
plan's §0 identified as the actual gap.

**Identity is a UUID in `/NM`, never `xref`.** Every operation in this
codebase writes a *new* working file, and xrefs are not stable across
that: verified directly - a pikepdf round-trip renumbered the same
three annotations from xrefs [7, 9, 13] to [4, 5, 6] while their `/NM`
values survived unchanged. PyMuPDF surfaces `/NM` as `annot.info["id"]`
for reading, but has no setter for it (`set_info(id=...)` raises), so
it is stamped with `doc.xref_set_key(annot.xref, "NM", "(...)")`.
Reading it back through `xref_get_key` yields the *unwrapped* string,
without the parentheses - a detail that silently broke the first lookup
written against it.

**Coordinates are this package's usual bottom-left-origin PDF points**,
matching Crop, Resize, Watermark, HeaderFooter and Sign, and converted
internally to fitz's top-left origin. Switching tools should never mean
switching coordinate conventions.

One `AddAnnotationOperation` covers every kind rather than nine
near-identical classes: the parameters are the same (a page, a region,
a colour) and only the PyMuPDF call differs, so a `kind` discriminator
carries its weight where nine subclasses would just be duplication.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import fitz

from core.errors import OperationError
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.ops.common import (
    allocate_working_path,
    next_session,
    read_working_bytes,
    snapshot_restore_invert,
)
from core.registry.plugin_base import ToolPlugin

#: Kinds that mark up existing text and take a rectangle.
TEXT_MARKUP_KINDS = ("highlight", "underline", "strikeout", "squiggly")
#: Kinds drawn as a shape within a rectangle.
SHAPE_KINDS = ("rect", "circle", "line")
#: Freehand strokes.
INK_KIND = "ink"
#: A collapsed note icon anchored at the rectangle's corner.
NOTE_KIND = "note"

ANNOTATION_KINDS = (*TEXT_MARKUP_KINDS, *SHAPE_KINDS, INK_KIND, NOTE_KIND)

_Rect = tuple[float, float, float, float]
_Color = tuple[float, float, float]
_Stroke = list[tuple[float, float]]

#: Matches every other ops module.
CORE_VERSION_RANGE = ">=1.0,<2.0"


def _require_pdf(doc: DocumentSession) -> None:
    if doc.working_path is None or not doc.working_path.exists():
        raise OperationError("No document is open.")


def _to_fitz_rect(rect: _Rect, page_height: float) -> fitz.Rect:
    """Bottom-left-origin (x0, y0, x1, y1) -> fitz's top-left Rect."""
    x0, y0, x1, y1 = rect
    return fitz.Rect(x0, page_height - y1, x1, page_height - y0)


def find_annotation(page: fitz.Page, annot_id: str) -> fitz.Annot | None:
    """The annotation on `page` carrying `annot_id` in its `/NM`.

    `annot.info["id"]` is PyMuPDF's read view of `/NM` - confirmed
    against annotations stamped via `xref_set_key`.
    """
    for annot in page.annots():
        if annot.info.get("id") == annot_id:
            return annot
    return None


def _stamp_id(document: fitz.Document, annot: fitz.Annot, annot_id: str) -> None:
    """PyMuPDF has no setter for /NM, so write the key directly."""
    document.xref_set_key(annot.xref, "NM", f"({annot_id})")


@dataclass
class AddAnnotationOperation(Operation):
    """Adds one annotation to `page` (1-indexed).

    `rect` is required for every kind except `ink`, which takes
    `strokes`; a `note` uses the rect's top-left corner as its anchor.
    All coordinates are bottom-left-origin PDF points.

    `annot_id` is generated when omitted and is part of `serialize()`,
    so an operation replayed from a saved workflow or reconstructed for
    undo addresses the same annotation it originally created.
    """

    page: int
    kind: str
    rect: _Rect | None = None
    #: Text markup covers a *selection*, which wraps across lines, so a
    #: highlight is one annotation over several rects rather than
    #: several annotations. Ignored by the shape and note kinds, which
    #: are a single region by definition.
    rects: list[_Rect] = field(default_factory=list)
    strokes: list[_Stroke] = field(default_factory=list)
    text: str = ""
    color: _Color = (1.0, 0.85, 0.0)
    opacity: float = 1.0
    annot_id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ANNOTATION_KINDS:
            raise OperationError(
                f"Unknown annotation kind {self.kind!r}; expected one of "
                f"{', '.join(ANNOTATION_KINDS)}."
            )
        if self.page < 1:
            raise OperationError(f"Page numbers are 1-based, got {self.page}.")
        if self.kind == INK_KIND:
            if not self.strokes or not any(len(s) >= 2 for s in self.strokes):
                raise OperationError("An ink annotation needs at least one stroke of 2+ points.")
        elif self.rect is None and not self.rects:
            raise OperationError(f"A {self.kind!r} annotation needs a rect.")
        if self.rect is not None and not self.rects:
            self.rects = [self.rect]
        for region in self.rects:
            x0, y0, x1, y1 = region
            if x1 <= x0 or y1 <= y0:
                raise OperationError(f"Rect must have positive width and height, got {region}.")
        if self.rects and self.rect is None:
            self.rect = self.rects[0]
        if not 0.0 <= self.opacity <= 1.0:
            raise OperationError(f"Opacity must be between 0 and 1, got {self.opacity}.")
        if not self.annot_id:
            self.annot_id = uuid.uuid4().hex

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_pdf(doc)
        assert doc.working_path is not None
        out_path = allocate_working_path(doc)
        with fitz.open(doc.working_path) as document:
            if self.page > document.page_count:
                raise OperationError(
                    f"Page {self.page} is out of range (document has {document.page_count})."
                )
            page = document[self.page - 1]
            annot = self._create(page, page.rect.height)
            if annot is None:
                raise OperationError(f"Could not create a {self.kind!r} annotation.")
            if self.kind != NOTE_KIND:
                # A Text (note) annotation is a fixed-size icon; setting
                # opacity on it just dims the marker.
                annot.set_opacity(self.opacity)
            annot.update()
            _stamp_id(document, annot, self.annot_id)
            document.save(out_path)
        return next_session(doc, out_path)

    def _create(self, page: fitz.Page, height: float) -> fitz.Annot | None:
        if self.kind == INK_KIND:
            # Plain (x, y) tuples, not fitz.Point: add_ink_annot checks
            # each point with PySequence_Size(p) == 2 and rejects a
            # Point outright ("arg must be seq of seq of float pairs").
            strokes = [
                [(x, height - y) for x, y in stroke]
                for stroke in self.strokes
                if len(stroke) >= 2
            ]
            annot = page.add_ink_annot(strokes)
            annot.set_colors(stroke=self.color)
            return annot

        assert self.rect is not None
        rect = _to_fitz_rect(self.rect, height)
        # PyMuPDF's text-markup helpers accept a list of regions, so a
        # selection spanning several lines stays *one* annotation - and
        # therefore one undo step and one audit entry.
        regions = [_to_fitz_rect(r, height) for r in self.rects] or [rect]
        if self.kind == "highlight":
            annot = page.add_highlight_annot(regions)
        elif self.kind == "underline":
            annot = page.add_underline_annot(regions)
        elif self.kind == "strikeout":
            annot = page.add_strikeout_annot(regions)
        elif self.kind == "squiggly":
            annot = page.add_squiggly_annot(regions)
        elif self.kind == "rect":
            annot = page.add_rect_annot(rect)
        elif self.kind == "circle":
            annot = page.add_circle_annot(rect)
        elif self.kind == "line":
            annot = page.add_line_annot(rect.tl, rect.br)
        else:  # NOTE_KIND
            annot = page.add_text_annot(rect.tl, self.text)
            return annot
        annot.set_colors(stroke=self.color)
        if self.text:
            annot.set_info(content=self.text)
        return annot

    def invert(self) -> Operation:
        # A precise inverse rather than a snapshot restore: the
        # annotation is addressable by the id this operation assigned.
        return DeleteAnnotationOperation(page=self.page, annot_id=self.annot_id)

    def affected_pages(self) -> list[int] | None:
        return [self.page]

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "add_annotation",
            "page": self.page,
            "kind": self.kind,
            "rect": list(self.rect) if self.rect else None,
            "rects": [list(r) for r in self.rects],
            "strokes": [[list(p) for p in stroke] for stroke in self.strokes],
            "text": self.text,
            "color": list(self.color),
            "opacity": self.opacity,
            "annot_id": self.annot_id,
        }

    def describe(self) -> str:
        return f"Added {self.kind} annotation to page {self.page}"


@dataclass
class EditAnnotationOperation(Operation):
    """Moves, restyles or re-labels an existing annotation.

    Every field is optional - only what is given is changed, which is
    what lets the canvas send a move without touching the colour.
    """

    page: int
    annot_id: str
    rect: _Rect | None = None
    color: _Color | None = None
    opacity: float | None = None
    text: str | None = None
    _previous: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_pdf(doc)
        assert doc.working_path is not None
        out_path = allocate_working_path(doc)
        with fitz.open(doc.working_path) as document:
            if self.page > document.page_count:
                raise OperationError(f"Page {self.page} is out of range.")
            page = document[self.page - 1]
            annot = find_annotation(page, self.annot_id)
            if annot is None:
                raise OperationError(f"No annotation {self.annot_id!r} on page {self.page}.")

            height = page.rect.height
            previous_rect = annot.rect
            stroke = annot.colors.get("stroke") or None
            self._previous = {
                "rect": (
                    previous_rect.x0,
                    height - previous_rect.y1,
                    previous_rect.x1,
                    height - previous_rect.y0,
                ),
                "color": tuple(stroke) if stroke else None,
                "opacity": annot.opacity if annot.opacity >= 0 else None,
                "text": annot.info.get("content", ""),
            }

            if self.rect is not None:
                annot.set_rect(_to_fitz_rect(self.rect, height))
            if self.color is not None:
                annot.set_colors(stroke=self.color)
            if self.opacity is not None:
                annot.set_opacity(self.opacity)
            if self.text is not None:
                annot.set_info(content=self.text)
            annot.update()
            document.save(out_path)
        return next_session(doc, out_path)

    def invert(self) -> Operation:
        if self._previous is None:
            raise OperationError("Cannot invert an edit that was never applied.")
        return EditAnnotationOperation(
            page=self.page,
            annot_id=self.annot_id,
            rect=self._previous["rect"],
            color=self._previous["color"],
            opacity=self._previous["opacity"],
            text=self._previous["text"],
        )

    def affected_pages(self) -> list[int] | None:
        return [self.page]

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "edit_annotation",
            "page": self.page,
            "annot_id": self.annot_id,
            "rect": list(self.rect) if self.rect else None,
            "color": list(self.color) if self.color else None,
            "opacity": self.opacity,
            "text": self.text,
        }

    def describe(self) -> str:
        return f"Edited annotation on page {self.page}"


@dataclass
class DeleteAnnotationOperation(Operation):
    """Removes one annotation by its `/NM` id."""

    page: int
    annot_id: str
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_pdf(doc)
        assert doc.working_path is not None
        self._pre_snapshot = read_working_bytes(doc)
        out_path = allocate_working_path(doc)
        with fitz.open(doc.working_path) as document:
            if self.page > document.page_count:
                raise OperationError(f"Page {self.page} is out of range.")
            page = document[self.page - 1]
            annot = find_annotation(page, self.annot_id)
            if annot is None:
                raise OperationError(f"No annotation {self.annot_id!r} on page {self.page}.")
            page.delete_annot(annot)
            document.save(out_path)
        return next_session(doc, out_path)

    def invert(self) -> Operation:
        # Deleting discards the annotation's full definition - its
        # appearance stream, quad points, author, dates - which no
        # add-operation could faithfully reconstruct from an id alone.
        # A snapshot restore is the honest inverse here, as it is for
        # OCR and the other lossy operations in this codebase.
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def affected_pages(self) -> list[int] | None:
        return [self.page]

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "delete_annotation",
            "page": self.page,
            "annot_id": self.annot_id,
        }

    def describe(self) -> str:
        return f"Deleted annotation from page {self.page}"


class AddAnnotationPlugin(ToolPlugin):
    tool_id = "add_annotation"
    display_name = "Add Annotation"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return AddAnnotationOperation(**kwargs)

    def operation_class(self) -> type[Operation]:
        return AddAnnotationOperation


class EditAnnotationPlugin(ToolPlugin):
    tool_id = "edit_annotation"
    display_name = "Edit Annotation"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return EditAnnotationOperation(**kwargs)

    def operation_class(self) -> type[Operation]:
        return EditAnnotationOperation


class DeleteAnnotationPlugin(ToolPlugin):
    tool_id = "delete_annotation"
    display_name = "Delete Annotation"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return DeleteAnnotationOperation(**kwargs)

    def operation_class(self) -> type[Operation]:
        return DeleteAnnotationOperation
