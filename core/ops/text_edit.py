"""Editing text that is already on the page - the experimental slice.

Phase 6h (docs/GUI_PLAN.md §5.1). This is the single feature users most
associate with "PDF editor" and the one most likely to disappoint, so
its limits are stated here rather than discovered.

**There is no "change this text run" API**, in PyMuPDF or anywhere else
available to this project. The workable technique, verified end to end
rather than assumed:

1. `page.get_text("dict")` gives each span's text, font name, size,
   colour, bbox and baseline origin.
2. `doc.extract_font(xref)` says whether that font can be reproduced.
   Measured: a base-14 font (Helvetica) returns `ext='n/a'` and a
   **0-byte** buffer, while a genuinely embedded font returns
   `ext='ttf'` and 759 KB - so buffer length is the test, not the font
   name.
3. Redact the old span's bbox, then re-insert the new text at the same
   origin. Where the font was extractable it is written to the session
   temp dir and re-embedded, and the replacement renders in the
   *original* typeface (confirmed: the edited span came back reporting
   font `DejaVuSans`). Where it was not, a base-14 substitute is used
   and the appearance changes.

**Scope, deliberately narrow:** one span, one line. Reflowing a
paragraph across line breaks, around figures or across a page boundary
is a materially harder problem and is not attempted. Acrobat itself
does this imperfectly; the failure mode to avoid is not imperfection
but *undisclosed* imperfection - which is why `resolve_font()` exists
and why the GUI holds the commit behind a preview when a substitution
would occur (decision 12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

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

CORE_VERSION_RANGE = ">=1.0,<2.0"

#: Used when the original font cannot be reproduced. Helvetica is the
#: least surprising of the base-14 set for body text.
FALLBACK_FONT = "helv"

_Rect = tuple[float, float, float, float]


class TextSpan(NamedTuple):
    """One run of text on a page, in bottom-left-origin PDF points."""

    page: int
    text: str
    rect: _Rect
    #: Baseline start, which is where re-inserted text must be anchored.
    origin: tuple[float, float]
    font_name: str
    font_size: float
    colour: tuple[float, float, float]


class FontResolution(NamedTuple):
    """Whether a span's font can be reproduced, and what will be used.

    `is_exact` False means the replacement will *look different* - the
    fact decision 12 requires be shown to the user before anything is
    written.
    """

    requested: str
    resolved: str
    is_exact: bool

    @property
    def warning(self) -> str:
        if self.is_exact:
            return ""
        return (
            f"'{self.requested}' is not embedded in this document and cannot be "
            f"reproduced. The replacement will be drawn in '{self.resolved}', so "
            f"its appearance will change."
        )


def _to_fitz_rect(rect: _Rect, height: float) -> fitz.Rect:
    x0, y0, x1, y1 = rect
    return fitz.Rect(x0, height - y1, x1, height - y0)


def _int_to_rgb(colour: int) -> tuple[float, float, float]:
    return ((colour >> 16 & 255) / 255, (colour >> 8 & 255) / 255, (colour & 255) / 255)


def find_text_spans(path: Path, page: int) -> list[TextSpan]:
    """Every text span on `page` (1-based), bottom-left origin."""
    spans: list[TextSpan] = []
    with fitz.open(path) as document:
        if not 1 <= page <= document.page_count:
            return []
        target = document[page - 1]
        height = target.rect.height
        for block in target.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    x0, y0, x1, y1 = span["bbox"]
                    spans.append(
                        TextSpan(
                            page=page,
                            text=span["text"],
                            rect=(x0, height - y1, x1, height - y0),
                            origin=(span["origin"][0], height - span["origin"][1]),
                            font_name=span["font"],
                            font_size=span["size"],
                            colour=_int_to_rgb(int(span["color"])),
                        )
                    )
    return spans


def span_at(path: Path, page: int, x: float, y: float) -> TextSpan | None:
    """The span containing a bottom-left-origin point, if any."""
    for span in find_text_spans(path, page):
        x0, y0, x1, y1 = span.rect
        if x0 <= x <= x1 and y0 <= y <= y1:
            return span
    return None


def _normalise_font_name(name: str) -> str:
    """Compare font names the way they actually vary between APIs.

    `get_fonts()` and a text span do not agree on spelling: the same
    embedded face came back as "DejaVu Sans Book" from the font table
    and "DejaVuSans" from the span. Subset prefixes ("ABCDEF+Foo"),
    spaces, hyphens and case all differ too, so matching on the raw
    string finds nothing and every font looks non-embedded.
    """
    without_subset = name.split("+")[-1]
    return "".join(ch for ch in without_subset if ch.isalnum()).lower()


def _extract_font(document: fitz.Document, page: fitz.Page, font_name: str) -> bytes:
    """The font's file bytes, or empty when it is not embedded.

    Buffer length is the test, not the name: a base-14 font reports a
    perfectly ordinary name and yields nothing.
    """
    wanted = _normalise_font_name(font_name)
    for entry in page.get_fonts(full=True):
        xref, basename = entry[0], entry[3]
        candidate = _normalise_font_name(basename)
        # Either direction: the table name often carries a style suffix
        # ("...Book") the span name does not.
        if not (wanted and (wanted in candidate or candidate in wanted)):
            continue
        try:
            _name, _extension, _sub, buffer = document.extract_font(xref)
        except (RuntimeError, ValueError):
            return b""
        return bytes(buffer)
    return b""


def resolve_font(path: Path, page: int, font_name: str) -> FontResolution:
    """Whether `font_name` on `page` can be reproduced exactly."""
    with fitz.open(path) as document:
        if not 1 <= page <= document.page_count:
            return FontResolution(font_name, FALLBACK_FONT, False)
        buffer = _extract_font(document, document[page - 1], font_name)
    if buffer:
        return FontResolution(font_name, font_name, True)
    return FontResolution(font_name, FALLBACK_FONT, False)


@dataclass
class EditTextSpanOperation(Operation):
    """Replaces the text of one span, in place.

    `rect` identifies the span (bottom-left-origin PDF points) and
    `new_text` is what replaces it. The original font is re-embedded
    when the document carries it; otherwise `fallback_font` is used and
    `describe()` says so, the same way the Phase 3 dual-engine
    conversions report which engine actually ran.
    """

    page: int
    rect: _Rect
    new_text: str
    fallback_font: str = FALLBACK_FONT
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)
    _used_fallback: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.page < 1:
            raise OperationError(f"Page numbers are 1-based, got {self.page}.")
        x0, y0, x1, y1 = self.rect
        if x1 <= x0 or y1 <= y0:
            raise OperationError(f"Span rect must have positive size, got {self.rect}.")

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if doc.working_path is None or not doc.working_path.exists():
            raise OperationError("No document is open.")
        self._pre_snapshot = read_working_bytes(doc)
        out_path = allocate_working_path(doc)

        with fitz.open(doc.working_path) as document:
            if self.page > document.page_count:
                raise OperationError(
                    f"Page {self.page} is out of range (document has "
                    f"{document.page_count})."
                )
            page = document[self.page - 1]
            height = page.rect.height
            span = self._locate_span(page, height)
            if span is None:
                raise OperationError("No text found in the region to edit.")

            buffer = _extract_font(document, page, span.font_name)
            self._used_fallback = not buffer
            font_file: Path | None = None
            if buffer:
                # Written into the session temp dir, which is securely
                # wiped with the rest of the session.
                font_file = allocate_working_path(doc, suffix=".font")
                font_file.write_bytes(buffer)

            # Redact rather than paint over: the old glyphs must be gone
            # from the content stream, not merely hidden.
            page.add_redact_annot(_to_fitz_rect(span.rect, height))
            page.apply_redactions()
            page.insert_text(
                fitz.Point(span.origin[0], height - span.origin[1]),
                self.new_text,
                fontsize=span.font_size,
                color=span.colour,
                fontname="EditedSpan" if font_file else self.fallback_font,
                fontfile=str(font_file) if font_file else None,
            )
            document.save(out_path, garbage=4, clean=True, deflate=True)

        return next_session(doc, out_path)

    def _locate_span(self, page: fitz.Page, height: float) -> TextSpan | None:
        """The span overlapping `rect` most - the click that produced
        the rect may not land exactly on the stored bbox."""
        target = _to_fitz_rect(self.rect, height)
        best: TextSpan | None = None
        best_area = 0.0
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for raw in line.get("spans", []):
                    overlap = fitz.Rect(raw["bbox"]) & target
                    area = abs(overlap.get_area()) if not overlap.is_empty else 0.0
                    if area > best_area:
                        x0, y0, x1, y1 = raw["bbox"]
                        best_area = area
                        best = TextSpan(
                            page=self.page,
                            text=raw["text"],
                            rect=(x0, height - y1, x1, height - y0),
                            origin=(raw["origin"][0], height - raw["origin"][1]),
                            font_name=raw["font"],
                            font_size=raw["size"],
                            colour=_int_to_rgb(int(raw["color"])),
                        )
        return best

    def invert(self) -> Operation:
        # Redact-and-reinsert destroys the original glyphs, so the only
        # honest inverse is the document as it was.
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def affected_pages(self) -> list[int] | None:
        return [self.page]

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "edit_text",
            "page": self.page,
            "rect": list(self.rect),
            "new_text": self.new_text,
            "fallback_font": self.fallback_font,
        }

    def describe(self) -> str:
        suffix = " (substituted font)" if self._used_fallback else ""
        return f"Edited text on page {self.page}{suffix}"


class EditTextPlugin(ToolPlugin):
    tool_id = "edit_text"
    display_name = "Edit Text (experimental)"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return EditTextSpanOperation(**kwargs)

    def operation_class(self) -> type[Operation]:
        return EditTextSpanOperation
