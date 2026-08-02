"""Watermark operation (SPEC.md Phase 1 list).

Renders `text` as a rotated, semi-transparent overlay (via reportlab)
and stamps it onto every page of the working document (via pikepdf's
`Page.add_overlay`, sized per-page so mixed page sizes still line up).
"""

from __future__ import annotations

import io
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
    snapshot_restore_invert,
)
from core.registry.plugin_base import ToolPlugin

CORE_VERSION_RANGE = ">=1.0,<2.0"

_OVERLAY_SIZE = (1000.0, 1000.0)  # points; scaled to fit each page via add_overlay(rect=...)


def _render_watermark_page(text: str, opacity: float, font_size: int) -> pikepdf.Pdf:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=_OVERLAY_SIZE)
    c.saveState()
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.setFillAlpha(opacity)
    c.translate(_OVERLAY_SIZE[0] / 2, _OVERLAY_SIZE[1] / 2)
    c.rotate(45)
    c.setFont("Helvetica-Bold", font_size)
    c.drawCentredString(0, 0, text)
    c.restoreState()
    c.save()
    buffer.seek(0)
    return pikepdf.Pdf.open(buffer)


@dataclass
class WatermarkOperation(Operation):
    """Stamps `text` diagonally across every page."""

    text: str
    opacity: float = 0.3
    font_size: int = 40
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if doc.working_path is None:
            raise OperationError("No document open.")
        if not self.text.strip():
            raise OperationError("Watermark text must not be empty.")
        if not (0.0 < self.opacity <= 1.0):
            raise OperationError(f"opacity must be in (0, 1], got {self.opacity}.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with (
            open_pdf(doc.working_path) as pdf,
            _render_watermark_page(self.text, self.opacity, self.font_size) as stamp,
        ):
            stamp_page = stamp.pages[0]
            for page in pdf.pages:
                page.add_overlay(stamp_page, rect=pikepdf.Rectangle(page.mediabox))
            pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "watermark",
            "text": self.text,
            "opacity": self.opacity,
            "font_size": self.font_size,
        }

    def describe(self) -> str:
        return f"Watermarked with '{self.text}'"


class WatermarkPlugin(ToolPlugin):
    tool_id = "watermark"
    display_name = "Watermark"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            text = kwargs["text"]
        except KeyError as exc:
            raise OperationError("Watermark requires 'text'.") from exc
        return WatermarkOperation(
            text=text,
            opacity=kwargs.get("opacity", 0.3),
            font_size=kwargs.get("font_size", 40),
        )

    def operation_class(self) -> type[Operation]:
        return WatermarkOperation
