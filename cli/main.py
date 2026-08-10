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
from core.session.workflow_store import WorkflowStore

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

    crop = sub.add_parser("crop", help="Trim margins off pages")
    add_single_input(crop)
    crop.add_argument("--margin-top", type=float, default=0.0)
    crop.add_argument("--margin-right", type=float, default=0.0)
    crop.add_argument("--margin-bottom", type=float, default=0.0)
    crop.add_argument("--margin-left", type=float, default=0.0)
    crop.add_argument("--pages", default="", help="comma-separated 1-indexed pages; default: all")

    resize = sub.add_parser("resize", help="Scale pages to a target size in points")
    add_single_input(resize)
    resize.add_argument("--width", type=float, required=True)
    resize.add_argument("--height", type=float, required=True)
    resize.add_argument("--pages", default="", help="comma-separated 1-indexed pages; default: all")

    n_up = sub.add_parser("n_up", help="Combine several pages per output sheet")
    add_single_input(n_up)
    n_up.add_argument("--pages-per-sheet", type=int, required=True)
    n_up.add_argument("--sheet-width", type=float, default=612.0)
    n_up.add_argument("--sheet-height", type=float, default=792.0)

    grayscale = sub.add_parser("grayscale", help="Convert pages to grayscale (rasterizes them)")
    add_single_input(grayscale)
    grayscale.add_argument("--pages", default="", help="comma-separated 1-indexed pages; default: all")
    grayscale.add_argument("--dpi", type=int, default=200)

    flip = sub.add_parser("flip", help="Mirror pages horizontally or vertically")
    add_single_input(flip)
    flip.add_argument("--direction", required=True, choices=["horizontal", "vertical"])
    flip.add_argument("--pages", default="", help="comma-separated 1-indexed pages; default: all")

    header_footer = sub.add_parser("header_footer", help="Stamp header/footer text on every page")
    add_single_input(header_footer)
    header_footer.add_argument("--header-text", default="")
    header_footer.add_argument("--footer-text", default="")
    header_footer.add_argument("--font-size", type=int, default=10)
    header_footer.add_argument(
        "--pages", default="", help="comma-separated 1-indexed pages; default: all"
    )

    bates = sub.add_parser("bates_numbering", help="Stamp sequential page numbers")
    add_single_input(bates)
    bates.add_argument("--prefix", default="")
    bates.add_argument("--start", type=int, default=1)
    bates.add_argument("--digits", type=int, default=5)
    bates.add_argument(
        "--position",
        default="bottom-right",
        choices=["bottom-right", "bottom-left", "bottom-center", "top-right", "top-left"],
    )
    bates.add_argument("--font-size", type=int, default=10)
    bates.add_argument("--pages", default="", help="comma-separated 1-indexed pages; default: all")

    flatten = sub.add_parser("flatten", help="Bake annotation appearances into page content")
    add_single_input(flatten)
    flatten.add_argument("--pages", default="", help="comma-separated 1-indexed pages; default: all")

    remove_annotations = sub.add_parser("remove_annotations", help="Remove annotations")
    add_single_input(remove_annotations)
    remove_annotations.add_argument(
        "--pages", default="", help="comma-separated 1-indexed pages; default: all"
    )
    remove_annotations.add_argument(
        "--subtypes", default="", help="comma-separated subtypes to remove; default: all"
    )

    fill_form = sub.add_parser("fill_form", help="Set AcroForm field values")
    add_single_input(fill_form)
    fill_form.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="repeatable, e.g. --field name=Jane --field date=2025-06-03",
    )

    sign = sub.add_parser("sign", help="Place a signature/initials image on a page")
    add_single_input(sign)
    sign.add_argument("--image", type=Path, required=True, help="signature image file")
    sign.add_argument("--page", type=int, required=True, help="1-indexed target page")
    sign.add_argument(
        "--rect",
        required=True,
        help="x0,y0,x1,y1 in points, PDF-native origin bottom-left, e.g. 50,50,250,130",
    )

    create_form_field = sub.add_parser(
        "create_form_field", help="Add a new text/checkbox/radio AcroForm field"
    )
    add_single_input(create_form_field)
    create_form_field.add_argument("--page", type=int, required=True, help="1-indexed target page")
    create_form_field.add_argument("--field-name", required=True)
    create_form_field.add_argument(
        "--field-type", required=True, choices=["text", "checkbox", "radio"]
    )
    create_form_field.add_argument(
        "--rect",
        required=True,
        help="x0,y0,x1,y1 in points, PDF-native origin bottom-left, e.g. 50,300,250,320",
    )
    create_form_field.add_argument(
        "--default-value", default="", help="initial text (--field-type text only)"
    )
    create_form_field.add_argument(
        "--checked",
        action="store_true",
        help="initial checked state (--field-type checkbox/radio only)",
    )

    pdf_to_docx = sub.add_parser("pdf_to_docx", help="Convert the current document to Word (.docx)")
    add_single_input(pdf_to_docx)

    pdf_to_pptx = sub.add_parser(
        "pdf_to_pptx", help="Convert the current document to PowerPoint (.pptx)"
    )
    add_single_input(pdf_to_pptx)
    pdf_to_pptx.add_argument("--dpi", type=int, default=150, help="fallback-path image quality")

    pdf_to_xlsx = sub.add_parser(
        "pdf_to_xlsx", help="Extract tables from the current document into Excel (.xlsx)"
    )
    add_single_input(pdf_to_xlsx)

    pdf_to_html = sub.add_parser(
        "pdf_to_html", help="Export the current document's text as HTML"
    )
    add_single_input(pdf_to_html)

    pdf_to_jpg = sub.add_parser("pdf_to_jpg", help="Render one page to a JPEG")
    add_single_input(pdf_to_jpg)
    pdf_to_jpg.add_argument("--page", type=int, required=True, help="1-indexed page to render")
    pdf_to_jpg.add_argument("--dpi", type=int, default=200)

    docx_to_pdf = sub.add_parser("docx_to_pdf", help="Convert a Word document to PDF")
    docx_to_pdf.add_argument("source", type=Path, help="source .docx file")
    docx_to_pdf.add_argument("-o", "--output", type=Path, required=True)

    pptx_to_pdf = sub.add_parser("pptx_to_pdf", help="Convert a PowerPoint file to PDF")
    pptx_to_pdf.add_argument("source", type=Path, help="source .pptx file")
    pptx_to_pdf.add_argument("-o", "--output", type=Path, required=True)

    xlsx_to_pdf = sub.add_parser("xlsx_to_pdf", help="Convert an Excel workbook to PDF")
    xlsx_to_pdf.add_argument("source", type=Path, help="source .xlsx file")
    xlsx_to_pdf.add_argument("-o", "--output", type=Path, required=True)

    html_to_pdf = sub.add_parser("html_to_pdf", help="Convert an HTML file to PDF")
    html_to_pdf.add_argument("source", type=Path, help="source .html file")
    html_to_pdf.add_argument("-o", "--output", type=Path, required=True)

    jpg_to_pdf = sub.add_parser("jpg_to_pdf", help="Combine images into a PDF, one page each")
    jpg_to_pdf.add_argument("sources", type=Path, nargs="+", help="source images, in order")
    jpg_to_pdf.add_argument("-o", "--output", type=Path, required=True)

    ocr = sub.add_parser("ocr", help="Add a searchable text layer via OCR")
    add_single_input(ocr)
    ocr.add_argument("--language", default="eng", help="Tesseract language code")
    ocr.add_argument("--force-ocr", action="store_true", help="re-OCR pages that already have text")
    ocr.add_argument(
        "--no-skip-text",
        dest="skip_text",
        action="store_false",
        default=True,
        help="also OCR pages that already have text (default: skip them)",
    )

    deskew = sub.add_parser("deskew", help="Correct rotational skew on scanned pages")
    add_single_input(deskew)
    deskew.add_argument("--pages", default="", help="comma-separated 1-indexed pages; default: all")
    deskew.add_argument("--dpi", type=int, default=200)

    repair = sub.add_parser("repair", help="Recover a possibly-corrupt PDF")
    repair.add_argument("source", type=Path, help="source (possibly corrupt) PDF file")
    repair.add_argument("-o", "--output", type=Path, required=True)

    sub.add_parser("list-workflows", help="List saved Workflow names")

    run_workflow = sub.add_parser(
        "run-workflow", help="Run a saved Workflow against a new input file, unattended"
    )
    run_workflow.add_argument("name", help="saved workflow name")
    run_workflow.add_argument("input", type=Path, help="input PDF")
    run_workflow.add_argument("-o", "--output", type=Path, required=True)

    return parser


