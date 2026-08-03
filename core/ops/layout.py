"""Crop, Resize, N-up, and Grayscale operations (SPEC.md Phase 2 list).

Grayscale note: this rasterizes each target page to a grayscale image
at `dpi` and replaces the page with it (via PyMuPDF/fitz, an existing
project dependency) - full vector-preserving color-to-grayscale
remapping (recoloring text/vector operators in place, keeping text
selectable) is a much larger undertaking than this Phase 2 pass covers.
Pages converted this way lose text selection/search; pages left
untouched (via `pages`) keep it. This tradeoff is what most "convert
to grayscale" tools make when applied generically to arbitrary PDFs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class CropOperation(Operation):
    """Trims `margin_top`/`right`/`bottom`/`left` points off each edge
    of `pages` (1-indexed; empty means all pages)."""

    margin_top: float = 0.0
    margin_right: float = 0.0
    margin_bottom: float = 0.0
    margin_left: float = 0.0
    pages: list[int] = field(default_factory=list)
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        if min(self.margin_top, self.margin_right, self.margin_bottom, self.margin_left) < 0:
            raise OperationError("Crop margins must not be negative.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            total = len(pdf.pages)
            targets = _resolve_targets(self.pages, total)
            for n in targets:
                page = pdf.pages[n - 1]
                box = [float(x) for x in page.mediabox]
                new_box = [
                    box[0] + self.margin_left,
                    box[1] + self.margin_bottom,
                    box[2] - self.margin_right,
                    box[3] - self.margin_top,
                ]
                if new_box[2] <= new_box[0] or new_box[3] <= new_box[1]:
                    raise OperationError(f"Crop margins leave no visible area on page {n}.")
                page.mediabox = pikepdf.Array(new_box)
                page.cropbox = pikepdf.Array(new_box)
            pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "crop",
            "margin_top": self.margin_top,
            "margin_right": self.margin_right,
            "margin_bottom": self.margin_bottom,
            "margin_left": self.margin_left,
            "pages": list(self.pages),
        }

    def describe(self) -> str:
        return "Cropped pages"


@dataclass
class ResizeOperation(Operation):
    """Scales `pages` (1-indexed; empty means all) to `width`x`height`
    points, stretching content to fit (not preserving aspect ratio -
    pass a `width`/`height` matching the source aspect ratio to avoid
    distortion)."""

    width: float
    height: float
    pages: list[int] = field(default_factory=list)
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        if self.width <= 0 or self.height <= 0:
            raise OperationError("width and height must be positive.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            total = len(pdf.pages)
            targets = _resolve_targets(self.pages, total)
            for n in targets:
                page = pdf.pages[n - 1]
                box = [float(x) for x in page.mediabox]
                current_width = box[2] - box[0]
                current_height = box[3] - box[1]
                scale_x = self.width / current_width
                scale_y = self.height / current_height
                matrix = f"q {scale_x} 0 0 {scale_y} {-box[0] * scale_x} {-box[1] * scale_y} cm\n"
                page.contents_add(matrix.encode(), prepend=True)
                page.contents_add(b"\nQ", prepend=False)
                page.mediabox = pikepdf.Array([0, 0, self.width, self.height])
                page.cropbox = pikepdf.Array([0, 0, self.width, self.height])
            pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "resize",
            "width": self.width,
            "height": self.height,
            "pages": list(self.pages),
        }

    def describe(self) -> str:
        return f"Resized to {self.width}x{self.height}pt"


@dataclass
class NUpOperation(Operation):
    """Combines `pages_per_sheet` consecutive source pages onto each
    output page, arranged in a grid, at `sheet_width`x`sheet_height`
    points (default: same as the source's first page, doubled area for
    2-up, etc. is the caller's call - this just lays out a grid on
    whatever sheet size is given)."""

    pages_per_sheet: int
    sheet_width: float
    sheet_height: float
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.pages_per_sheet < 1:
            raise OperationError("pages_per_sheet must be at least 1.")

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        if self.sheet_width <= 0 or self.sheet_height <= 0:
            raise OperationError("sheet_width and sheet_height must be positive.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            total = len(pdf.pages)
            cols = _grid_columns(self.pages_per_sheet)
            rows = -(-self.pages_per_sheet // cols)  # ceil division
            cell_width = self.sheet_width / cols
            cell_height = self.sheet_height / rows

            result = pikepdf.Pdf.new()
            for sheet_start in range(0, total, self.pages_per_sheet):
                sheet_page = result.add_blank_page(page_size=(self.sheet_width, self.sheet_height))
                for offset in range(min(self.pages_per_sheet, total - sheet_start)):
                    source_page = pdf.pages[sheet_start + offset]
                    col = offset % cols
                    row = offset // cols
                    rect = pikepdf.Rectangle(
                        col * cell_width,
                        self.sheet_height - (row + 1) * cell_height,
                        (col + 1) * cell_width,
                        self.sheet_height - row * cell_height,
                    )
                    foreign_page = result.copy_foreign(source_page.as_form_xobject())
                    sheet_page.add_overlay(foreign_page, rect=rect)
            result.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "n_up",
            "pages_per_sheet": self.pages_per_sheet,
            "sheet_width": self.sheet_width,
            "sheet_height": self.sheet_height,
        }

    def describe(self) -> str:
        return f"{self.pages_per_sheet}-up layout"


def _grid_columns(pages_per_sheet: int) -> int:
    """A reasonable grid shape for common N-up counts (2 -> 2x1, 4 ->
    2x2, 6 -> 3x2, 9 -> 3x3); falls back to ceil(sqrt(n)) columns for
    anything else."""
    known = {2: 2, 4: 2, 6: 3, 8: 4, 9: 3}
    if pages_per_sheet in known:
        return known[pages_per_sheet]
    n = 1
    while n * n < pages_per_sheet:
        n += 1
    return n


@dataclass
class GrayscaleOperation(Operation):
    """Rasterizes `pages` (1-indexed; empty means all) to grayscale at
    `dpi` - see module docstring for the vector/text tradeoff."""

    pages: list[int] = field(default_factory=list)
    dpi: int = 200
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.dpi < 36:
            raise OperationError(f"dpi must be at least 36, got {self.dpi}.")

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        try:
            with fitz.open(doc.working_path) as src:
                total = src.page_count
                targets = set(_resolve_targets(self.pages, total))
                matrix = fitz.Matrix(self.dpi / 72, self.dpi / 72)

                with fitz.open() as result:
                    for i in range(total):
                        if (i + 1) not in targets:
                            result.insert_pdf(src, from_page=i, to_page=i)
                            continue
                        page = src[i]
                        pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
                        new_page = result.new_page(width=page.rect.width, height=page.rect.height)
                        new_page.insert_image(new_page.rect, pixmap=pixmap)
                    result.save(out_path)
        except fitz.FileDataError as exc:
            raise OperationError(f"Could not process document for grayscale: {exc}") from exc

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "grayscale",
            "pages": list(self.pages),
            "dpi": self.dpi,
        }

    def describe(self) -> str:
        return "Converted to grayscale"


class CropPlugin(ToolPlugin):
    tool_id = "crop"
    display_name = "Crop"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return CropOperation(
            margin_top=kwargs.get("margin_top", 0.0),
            margin_right=kwargs.get("margin_right", 0.0),
            margin_bottom=kwargs.get("margin_bottom", 0.0),
            margin_left=kwargs.get("margin_left", 0.0),
            pages=list(kwargs.get("pages", [])),
        )

    def operation_class(self) -> type[Operation]:
        return CropOperation


class ResizePlugin(ToolPlugin):
    tool_id = "resize"
    display_name = "Resize"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            width = kwargs["width"]
            height = kwargs["height"]
        except KeyError as exc:
            raise OperationError("Resize requires 'width' and 'height'.") from exc
        return ResizeOperation(width=width, height=height, pages=list(kwargs.get("pages", [])))

    def operation_class(self) -> type[Operation]:
        return ResizeOperation


class NUpPlugin(ToolPlugin):
    tool_id = "n_up"
    display_name = "N-up"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            pages_per_sheet = kwargs["pages_per_sheet"]
        except KeyError as exc:
            raise OperationError("N-up requires 'pages_per_sheet'.") from exc
        return NUpOperation(
            pages_per_sheet=pages_per_sheet,
            sheet_width=kwargs.get("sheet_width", 612.0),
            sheet_height=kwargs.get("sheet_height", 792.0),
        )

    def operation_class(self) -> type[Operation]:
        return NUpOperation


class GrayscalePlugin(ToolPlugin):
    tool_id = "grayscale"
    display_name = "Grayscale"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return GrayscaleOperation(pages=list(kwargs.get("pages", [])), dpi=kwargs.get("dpi", 200))

    def operation_class(self) -> type[Operation]:
        return GrayscaleOperation
