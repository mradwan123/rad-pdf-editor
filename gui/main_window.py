"""Main window: multi-document tabs, thumbnail grids, Tools menu,
undo/redo (SPEC.md section 2, Phase 1 scope: "basic thumbnail UI +
undo/redo wired to the framework").

Each tab is an independently editable document backed by its own
`AppController` (gui/document_tab.py) - own session temp dir, undo/redo
stack and dirty flag. Everything in here that used to act on "the"
document now acts on the *current tab*: `self.controller` and
`self.thumbnail_list` are read-only views onto whichever tab is
active, and are None when no tab is open.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import fitz
from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.document_info import DocumentInfo, read_document_info
from core.errors import OperationError, PDFEditorError
from core.logging_config import get_logger
from core.model.document import DocumentSession
from core.ops.forms import list_form_field_names
from core.registry.registry import Registry, discover_and_load
from core.session.audit_log import AuditLog
from core.session.autosave import (
    discard_active_session,
    mark_active_session,
    recover_active_session,
)
from core.session.recent_files import RecentFiles
from core.session.session_dir import SessionTempDir
from core.session.workflow_store import WorkflowStore
from gui.controller import AppController
from gui.dialogs.base_tool_dialog import BaseToolDialog
from gui.dialogs.fill_form_dialog import FillFormDialog
from gui.dialogs.properties_dialog import PropertiesDialog
from gui.dialogs.run_workflow_dialog import RunWorkflowDialog
from gui.dialogs.sign_dialog import SignDialog
from gui.dialogs.tab_placement_dialog import (
    PLACEMENT_NEW_TAB,
    PLACEMENT_REPLACE_CURRENT,
    TabPlacementDialog,
)
from gui.dialogs.tool_dialog_registry import TOOL_DIALOGS, DialogFactory
from gui.dialogs.workflow_builder_dialog import WorkflowBuilderDialog
from gui.document_tab import DocumentTab
from gui.resources import build_logo_pixmap

log = get_logger(__name__)

_APP_NAME = "Rad PDF Editor"

#: PDF -> external-format conversions, mapped to the extension and
#: file-dialog filter of what they produce. These are *exports*: the
#: result is a file the user keeps, not a new editing state for the
#: open tab, so `_run_tool` routes them to `_export_document` instead
#: of `AppController.apply_operation`. Applying one to a tab replaced
#: its PDF with a file the thumbnail grid cannot render - a blank
#: window, and the converted file wiped with the session. The
#: operations themselves are unchanged: their PDF-in/file-out shape is
#: correct for the CLI and for Workflow steps.
_EXPORT_TOOLS: dict[str, tuple[str, str]] = {
    "pdf_to_docx": (".docx", "Word document (*.docx)"),
    "pdf_to_pptx": (".pptx", "PowerPoint presentation (*.pptx)"),
    "pdf_to_xlsx": (".xlsx", "Excel workbook (*.xlsx)"),
    "pdf_to_html": (".html", "HTML page (*.html)"),
    "pdf_to_jpg": (".jpg", "JPEG image (*.jpg)"),
}
_THUMBNAIL_SIZE = QSize(120, 160)
# View > Thumbnail zoom: width-driven (height is derived from
# _THUMBNAIL_SIZE's own aspect ratio, recomputed from the *original*
# width/height each time rather than compounded step-over-step, so
# repeated zooming can't drift the aspect ratio away from the source).
# Window-level, not per-tab: it's a property of how this window
# displays pages, not of any one document.
_THUMBNAIL_ZOOM_MIN_WIDTH = 60
# 3x the original 240px max, so users can zoom in on fine page detail
# (fine print, small diagrams) rather than only seeing more pages at
# once. QPdfDocument.render() (see _render_thumbnails) always
# rasterizes directly at the requested QSize - there's no fixed
# intermediate-resolution cache to outrun - so this stays genuinely
# sharp at 720px, confirmed by hand and by
# test_view_menu_zoom_in_out_and_reset_resize_the_icon_and_rerender's
# QIcon.actualSize() check plus a visual grab() spot-check.
_THUMBNAIL_ZOOM_MAX_WIDTH = 720
_THUMBNAIL_ZOOM_STEP = 20

#: Prefix marking a tab whose document has unsaved changes.
_DIRTY_TAB_MARKER = "• "


def _render_page_with_fitz(src: fitz.Document, index: int, size: QSize) -> QImage:
    """Render one page to a QImage of at most `size` via PyMuPDF, for
    documents QtPdf refuses to load (see
    `MainWindow._render_thumbnails`).

    Scaled down to the thumbnail size rather than returned at the
    page's natural size: the caller composites the result at (0, 0) on
    a thumbnail-sized canvas, so a full-resolution page would be
    cropped to its top-left corner instead of shown. Aspect ratio is
    kept, leaving the canvas's white showing around a page whose
    proportions differ from the thumbnail's.

    The QImage is `.copy()`d because constructing one over `samples`
    does not take ownership of the buffer - the Pixmap owns it, and it
    dies with this call, leaving the QImage pointing at freed memory.
    """
    page = src[index]
    page_rect = page.rect
    zoom = min(size.width() / page_rect.width, size.height() / page_rect.height)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = QImage(
        pixmap.samples,
        pixmap.width,
        pixmap.height,
        pixmap.stride,
        QImage.Format.Format_RGB888,
    )
    return image.copy()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_APP_NAME)
        self.resize(900, 700)

        # App-wide, shared by every tab's AppController: one plugin
        # scan and one append-only audit trail for the whole process,
        # rather than one of each per open document.
        self.registry = Registry()
        discover_and_load(self.registry)
        self.audit_log = AuditLog()
        self.recent_files = RecentFiles()

        # Mutable, unlike _THUMBNAIL_SIZE (the fixed default the View >
        # Reset Zoom action returns to) - View > Zoom In/Out reassigns
        # this and re-renders thumbnails at the new size.
        self.thumbnail_size = QSize(_THUMBNAIL_SIZE)

        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)  # drag-to-reorder tabs
        self.tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tab_widget.currentChanged.connect(self._on_current_tab_changed)
        tab_bar = self.tab_widget.tabBar()
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._show_tab_context_menu)

        self.empty_state = self._build_empty_state()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty_state)
        self.stack.addWidget(self.tab_widget)
        self.setCentralWidget(self.stack)

        # Set by _render_tab; read by _refresh to say so in the status
        # bar rather than showing an unexplained empty grid.
        self._thumbnails_failed = False

        self.tool_actions: dict[str, QAction] = {}
        self._build_actions()
        self._refresh()

    # --- current-tab views ------------------------------------------------
    #
    # Read-only conveniences so the rest of this class (and the tests)
    # can say "the document being edited" without repeating the
    # tab lookup. All three are None when no tab is open - every caller
    # has to handle that, which is exactly the state the empty-state
    # welcome screen represents.

    @property
    def current_tab(self) -> DocumentTab | None:
        widget = self.tab_widget.currentWidget()
        return widget if isinstance(widget, DocumentTab) else None

    @property
    def controller(self) -> AppController | None:
        tab = self.current_tab
        return tab.controller if tab is not None else None

    @property
    def thumbnail_list(self) -> QListWidget | None:
        tab = self.current_tab
        return tab.thumbnail_list if tab is not None else None

    def tabs(self) -> list[DocumentTab]:
        """Every open tab, in tab-bar order (which the user can change
        by dragging - this always reflects the current visual order)."""
        widgets = (self.tab_widget.widget(i) for i in range(self.tab_widget.count()))
        return [w for w in widgets if isinstance(w, DocumentTab)]

    def _build_empty_state(self) -> QWidget:
        """Branded welcome screen shown in place of the tab area when
        no document is open."""
        widget = QWidget()
        widget.setObjectName("emptyState")

        logo_label = QLabel()
        logo_label.setPixmap(build_logo_pixmap(128))
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_label = QLabel(_APP_NAME)
        title_label.setObjectName("emptyStateTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle_label = QLabel(self.tr("Open a PDF to get started"))
        subtitle_label.setObjectName("emptyStateSubtitle")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        open_button = QPushButton(self.tr("Open PDF..."))
        open_button.clicked.connect(self._open_document)

        layout = QVBoxLayout(widget)
        layout.addStretch(1)
        layout.addWidget(logo_label)
        layout.addSpacing(12)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        layout.addSpacing(16)
        layout.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        return widget

    # --- action / menu / toolbar setup ----------------------------------

    def _build_actions(self) -> None:
        self.open_action = QAction(self.tr("&Open..."), self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(self._open_document)

        # Lambdas, not bare bound methods, for every slot whose first
        # parameter is optional: QAction.triggered carries a `checked`
        # bool, which PySide6 would happily bind to `_save_as(tab=...)`
        # / `_close_other_tabs(index=...)` as a positional argument.
        self.save_as_action = QAction(self.tr("&Save As..."), self)
        self.save_as_action.setShortcut("Ctrl+S")
        self.save_as_action.triggered.connect(lambda: self._save_as())

        # Ctrl+D is Acrobat's binding for Document Properties. A plain
        # QAction, not a tool: it reports, it never touches an
        # Operation or the undo stack - same reasoning as the View
        # menu's items (see _build_view_menu).
        self.properties_action = QAction(self.tr("Propert&ies..."), self)
        self.properties_action.setShortcut("Ctrl+D")
        self.properties_action.triggered.connect(self._show_properties)

        self.close_action = QAction(self.tr("&Close Tab"), self)
        self.close_action.setShortcut("Ctrl+W")
        self.close_action.triggered.connect(self._close_document)

        self.close_other_tabs_action = QAction(self.tr("Close Ot&her Tabs"), self)
        self.close_other_tabs_action.triggered.connect(lambda: self._close_other_tabs())

        self.close_all_tabs_action = QAction(self.tr("Close &All Tabs"), self)
        self.close_all_tabs_action.triggered.connect(lambda: self._close_all_tabs())

        # QTabWidget has no built-in tab cycling - these are wired
        # explicitly (and live in the File menu so their shortcuts are
        # actually registered with the window, not just declared).
        self.next_tab_action = QAction(self.tr("&Next Tab"), self)
        self.next_tab_action.setShortcut("Ctrl+Tab")
        self.next_tab_action.triggered.connect(lambda: self._cycle_tab(1))

        self.previous_tab_action = QAction(self.tr("&Previous Tab"), self)
        self.previous_tab_action.setShortcut("Ctrl+Shift+Tab")
        self.previous_tab_action.triggered.connect(lambda: self._cycle_tab(-1))

        self.undo_action = QAction(self.tr("&Undo"), self)
        self.undo_action.setShortcut("Ctrl+Z")
        self.undo_action.triggered.connect(self._undo)

        self.redo_action = QAction(self.tr("&Redo"), self)
        self.redo_action.setShortcut("Ctrl+Shift+Z")
        self.redo_action.triggered.connect(self._redo)

        file_menu = self.menuBar().addMenu(self.tr("&File"))
        file_menu.addAction(self.open_action)
        self.recent_files_menu = file_menu.addMenu(self.tr("Open &Recent"))
        self.recent_files_menu.aboutToShow.connect(self._populate_recent_files_menu)
        file_menu.addAction(self.save_as_action)
        # Its own separator group: a document-info item, not one of the
        # tab-management items below it.
        file_menu.addSeparator()
        file_menu.addAction(self.properties_action)
        file_menu.addSeparator()
        file_menu.addAction(self.close_action)
        file_menu.addAction(self.close_other_tabs_action)
        file_menu.addAction(self.close_all_tabs_action)
        file_menu.addSeparator()
        file_menu.addAction(self.next_tab_action)
        file_menu.addAction(self.previous_tab_action)

        edit_menu = self.menuBar().addMenu(self.tr("&Edit"))
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)

        tools_menu = self.menuBar().addMenu(self.tr("&Tools"))
        #: (submenu label, ordered tool_ids) - grouped so the Tools menu
        #: doesn't grow into one flat 30+-item list as new tool_ids are
        #: added; every TOOL_DIALOGS key must appear in exactly one
        #: group (checked below) so a forgotten category can't silently
        #: drop a tool from the menu.
        tool_categories: list[tuple[str, list[str]]] = [
            (
                self.tr("&Organize Pages"),
                [
                    "merge",
                    "extract_pages",
                    "reorder_pages",
                    "rotate_pages",
                    "delete_pages",
                    "flip",
                ],
            ),
            (
                self.tr("&Edit and Design"),
                [
                    "crop",
                    "resize",
                    "n_up",
                    "grayscale",
                    "watermark",
                    "header_footer",
                    "bates_numbering",
                ],
            ),
            (
                self.tr("F&orms and Signatures"),
                ["fill_form", "sign", "create_form_field", "flatten", "remove_annotations"],
            ),
            (self.tr("&Security"), ["protect", "unlock"]),
            (self.tr("&Document Properties"), ["set_metadata", "rename", "compress"]),
            (
                self.tr("Convert &from PDF"),
                ["pdf_to_docx", "pdf_to_pptx", "pdf_to_xlsx", "pdf_to_html", "pdf_to_jpg"],
            ),
            (
                self.tr("Convert &to PDF"),
                ["docx_to_pdf", "pptx_to_pdf", "xlsx_to_pdf", "html_to_pdf", "jpg_to_pdf"],
            ),
            # Phase 4 (Scans) didn't exist when the seven categories
            # above were first drawn up, and none of them is a clean
            # fit - OCR/Deskew/Repair operate on a whole scanned/
            # damaged document, not page layout, form/security, or
            # format conversion. An eighth category, rather than
            # forcing one of these into a group it doesn't belong in.
            (self.tr("Scans &and Repair"), ["ocr", "deskew", "repair"]),
        ]
        categorized_tool_ids: set[str] = set()
        for category_label, tool_ids in tool_categories:
            category_menu = tools_menu.addMenu(category_label)
            for tool_id in tool_ids:
                dialog_cls = TOOL_DIALOGS[tool_id]
                plugin = self.registry.get(tool_id)
                action = QAction(plugin.display_name, self)
                action.triggered.connect(self._make_tool_handler(tool_id, dialog_cls))
                category_menu.addAction(action)
                self.tool_actions[tool_id] = action
                categorized_tool_ids.add(tool_id)
        if categorized_tool_ids != set(TOOL_DIALOGS):
            missing = sorted(set(TOOL_DIALOGS) - categorized_tool_ids)
            raise ValueError(f"Tools menu categories missing tool_id(s): {missing}")

        # Building/running a workflow is document-independent (Build
        # doesn't touch any open document at all; Run works against a
        # standalone input/output pair), so these two actions are
        # hand-wired here rather than going through TOOL_DIALOGS / the
        # Tools-menu loop above, and are never added to
        # self.tool_actions (which _update_action_state disables when
        # no document is open).
        self.build_workflow_action = QAction(self.tr("&Build Workflow..."), self)
        self.build_workflow_action.triggered.connect(self._build_workflow)

        self.run_workflow_action = QAction(self.tr("&Run Workflow..."), self)
        self.run_workflow_action.triggered.connect(self._run_workflow)

        workflows_menu = self.menuBar().addMenu(self.tr("&Workflows"))
        workflows_menu.addAction(self.build_workflow_action)
        workflows_menu.addAction(self.run_workflow_action)

        self.toolbar = QToolBar(self.tr("Main"))
        self.toolbar.setAccessibleName(self.tr("Main toolbar"))
        self.addToolBar(self.toolbar)
        self.toolbar.addAction(self.open_action)
        self.toolbar.addAction(self.save_as_action)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.undo_action)
        self.toolbar.addAction(self.redo_action)

        self._build_view_menu()

    def _build_view_menu(self) -> None:
        self.zoom_in_action = QAction(self.tr("Zoom &In"), self)
        # QKeySequence.StandardKey.ZoomIn resolves to the literal
        # "Ctrl++" on this platform (confirmed via
        # QKeySequence.keyBindings), but '+' isn't its own physical key
        # on most keyboard layouts - it's Shift+'=' on a US layout, and
        # varies further on non-US ones. Relying on the standard key
        # alone means a user who presses the unshifted "Ctrl+=" (the
        # binding browsers/editors also accept for exactly this reason)
        # sees nothing happen. setShortcuts (plural) keeps every
        # platform alternate StandardKey.ZoomIn already provides and
        # adds the unshifted "Ctrl+=" explicitly, rather than replacing
        # the standard binding outright.
        self.zoom_in_action.setShortcuts(
            [*QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomIn), QKeySequence("Ctrl+=")]
        )
        self.zoom_in_action.triggered.connect(self._zoom_in)

        self.zoom_out_action = QAction(self.tr("Zoom &Out"), self)
        self.zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        self.zoom_out_action.triggered.connect(self._zoom_out)

        self.reset_zoom_action = QAction(self.tr("&Reset Zoom"), self)
        self.reset_zoom_action.setShortcut("Ctrl+0")
        self.reset_zoom_action.triggered.connect(self._reset_zoom)

        self.toggle_toolbar_action = QAction(self.tr("Show &Toolbar"), self)
        self.toggle_toolbar_action.setCheckable(True)
        self.toggle_toolbar_action.setChecked(True)
        self.toggle_toolbar_action.toggled.connect(self._toggle_toolbar)

        self.toggle_statusbar_action = QAction(self.tr("Show Status &Bar"), self)
        self.toggle_statusbar_action.setCheckable(True)
        self.toggle_statusbar_action.setChecked(True)
        self.toggle_statusbar_action.toggled.connect(self._toggle_statusbar)

        self.full_screen_action = QAction(self.tr("&Full Screen"), self)
        self.full_screen_action.setShortcut("F11")
        self.full_screen_action.setCheckable(True)
        self.full_screen_action.toggled.connect(self._toggle_full_screen)

        view_menu = self.menuBar().addMenu(self.tr("&View"))
        view_menu.addAction(self.zoom_in_action)
        view_menu.addAction(self.zoom_out_action)
        view_menu.addAction(self.reset_zoom_action)
        view_menu.addSeparator()
        view_menu.addAction(self.toggle_toolbar_action)
        view_menu.addAction(self.toggle_statusbar_action)
        view_menu.addSeparator()
        view_menu.addAction(self.full_screen_action)

    # --- view menu: thumbnail zoom / toolbar / status bar / full screen --

    def _set_thumbnail_zoom(self, width: int) -> None:
        width = max(_THUMBNAIL_ZOOM_MIN_WIDTH, min(_THUMBNAIL_ZOOM_MAX_WIDTH, width))
        # Derived from the *original* _THUMBNAIL_SIZE ratio each call,
        # not from self.thumbnail_size's current value - repeated
        # zoom-in/zoom-out calls can't compound rounding error and
        # drift the aspect ratio away from the source.
        height = round(width * _THUMBNAIL_SIZE.height() / _THUMBNAIL_SIZE.width())
        self.thumbnail_size = QSize(width, height)
        # Window-level, so every tab's grid gets the new icon size, not
        # just the visible one. Only the current tab is re-rendered
        # here (see _refresh); a background tab re-renders when it's
        # next activated, which _on_current_tab_changed handles.
        for tab in self.tabs():
            tab.thumbnail_list.setIconSize(self.thumbnail_size)
        # Existing thumbnail pixmaps were rendered at the old size -
        # re-render from the PDF at the new size rather than letting
        # Qt stretch/shrink the old QIcon blurrily.
        self._refresh()

    def _zoom_in(self) -> None:
        self._set_thumbnail_zoom(self.thumbnail_size.width() + _THUMBNAIL_ZOOM_STEP)

    def _zoom_out(self) -> None:
        self._set_thumbnail_zoom(self.thumbnail_size.width() - _THUMBNAIL_ZOOM_STEP)

    def _reset_zoom(self) -> None:
        self._set_thumbnail_zoom(_THUMBNAIL_SIZE.width())

    def _toggle_toolbar(self, checked: bool) -> None:
        self.toolbar.setVisible(checked)

    def _toggle_statusbar(self, checked: bool) -> None:
        self.statusBar().setVisible(checked)

    def _toggle_full_screen(self, checked: bool) -> None:
        if checked:
            self.showFullScreen()
        else:
            self.showNormal()

    def _make_tool_handler(self, tool_id: str, dialog_cls: DialogFactory) -> Any:
        return lambda: self._run_tool(tool_id, dialog_cls)

    @contextmanager
    def _busy_cursor(self) -> Iterator[None]:
        """Visual feedback around a synchronous operation that might
        take a moment (large PDFs, compress, N-up rendering, etc.) -
        deliberately not a background-thread rewrite, just making the
        otherwise-unresponsive-looking wait visible rather than silent."""
        self.statusBar().showMessage(self.tr("Working..."))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()

    # --- tab management ---------------------------------------------------

    def _add_tab(self, controller: AppController | None = None, *, activate: bool = True) -> DocumentTab:
        """Create and wire up a new document tab, with no document in
        it yet - the caller loads or builds one immediately afterward.

        `activate=True` (the default) makes the new tab current right
        away. Every real caller in this file passes `activate=False`
        instead and activates the tab itself only once it actually has
        a document, via `self.tab_widget.setCurrentWidget(tab)` (or an
        explicit `_refresh()` - see below for why both are needed) -
        see the real bug this avoids, below. `activate=True` is kept
        as the default only for callers (e.g. tests) that don't need
        that care.

        Found and fixed here, confirmed by grab()ing the real window,
        not just reasoned about: `setCurrentIndex` makes the tab
        current *synchronously*, which fires `currentChanged` ->
        `_on_current_tab_changed` -> `_refresh()` immediately - before
        the caller has had a chance to open/build a document in it.
        `_refresh()` at that point renders a real, capturable frame:
        an empty "Untitled" tab with a plain dark thumbnail grid (the
        grid's own background color, since there are zero items) and
        "0 page(s)" in the status bar - exactly the "black, empty tab,
        the PDF content never appears" bug report. A later explicit
        `_refresh()` call (once the document is actually loaded)
        overwrites this in the same call stack with no event-loop turn
        in between, so a purely synchronous script self-heals too
        fast to see it - but any real repaint trigger in between (a
        slow file copy, a window-manager-driven redraw) can expose the
        empty frame, and grab() proves it exists as a real renderable
        state, not just a timing curiosity.

        `activate=False` blocks the tab widget's signals for the
        `addTab` call too, not just skips `setCurrentIndex` - adding
        the very *first* tab to an empty `QTabWidget` makes Qt select
        it automatically (confirmed directly: `addTab` alone, with no
        `setCurrentIndex` call at all, still fires `currentChanged`),
        so `activate=False` has to suppress that emission as well or
        the first-tab-ever case would hit the exact same premature
        render this exists to prevent. The tab's *actual* current-ness
        (`tab_widget.currentIndex()`/`currentWidget()`) is unaffected
        by blocking signals - only our own signal-driven refresh is -
        so a caller opening the very first document still needs an
        explicit `_refresh()` once loading succeeds: `setCurrentWidget`
        would be a no-op there (Qt already made it current, silently,
        during `addTab`) and wouldn't re-fire `currentChanged` on its
        own.
        """
        if controller is None:
            controller = AppController(self.registry, self.audit_log)
        tab = DocumentTab(controller, self.thumbnail_size)
        # Bound to this specific tab rather than resolved as "whatever
        # is current when the signal arrives": _on_thumbnails_reordered
        # defers its work to the next event-loop turn, by which point
        # the current tab could in principle have changed.
        tab.thumbnail_list.model().rowsMoved.connect(
            lambda *_args, t=tab: self._on_thumbnails_reordered(t)
        )
        tab.thumbnail_list.customContextMenuRequested.connect(
            lambda pos, t=tab: self._show_thumbnail_context_menu(t, pos)
        )
        if activate:
            index = self.tab_widget.addTab(tab, self._tab_label(tab))
            self.tab_widget.setCurrentIndex(index)
        else:
            self.tab_widget.blockSignals(True)
            try:
                self.tab_widget.addTab(tab, self._tab_label(tab))
            finally:
                self.tab_widget.blockSignals(False)
        return tab

    def _discard_tab(self, tab: DocumentTab) -> None:
        """Remove `tab` and securely wipe its session temp dir - that
        one document's working files only, while every other tab keeps
        its own session intact."""
        index = self.tab_widget.indexOf(tab)
        if index != -1:
            self.tab_widget.removeTab(index)
        tab.controller.close_session()
        tab.deleteLater()
        self._mark_active_session()

    def _tab_label(self, tab: DocumentTab) -> str:
        name = tab.document_name()
        return f"{_DIRTY_TAB_MARKER}{name}" if tab.controller.is_dirty else name

    def _update_tab_labels(self) -> None:
        for index, tab in enumerate(self.tabs()):
            self.tab_widget.setTabText(index, self._tab_label(tab))
            source = tab.controller.doc.source_path
            self.tab_widget.setTabToolTip(index, str(source) if source else tab.document_name())

    def _on_current_tab_changed(self, _index: int) -> None:
        # A background tab's thumbnails may have been rendered at a
        # different zoom level (zoom is window-level), so activating a
        # tab re-renders it rather than showing stale pixmaps.
        self._refresh()
        self._mark_active_session()

    def _cycle_tab(self, delta: int) -> None:
        count = self.tab_widget.count()
        if count < 2:
            return
        self.tab_widget.setCurrentIndex((self.tab_widget.currentIndex() + delta) % count)

    def _on_tab_close_requested(self, index: int) -> None:
        self._close_tab(index)

    def _close_tab(self, index: int) -> bool:
        """Close one tab after its own unsaved-changes check. False
        means the user cancelled and nothing was closed."""
        widget = self.tab_widget.widget(index)
        if not isinstance(widget, DocumentTab):
            return True
        if not self._confirm_discard_if_dirty(widget):
            return False
        self._discard_tab(widget)
        self._refresh()
        return True

    def _close_other_tabs(self, index: int | None = None) -> bool:
        """Close every tab except `index` (the current one by default),
        each through its own unsaved-changes check. Stops at the first
        cancelled prompt, leaving that tab and any not-yet-visited ones
        open."""
        keep = self.current_tab if index is None else self.tab_widget.widget(index)
        for tab in self.tabs():
            if tab is keep:
                continue
            if not self._confirm_discard_if_dirty(tab):
                self._refresh()
                return False
            self._discard_tab(tab)
        self._refresh()
        return True

    def _close_all_tabs(self) -> bool:
        """Close every tab, each through its own unsaved-changes check.
        False means one of them was cancelled - the remaining tabs stay
        open (and, from closeEvent, the window stays open too)."""
        for tab in self.tabs():
            if not self._confirm_discard_if_dirty(tab):
                self._refresh()
                return False
            self._discard_tab(tab)
        self._refresh()
        return True

    def _show_tab_context_menu(self, pos: QPoint) -> None:
        tab_bar = self.tab_widget.tabBar()
        index = tab_bar.tabAt(pos)
        if index < 0:
            return
        menu = QMenu(self)
        close_action = menu.addAction(self.tr("Close Tab"))
        close_others_action = menu.addAction(self.tr("Close Other Tabs"))
        close_all_action = menu.addAction(self.tr("Close All Tabs"))
        close_others_action.setEnabled(self.tab_widget.count() > 1)

        chosen = menu.exec(tab_bar.mapToGlobal(pos))
        if chosen is close_action:
            self._close_tab(index)
        elif chosen is close_others_action:
            self._close_other_tabs(index)
        elif chosen is close_all_action:
            self._close_all_tabs()

    # --- document lifecycle ----------------------------------------------

    def _open_document(self) -> None:
        path_str, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.tr("Open PDF"),
            "",
            self.tr("PDF files (*.pdf)"),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path_str:
            return
        self._open_document_path(Path(path_str))

    def _ask_tab_placement(self, document_name: str | None = None) -> str | None:
        """New Tab / Replace Current Tab / Cancel (None). With nothing
        open there's nothing to replace and nothing ambiguous, so the
        prompt is skipped entirely."""
        if self.tab_widget.count() == 0:
            return PLACEMENT_NEW_TAB
        dialog = TabPlacementDialog(document_name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.placement

    def _open_document_path(self, path: Path, placement: str | None = None) -> None:
        """Shared by the Open dialog and the Recent Files menu. Asks
        where to put the document unless the caller already decided."""
        if placement is None:
            placement = self._ask_tab_placement(path.name)
            if placement is None:
                return

        tab = self.current_tab
        if placement == PLACEMENT_REPLACE_CURRENT and tab is not None:
            # Scoped to the tab actually being replaced, not "is
            # anything anywhere dirty".
            if not self._confirm_discard_if_dirty(tab):
                return
            opened_new_tab = False
        else:
            # activate=False: don't switch to (and render) the new tab
            # until it actually has a document - see _add_tab's
            # docstring for the black-empty-tab bug this avoids.
            tab = self._add_tab(activate=False)
            opened_new_tab = True

        try:
            tab.controller.open_document(path)
        except PDFEditorError as exc:
            # A recent-file entry that fails to open (moved/deleted
            # since last time) is stale - drop it so it doesn't keep
            # reappearing in the menu instead of just erroring forever.
            self.recent_files.remove(path)
            if opened_new_tab:
                # Don't strand an empty tab for a document that never
                # opened; a replaced tab keeps its existing document,
                # which open_document() leaves untouched on failure.
                # It was never activated, so nothing was ever shown.
                self._discard_tab(tab)
            self._show_error(exc)
            self._refresh()
            return
        if opened_new_tab:
            # Now that it actually has a document: setCurrentWidget
            # handles activation for a second-or-later tab (firing
            # _refresh() itself); the explicit _refresh() below is
            # still needed for the very first tab, where Qt already
            # silently made it current inside addTab and this is a
            # no-op that fires no signal (see _add_tab's docstring).
            self.tab_widget.setCurrentWidget(tab)
        self.recent_files.add(path)
        self._mark_active_session()
        self._refresh()

    def _populate_recent_files_menu(self) -> None:
        self.recent_files_menu.clear()
        paths = self.recent_files.list()
        if not paths:
            empty_action = self.recent_files_menu.addAction(self.tr("(No recent files)"))
            empty_action.setEnabled(False)
            return
        for path in paths:
            action = self.recent_files_menu.addAction(path.name)
            action.setToolTip(str(path))
            action.triggered.connect(self._make_recent_file_handler(path))
        self.recent_files_menu.addSeparator()
        clear_action = self.recent_files_menu.addAction(self.tr("Clear Recent Files"))
        clear_action.triggered.connect(self.recent_files.clear)

    def _make_recent_file_handler(self, path: Path) -> Any:
        return lambda: self._open_recent_file(path)

    def _open_recent_file(self, path: Path) -> None:
        self._open_document_path(path)

    def _save_as(self, tab: DocumentTab | None = None) -> bool:
        """Returns True if the document was actually saved (used by
        the unsaved-changes prompt to know whether to proceed).
        Defaults to the current tab; the prompt passes the specific tab
        it's asking about, which during a multi-tab close may not be
        the one that was active when the close started."""
        if tab is None:
            tab = self.current_tab
        if tab is None or not tab.controller.is_open:
            return False
        path_str, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.tr("Save PDF As"),
            "",
            self.tr("PDF files (*.pdf)"),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path_str:
            return False
        try:
            tab.controller.save_as(Path(path_str))
        except PDFEditorError as exc:
            self._show_error(exc)
            return False
        # The tab's dirty marker is part of its label - saving has to
        # clear it even when the saved tab isn't the visible one.
        self._update_tab_labels()
        self.statusBar().showMessage(self.tr("Saved to {0}").format(path_str), 5000)
        return True

    # --- document properties -------------------------------------------

    def _read_properties(self, tab: DocumentTab) -> DocumentInfo:
        """The properties report for `tab`'s document.

        The *working copy* is what gets parsed - the current in-memory
        edit state, unsaved changes included - while `source_path` is
        only stat()ed for the "File on disk" section. The dialog says
        which is which; see core/document_info.py.
        """
        return read_document_info(
            tab.controller.doc.working_path,
            source_path=tab.controller.doc.source_path,
            has_unsaved_changes=tab.controller.is_dirty,
        )

    def _show_properties(self) -> None:
        """File > Properties... / Ctrl+D. Read-only: no Operation, no
        undo entry, nothing written."""
        tab = self.current_tab
        # The action is disabled with no document open (see
        # _update_action_state, the same treatment Save As gets). This
        # guard is the belt to that's braces - _save_as has exactly the
        # same pair, and returns rather than erroring for the same
        # reason: an unavailable menu item that somehow fires should do
        # nothing, not pop an error at a user who didn't ask for one.
        if tab is None or not tab.controller.is_open:
            return
        dialog = PropertiesDialog(
            self._read_properties(tab),
            lambda: self._edit_metadata_from_properties(tab),
            self,
        )
        dialog.exec()

    def _edit_metadata_from_properties(self, tab: DocumentTab) -> DocumentInfo | None:
        """Run the ordinary Metadata tool for `tab`, and hand the
        Properties dialog a fresh report if it actually changed
        anything (None means the user cancelled, so nothing to
        refresh).

        Deliberately goes through `_run_tool` rather than applying a
        `SetMetadataOperation` directly: that is the one path that puts
        the edit on the undo stack and in the audit log, and a second
        hand-rolled apply route would silently skip both.
        """
        operations_before = len(tab.controller.doc.operation_log)
        self._run_tool("set_metadata", TOOL_DIALOGS["set_metadata"])
        if len(tab.controller.doc.operation_log) == operations_before:
            return None
        return self._read_properties(tab)

    def _close_document(self) -> None:
        """File > Close Tab / Ctrl+W - closes the current tab."""
        index = self.tab_widget.currentIndex()
        if index < 0:
            return
        self._close_tab(index)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override, fixed name
        # Every tab is checked, not just the active one: sequential
        # per-tab Save/Discard/Cancel prompts (each tab is made visible
        # before it's asked about, so "this document" is unambiguous),
        # and Cancel on any single one aborts the whole window close,
        # leaving the tabs that hadn't been reached yet untouched.
        if not self._close_all_tabs():
            event.ignore()
            return
        # A clean shutdown leaves nothing to recover - drop the pointer
        # so the next launch doesn't offer a stale session.
        mark_active_session(None)
        super().closeEvent(event)

    def _confirm_discard_if_dirty(self, tab: DocumentTab) -> bool:
        """True if it's safe to proceed with closing/replacing `tab`:
        either there's nothing that could be lost, or the user
        explicitly chose to save or discard. False means the caller
        should abort and leave everything as-is."""
        if not tab.controller.is_dirty:
            return True
        # Make the document being asked about the visible one first -
        # during a Close All / window close the prompt would otherwise
        # name a document the user can't see.
        self.tab_widget.setCurrentWidget(tab)
        response = QMessageBox.warning(
            self,
            self.tr("Unsaved Changes"),
            self.tr("'{0}' has unsaved changes. Save before continuing?").format(
                tab.document_name()
            ),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if response == QMessageBox.StandardButton.Cancel:
            return False
        if response == QMessageBox.StandardButton.Save:
            return self._save_as(tab)
        return True  # Discard

    # --- crash recovery ---------------------------------------------------

    def restore_autosaved_session(self) -> bool:
        """Offer to reopen the most recently active tab from the last
        run if it died without closing cleanly (core/session/autosave.py's
        pointer). Returns True if a document was actually restored.

        Called from gui/main.py after the window is shown, not from
        __init__ - a modal prompt fired from a constructor is both bad
        practice and untestable without every MainWindow() in the suite
        blocking on it.
        """
        recovery = recover_active_session()
        if recovery is None or recovery.checkpoint_path is None:
            return False
        name = recovery.display_name or (
            recovery.source_path.name if recovery.source_path else self.tr("Untitled")
        )
        response = QMessageBox.question(
            self,
            self.tr("Restore Document"),
            self.tr(
                "'{0}' was open when the app last closed unexpectedly. "
                "Restore the unsaved version?"
            ).format(name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        restored = False
        if response == QMessageBox.StandardButton.Yes:
            # activate=False: see _add_tab's docstring - don't switch
            # to (and render) the new tab until it actually has a
            # document.
            tab = self._add_tab(activate=False)
            try:
                tab.controller.restore_from_checkpoint(
                    recovery.checkpoint_path,
                    source_path=recovery.source_path,
                    display_name=recovery.display_name,
                )
                restored = True
                self.tab_widget.setCurrentWidget(tab)
            except PDFEditorError as exc:
                self._discard_tab(tab)
                self._show_error(exc)
        # Either way the offer is consumed: the crashed session's
        # journal is wiped (its data is either now in a live tab or
        # explicitly declined) so it can't be offered again next launch.
        discard_active_session()
        self._mark_active_session()
        self._refresh()
        return restored

    def _mark_active_session(self) -> None:
        """Record which tab's session crash recovery should offer next
        time - the currently active one (see decision: v1 restores the
        most recently active tab, not every open tab)."""
        controller = self.controller
        mark_active_session(controller.session_id if controller is not None else None)

    # --- undo/redo ---------------------------------------------------------

    def _undo(self) -> None:
        controller = self.controller
        if controller is None:
            return
        try:
            with self._busy_cursor():
                controller.undo()
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

    def _redo(self) -> None:
        controller = self.controller
        if controller is None:
            return
        try:
            with self._busy_cursor():
                controller.redo()
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self._refresh()

    # --- tools ---------------------------------------------------------------

    def _export_document(self, tool_id: str, values: dict[str, Any], tab: DocumentTab) -> None:
        """Run a PDF -> external-format conversion as an **export**:
        write the result to a file the user picks, and leave the open
        document exactly as it was.

        These five operations return a `DocumentSession` whose
        `working_path` is a .docx/.pptx/.xlsx/.html/.jpg, because that
        is the right shape for the CLI (which writes it straight to
        `-o`) and for a Workflow step. Applying one to a *tab*,
        though, replaced that tab's PDF with a file the thumbnail
        grid cannot render - the window went blank, the document
        appeared to vanish, and the converted file was left in the
        private session dir to be securely wiped when the tab closed.
        The user's Word file was destroyed, not delivered.

        So the conversion runs against a throwaway `SessionTempDir`
        seeded with a copy of the working PDF, exactly as
        `_run_workflow` does, and the tab's own AppController,
        document and undo stack are never touched. There is
        deliberately no undo entry: converting to Word does not modify
        the PDF, so there is nothing to undo. The audit log still
        records it, for the same reason `_run_workflow` does.
        """
        suffix, filter_text = _EXPORT_TOOLS[tool_id]
        working_path = tab.controller.doc.working_path
        assert working_path is not None  # guaranteed by _run_tool's is_open check

        source_path = tab.controller.doc.source_path
        directory = source_path.parent if source_path is not None else Path.home()
        suggestion = directory / (Path(tab.document_name()).stem + suffix)
        path_str, _selected_filter = QFileDialog.getSaveFileName(
            self,
            self.tr("Export As"),
            str(suggestion),
            self.tr(filter_text),
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not path_str:
            return
        target = Path(path_str)
        if not target.suffix:
            # Only when the user typed no extension at all - an
            # explicit one they chose themselves is left alone.
            target = target.with_suffix(suffix)

        try:
            with self._busy_cursor():
                operation = self.registry.get(tool_id).build_operation(**values)
                with SessionTempDir() as session:
                    scratch = session.path / f"working{working_path.suffix}"
                    shutil.copyfile(working_path, scratch)
                    # source_path is carried over so the operation sees
                    # the same document identity it would in the tab.
                    result = operation.apply(
                        DocumentSession(working_path=scratch, source_path=source_path)
                    )
                    if result.working_path is None:  # pragma: no cover - defensive
                        raise OperationError("The conversion produced no output file.")
                    shutil.copyfile(result.working_path, target)
                    self.audit_log.record_operation(operation, document_label=str(target))
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self.statusBar().showMessage(self.tr("Exported to {0}").format(target), 5000)

    def _run_tool(self, tool_id: str, dialog_cls: DialogFactory) -> None:
        tab = self.current_tab
        if tool_id != "merge" and (tab is None or not tab.controller.is_open):
            self._show_error_message(self.tr("Open a document first."))
            return

        if tool_id == "fill_form":
            assert tab is not None  # guaranteed by the check above
            working_path = tab.controller.doc.working_path
            assert working_path is not None
            dialog: BaseToolDialog = FillFormDialog(list_form_field_names(working_path), self)
        elif tool_id == "sign":
            # Like fill_form, SignDialog can do more when it knows which
            # document is being edited: given the working path it shows
            # the real page and lets the image be placed by mouse. It
            # still works without one (the Workflow builder builds it
            # that way), falling back to numeric entry only.
            assert tab is not None  # guaranteed by the check above
            working_path = tab.controller.doc.working_path
            assert working_path is not None
            dialog = SignDialog(self, working_path)
        else:
            dialog = dialog_cls(self)

        # A dialog can hold an OS handle on a file in the session temp
        # dir (SignDialog's placement canvas keeps the working copy
        # open through a QPdfDocument), and every dialog here is
        # parented to this window, so it outlives exec() by however
        # long Qt takes to destroy it - potentially past the point
        # close_session() securely wipes that dir, which Windows then
        # refuses to do (WinError 32). Released deterministically here,
        # on every path out, rather than left to destruction order.
        try:
            if dialog.exec() != BaseToolDialog.DialogCode.Accepted:
                return
            if tool_id in _EXPORT_TOOLS:
                # Produces a file to keep, not a new state for this
                # tab - see _export_document for why applying one of
                # these to the tab blanked the window.
                assert tab is not None  # export tools all require an open document
                self._export_document(tool_id, dialog.values(), tab)
                return
            created_tab = tab is None
            if created_tab:
                # Merge with no tabs open: it builds a document from
                # scratch, so it gets a fresh tab - created only now
                # that the dialog was actually accepted, so a cancelled
                # Merge can't strand an empty tab. activate=False:
                # don't switch to (and render) it until apply_operation
                # below actually succeeds - see _add_tab's docstring
                # for the black-empty-tab bug this avoids.
                tab = self._add_tab(activate=False)
            assert tab is not None  # either pre-existing (checked above) or just created
            try:
                plugin = self.registry.get(tool_id)
                operation = plugin.build_operation(**dialog.values())
                with self._busy_cursor():
                    tab.controller.apply_operation(operation)
            except PDFEditorError as exc:
                if created_tab:
                    # Don't strand an empty tab for a Merge that built
                    # nothing (e.g. every input file was invalid).
                    self._discard_tab(tab)
                self._show_error(exc)
                return
            finally:
                self._mark_active_session()
            if created_tab:
                self.tab_widget.setCurrentWidget(tab)
            self._refresh()
        finally:
            dialog.release_resources()

    # --- workflows -------------------------------------------------------------

    def _build_workflow(self) -> None:
        """Document-independent by design: a workflow is a saved,
        named sequence of Operations, not a live edit against any open
        tab's document (that's what _run_tool is for)."""
        dialog = WorkflowBuilderDialog(self.registry, self)
        if dialog.exec() != BaseToolDialog.DialogCode.Accepted:
            return
        pipeline = dialog.build_pipeline()
        try:
            WorkflowStore().save(pipeline)
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self.statusBar().showMessage(
            self.tr("Saved workflow '{0}'").format(pipeline.name), 5000
        )

    def _run_workflow(self) -> None:
        """A standalone batch run against an input/output file pair -
        deliberately touching no tab's AppController, document or undo
        stack (only the app-wide audit_log, to record the steps), the
        same "external file(s) in" shape Merge and the Phase 3
        conversion ops already use, run through a throwaway
        SessionTempDir rather than any live editing session. It never
        opens a tab either."""
        names = WorkflowStore().list_workflows()
        if not names:
            QMessageBox.information(
                self,
                self.tr("Run Workflow"),
                self.tr("No saved workflows yet. Use Build Workflow... first."),
            )
            return

        dialog = RunWorkflowDialog(names, self)
        if dialog.exec() != BaseToolDialog.DialogCode.Accepted:
            return

        try:
            values = dialog.values()
            with self._busy_cursor():
                pipeline = WorkflowStore().load(values["workflow_name"], self.registry)
                with SessionTempDir() as session:
                    input_path: Path = values["input_path"]
                    working = session.path / f"working{input_path.suffix or '.pdf'}"
                    shutil.copyfile(input_path, working)
                    doc = DocumentSession(working_path=working, source_path=input_path)
                    result = pipeline.run(doc)
                    if result.working_path is None:
                        raise OperationError("Workflow produced no output document.")
                    shutil.copyfile(result.working_path, values["output_path"])
                    # Every other path that applies an Operation (tool
                    # dialogs, via AppController.apply_operation; the
                    # CLI's own run-workflow) records to the audit
                    # trail - a workflow run through the GUI must too,
                    # or its steps would be invisible to the audit log
                    # despite having actually modified a document.
                    for operation in pipeline.operations:
                        self.audit_log.record_operation(
                            operation, document_label=str(values["output_path"])
                        )
        except PDFEditorError as exc:
            self._show_error(exc)
            return
        self.statusBar().showMessage(
            self.tr("Workflow run complete: {0}").format(values["output_path"]), 5000
        )

    # --- rendering ------------------------------------------------------------

    def _refresh(self) -> None:
        tab = self.current_tab
        if tab is not None:
            self._render_tab(tab)
            self.setWindowTitle(f"{_APP_NAME} - {tab.document_name()}")
            if self._thumbnails_failed:
                # Never leave an empty grid unexplained: before this,
                # a document QtPdf could not render showed no pages and
                # no reason, which reads as the app being broken.
                # Deliberately the status bar and not a modal dialog -
                # _refresh runs on every operation, undo, redo and tab
                # change, and a modal here would fire repeatedly (and
                # hang the headless suite).
                self.statusBar().showMessage(
                    self.tr("Could not render page thumbnails for this document.")
                )
            else:
                self.statusBar().showMessage(
                    self.tr("{0} page(s)").format(tab.thumbnail_list.count())
                )
            self.stack.setCurrentWidget(self.tab_widget)
        else:
            self.setWindowTitle(_APP_NAME)
            self.statusBar().showMessage(self.tr("No document open"))
            self.stack.setCurrentWidget(self.empty_state)
        self._update_tab_labels()
        self._update_action_state()

    def _render_tab(self, tab: DocumentTab) -> None:
        tab.thumbnail_list.clear()
        working_path = tab.controller.doc.working_path
        if tab.controller.is_open and working_path is not None:
            self._thumbnails_failed = not self._render_thumbnails(
                tab.thumbnail_list, working_path
            )
        else:
            self._thumbnails_failed = False

    def _render_thumbnails(self, thumbnail_list: QListWidget, path: Path) -> bool:
        """Fill `thumbnail_list` with one icon per page. Returns False
        if no engine could render the document at all.

        Two engines, because they genuinely disagree about which files
        are readable. The app opens and validates documents with pikepdf
        (qpdf), which silently repairs a damaged cross-reference table,
        so a truncated PDF opens perfectly happily and reports its real
        page count - while QtPdf rejects the identical file outright
        with InvalidFileFormat. That combination used to log one line
        and return, leaving a document that was "open" with an
        empty grid and nothing on screen explaining why: the reported
        "I open a PDF and there is no thumbnail" bug, reproduced exactly
        against a PDF truncated to 85% of its length.

        QtPdf stays the primary engine (unchanged for every file that
        already worked). PyMuPDF - already a dependency, and the same
        renderer the conversion ops use - is tried only when QtPdf
        refuses, and renders those damaged files fine.
        """
        # No parent: this is a short-lived, throwaway document used
        # only to render thumbnails for this one _refresh() call. A
        # `self`-parented QPdfDocument would live as long as
        # MainWindow itself - confirmed via review to leak one
        # instance per call (every operation/undo/redo triggers a
        # _refresh()), unbounded over a session.
        pdf_doc = QPdfDocument()
        if pdf_doc.load(str(path)) == QPdfDocument.Error.None_:
            for i in range(pdf_doc.pageCount()):
                self._add_thumbnail(thumbnail_list, pdf_doc.render(i, self.thumbnail_size), i)
            return True

        log.warning(
            "QtPdf could not load '%s' for thumbnails; falling back to PyMuPDF.", path
        )
        try:
            with fitz.open(str(path)) as src:
                for i in range(src.page_count):
                    self._add_thumbnail(
                        thumbnail_list,
                        _render_page_with_fitz(src, i, self.thumbnail_size),
                        i,
                    )
                rendered_any = src.page_count > 0
        except Exception:
            log.exception("No engine could render thumbnails for '%s'", path)
            return False
        return rendered_any

    def _add_thumbnail(self, thumbnail_list: QListWidget, rendered: QImage, index: int) -> None:
        # QtPdf leaves any unpainted area of the page fully
        # transparent (alpha=0) rather than opaque white - most
        # visible on blank/near-empty pages. Composite onto a
        # white backdrop so a thumbnail always reads as a page,
        # not as "nothing" wherever the source PDF painted nothing.
        page_image = QImage(self.thumbnail_size, QImage.Format.Format_ARGB32_Premultiplied)
        page_image.fill(Qt.GlobalColor.white)
        painter = QPainter(page_image)
        painter.drawImage(0, 0, rendered)
        painter.end()
        item = QListWidgetItem(
            QIcon(QPixmap.fromImage(page_image)), self.tr("Page {0}").format(index + 1)
        )
        # Tracks which page this item represents in the *current*
        # working document, independent of drag position - read
        # back in visual order by _apply_thumbnail_reorder to
        # build the ReorderPagesOperation's page_order.
        item.setData(Qt.ItemDataRole.UserRole, index + 1)
        thumbnail_list.addItem(item)

    def _on_thumbnails_reordered(self, tab: DocumentTab) -> None:
        # Deferred to the next event loop turn: applying an operation
        # (which rebuilds the tab's thumbnail list via _refresh) from
        # directly inside the model's own rowsMoved signal would fight
        # with Qt's own post-move bookkeeping for the same signal - the
        # standard-idiom fix is to let the current emission finish
        # first (see QTimer.singleShot(0, ...)).
        QTimer.singleShot(0, lambda: self._apply_thumbnail_reorder(tab))

    def _apply_thumbnail_reorder(self, tab: DocumentTab) -> None:
        page_order = [
            tab.thumbnail_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(tab.thumbnail_list.count())
        ]
        if page_order == list(range(1, len(page_order) + 1)):
            # No actual change (e.g. a drag that ends where it started,
            # or - for a multi-item drag - a second rowsMoved signal
            # for the same gesture arriving after the first one already
            # applied the reorder and _refresh() reset everything to
            # sequential order). Applying here would push a no-op
            # ReorderPagesOperation onto the undo stack for nothing.
            return
        self._apply_to_tab(tab, "reorder_pages", page_order=page_order)

    def _show_thumbnail_context_menu(self, tab: DocumentTab, pos: QPoint) -> None:
        selected = tab.thumbnail_list.selectedItems()
        if not selected:
            return
        pages = sorted(int(item.data(Qt.ItemDataRole.UserRole)) for item in selected)

        menu = QMenu(self)
        rotate_left_action = menu.addAction(self.tr("Rotate Left"))
        rotate_right_action = menu.addAction(self.tr("Rotate Right"))
        menu.addSeparator()
        delete_action = menu.addAction(self.tr("Delete Selected"))

        chosen = menu.exec(tab.thumbnail_list.viewport().mapToGlobal(pos))
        if chosen is rotate_left_action:
            self._apply_thumbnail_rotate(tab, pages, angle=-90)
        elif chosen is rotate_right_action:
            self._apply_thumbnail_rotate(tab, pages, angle=90)
        elif chosen is delete_action:
            self._apply_thumbnail_delete(tab, pages)

    def _apply_thumbnail_rotate(self, tab: DocumentTab, pages: list[int], angle: int) -> None:
        self._apply_to_tab(tab, "rotate_pages", angle=angle, pages=pages)

    def _apply_thumbnail_delete(self, tab: DocumentTab, pages: list[int]) -> None:
        self._apply_to_tab(tab, "delete_pages", pages=pages)

    def _apply_to_tab(self, tab: DocumentTab, tool_id: str, **kwargs: Any) -> None:
        """Apply a dialog-free operation (thumbnail drag-reorder,
        context-menu rotate/delete) to one specific tab's document."""
        try:
            operation = self.registry.get(tool_id).build_operation(**kwargs)
            with self._busy_cursor():
                tab.controller.apply_operation(operation)
        except PDFEditorError as exc:
            self._show_error(exc)
        # Refresh either way: on success this rebuilds thumbnails from
        # the new document (confirming the change), on failure it
        # discards any stale drag-and-drop visual order so the grid
        # matches the actual (unchanged) document again.
        self._refresh()

    def _update_action_state(self) -> None:
        controller = self.controller
        is_open = controller is not None and controller.is_open
        tab_count = self.tab_widget.count()
        self.save_as_action.setEnabled(is_open)
        self.properties_action.setEnabled(is_open)
        self.close_action.setEnabled(tab_count > 0)
        self.close_other_tabs_action.setEnabled(tab_count > 1)
        self.close_all_tabs_action.setEnabled(tab_count > 0)
        self.next_tab_action.setEnabled(tab_count > 1)
        self.previous_tab_action.setEnabled(tab_count > 1)
        self.undo_action.setEnabled(controller is not None and controller.can_undo)
        self.redo_action.setEnabled(controller is not None and controller.can_redo)
        for tool_id, action in self.tool_actions.items():
            action.setEnabled(is_open or tool_id == "merge")

    # --- error display -----------------------------------------------------------

    def _show_error(self, exc: Exception) -> None:
        self._show_error_message(str(exc))

    def _show_error_message(self, message: str) -> None:
        QMessageBox.critical(self, self.tr("Error"), message)
