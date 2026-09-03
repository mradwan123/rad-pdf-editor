"""Redaction: permanently removing content, not covering it.

Phase 6f (docs/GUI_PLAN.md §3.7, decision 10). Split into its own slice
because it is the one feature where a shortcut is a security failure
rather than a UX compromise - this app's stated purpose is confidential
and regulated documents (SPEC.md section 1).

Two findings from building it, both measured rather than assumed, and
both the difference between real and cosmetic redaction:

**1. `apply_redactions()` alone is not enough - the save matters.**
After redacting "Jane Doe" from every page, `page.get_text()` came back
clean and the string was *still present in the raw file bytes*: a plain
`Document.save()` leaves the superseded content stream in the file as
an unreferenced object, trivially recoverable. Saving with
`garbage=4, clean=True, deflate=True` removes it. Measured on the same
fixture:

    plain save          "Jane Doe" in raw bytes: True   (3463 bytes)
    garbage=4, clean    "Jane Doe" in raw bytes: False  (1242 bytes)

A test that only checked extracted text would have passed the leaking
version, which is why `tests/unit/test_redact.py` asserts against the
raw bytes.

**2. Page content is not the only place the text lives.** Document
metadata, the XMP packet, bookmark titles and embedded attachments all
survive a content redaction untouched, and are where a "redacted" PDF
most often still leaks. Scrubbing them is part of the operation, not an
afterthought for the caller.

`RedactOperation` takes an explicit target list so a CLI or Workflow
run is exactly as thorough as an interactive one - the GUI's review
step narrows the targets, it does not add capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

import fitz

from core.errors import OperationError
from core.model.document import DocumentSession
from core.model.operation import Operation
from core.model.progress import SupportsProgress
from core.ops.common import (
    allocate_working_path,
    next_session,
    read_working_bytes,
    snapshot_restore_invert,
)
from core.registry.plugin_base import ToolPlugin

CORE_VERSION_RANGE = ">=1.0,<2.0"

#: (1-based page, x0, y0, x1, y1) in bottom-left-origin PDF points.
RedactRect = tuple[int, float, float, float, float]


class RedactionScan(NamedTuple):
    """Everywhere a term appears, for the review step to show.

    Deliberately reports the non-content hits separately: those are the
    ones a user does not think of, and the ones a content-only
    redaction silently leaves behind.
    """

    #: 1-based page -> rects in bottom-left-origin PDF points.
    page_hits: dict[int, list[tuple[float, float, float, float]]]
    #: Metadata keys whose value contains the term.
    metadata_keys: list[str]
    #: Bookmark titles containing the term.
    bookmarks: list[str]
    #: Embedded attachment names containing the term, or whose content does.
    attachments: list[str]
    #: True if the XMP packet contains the term.
    xmp: bool

    @property
    def total_page_hits(self) -> int:
        return sum(len(v) for v in self.page_hits.values())

    @property
    def is_empty(self) -> bool:
        return not (
            self.page_hits
            or self.metadata_keys
            or self.bookmarks
            or self.attachments
            or self.xmp
        )


def scan_for_text(path: Path, text: str) -> RedactionScan:
    """Find every occurrence of `text`, including the places a
    content-only redaction would miss."""
    if not text:
        return RedactionScan({}, [], [], [], False)
    page_hits: dict[int, list[tuple[float, float, float, float]]] = {}
    metadata_keys: list[str] = []
    bookmarks: list[str] = []
    attachments: list[str] = []
    xmp = False
    with fitz.open(path) as document:
        for index, page in enumerate(document, start=1):
            height = page.rect.height
            rects = [
                (r.x0, height - r.y1, r.x1, height - r.y0) for r in page.search_for(text)
            ]
            if rects:
                page_hits[index] = rects
        metadata_keys = [
            key
            for key, value in (document.metadata or {}).items()
            if key != "format" and isinstance(value, str) and text.lower() in value.lower()
        ]
        bookmarks = [entry[1] for entry in document.get_toc() if text.lower() in entry[1].lower()]
        for name in document.embfile_names():
            if text.lower() in name.lower():
                attachments.append(name)
                continue
            try:
                if text.encode() in document.embfile_get(name):
                    attachments.append(name)
            except (RuntimeError, ValueError):  # unreadable attachment
                continue
        xmp = text.lower() in (document.xref_xml_metadata() or "").lower()
    return RedactionScan(page_hits, metadata_keys, bookmarks, attachments, xmp)


@dataclass
class RedactOperation(Operation, SupportsProgress):
    """Permanently removes regions of pages, and optionally every other
    trace of a search term.

    `rects` are explicit regions in bottom-left-origin PDF points, the
    convention used throughout this package. `search_text` additionally
    finds and removes every occurrence, which is what makes the
    operation usable unattended from the CLI or a saved Workflow.

    The `scrub_*` flags only do anything when `search_text` is given -
    without a term there is nothing to match metadata or a bookmark
    against, and blanking all metadata unasked would be a surprise.
    """

    rects: list[RedactRect] = field(default_factory=list)
    search_text: str = ""
    scrub_metadata: bool = True
    scrub_bookmarks: bool = True
    scrub_attachments: bool = True
    scrub_xmp: bool = True
    fill: tuple[float, float, float] = (0.0, 0.0, 0.0)
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)
    _removed: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.rects and not self.search_text:
            raise OperationError("Redaction needs either regions or a search term.")
        for page, x0, y0, x1, y1 in self.rects:
            if page < 1:
                raise OperationError(f"Page numbers are 1-based, got {page}.")
            if x1 <= x0 or y1 <= y0:
                raise OperationError(
                    f"Redaction rect must have positive width and height, got "
                    f"{(x0, y0, x1, y1)}."
                )

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if doc.working_path is None or not doc.working_path.exists():
            raise OperationError("No document is open.")
        self._pre_snapshot = read_working_bytes(doc)
        out_path = allocate_working_path(doc)
        removed = 0

        by_page: dict[int, list[tuple[float, float, float, float]]] = {}
        for page, x0, y0, x1, y1 in self.rects:
            by_page.setdefault(page, []).append((x0, y0, x1, y1))

        with fitz.open(doc.working_path) as document:
            for page_number in by_page:
                if page_number > document.page_count:
                    raise OperationError(
                        f"Page {page_number} is out of range "
                        f"(document has {document.page_count})."
                    )
            total = document.page_count
            for index, page in enumerate(document, start=1):
                self.report_progress(index - 1, total)
                height = page.rect.height
                marked = 0
                for x0, y0, x1, y1 in by_page.get(index, []):
                    page.add_redact_annot(
                        fitz.Rect(x0, height - y1, x1, height - y0), fill=self.fill
                    )
                    marked += 1
                if self.search_text:
                    for rect in page.search_for(self.search_text):
                        page.add_redact_annot(rect, fill=self.fill)
                        marked += 1
                if marked:
                    page.apply_redactions()
                    removed += marked

            if self.search_text:
                removed += self._scrub(document)

            # garbage=4 is load-bearing, not an optimisation: a plain
            # save leaves the superseded content stream in the file as an
            # unreferenced object, and the redacted text is recoverable
            # straight out of the raw bytes. See the module docstring.
            document.save(out_path, garbage=4, clean=True, deflate=True)

        self._removed = removed
        return next_session(doc, out_path)

    def _scrub(self, document: fitz.Document) -> int:
        """Remove the term from everywhere that is not page content."""
        term = self.search_text.lower()
        removed = 0

        if self.scrub_metadata:
            metadata = dict(document.metadata or {})
            cleaned = {
                key: ("" if isinstance(value, str) and term in value.lower() else value)
                for key, value in metadata.items()
                if key != "format"
            }
            if cleaned != {k: v for k, v in metadata.items() if k != "format"}:
                removed += sum(
                    1
                    for key, value in metadata.items()
                    if key != "format" and isinstance(value, str) and term in value.lower()
                )
                document.set_metadata(cleaned)

        if self.scrub_bookmarks:
            toc = document.get_toc()
            kept = [entry for entry in toc if term not in entry[1].lower()]
            if len(kept) != len(toc):
                removed += len(toc) - len(kept)
                document.set_toc(kept)

        if self.scrub_attachments:
            for name in list(document.embfile_names()):
                matches = term in name.lower()
                if not matches:
                    try:
                        matches = self.search_text.encode() in document.embfile_get(name)
                    except (RuntimeError, ValueError):
                        matches = False
                if matches:
                    document.embfile_del(name)
                    removed += 1

        if self.scrub_xmp and term in (document.xref_xml_metadata() or "").lower():
            document.del_xml_metadata()
            removed += 1

        return removed

    def invert(self) -> Operation:
        # Redaction is destructive by design; the only honest inverse is
        # the document as it was.
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def affected_pages(self) -> list[int] | None:
        if self.search_text:
            return None  # a term can appear anywhere
        return sorted({page for page, *_ in self.rects}) or None

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "redact",
            "rects": [list(r) for r in self.rects],
            "search_text": self.search_text,
            "scrub_metadata": self.scrub_metadata,
            "scrub_bookmarks": self.scrub_bookmarks,
            "scrub_attachments": self.scrub_attachments,
            "scrub_xmp": self.scrub_xmp,
            "fill": list(self.fill),
        }

    def describe(self) -> str:
        if self.search_text:
            return f"Redacted '{self.search_text}' ({self._removed} occurrence(s))"
        return f"Redacted {len(self.rects)} region(s)"


class RedactPlugin(ToolPlugin):
    tool_id = "redact"
    display_name = "Redact"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        return RedactOperation(**kwargs)

    def operation_class(self) -> type[Operation]:
        return RedactOperation