def _parse_int_list(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


def _parse_field_values(raw: list[str]) -> dict[str, str]:
    fields = {}
    for item in raw:
        name, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"--field must be NAME=VALUE, got '{item}'")
        fields[name] = value
    return fields


def _parse_rect(raw: str) -> tuple[float, float, float, float]:
    parts = [float(x) for x in raw.split(",")]
    if len(parts) != 4:
        raise ValueError(f"--rect must be x0,y0,x1,y1, got '{raw}'")
    return (parts[0], parts[1], parts[2], parts[3])


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
    if tool_id == "crop":
        return {
            "margin_top": args.margin_top,
            "margin_right": args.margin_right,
            "margin_bottom": args.margin_bottom,
            "margin_left": args.margin_left,
            "pages": _parse_int_list(args.pages),
        }
    if tool_id == "resize":
        return {"width": args.width, "height": args.height, "pages": _parse_int_list(args.pages)}
    if tool_id == "n_up":
        return {
            "pages_per_sheet": args.pages_per_sheet,
            "sheet_width": args.sheet_width,
            "sheet_height": args.sheet_height,
        }
    if tool_id == "grayscale":
        return {"pages": _parse_int_list(args.pages), "dpi": args.dpi}
    if tool_id == "flip":
        return {"direction": args.direction, "pages": _parse_int_list(args.pages)}
    if tool_id == "header_footer":
        return {
            "header_text": args.header_text,
            "footer_text": args.footer_text,
            "font_size": args.font_size,
            "pages": _parse_int_list(args.pages),
        }
    if tool_id == "bates_numbering":
        return {
            "prefix": args.prefix,
            "start": args.start,
            "digits": args.digits,
            "position": args.position,
            "font_size": args.font_size,
            "pages": _parse_int_list(args.pages),
        }
    if tool_id == "flatten":
        return {"pages": _parse_int_list(args.pages)}
    if tool_id == "remove_annotations":
        return {
            "pages": _parse_int_list(args.pages),
            "subtypes": [s.strip() for s in args.subtypes.split(",") if s.strip()],
        }
    if tool_id == "fill_form":
        return {"field_values": _parse_field_values(args.field)}
    if tool_id == "sign":
        return {"image_path": args.image, "page": args.page, "rect": _parse_rect(args.rect)}
    if tool_id == "create_form_field":
        return {
            "page": args.page,
            "field_name": args.field_name,
            "field_type": args.field_type,
            "rect": _parse_rect(args.rect),
            "default_value": args.default_value,
            "checked": args.checked,
        }
    if tool_id in ("pdf_to_docx", "pdf_to_xlsx", "pdf_to_html"):
        return {}
    if tool_id == "pdf_to_pptx":
        return {"dpi": args.dpi}
    if tool_id == "pdf_to_jpg":
        return {"page": args.page, "dpi": args.dpi}
    if tool_id in ("docx_to_pdf", "pptx_to_pdf", "xlsx_to_pdf", "html_to_pdf"):
        return {"source_path": args.source}
    if tool_id == "jpg_to_pdf":
        return {"sources": args.sources}
    if tool_id == "ocr":
        return {"language": args.language, "force_ocr": args.force_ocr, "skip_text": args.skip_text}
    if tool_id == "deskew":
        return {"pages": _parse_int_list(args.pages), "dpi": args.dpi}
    if tool_id == "repair":
        return {"source_path": args.source}
    raise AssertionError(f"unhandled tool_id: {tool_id}")  # unreachable - argparse validates choices


