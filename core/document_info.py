"""Read-only inspection of a PDF: document metadata, on-disk file
stats, page geometry, and PDF structure/security flags.

Qt-free on purpose, like `gui/controller.py`'s `AppController`, so the
whole report can be unit-tested without a display server; the GUI's
Document Properties dialog (`gui/dialogs/properties_dialog.py`) is a
thin presentation layer over `read_document_info()`.

**Why this lives at the top level of `core/` rather than in a
subpackage.** It is engine code, so it belongs under `core/` (and gets
`mypy --strict`), but none of the existing subpackages owns it:

- not `core/ops/` - SPEC.md section 2 defines that as "first-party
  plugins, one module per category" and this is not an `Operation`; it
  mutates nothing, has no inverse and never enters the undo stack.
  It also spans three of those categories at once (metadata, layout,
  security), so no single ops module is the natural home.
- not `core/model/` - that is the frozen `Operation`/`DocumentSession`
  /`Pipeline` framework (SPEC.md 6.1). A description of a *file* is
  not a model of an editing session, and putting it there would make
  the frozen-interface package depend on things above it.
- not `core/session/` - nothing here is persisted.

That leaves the top level, next to the other cross-cutting shared
modules (`core/errors.py`, `core/logging_config.py`).

**This module never raises.** A read-only report that crashes on a
malformed field is worse than one that says "unknown": every field is
guarded individually and a document that cannot be opened at all comes
back as a `DocumentInfo` with `read_error` set, so the caller has
nothing to catch. Nothing in here writes, so there is no failure that
needs to propagate as a `core/errors.py` exception.

**Cost.** Deliberately catalog/trailer reads plus one page-box read
per page - no page rendering, no content-stream parsing, no font or
annotation enumeration - so opening the dialog on a large document is
effectively instant.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pikepdf

from core.logging_config import get_logger

log = get_logger(__name__)

#: Document-info keys read for the metadata section.
#:
#: The first six intentionally mirror `core/ops/metadata.py`'s
#: `_FIELD_TO_DOCINFO_KEY` exactly - the same fields
#: `SetMetadataOperation` writes, under the same names - so what this
#: reader reports and what that operation writes cannot disagree.
#: Duplicated rather than imported: `core/ops` sits *above* this
#: module architecturally (ops import core, not the other way round),
#: and reaching into another module's private name to save eight lines
#: is not worth inverting that. `test_document_info.py` pins the two
#: maps together instead, so a future edit to either one fails loudly.
#:
#: /Creator and /Producer are read-only extras: they identify the
#: software that produced the file, which `SetMetadataOperation`
#: deliberately does not offer for editing.
_DOCINFO_KEYS: dict[str, str] = {
    "title": "/Title",
    "author": "/Author",
    "subject": "/Subject",
    "keywords": "/Keywords",
    "creator": "/Creator",
    "producer": "/Producer",
    "creation_date": "/CreationDate",
    "mod_date": "/ModDate",
}

#: Fields holding a PDF date string rather than free text.
_DATE_FIELDS = frozenset({"creation_date", "mod_date"})

#: PDF date strings (PDF 32000-1 7.9.4): `D:YYYYMMDDHHmmSSOHH'mm'`,
#: with everything after the year optional and `O` one of `+`, `-`,
#: `Z`. Deliberately lenient - real-world producers omit the `D:`
#: prefix, the trailing apostrophes, or the whole time part - because
#: the alternative to parsing a sloppy date is showing the user the
#: raw `D:2025...` string, not an error.
_PDF_DATE_RE = re.compile(
    r"^D?:?"
    r"(?P<year>\d{4})"
    r"(?:(?P<month>\d{2})"
    r"(?:(?P<day>\d{2})"
    r"(?:(?P<hour>\d{2})"
    r"(?:(?P<minute>\d{2})"
    r"(?:(?P<second>\d{2}))?)?)?)?)?"
    r"(?:(?P<tz_sign>[+\-Z])(?:(?P<tz_hour>\d{2})'?(?:(?P<tz_minute>\d{2})'?)?)?)?$"
)

#: 1 inch = 72 PDF points = 25.4 mm.
_POINTS_PER_INCH = 72.0
_MM_PER_INCH = 25.4

#: Two page boxes within this many points of each other count as the
#: same size. Producers routinely emit 595.276 x 841.89 for A4 on one
#: page and 595.28 x 841.89 on the next; reporting that as "Mixed
#: (2 sizes)" would be technically true and completely useless.
_SIZE_TOLERANCE_PT = 0.5

Orientation = Literal["portrait", "landscape", "square"]


def points_to_mm(points: float) -> float:
    return points * _MM_PER_INCH / _POINTS_PER_INCH


def points_to_inches(points: float) -> float:
    return points / _POINTS_PER_INCH


def human_readable_size(size_bytes: int) -> str:
    """A short size string ("1.4 MB"). 1024-based, matching what file
    managers show. Callers display the exact byte count alongside it,
    so the KB-vs-KiB ambiguity never has to be resolved by the reader.
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    value = float(size_bytes)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass(frozen=True)
class FileInfo:
    """The document's original file on disk.

    Everything here describes that file as it currently sits on disk -
    *not* the in-memory working copy the other sections describe.
    `has_unsaved_changes` is what makes the difference legible: a
    modification time from three days ago is actively misleading next
    to twenty unsaved edits unless the report says so.
    """

    #: The file the document was opened from. None for a document
    #: built from scratch in the app (Merge) with nothing on disk yet.
    path: Path | None
    exists: bool
    size_bytes: int | None
    #: Filesystem creation time. None where the platform doesn't
    #: record one (`st_birthtime` is absent on Linux before Python
    #: 3.13/statx) - reported as unavailable rather than substituting
    #: `st_ctime`, which is the inode *change* time and would quietly
    #: show something that isn't a creation date at all.
    created: datetime | None
    modified: datetime | None
    #: The controller's dirty flag for this document.
    has_unsaved_changes: bool


