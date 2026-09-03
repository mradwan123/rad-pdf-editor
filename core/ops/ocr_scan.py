"""OCR and Deskew operations (SPEC.md Phase 4 list; Repair is in
core/ops/repair.py).

Both `tesseract` and `deskew`'s angle detection were verified by hand
against this machine's real environment before this module was
written - not assumed from documentation. `tesseract` itself was not
installed at first; OCR was confirmed working end-to-end only after
installing it (a real system prerequisite, no pure-Python fallback
exists for OCR the way Phase 3 had LibreOffice-vs-pure-Python).

**Deskew is deliberately its own operation, not `ocrmypdf`'s bundled
`deskew=True` flag.** That flag was tried first and found unreliable:
it has no standalone mode (always runs the full OCR pipeline just to
get a rotation correction) and its angle detection silently reported
`0.000°` - no correction, no error - on a page hand-rotated by a real
8°, reproduced on both a sparse and a denser/more realistic text
fixture. The `deskew` package (Hough-transform-based, via
`scikit-image`) was verified instead: it detected the same rotation as
`-7.999999999999986°` and, once actually applied, produced a visibly
level page (confirmed by rendering before/after PNGs and looking, not
just trusting a returned angle). `DeskewOperation` rasterizes affected
pages (same tradeoff `GrayscaleOperation` already documents - loses
vector/text-selectability on corrected pages only).
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass, field
from typing import Any

import fitz
import numpy as np
import ocrmypdf
import ocrmypdf.exceptions
from deskew import determine_skew
from PIL import Image

from core.errors import ConversionError, OperationError
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.model.progress import SupportsProgress
from core.ops.common import (
    allocate_working_path,
    next_session,
    read_working_bytes,
    resolve_page_targets,
    snapshot_restore_invert,
)
from core.registry.plugin_base import ToolPlugin

CORE_VERSION_RANGE = ">=1.0,<2.0"

#: Below this, a detected angle is treated as "already level" - avoids
#: a pointless quality-losing rasterization for a no-op rotation.
_MIN_CORRECTABLE_ANGLE_DEGREES = 0.1


def _require_working_pdf(doc: DocumentSession) -> None:
    if doc.working_path is None:
        raise OperationError("No document open.")


def tesseract_available() -> bool:
    """Whether the `tesseract` binary OCR itself is on `PATH` - unlike
    LibreOffice in Phase 3, there is no pure-Python fallback for real
    OCR, so callers must check this and fail clearly rather than
    degrade silently."""
    return shutil.which("tesseract") is not None


@dataclass
class OCROperation(Operation):
    """Adds a searchable text layer to the working PDF via
    `ocrmypdf.ocr()`. `skip_text=True` (default) leaves pages that
    already have real text untouched - the safer default for a
    confidential-documents tool, avoiding silently replacing existing
    selectable text. `force_ocr` re-OCRs every page, replacing any
    existing text; it is mutually exclusive with `skip_text`, same as
    ocrmypdf itself treats them.
    """

    language: str = "eng"
    force_ocr: bool = False
    skip_text: bool = True
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.force_ocr and self.skip_text:
            raise OperationError("force_ocr and skip_text cannot both be set.")

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        if not tesseract_available():
            raise ConversionError(
                "tesseract is not installed on this machine - OCR requires it "
                "(no pure-Python fallback exists for real text recognition)."
            )
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        try:
            exit_code = ocrmypdf.ocr(
                doc.working_path,
                out_path,
                language=[self.language],
                force_ocr=self.force_ocr,
                skip_text=self.skip_text,
                progress_bar=False,
            )
        except ocrmypdf.exceptions.ExitCodeException as exc:
            raise ConversionError(f"OCR failed: {exc}") from exc
        if exit_code != ocrmypdf.ExitCode.ok:
            raise ConversionError(f"OCR failed with exit code {exit_code.name}.")

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "ocr",
            "language": self.language,
            "force_ocr": self.force_ocr,
            "skip_text": self.skip_text,
        }

    def describe(self) -> str:
        return f"Applied OCR ({self.language})"


@dataclass
class DeskewOperation(Operation, SupportsProgress):
    """Corrects rotational skew on `pages` (1-indexed; empty means
    all) by rendering each to a raster image, detecting its dominant
    text-line angle via `deskew.determine_skew`, and re-embedding a
    rotated copy when a confident, non-trivial angle is found. Pages
    where no confident angle is detected, or the detected angle is
    below `_MIN_CORRECTABLE_ANGLE_DEGREES`, are left untouched -
    keeping their original vector/text content rather than being
    needlessly rasterized.
    """

    pages: list[int] = field(default_factory=list)
    dpi: int = 200
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)
    _corrected_count: int = field(default=0, init=False, repr=False)
    _target_count: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.dpi < 72:
            raise OperationError(f"dpi must be at least 72, got {self.dpi}.")

    def apply(self, doc: DocumentSession) -> DocumentSession:
        _require_working_pdf(doc)
        assert doc.working_path is not None
        self._pre_snapshot = read_working_bytes(doc)
        self._corrected_count = 0

        out_path = allocate_working_path(doc)
        try:
            with fitz.open(doc.working_path) as src:
                total = src.page_count
                targets = set(resolve_page_targets(self.pages, total))
                self._target_count = len(targets)
                matrix = fitz.Matrix(self.dpi / 72, self.dpi / 72)

                with fitz.open() as result:
                    for i in range(total):
                        self.report_progress(i, total)
                        page_num = i + 1
                        if page_num not in targets:
                            result.insert_pdf(src, from_page=i, to_page=i)
                            continue

                        page = src[i]
                        gray_pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
                        gray_array = np.frombuffer(gray_pixmap.samples, dtype=np.uint8).reshape(
                            gray_pixmap.height, gray_pixmap.width
                        )
                        angle = determine_skew(gray_array)
                        if angle is None or abs(float(angle)) < _MIN_CORRECTABLE_ANGLE_DEGREES:
                            result.insert_pdf(src, from_page=i, to_page=i)
                            continue

                        color_pixmap = page.get_pixmap(matrix=matrix)
                        color_image = Image.frombytes(
                            "RGB",
                            (color_pixmap.width, color_pixmap.height),
                            color_pixmap.samples,
                        )
                        corrected = color_image.rotate(
                            float(angle), expand=True, fillcolor="white"
                        )
                        buf = io.BytesIO()
                        corrected.save(buf, format="PNG")

                        page_width = corrected.width / self.dpi * 72
                        page_height = corrected.height / self.dpi * 72
                        new_page = result.new_page(width=page_width, height=page_height)
                        new_page.insert_image(new_page.rect, stream=buf.getvalue())
                        self._corrected_count += 1
                    result.save(out_path)
        except fitz.FileDataError as exc:
            raise OperationError(f"Could not process document for deskew: {exc}") from exc

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "deskew",
            "pages": list(self.pages),
            "dpi": self.dpi,
        }

    def affected_pages(self) -> list[int] | None:
        """Page count and order are unchanged by this operation, so only
        the pages it targets need re-rendering. An empty `pages` means
        "every page", which is exactly the base class's `None`."""
        return list(self.pages) or None

    def describe(self) -> str:
        return f"Deskewed {self._corrected_count} of {self._target_count} page(s)"


class OCRPlugin(ToolPlugin):
    tool_id = "ocr"
    display_name = "OCR"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return OCROperation(
            language=kwargs.get("language", "eng"),
            force_ocr=kwargs.get("force_ocr", False),
            skip_text=kwargs.get("skip_text", True),
        )

    def operation_class(self) -> type[Operation]:
        return OCROperation


class DeskewPlugin(ToolPlugin):
    tool_id = "deskew"
    display_name = "Deskew"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return DeskewOperation(pages=list(kwargs.get("pages", [])), dpi=kwargs.get("dpi", 200))

    def operation_class(self) -> type[Operation]:
        return DeskewOperation
