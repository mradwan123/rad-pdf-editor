"""tool_id -> dialog class registry.

Split out of gui/main_window.py so that gui/dialogs/workflow_builder_dialog.py
(a `gui/dialogs/` module) can reuse the exact same mapping when it opens
a tool's real dialog for an "Add Step..." pick, without a circular
import: main_window.py already imports every dialog module, so a
dialog module importing back from main_window.py would cycle.

main_window.py's Tools menu loop, `_make_tool_handler`, and `_run_tool`
all import `TOOL_DIALOGS`/`DialogFactory` from here instead of defining
them inline.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QWidget

from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.bates_numbering_dialog import BatesNumberingDialog
from gui.dialogs.compress_dialog import CompressDialog
from gui.dialogs.create_form_field_dialog import CreateFormFieldDialog
from gui.dialogs.crop_dialog import CropDialog
from gui.dialogs.delete_pages_dialog import DeletePagesDialog
from gui.dialogs.deskew_dialog import DeskewDialog
from gui.dialogs.docx_to_pdf_dialog import DocxToPdfDialog
from gui.dialogs.extract_pages_dialog import ExtractPagesDialog
from gui.dialogs.fill_form_dialog import FillFormDialog
from gui.dialogs.flatten_dialog import FlattenDialog
from gui.dialogs.flip_dialog import FlipDialog
from gui.dialogs.grayscale_dialog import GrayscaleDialog
from gui.dialogs.header_footer_dialog import HeaderFooterDialog
from gui.dialogs.html_to_pdf_dialog import HtmlToPdfDialog
from gui.dialogs.jpg_to_pdf_dialog import JpgToPdfDialog
from gui.dialogs.merge_dialog import MergeDialog
from gui.dialogs.metadata_dialog import MetadataDialog
from gui.dialogs.n_up_dialog import NUpDialog
from gui.dialogs.ocr_dialog import OcrDialog
from gui.dialogs.pdf_to_docx_dialog import PdfToDocxDialog
from gui.dialogs.pdf_to_html_dialog import PdfToHtmlDialog
from gui.dialogs.pdf_to_jpg_dialog import PdfToJpgDialog
from gui.dialogs.pdf_to_pptx_dialog import PdfToPptxDialog
from gui.dialogs.pdf_to_xlsx_dialog import PdfToXlsxDialog
from gui.dialogs.pptx_to_pdf_dialog import PptxToPdfDialog
from gui.dialogs.protect_dialog import ProtectDialog
from gui.dialogs.remove_annotations_dialog import RemoveAnnotationsDialog
from gui.dialogs.rename_dialog import RenameDialog
from gui.dialogs.reorder_pages_dialog import ReorderPagesDialog
from gui.dialogs.repair_dialog import RepairDialog
from gui.dialogs.resize_dialog import ResizeDialog
from gui.dialogs.rotate_dialog import RotateDialog
from gui.dialogs.sign_dialog import SignDialog
from gui.dialogs.unlock_dialog import UnlockDialog
from gui.dialogs.watermark_dialog import WatermarkDialog
from gui.dialogs.xlsx_to_pdf_dialog import XlsxToPdfDialog

#: Every concrete BaseToolDialog subclass takes (parent=None), a
#: different signature than BaseToolDialog.__init__'s own (title,
#: parent) - so the factory type is this Callable, not type[BaseToolDialog].
DialogFactory = Callable[[QWidget | None], BaseToolDialog]

#: tool_id -> dialog class, drives MainWindow's Tools menu generically
#: instead of one hand-written branch per tool - and, since this is now
#: shared, WorkflowBuilderDialog's "Add Step..." picker opens the exact
#: same dialog every other feature uses for a given tool_id.
TOOL_DIALOGS: dict[str, DialogFactory] = {
    "merge": MergeDialog,
    "extract_pages": ExtractPagesDialog,
    "reorder_pages": ReorderPagesDialog,
    "rotate_pages": RotateDialog,
    "delete_pages": DeletePagesDialog,
    "compress": CompressDialog,
    "set_metadata": MetadataDialog,
    "rename": RenameDialog,
    "protect": ProtectDialog,
    "unlock": UnlockDialog,
    "watermark": WatermarkDialog,
    "crop": CropDialog,
    "resize": ResizeDialog,
    "n_up": NUpDialog,
    "grayscale": GrayscaleDialog,
    "header_footer": HeaderFooterDialog,
    "bates_numbering": BatesNumberingDialog,
    "flatten": FlattenDialog,
    "remove_annotations": RemoveAnnotationsDialog,
    # SignDialog's __init__ takes an optional second argument, the
    # working path of the document being signed - given one it shows an
    # interactive placement canvas instead of numbers alone. Called
    # through this factory (Workflow builder: no live document) it gets
    # only a parent and stays numeric-only, which is exactly right for
    # configuring a step against no particular file. MainWindow._run_tool
    # special-cases "sign" to pass the path when there is one.
    "sign": SignDialog,
    "create_form_field": CreateFormFieldDialog,
    # FillFormDialog's __init__ takes (field_names, parent), not just
    # (parent) - it needs the open document's actual AcroForm field
    # names before it can lay out its inputs. MainWindow._run_tool
    # special-cases tool_id == "fill_form" and never actually calls
    # this factory; it's here only so the Tools menu loop (which needs
    # *a* callable matching DialogFactory for every tool_id) has an
    # entry to iterate. WorkflowBuilderDialog's step picker excludes
    # "fill_form" outright for the same reason (no live document to
    # source field names from at workflow-build time).
    "fill_form": lambda parent: FillFormDialog([], parent),
    "flip": FlipDialog,
    "pdf_to_docx": PdfToDocxDialog,
    "pdf_to_pptx": PdfToPptxDialog,
    "pdf_to_xlsx": PdfToXlsxDialog,
    "pdf_to_html": PdfToHtmlDialog,
    "pdf_to_jpg": PdfToJpgDialog,
    "docx_to_pdf": DocxToPdfDialog,
    "pptx_to_pdf": PptxToPdfDialog,
    "xlsx_to_pdf": XlsxToPdfDialog,
    "html_to_pdf": HtmlToPdfDialog,
    "jpg_to_pdf": JpgToPdfDialog,
    "ocr": OcrDialog,
    "deskew": DeskewDialog,
    "repair": RepairDialog,
}