@dataclass(frozen=True)
class MetadataInfo:
    """Document-info dictionary fields.

    `None` means the key is absent; `""` means it is present but
    blank. The dialog renders those two differently on purpose - "this
    document has no Title" and "this document has a Title that
    somebody blanked out" are different facts.

    The two date fields hold an ISO 8601 string when the PDF date
    parsed (the same format `MetadataDialog` asks the user to type, so
    a value read here can be pasted straight back into an edit), or
    the raw PDF date string verbatim when it did not.
    """

    title: str | None
    author: str | None
    subject: str | None
    keywords: str | None
    creator: str | None
    producer: str | None
    creation_date: str | None
    mod_date: str | None


@dataclass(frozen=True)
class PageSizeGroup:
    """One distinct page size, and how many pages have it."""

    width_pt: float
    height_pt: float
    page_count: int

    @property
    def orientation(self) -> Orientation:
        """Derived from the page box alone. A page's `/Rotate` is
        reported separately rather than folded in here, so that
        "612 x 792 pt, Portrait" never contradicts the dimensions
        printed beside it.
        """
        if abs(self.width_pt - self.height_pt) < _SIZE_TOLERANCE_PT:
            return "square"
        return "landscape" if self.width_pt > self.height_pt else "portrait"

    @property
    def width_mm(self) -> float:
        return points_to_mm(self.width_pt)

    @property
    def height_mm(self) -> float:
        return points_to_mm(self.height_pt)

    @property
    def width_in(self) -> float:
        return points_to_inches(self.width_pt)

    @property
    def height_in(self) -> float:
        return points_to_inches(self.height_pt)


@dataclass(frozen=True)
class RotationGroup:
    """One distinct `/Rotate` value, and how many pages have it."""

    degrees: int
    page_count: int


@dataclass(frozen=True)
class PageGeometryInfo:
    page_count: int
    #: Distinct page sizes, most common first. More than one entry
    #: means the document genuinely has mixed page sizes - reported as
    #: such rather than silently presenting page 1's size as the
    #: whole document's.
    size_groups: tuple[PageSizeGroup, ...]
    #: Distinct `/Rotate` values, most common first. Always at least
    #: one entry (a page with no `/Rotate` counts as 0).
    rotation_groups: tuple[RotationGroup, ...]
    #: Pages whose box or rotation could not be read (missing
    #: `/MediaBox`, non-numeric `/Rotate`, ...). Counted, not fatal.
    unreadable_pages: int


@dataclass(frozen=True)
class PermissionInfo:
    """What an encrypted document's permission flags allow.

    Mapped from pikepdf's `Pdf.allow`: `printing` is
    `print_lowres or print_highres` (with `high_quality_printing`
    carrying the distinction, since "printing allowed, but only at
    draft quality" is a materially different answer from "printing
    allowed"), `copying` is `extract`, `modifying` is `modify_other`,
    `annotating` is `modify_annotation`.
    """

    printing: bool
    high_quality_printing: bool
    copying: bool
    modifying: bool
    annotating: bool


