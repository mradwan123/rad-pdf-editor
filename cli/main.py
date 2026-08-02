"""Minimal scripting entry point over core/ - see SPEC.md's /cli row
("scripting entry point, reuses /core directly").

Each subcommand maps 1:1 to a registered ToolPlugin.tool_id. Working
copies live in a private per-invocation session temp directory under
the app-data dir (core/session/session_dir.py) - never the user's own
files or working directory - and are securely wiped on exit
(core/security/secure_delete.py), not just deleted (SPEC.md section 1
and 6.4). Outbound networking is blocked for the duration of the
operation (core/security/sandbox.py), defense in depth on top of this
codebase simply never calling out. Every successful run is appended to
the local audit trail (core/session/audit_log.py).

Usage:
    python -m cli.main merge a.pdf b.pdf -o merged.pdf
    python -m cli.main rotate_pages in.pdf -o out.pdf --angle 90
    python -m cli.main watermark in.pdf -o out.pdf --text CONFIDENTIAL
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from core.errors import PDFEditorError
from core.logging_config import configure_logging, get_logger
from core.model.document import DocumentSession
from core.registry.registry import Registry, discover_and_load
from core.security.sandbox import network_lockdown
from core.session.audit_log import AuditLog
from core.session.session_dir import SessionTempDir

log = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdf-editor", description="Local, offline PDF editing CLI.")
    sub = parser.add_subparsers(dest="tool_id", required=True)

    def add_single_input(p: argparse.ArgumentParser) -> None:
        p.add_argument("input", type=Path, help="input PDF")
        p.add_argument("-o", "--output", type=Path, required=True, help="output PDF path")

    merge = sub.add_parser("merge", help="Combine several PDFs into one")
    merge.add_argument("inputs", type=Path, nargs="+", help="source PDFs, in order")
    merge.add_argument("-o", "--output", type=Path, required=True)

    extract = sub.add_parser("extract_pages", help="Keep only the given pages")
    add_single_input(extract)
    extract.add_argument("--pages", required=True, help="comma-separated 1-indexed pages, e.g. 1,3,5")

    reorder = sub.add_parser("reorder_pages", help="Reorder all pages")
    add_single_input(reorder)
    reorder.add_argument(
        "--page-order", required=True, help="comma-separated full permutation, e.g. 3,1,2"
    )

    rotate = sub.add_parser("rotate_pages", help="Rotate pages by a multiple of 90 degrees")
    add_single_input(rotate)
    rotate.add_argument("--angle", type=int, required=True)
    rotate.add_argument("--pages", default="", help="comma-separated 1-indexed pages; default: all")

    delete = sub.add_parser("delete_pages", help="Remove pages")
    add_single_input(delete)
    delete.add_argument("--pages", required=True, help="comma-separated 1-indexed pages")

    compress = sub.add_parser("compress", help="Optimize PDF stream/object structure")
    add_single_input(compress)

    metadata = sub.add_parser("set_metadata", help="Set document-info fields")
    add_single_input(metadata)
    metadata.add_argument("--title")
    metadata.add_argument("--author")
    metadata.add_argument("--subject")
    metadata.add_argument("--keywords")
    metadata.add_argument(
        "--creation-date", help="ISO 8601, e.g. 2025-06-03T12:00:00+00:00 or 2025-06-03"
    )
    metadata.add_argument(
        "--mod-date", help="ISO 8601, e.g. 2025-06-03T12:00:00+00:00 or 2025-06-03"
    )

    rename = sub.add_parser("rename", help="Set the session's output filename")
    add_single_input(rename)
    rename.add_argument("--name", required=True)

    protect = sub.add_parser("protect", help="Add password encryption")
    add_single_input(protect)
    protect.add_argument("--user-password", required=True)
    protect.add_argument("--owner-password")

    unlock = sub.add_parser("unlock", help="Remove password encryption")
    add_single_input(unlock)
    unlock.add_argument("--password", required=True)

    watermark = sub.add_parser("watermark", help="Stamp diagonal text on every page")
    add_single_input(watermark)
    watermark.add_argument("--text", required=True)
    watermark.add_argument("--opacity", type=float, default=0.3)
    watermark.add_argument("--font-size", type=int, default=40)

    return parser


def _parse_int_list(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


def _build_kwargs(args: argparse.Namespace) -> dict[str, object]:
    tool_id: str = args.tool_id
    if tool_id == "merge":
        return {"sources": args.inputs}
    if tool_id == "extract_pages":
        return {"pages": _parse_int_list(args.pages)}
    if tool_id == "reorder_pages":
        return {"page_order": _parse_int_list(args.page_order)}
    if tool_id == "rotate_pages":
        return {"angle": args.angle, "pages": _parse_int_list(args.pages)}
    if tool_id == "delete_pages":
        return {"pages": _parse_int_list(args.pages)}
    if tool_id == "compress":
        return {}
    if tool_id == "set_metadata":
        fields = {
            name: value
            for name, value in {
                "title": args.title,
                "author": args.author,
                "subject": args.subject,
                "keywords": args.keywords,
                "creation_date": args.creation_date,
                "mod_date": args.mod_date,
            }.items()
            if value is not None
        }
        return {"fields": fields}
    if tool_id == "rename":
        return {"new_name": args.name}
    if tool_id == "protect":
        return {"user_password": args.user_password, "owner_password": args.owner_password}
    if tool_id == "unlock":
        return {"password": args.password}
    if tool_id == "watermark":
        return {"text": args.text, "opacity": args.opacity, "font_size": args.font_size}
    raise AssertionError(f"unhandled tool_id: {tool_id}")  # unreachable - argparse validates choices


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)

    registry = Registry()
    discover_and_load(registry)
    plugin = registry.get(args.tool_id)

    with network_lockdown(), SessionTempDir() as session:
        try:
            doc = DocumentSession(working_path=None, source_path=None)
            if args.tool_id != "merge":
                working = session.path / f"working{args.input.suffix or '.pdf'}"
                shutil.copyfile(args.input, working)
                doc = DocumentSession(working_path=working, source_path=args.input)

            kwargs = _build_kwargs(args)
            operation = plugin.build_operation(**kwargs)
            result = doc.apply(operation)

            if result.working_path is None:
                print("Operation produced no output document.", file=sys.stderr)
                return 1

            args.output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(result.working_path, args.output)
            AuditLog().record_operation(operation, document_label=str(args.output))
            print(f"{operation.describe()} -> {args.output}")
            return 0
        except PDFEditorError as exc:
            log.error("CLI operation failed: %s", exc)
            print(f"Error: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
