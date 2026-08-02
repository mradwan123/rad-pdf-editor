"""Protect and Unlock operations (SPEC.md Phase 1 list).

Protect adds password encryption to the working document; Unlock
removes it, given the correct current password. Both need the
password itself to invert (there's no way to re-derive it from a byte
snapshot alone without prompting the user again), so - unlike the rest
of core/ops - these use a precise `invert()` instead of the generic
snapshot-restore fallback, storing only the bytes actually needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pikepdf

from core.errors import OperationError, SecurityError
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


@dataclass
class ProtectOperation(Operation):
    """Encrypts the working document with `user_password` (required to
    open) and `owner_password` (required to change permissions;
    defaults to `user_password` if not given)."""

    user_password: str
    owner_password: str | None = None
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if doc.working_path is None:
            raise OperationError("No document open.")
        if not self.user_password:
            raise OperationError("Protect requires a non-empty user_password.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        try:
            with pikepdf.Pdf.open(doc.working_path) as pdf:
                pdf.save(
                    out_path,
                    encryption=pikepdf.Encryption(
                        user=self.user_password,
                        owner=self.owner_password or self.user_password,
                    ),
                )
        except pikepdf.PdfError as exc:
            raise SecurityError(f"Could not encrypt document: {exc}") from exc

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        # Deliberately excludes the passwords - the audit log/autosave
        # journal must not persist secrets in plaintext (SPEC.md
        # section 1, "confidential/regulated docs").
        return {"schema_version": self.schema_version, "type": "protect"}

    def describe(self) -> str:
        return "Added password protection"


@dataclass
class UnlockOperation(Operation):
    """Removes password encryption, given the correct current password."""

    password: str
    _pre_snapshot: bytes | None = field(default=None, init=False, repr=False)

    def apply(self, doc: DocumentSession) -> DocumentSession:
        if doc.working_path is None:
            raise OperationError("No document open.")
        self._pre_snapshot = read_working_bytes(doc)

        out_path = allocate_working_path(doc)
        try:
            with pikepdf.Pdf.open(doc.working_path, password=self.password) as pdf:
                pdf.save(out_path, encryption=False)
        except pikepdf.PasswordError as exc:
            raise SecurityError("Incorrect password.") from exc
        except pikepdf.PdfError as exc:
            raise SecurityError(f"Could not decrypt document: {exc}") from exc

        return next_session(doc, out_path)

    def invert(self) -> Operation:
        return snapshot_restore_invert(self._pre_snapshot, self.describe())

    def serialize(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "type": "unlock"}

    def describe(self) -> str:
        return "Removed password protection"


class ProtectPlugin(ToolPlugin):
    tool_id = "protect"
    display_name = "Protect (Add Password)"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            user_password = kwargs["user_password"]
        except KeyError as exc:
            raise OperationError("Protect requires a 'user_password'.") from exc
        return ProtectOperation(
            user_password=user_password, owner_password=kwargs.get("owner_password")
        )

    def operation_class(self) -> type[Operation]:
        return ProtectOperation


class UnlockPlugin(ToolPlugin):
    tool_id = "unlock"
    display_name = "Unlock (Remove Password)"
    compatible_core_version = CORE_VERSION_RANGE

    def build_operation(self, **kwargs: Any) -> Operation:
        try:
            password = kwargs["password"]
        except KeyError as exc:
            raise OperationError("Unlock requires the current 'password'.") from exc
        return UnlockOperation(password=password)

    def operation_class(self) -> type[Operation]:
        return UnlockOperation