@dataclass(frozen=True)
class TechnicalInfo:
    """PDF structure and security flags. Every field is optional
    because a document that needs a password to open yields only
    `encrypted=True` and nothing else.
    """

    pdf_version: str | None
    linearized: bool | None
    tagged: bool | None
    encrypted: bool | None
    #: Only populated for an encrypted document that could still be
    #: opened; None for an unencrypted one (where nothing is
    #: restricted) or one that could not be read at all.
    permissions: PermissionInfo | None


@dataclass(frozen=True)
class DocumentInfo:
    """Everything the Document Properties report shows.

    `metadata` / `geometry` are None when the PDF could not be read,
    in which case `read_error` says why in one user-facing sentence.
    `file` is always populated (it needs no PDF parsing at all) and
    `technical` always exists, though its fields may all be None.
    """

    file: FileInfo
    metadata: MetadataInfo | None
    geometry: PageGeometryInfo | None
    technical: TechnicalInfo
    read_error: str | None
    #: True when the document is encrypted with a user password, so it
    #: could not be opened for inspection at all. Distinct from
    #: `technical.encrypted`, which is also True for a document that
    #: is encrypted but still readable (owner password only).
    password_protected: bool


def read_document_info(
    working_path: Path | None,
    *,
    source_path: Path | None = None,
    has_unsaved_changes: bool = False,
) -> DocumentInfo:
    """Inspect a PDF and return everything the properties report needs.

    `working_path` is the PDF actually parsed - for the GUI that is the
    session's private *working copy*, i.e. the current edit state
    including anything unsaved. `source_path` is only stat()ed, never
    parsed: it is the untouched original the document was opened from,
    and it is what the "File on disk" section describes.

    Never raises; see the module docstring.
    """
    file_info = _read_file_info(source_path, has_unsaved_changes)

    if working_path is None:
        return DocumentInfo(
            file=file_info,
            metadata=None,
            geometry=None,
            technical=TechnicalInfo(None, None, None, None, None),
            read_error="No document is open.",
            password_protected=False,
        )

    try:
        pdf = pikepdf.Pdf.open(working_path)
    except pikepdf.PasswordError:
        # PasswordError is *not* a subclass of pikepdf.PdfError
        # (verified against pikepdf 10.11 - its base is plain
        # Exception), so it needs its own clause and would escape a
        # PdfError-only handler. Reachable in the app for real:
        # ProtectOperation requires a non-empty user password, so the
        # working copy of a just-protected document lands here.
        log.info("Document is password-protected", extra={"context": str(working_path)})
        return DocumentInfo(
            file=file_info,
            metadata=None,
            geometry=None,
            technical=TechnicalInfo(None, None, None, True, None),
            read_error="This document is password-protected, so its contents could not be read.",
            password_protected=True,
        )
    except (pikepdf.PdfError, OSError, RuntimeError) as exc:
        # RuntimeError too, not defensively: pikepdf raises a plain
        # RuntimeError for some structural damage (see
        # core/ops/repair.py, which found the same thing).
        log.warning("Could not inspect document", extra={"context": f"{working_path}: {exc}"})
        return DocumentInfo(
            file=file_info,
            metadata=None,
            geometry=None,
            technical=TechnicalInfo(None, None, None, None, None),
            read_error=f"This document could not be read: {exc}",
            password_protected=False,
        )

    with pdf:
        return DocumentInfo(
            file=file_info,
            metadata=_read_metadata(pdf),
            geometry=_read_geometry(pdf),
            technical=_read_technical(pdf),
            read_error=None,
            password_protected=False,
        )


def _read_file_info(source_path: Path | None, has_unsaved_changes: bool) -> FileInfo:
    if source_path is None:
        return FileInfo(None, False, None, None, None, has_unsaved_changes)
    try:
        stat = source_path.stat()
    except OSError as exc:
        log.info("Could not stat source file", extra={"context": f"{source_path}: {exc}"})
        return FileInfo(source_path, False, None, None, None, has_unsaved_changes)

    birthtime = getattr(stat, "st_birthtime", None)
    return FileInfo(
        path=source_path,
        exists=True,
        size_bytes=stat.st_size,
        created=_timestamp_to_datetime(birthtime),
        modified=_timestamp_to_datetime(stat.st_mtime),
        has_unsaved_changes=has_unsaved_changes,
    )


