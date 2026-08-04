"""Shared helpers for Phase 3 conversion operations
(core/ops/convert_from.py, core/ops/convert_to.py).

Engine strategy: LibreOffice headless (`run_libreoffice_convert`) is
used as the primary engine for every "external file -> PDF" direction
(convert_to.py) - confirmed by hand that Writer/Impress/Calc/Writer-Web
all export straight to PDF reliably - with a pure-Python fallback used
automatically when `soffice`/`libreoffice` isn't found on `PATH`.

The reverse direction ("PDF -> external format", convert_from.py) is
pure-Python only, everywhere, no LibreOffice attempt at all: confirmed
by hand that a PDF always imports into LibreOffice as a *Draw*
document, and Draw's export filter set only covers odg/pdf/image
formats - never docx or pptx ("Error: no export filter ... aborting.",
reproduced for both). Calc additionally has no PDF-import filter
whatsoever. So there is no working LibreOffice path in that direction
to even attempt before falling back - see convert_from.py's module
docstring for the full detail.

**Known limitation, partially mitigated**: `core.security.sandbox.
network_lockdown()`'s socket patch is process-local and does not
extend to the LibreOffice subprocess spawned here. This was not just
theorized - confirmed by hand that a converted HTML file referencing a
remote `<img src>` made LibreOffice actually attempt an outbound TCP
connection (an 11s conversion vs. the ~1s local-only baseline,
measured against an RFC 5737 black-hole address). `_NETWORK_LOCKED_ENV_OVERRIDES`
below forces every proxy env var to a dead loopback address for the
subprocess, which was confirmed to bring the same malicious HTML back
down to the ~1s baseline (LibreOffice's own outbound attempts now fail
closed immediately instead of ever reaching a real remote host). This
covers plain HTTP(S)/FTP fetches through the subprocess's normal proxy
resolution - it is an environment-level mitigation, not a kernel-
enforced boundary (same caveat `core/security/sandbox.py` already
documents about its own socket patch), so a component that ignores
proxy env vars entirely (rather than failing to reach one) wouldn't be
stopped by this. True OS-level network isolation for subprocesses
remains the open item `docs/SPEC.md` section 5 already lists.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import fitz
import pdfplumber

from core.errors import ConversionError
from core.logging_config import get_logger
from core.security.secure_delete import secure_delete_dir

log = get_logger(__name__)

#: Generous timeout to absorb LibreOffice's ~3s process-startup cost
#: (measured) plus real conversion time for larger documents.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: Confirmed by hand: a converted HTML file referencing a remote
#: <img src> made LibreOffice's subprocess actually attempt an
#: outbound TCP connection (an 11s conversion vs. the ~1s local-only
#: baseline, timing out against an RFC 5737 black-hole address) -
#: `network_lockdown()`'s socket patch is process-local and does not
#: reach this subprocess at all. Forcing every proxy env var to a dead
#: loopback address makes LibreOffice's own outbound attempts fail
#: closed immediately instead of ever reaching a real remote host -
#: confirmed by hand too: the same malicious HTML dropped back to the
#: ~1s local-only baseline with this in place. This is defense in
#: depth alongside `_reject_remote_uri` (convert_to.py's xhtml2pdf
#: fallback path) - together they cover both engines for HTML, and
#: this env-based guard is the only thing covering the docx/pptx/xlsx
#: LibreOffice paths (Office formats can embed remote/linked content
#: too - OLE links, remote images - not just HTML).
_DEAD_PROXY = "http://127.0.0.1:1"
_NETWORK_LOCKED_ENV_OVERRIDES = {
    "http_proxy": _DEAD_PROXY,
    "https_proxy": _DEAD_PROXY,
    "ftp_proxy": _DEAD_PROXY,
    "all_proxy": _DEAD_PROXY,
    "HTTP_PROXY": _DEAD_PROXY,
    "HTTPS_PROXY": _DEAD_PROXY,
    "FTP_PROXY": _DEAD_PROXY,
    "ALL_PROXY": _DEAD_PROXY,
    # Cleared, not just left alone - a stray no_proxy could otherwise
    # exempt a specific host from the dead-proxy override above.
    "no_proxy": "",
    "NO_PROXY": "",
}


def libreoffice_binary() -> str | None:
    """The `soffice`/`libreoffice` executable on `PATH`, or None if
    neither is installed - callers use this to decide whether to
    attempt the LibreOffice path at all before falling back."""
    return shutil.which("soffice") or shutil.which("libreoffice")


def run_libreoffice_convert(
    source: Path,
    target_format: str,
    out_dir: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """Convert `source` to `target_format` via `soffice --headless
    --convert-to`, writing into `out_dir` (must already exist, and
    should be a private session-temp location - never the OS system
    temp dir, matching this project's session-dir convention
    everywhere else). Raises `ConversionError` if LibreOffice isn't
    installed, times out, exits nonzero, or doesn't produce the
    expected output file.

    Each call gets its own fresh, securely-wiped `-env:UserInstallation`
    profile directory (a sibling of `out_dir`) so no state persists
    between conversions and the real user LibreOffice profile is never
    touched.
    """
    binary = libreoffice_binary()
    if binary is None:
        raise ConversionError("LibreOffice is not installed on this machine.")

    profile_dir = out_dir.parent / f"lo_profile_{uuid.uuid4().hex}"
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        cmd = [
            binary,
            "--headless",
            "--norestore",
            f"-env:UserInstallation=file://{profile_dir}",
            "--convert-to",
            target_format,
            "--outdir",
            str(out_dir),
            str(source),
        ]
        env = {**os.environ, **_NETWORK_LOCKED_ENV_OVERRIDES}
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=timeout, text=True, check=False, env=env
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(
                f"LibreOffice conversion of '{source.name}' timed out after {timeout}s."
            ) from exc
        except OSError as exc:
            raise ConversionError(f"Could not launch LibreOffice: {exc}") from exc

        if result.returncode != 0:
            raise ConversionError(
                f"LibreOffice failed to convert '{source.name}' to {target_format}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

        out_path = out_dir / f"{source.stem}.{target_format}"
        if not out_path.exists():
            raise ConversionError(
                f"LibreOffice did not produce the expected output '{out_path.name}'."
            )
        return out_path
    finally:
        secure_delete_dir(profile_dir)


def extract_pdf_text_by_page(pdf_path: Path) -> list[str]:
    """Plain text of each page, in order (empty string for a page with
    no extractable text). Used by the pure-Python fallback paths for
    PDF -> DOCX and PDF -> HTML - text-only, no layout/images."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


TableRows = list[list[str | None]]


def extract_pdf_tables_by_page(pdf_path: Path) -> list[list[TableRows]]:
    """Every detected table on each page, in order. Used by PDF ->
    XLSX (pdfplumber is the only engine for this direction - Calc has
    no PDF-import filter at all)."""
    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_tables() for page in pdf.pages]


def render_pdf_page_to_image(pdf_path: Path, page_index: int, dpi: int, out_path: Path) -> Path:
    """Render one page (0-indexed) of `pdf_path` to an image file at
    `out_path`, whose format is inferred from its suffix. Used by
    PdfToJpgOperation and the PDF -> PPTX fallback's full-slide-image
    approach."""
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        pixmap = page.get_pixmap(matrix=matrix)
        pixmap.save(out_path)
    return out_path
