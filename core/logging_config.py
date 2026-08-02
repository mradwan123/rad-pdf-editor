"""Structured, local-only logging setup shared by every module.

No module should call `print()` or configure its own logger — import
`get_logger` from here instead. Logs are written as JSON-lines to the
OS-appropriate application-data directory and double as the raw feed
for the audit trail (see core/session/audit_log.py, not yet built).

This never transmits logs over the network: the app makes no network
calls of any kind, by design (see SPEC.md section 1 and 6.4).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOG_SCHEMA_VERSION = 1


class JsonLinesFormatter(logging.Formatter):
    """Formats each log record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "schema_version": LOG_SCHEMA_VERSION,
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "context", None)
        if extra:
            payload["context"] = extra
        return json.dumps(payload, ensure_ascii=False)


def app_data_dir() -> Path:
    """OS-appropriate app-data directory. Never the user's working
    directory — avoids writing anything alongside confidential source
    documents (see SPEC.md section 6.4). Shared by logging, autosave,
    and the audit log.
    """
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Local" / "PDFEditor"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "PDFEditor"
    else:
        base = Path.home() / ".local" / "share" / "pdfeditor"
    base.mkdir(parents=True, exist_ok=True)
    return base


def configure_logging(level: int = logging.INFO) -> None:
    """Call once at application startup (GUI main() and CLI entry point)."""
    log_path = app_data_dir() / "app.log.jsonl"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(JsonLinesFormatter())

    root = logging.getLogger("pdfeditor")
    root.setLevel(level)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Every module gets its logger via this function:

        from core.logging_config import get_logger
        log = get_logger(__name__)
    """
    return logging.getLogger(f"pdfeditor.{name}")
