"""Metadata and Rename operations (SPEC.md Phase 1 list).

Metadata edits the working PDF's document-info dictionary (Title,
Author, Subject, Keywords, Creation Date, Modification Date). Rename
only changes the session's user-facing output filename
(`DocumentSession.display_name`) - it never touches PDF bytes, so it
needs no snapshot-restore fallback; a precise inverse is trivial.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

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

#: Maps this op's user-facing field names to the pikepdf docinfo keys
#: they write - keeps arbitrary dictionary keys (and thus arbitrary
#: PDF structure edits) out of the "set some metadata fields" tool's
#: blast radius.
_FIELD_TO_DOCINFO_KEY = {
    "title": "/Title",
    "author": "/Author",
    "subject": "/Subject",
    "keywords": "/Keywords",
    "creation_date": "/CreationDate",
    "mod_date": "/ModDate",
}

#: Fields whose value is a date - parsed as ISO 8601 and converted to
#: the PDF date-string format (D:YYYYMMDDHHmmSS+HH'mm') rather than
#: written through as plain text like the other fields.
_DATE_FIELDS = {"creation_date", "mod_date"}


def _to_pdf_date(value: str) -> str:
    """Convert an ISO 8601 string (e.g. "2025-06-03T12:00:00+00:00" or
    "2025-06-03") to the PDF docinfo date format. Naive datetimes are
    treated as UTC."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise OperationError(
            f"Invalid date '{value}': expected ISO 8601, e.g. 2025-06-03T12:00:00+00:00"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    offset = dt.utcoffset() or timedelta(0)
    sign = "+" if offset >= timedelta(0) else "-"
    offset_minutes = int(abs(offset).total_seconds() // 60)
    offset_hh, offset_mm = divmod(offset_minutes, 60)
    return f"D:{dt.strftime('%Y%m%d%H%M%S')}{sign}{offset_hh:02d}'{offset_mm:02d}'"


@dataclass
class SetMetadataOperation(Operation):
    """Sets docinfo fields (title/author/subject/keywords/creation_date/
    mod_date) on the working document. `creation_date`/`mod_date` must
    be ISO 8601 strings; the rest are plain text. Unset fields are left
    untouched; pass an empty string to clear a text field."""

    fields: dict[str, str]
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        unknown = set(self.fields) - set(_FIELD_TO_DOCINFO_KEY)
        if unknown:
            raise OperationError(f"Unsupported metadata field(s): {sorted(unknown)}")

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if doc.working_path is None:
            raise OperationError("No document open.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        with open_pdf(doc.working_path) as pdf:
            for name, value in self.fields.items():
                docinfo_value = _to_pdf_date(value) if name in _DATE_FIELDS else value
                pdf.docinfo[_FIELD_TO_DOCINFO_KEY[name]] = docinfo_value
            pdf.save(out_path)

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "set_metadata",
            "fields": dict(self.fields),
        }

    def describe(self) -> str:
        return f"Set metadata: {', '.join(sorted(self.fields))}"


@dataclass
class RenameOperation(Operation):
    """Changes the session's output filename. No PDF content is
    touched, so undo is a precise inverse rather than a byte snapshot.

    `new_name=None` means "no display name" - only used internally by
    `invert()` to restore a session that had none set yet; user-facing
    callers (ToolPlugin.build_operation) must always supply a non-empty
    string.
    """

    new_name: str | None
    _previous_name: str | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if self.new_name is not None and not self.new_name.strip():
            raise OperationError("New name must not be empty.")
        self._previous_name = doc.display_name

        result = next_session(doc, doc.working_path)
        result.display_name = self.new_name
        return result

    def invert(self) -> Operation:
        # _previous_name is populated by apply().
        return RenameOperation(new_name=self._previous_name)

    def serialize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": "rename",
            "new_name": self.new_name,
        }

    def describe(self) -> str:
        return f"Renamed to '{self.new_name}'"


class SetMetadataPlugin(ToolPlugin):
    tool_id = "set_metadata"
    display_name = "Metadata"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            fields = kwargs["fields"]
        except KeyError as exc:
            raise OperationError("Metadata requires a 'fields' dict.") from exc
        return SetMetadataOperation(fields=dict(fields))

    def operation_class(self) -> type[Operation]:
        return SetMetadataOperation


class RenamePlugin(ToolPlugin):
    tool_id = "rename"
    display_name = "Rename"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            new_name = kwargs["new_name"]
        except KeyError as exc:
            raise OperationError("Rename requires a 'new_name'.") from exc
        return RenameOperation(new_name=new_name)

    def operation_class(self) -> type[Operation]:
        return RenameOperation