def _timestamp_to_datetime(timestamp: float | None) -> datetime | None:
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp).astimezone()
    except (OSError, OverflowError, ValueError):  # pragma: no cover - absurd mtimes only
        return None


def _read_metadata(pdf: pikepdf.Pdf) -> MetadataInfo:
    values: dict[str, str | None] = {}
    for name, key in _DOCINFO_KEYS.items():
        values[name] = _read_docinfo_field(pdf, name, key)
    return MetadataInfo(**values)


def _read_docinfo_field(pdf: pikepdf.Pdf, name: str, key: str) -> str | None:
    try:
        raw = pdf.docinfo.get(key)
    except Exception as exc:  # a broken docinfo must not kill the report
        log.warning("Unreadable docinfo entry", extra={"context": f"{key}: {exc}"})
        return None
    if raw is None:
        return None
    try:
        text = str(raw)
    except Exception as exc:  # as above
        log.warning("Undecodable docinfo entry", extra={"context": f"{key}: {exc}"})
        return None
    if name in _DATE_FIELDS:
        return format_pdf_date(text)
    return text


def format_pdf_date(raw: str) -> str:
    """A PDF date string as ISO 8601, or `raw` unchanged if it doesn't
    parse. Never raises - an unrecognisable date is shown as-is rather
    than swallowed or turned into an error."""
    parsed = parse_pdf_date(raw)
    return parsed.isoformat() if parsed is not None else raw


def parse_pdf_date(raw: str) -> datetime | None:
    """Parse a PDF date string (`D:YYYYMMDDHHmmSS+HH'mm'`), the format
    `core/ops/metadata.py`'s `_to_pdf_date` writes. None if it doesn't
    parse."""
    match = _PDF_DATE_RE.match(raw.strip())
    if match is None:
        return None
    parts = match.groupdict()
    try:
        naive = datetime(
            year=int(parts["year"]),
            month=int(parts["month"] or 1),
            day=int(parts["day"] or 1),
            hour=int(parts["hour"] or 0),
            minute=int(parts["minute"] or 0),
            second=int(parts["second"] or 0),
        )
    except ValueError:
        return None  # e.g. month 13, day 32

    sign = parts["tz_sign"]
    if sign is None:
        return naive  # no offset recorded: report local/unspecified, don't invent UTC
    try:
        offset = _utc_offset(sign, parts["tz_hour"], parts["tz_minute"])
    except ValueError:
        return naive
    return naive.replace(tzinfo=offset)


def _utc_offset(sign: str, hour: str | None, minute: str | None) -> timezone:
    if sign == "Z":
        return UTC
    delta = timedelta(hours=int(hour or 0), minutes=int(minute or 0))
    return timezone(-delta if sign == "-" else delta)


def _read_geometry(pdf: pikepdf.Pdf) -> PageGeometryInfo:
    """Page count, distinct page sizes and distinct rotations.

    One `/MediaBox` + `/Rotate` read per page - the whole cost of this
    function. MediaBox (not CropBox) is the page size reported, matching
    what every operation in `core/ops` treats as the page: Crop and
    Resize both write MediaBox and CropBox together, and Watermark,
    Header/Footer and Bates all position against MediaBox.
    """
    sizes: Counter[tuple[float, float]] = Counter()
    rotations: Counter[int] = Counter()
    unreadable = 0
    page_count = 0

    try:
        pages = list(pdf.pages)
    except Exception as exc:  # a broken page tree must not kill the report
        log.warning("Could not enumerate pages", extra={"context": str(exc)})
        return PageGeometryInfo(0, (), (), 0)

    for page in pages:
        page_count += 1
        size = _page_size(page)
        rotation = _page_rotation(page)
        if size is None or rotation is None:
            unreadable += 1
            continue
        sizes[_bucket_size(size, sizes)] += 1
        rotations[rotation] += 1

    size_groups = tuple(
        PageSizeGroup(width_pt=width, height_pt=height, page_count=count)
        for (width, height), count in sizes.most_common()
    )
    rotation_groups = tuple(
        RotationGroup(degrees=degrees, page_count=count) for degrees, count in rotations.most_common()
    )
    return PageGeometryInfo(page_count, size_groups, rotation_groups, unreadable)


def _bucket_size(
    size: tuple[float, float], seen: Counter[tuple[float, float]]
) -> tuple[float, float]:
    """Fold a page size into an already-seen one within
    `_SIZE_TOLERANCE_PT`, so sub-point producer noise doesn't split one
    real page size into several reported ones."""
    for existing in seen:
        if (
            abs(existing[0] - size[0]) < _SIZE_TOLERANCE_PT
            and abs(existing[1] - size[1]) < _SIZE_TOLERANCE_PT
        ):
            return existing
    return size


