"""Header/Footer and Bates/page numbering operations (SPEC.md Phase 2
list). Both stamp positioned (non-rotated) text onto every page, one
reportlab-rendered overlay per page since Bates numbers differ page to
page and header/footer text must line up exactly with each page's own
size (unlike Watermark's single reused diagonal stamp)."""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pikepdf
from reportlab.pdfgen import canvas

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
_MARGIN = 24.0  # points from the page edge


def _require_working_pdf(doc: DocumentSession) -> None:
    if doc.working_path is None:
        raise OperationError("No document open.")


def _stamp_page(width: float, height: float, draw: Callable[[canvas.Canvas], None]) -> pikepdf.Pdf:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))
    draw(c)
    c.save()
    buffer.seek(0)
    return pikepdf.Pdf.open(buffer)


@dataclass
class HeaderFooterOperation(Operation):
    """Stamps `header_text` at the top and/or `footer_text` at the
    bottom of `pages` (1-indexed; empty means all), centered
    horizontally."""

    header_text: str = ""
    footer_text: str = ""
    font_size: int = 10
    pages: list[int] = field(default_factory=list)
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.header_text.strip() and not self.footer_text.strip():
            raise OperationError("Provide at least one of header_text or footer_text.")

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
                box = [float(x) for x in page.mediabox]
                width, height = box[2] - box[0], box[3] - box[1]

                def draw(c: canvas.Canvas, width: float = width, height: float = height) -> None:
                    c.setFont("Helvetica", self.font_size)
                    if self.header_text.strip():
                        c.drawCentredString(width / 2, height - _MARGIN, self.header_text)
                    if self.footer_text.strip():
                        c.drawCentredString(width / 2, _MARGIN - self.font_size / 2, self.footer_text)

                with _stamp_page(width, height, draw) as stamp:
                    page.add_overlay(stamp.pages[0], rect=pikepdf.Rectangle(page.mediabox))
            pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "header_footer",
            "header_text": self.header_text,
            "footer_text": self.footer_text,
            "font_size": self.font_size,
            "pages": list(self.pages),
        }

    def affected_pages(self) -> list[int] | None:
        """Page count and order are unchanged by this operation, so only
        the pages it targets need re-rendering. An empty `pages` means
        "every page", which is exactly the base class's `None`."""
        return list(self.pages) or None

    def describe(self) -> str:
        return "Added header/footer"


_BatesDrawFn = Callable[[canvas.Canvas, float, float, str, int], None]

_BATES_POSITIONS: dict[str, _BatesDrawFn] = {
    "bottom-right": lambda c, w, h, s, size: c.drawRightString(w - _MARGIN, _MARGIN - size / 2, s),
    "bottom-left": lambda c, w, h, s, size: c.drawString(_MARGIN, _MARGIN - size / 2, s),
    "bottom-center": lambda c, w, h, s, size: c.drawCentredString(w / 2, _MARGIN - size / 2, s),
    "top-right": lambda c, w, h, s, size: c.drawRightString(w - _MARGIN, h - _MARGIN, s),
    "top-left": lambda c, w, h, s, size: c.drawString(_MARGIN, h - _MARGIN, s),
}


@dataclass
class BatesNumberingOperation(Operation):
    """Stamps sequential numbers (``{prefix}{counter:0{digits}d}``) on
    `pages` (1-indexed; empty means all), in ascending page order,
    starting at `start`."""

    prefix: str = ""
    start: int = 1
    digits: int = 5
    position: str = "bottom-right"
    font_size: int = 10
    pages: list[int] = field(default_factory=list)
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.position not in _BATES_POSITIONS:
            raise OperationError(
                f"Unsupported position '{self.position}'; choose one of {sorted(_BATES_POSITIONS)}."
            )
        if self.start < 0:
            raise OperationError("start must not be negative.")
        if self.digits < 1:
            raise OperationError("digits must be at least 1.")

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            total = len(pdf.pages)
            targets = resolve_page_targets(self.pages, total)  # already ascending + deduped
            draw_fn = _BATES_POSITIONS[self.position]
            for offset, n in enumerate(targets):
                page = pdf.pages[n - 1]
                box = [float(x) for x in page.mediabox]
                width, height = box[2] - box[0], box[3] - box[1]
                label = f"{self.prefix}{self.start + offset:0{self.digits}d}"

                def draw(
                    c: canvas.Canvas,
                    width: float = width,
                    height: float = height,
                    label: str = label,
                ) -> None:
                    c.setFont("Helvetica", self.font_size)
                    draw_fn(c, width, height, label, self.font_size)

                with _stamp_page(width, height, draw) as stamp:
                    page.add_overlay(stamp.pages[0], rect=pikepdf.Rectangle(page.mediabox))
            pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "bates_numbering",
            "prefix": self.prefix,
            "start": self.start,
            "digits": self.digits,
            "position": self.position,
            "font_size": self.font_size,
            "pages": list(self.pages),
        }

    def affected_pages(self) -> list[int] | None:
        """Page count and order are unchanged by this operation, so only
        the pages it targets need re-rendering. An empty `pages` means
        "every page", which is exactly the base class's `None`."""
        return list(self.pages) or None

    def describe(self) -> str:
        return "Added page numbers"


class HeaderFooterPlugin(ToolPlugin):
    tool_id = "header_footer"
    display_name = "Header / Footer"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return HeaderFooterOperation(
            header_text=kwargs.get("header_text", ""),
            footer_text=kwargs.get("footer_text", ""),
            font_size=kwargs.get("font_size", 10),
            pages=list(kwargs.get("pages", [])),
        )

    def operation_class(self) -> type[Operation]:
        return HeaderFooterOperation


class BatesNumberingPlugin(ToolPlugin):
    tool_id = "bates_numbering"
    display_name = "Page Numbers"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return BatesNumberingOperation(
            prefix=kwargs.get("prefix", ""),
            start=kwargs.get("start", 1),
            digits=kwargs.get("digits", 5),
            position=kwargs.get("position", "bottom-right"),
            font_size=kwargs.get("font_size", 10),
            pages=list(kwargs.get("pages", [])),
        )

    def operation_class(self) -> type[Operation]:
        return BatesNumberingOperation