#: Tool ids whose source is one or more external files, not a PDF to
#: open as the working document - mirrors MergeOperation's shape
#: (core/ops/merge_split.py, core/ops/convert_to.py).
_EXTERNAL_SOURCE_TOOL_IDS = {
    "merge",
    "docx_to_pdf",
    "pptx_to_pdf",
    "xlsx_to_pdf",
    "html_to_pdf",
    "jpg_to_pdf",
    "repair",
}

def _run_list_workflows() -> int:
    names = WorkflowStore().list_workflows()
    if not names:
        print("No saved workflows.")
        return 0
    for name in names:
        print(name)
    return 0


def _run_workflow(args: argparse.Namespace, registry: Registry) -> int:
    with network_lockdown(), SessionTempDir() as session:
        try:
            pipeline = WorkflowStore().load(args.name, registry)

            working = session.path / f"working{args.input.suffix or '.pdf'}"
            shutil.copyfile(args.input, working)
            doc = DocumentSession(working_path=working, source_path=args.input)

            result = pipeline.run(doc)
            if result.working_path is None:
                print("Workflow produced no output document.", file=sys.stderr)
                return 1

            args.output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(result.working_path, args.output)
            audit_log = AuditLog()
            for operation in pipeline.operations:
                audit_log.record_operation(operation, document_label=str(args.output))
            print(
                f"Ran workflow '{args.name}' ({len(pipeline.operations)} step(s)) -> {args.output}"
            )
            return 0
        except (PDFEditorError, OSError) as exc:
            log.error("Workflow run failed: %s", exc)
            print(f"Error: {exc}", file=sys.stderr)
            return 1


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)

    registry = Registry()
    discover_and_load(registry)

    # "list-workflows"/"run-workflow" aren't ToolPlugins (they replay
    # a saved *sequence* of operations, not apply one), so they're
    # handled here rather than going through registry.get()/_build_kwargs.
    if args.tool_id == "list-workflows":
        return _run_list_workflows()
    if args.tool_id == "run-workflow":
        return _run_workflow(args, registry)

    plugin = registry.get(args.tool_id)

    with network_lockdown(), SessionTempDir() as session:
        try:
            if args.tool_id in _EXTERNAL_SOURCE_TOOL_IDS:
                # No document to open yet - the source is external
                # file(s), not a working PDF. `working_path` still
                # needs to point *somewhere inside the session dir* so
                # that `allocate_working_path`'s output (and, for the
                # conversion ops, the LibreOffice profile dir derived
                # from it) never falls back to the OS system temp dir -
                # this file need not exist, only its parent matters.
                # Same fix CLAUDE.md documents for the GUI's
                # AppController.apply_operation, applied here for the
                # CLI's equivalent gap.
                doc = DocumentSession(working_path=session.path / "placeholder.pdf", source_path=None)
            else:
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
        except (PDFEditorError, OSError, ValueError) as exc:
            # OSError covers a missing/unreadable --input or a
            # write-protected --output (shutil.copyfile); ValueError
            # covers malformed --pages/--field/--rect values (the
            # _parse_* helpers above). Both used to crash with a raw
            # Python traceback instead of a clean CLI error - found in
            # review, confirmed reproducible with a nonexistent input
            # path and with --pages "abc".
            log.error("CLI operation failed: %s", exc)
            print(f"Error: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
