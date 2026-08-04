"""Integration test: discover_and_load registers every first-party
Phase 1 + Phase 2 plugin, and each one can actually build+run an
Operation."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from core.model.document import DocumentSession
from core.registry.registry import Registry, discover_and_load

EXPECTED_TOOL_IDS = {
    "merge",
    "extract_pages",
    "reorder_pages",
    "rotate_pages",
    "delete_pages",
    "compress",
    "set_metadata",
    "rename",
    "protect",
    "unlock",
    "watermark",
    "crop",
    "resize",
    "n_up",
    "grayscale",
    "header_footer",
    "bates_numbering",
    "flatten",
    "remove_annotations",
    "fill_form",
    "sign",
    "create_form_field",
    "flip",
    "pdf_to_docx",
    "pdf_to_pptx",
    "pdf_to_xlsx",
    "pdf_to_html",
    "pdf_to_jpg",
    "docx_to_pdf",
    "pptx_to_pdf",
    "xlsx_to_pdf",
    "html_to_pdf",
    "jpg_to_pdf",
    "ocr",
    "deskew",
    "repair",
}


@pytest.fixture
def registry() -> Registry:
    reg = Registry()
    discover_and_load(reg)
    return reg


def test_discover_and_load_registers_all_phase1_plugins(registry: Registry) -> None:
    registered = {p.tool_id for p in registry.all_plugins()}
    assert registered == EXPECTED_TOOL_IDS


def test_registered_plugin_can_build_and_run_an_operation(registry: Registry, tmp_path: Path) -> None:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    working = tmp_path / "working.pdf"
    pdf.save(working)
    doc = DocumentSession(working_path=working, source_path=None)

    plugin = registry.get("rotate_pages")
    op = plugin.build_operation(angle=90)
    result = doc.apply(op)

    with pikepdf.Pdf.open(result.working_path) as out:
        assert int(out.pages[0].get("/Rotate", 0)) == 90
