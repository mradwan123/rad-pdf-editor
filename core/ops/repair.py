"""Repair operation (SPEC.md Phase 4 list; OCR and Deskew are in
core/ops/ocr_scan.py).

Shaped like MergeOperation/DocxToPdfOperation (core/ops/merge_split.py,
core/ops/convert_to.py): an external `source_path` is the input, not
`doc.working_path` - the whole point is recovering a file that might
not open via the app's normal "Open" flow at all, so it can't be
opened into a session first.

Two-tier engine strategy, confirmed by hand against real corrupted
fixtures before being locked in:

1. `pikepdf.Pdf.open()` alone auto-recovers common corruption for
   free (qpdf's built-in structural recovery) - confirmed: a file
   truncated mid-stream (missing its xref/trailer entirely) opened and
   re-saved cleanly with no extra code.
2. For corruption pikepdf can't parse at all (confirmed: randomly
   mangled bytes mid-file raises `RuntimeError: /Count is wrong after
   flattening pages tree`), Ghostscript's `-sDEVICE=pdfwrite` repair
   pass recovers a valid, openable PDF from the same file pikepdf
   rejected. Its output is **always re-verified** by reopening with
   pikepdf before being accepted as success - confirmed by hand that
   `gs` can exit 0 while only partially recovering a file ("errors
   that were repaired or ignored" in its own stderr), so a clean exit
   code alone is not proof of a usable result.

No network-lockdown subprocess caveat here unlike Phase 3's
LibreOffice: Ghostscript's `pdfwrite` repair pass is purely local
file-format processing with no reason to touch the network, and
`network_lockdown()` still wraps the whole CLI/GUI operation anyway.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf

from core.errors import ConversionError, OperationError
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

#: Generous timeout for Ghostscript's repair pass on a large/severely
#: damaged file.
_GHOSTSCRIPT_TIMEOUT_SECONDS = 120.0


def _ghostscript_binary() -> str | None:
    return shutil.which("gs")


def _repair_via_ghostscript(source_path: Path, out_path: Path) -> None:
    binary = _ghostscript_binary()
    if binary is None:
        raise ConversionError(
            "Ghostscript is not installed on this machine - needed to repair a PDF "
            "pikepdf's own structural recovery couldn't handle."
        )
    cmd = [
        binary,
        "-o",
        str(out_path),
        "-sDEVICE=pdfwrite",
        "-dPDFSETTINGS=/prepress",
        str(source_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=_GHOSTSCRIPT_TIMEOUT_SECONDS, text=True, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(
            f"Ghostscript repair of '{source_path.name}' timed out after "
            f"{_GHOSTSCRIPT_TIMEOUT_SECONDS}s."
        ) from exc
    except OSError as exc:
        raise ConversionError(f"Could not launch Ghostscript: {exc}") from exc

    if result.returncode != 0 or not out_path.exists():
        raise ConversionError(
            f"Ghostscript could not repair '{source_path.name}': "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    # gs can exit 0 while only partially recovering a file ("errors
    # that were repaired or ignored") - confirmed by hand. Always
    # re-verify the output actually opens before trusting it.
    try:
        with pikepdf.Pdf.open(out_path):
            pass
    except (pikepdf.PdfError, OSError) as exc:
        raise ConversionError(
            f"Ghostscript produced output for '{source_path.name}' but it still "
            f"doesn't open cleanly: {exc}"
        ) from exc


@dataclass
class RepairOperation(Operation):
    """Recovers a possibly-corrupt PDF at `source_path` into a fresh
    working document. Tries `pikepdf`'s own structural recovery first
    (handles common corruption for free); falls back to Ghostscript's
    `-sDEVICE=pdfwrite` repair pass, with its output always re-verified
    by reopening via pikepdf, when pikepdf can't parse the file at
    all."""

    source_path: Path
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)
    _engine_used: str = field(default="", init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if not self.source_path.is_file():
            raise OperationError(f"Source file not found: {self.source_path}")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        try:
            with pikepdf.Pdf.open(self.source_path) as pdf:
                pdf.save(out_path)
            self._engine_used = "structural recovery"
        except (pikepdf.PdfError, OSError, RuntimeError):
            # Confirmed by hand: pikepdf raises a plain RuntimeError
            # (e.g. "/Count is wrong after flattening pages tree"),
            # not just PdfError/OSError, for corruption severe enough
            # that its own structural recovery can't make sense of the
            # pages tree at all - that's exactly the case Ghostscript's
            # fallback below exists for.
            _repair_via_ghostscript(self.source_path, out_path)
            self._engine_used = "Ghostscript"

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "repair",
            "source_path": str(self.source_path),
        }

    def describe(self) -> str:
        return f"Repaired ({self._engine_used or 'pending'})"


class RepairPlugin(ToolPlugin):
    tool_id = "repair"
    display_name = "Repair"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            source_path = kwargs["source_path"]
        except KeyError as exc:
            raise OperationError("Repair requires a 'source_path'.") from exc
        return RepairOperation(source_path=Path(source_path))

    def operation_class(self) -> type[Operation]:
        return RepairOperation