def _page_size(page: pikepdf.Page) -> tuple[float, float] | None:
    """The page's MediaBox as (width, height).

    The guard here is belt-and-braces rather than a hot path: checked
    by hand against real files, qpdf repairs a missing or malformed
    /MediaBox (string entries, too few entries, a Name instead of an
    array) to a default letter page while opening, so this rarely gets
    the chance to fail. `_page_rotation` is the one that genuinely
    does - a non-integer /Rotate survives into the object model.
    """
    try:
        box = [float(value) for value in page.mediabox]
    except Exception as exc:  # missing/garbage MediaBox is a TypeError here
        log.warning("Unreadable page box", extra={"context": str(exc)})
        return None
    if len(box) != 4:
        return None
    return (round(abs(box[2] - box[0]), 2), round(abs(box[3] - box[1]), 2))


def _page_rotation(page: pikepdf.Page) -> int | None:
    try:
        raw = page.obj.get("/Rotate")
        if raw is None:
            return 0
        return int(raw) % 360
    except Exception as exc:  # a non-integer /Rotate raises TypeError
        log.warning("Unreadable page rotation", extra={"context": str(exc)})
        return None


def _read_technical(pdf: pikepdf.Pdf) -> TechnicalInfo:
    return TechnicalInfo(
        pdf_version=_read_pdf_version(pdf),
        linearized=_guarded_bool(lambda: bool(pdf.is_linearized), "linearized"),
        tagged=_read_tagged(pdf),
        encrypted=_guarded_bool(lambda: bool(pdf.is_encrypted), "encrypted"),
        permissions=_read_permissions(pdf),
    )


def _read_pdf_version(pdf: pikepdf.Pdf) -> str | None:
    """The document's effective PDF version.

    `Pdf.pdf_version` is the `%PDF-x.y` header only; PDF 1.4 onward
    lets the catalog's `/Version` override it (used when a file is
    incrementally updated to a later version without rewriting the
    header). Confirmed against a real file: pikepdf keeps reporting
    1.3 for a document whose catalog says 1.7, so the override has to
    be applied here or the dialog under-reports the version.
    """
    try:
        header_version = str(pdf.pdf_version)
    except Exception as exc:
        log.warning("Unreadable PDF version", extra={"context": str(exc)})
        return None
    try:
        override = pdf.Root.get("/Version")
    except Exception:  # a broken catalog just means "no override"
        return header_version
    if override is None:
        return header_version
    catalog_version = str(override).lstrip("/")
    return catalog_version if _version_tuple(catalog_version) > _version_tuple(header_version) else header_version


def _version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)


def _read_tagged(pdf: pikepdf.Pdf) -> bool | None:
    """A document is tagged (i.e. carries accessibility structure) when
    its catalog says `/MarkInfo << /Marked true >>`. Checked alongside
    `/StructTreeRoot`, because a `/Marked` flag with no structure tree
    behind it is a lie some producers tell."""
    try:
        mark_info = pdf.Root.get("/MarkInfo")
        marked = bool(mark_info.get("/Marked", False)) if mark_info is not None else False
        return marked and pdf.Root.get("/StructTreeRoot") is not None
    except Exception as exc:
        log.warning("Unreadable /MarkInfo", extra={"context": str(exc)})
        return None


def _read_permissions(pdf: pikepdf.Pdf) -> PermissionInfo | None:
    """Permission flags, only for an encrypted document.

    `Pdf.allow` reports everything as allowed for an unencrypted file,
    which is true but not worth a row - there are no restrictions to
    report when there is no encryption dictionary to carry them.
    """
    try:
        if not pdf.is_encrypted:
            return None
        allow = pdf.allow
        return PermissionInfo(
            printing=bool(allow.print_lowres or allow.print_highres),
            high_quality_printing=bool(allow.print_highres),
            copying=bool(allow.extract),
            modifying=bool(allow.modify_other),
            annotating=bool(allow.modify_annotation),
        )
    except Exception as exc:
        log.warning("Unreadable permissions", extra={"context": str(exc)})
        return None


def _guarded_bool(read: Callable[[], bool], what: str) -> bool | None:
    try:
        return read()
    except Exception as exc:
        log.warning(f"Unreadable {what} flag", extra={"context": str(exc)})
        return None
