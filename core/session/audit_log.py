"""Append-only local audit trail (SPEC.md section 2: "the operation log
*is* the audit trail; each entry gets a timestamp and description").

JSONL format, per SPEC.md section 5's leaning ("JSONL for simplicity +
easy diffing"). Stored under `core.logging_config.app_data_dir()` by
default - local-only, never transmitted (SPEC.md section 1).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.logging_config import app_data_dir, get_logger
from core.model.operation import Operation

log = get_logger(__name__)

AUDIT_LOG_SCHEMA_VERSION = 1


class AuditLog:
    """Appends one JSON line per recorded operation. Never overwrites
    or truncates - only ever opened in append mode, so a corrupted or
    partially-written entry can't erase prior history."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else app_data_dir() / "audit_log.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_operation(self, operation: Operation, *, document_label: str | None = None) -> None:
        entry = {
            "schema_version": AUDIT_LOG_SCHEMA_VERSION,
            "timestamp": datetime.now(UTC).isoformat(),
            "description": operation.describe(),
            "operation": operation.serialize(),
            "document": document_label,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.info("Audit log entry recorded", extra={"context": entry["description"]})

    def read_all(self) -> list[dict[str, Any]]:
        """Read back every recorded entry, in order. Mainly for tests
        and a future GUI audit-trail viewer."""
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries
