"""Main window shell: layout, document lifecycle, view state, refresh.

Each tab is an independently editable document backed by its own
`AppController` (gui/document_tab.py) - own session temp dir, undo/redo
stack and dirty flag. `self.controller` and `self.thumbnail_list` are
read-only views onto whichever tab is active, and are None when no tab
is open.

Phase 6a (docs/GUI_PLAN.md §3.1) split this module up. What remains
here is the window itself - construction, the current-tab views, the
empty state, opening/saving/closing documents, crash recovery, view
state (zoom, toolbar, full screen) and the refresh cycle. The rest
moved to:

- `gui/actions.py` - actions, menus and the toolbar (setup only).
- `gui/tab_manager.py` - `TabManagementMixin`: tab lifecycle.
- `gui/tool_runner.py` - `ToolRunnerMixin`: tools and workflows.
- `gui/rendering.py` - page rasterisation.

The two mixins are behaviour, and are mixed in rather than held as
collaborators so that every method the test suite calls on the window
still resolves on `MainWindow` - see `gui/window_parts.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QLabel,
    QListWidget,
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

from core.errors import PDFEditorError
from core.registry.registry import Registry, discover_and_load
from core.session.audit_log import AuditLog
from core.session.autosave import (
    discard_active_session,
    mark_active_session,
    recover_active_session,
)
from core.session.recent_files import RecentFiles
from core.session.ui_state import UiState, load_ui_state, save_ui_state
from gui.actions import apply_action_icons, build_actions
from gui.controller import AppController
from gui.dialogs.command_palette import Command, CommandPalette
from gui.dialogs.tab_placement_dialog import (
    PLACEMENT_NEW_TAB,
    PLACEMENT_REPLACE_CURRENT,
    TabPlacementDialog,
)
from gui.document_tab import DocumentTab
from gui.history_panel import HistoryPanel
from gui.palette import build_palette, build_stylesheet
from gui.resources import build_logo_pixmap
from gui.styles import load_stylesheet
from gui.tab_manager import TabManagementMixin
from gui.tool_runner import ToolRunnerMixin

_APP_NAME = "Rad PDF Editor"
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
# once. QPdfDocument.render() (see gui/rendering.py) always rasterizes
# directly at the requested QSize - there's no fixed
# intermediate-resolution cache to outrun - so this stays genuinely
# sharp at 720px, confirmed by hand and by
# test_view_menu_zoom_in_out_and_reset_resize_the_icon_and_rerender's
# QIcon.actualSize() check plus a visual grab() spot-check.
_THUMBNAIL_ZOOM_MAX_WIDTH = 720
_THUMBNAIL_ZOOM_STEP = 20


class MainWindow(TabManagementMixin, ToolRunnerMixin, QMainWindow):
    # Built by gui/actions.build_actions() during __init__ and assigned
    # onto the window from there. Declared here because mypy can only
    # infer an attribute from an assignment inside the class - these are
    # assigned from another module, so without these annotations every
    # later use (`self.undo_action.setEnabled(...)`) is an attr-defined
    # error. The names and types are exactly what they were when
    # _build_actions still lived in this file.
    open_action: QAction
    save_as_action: QAction
    close_action: QAction
    close_other_tabs_action: QAction
    close_all_tabs_action: QAction
    next_tab_action: QAction
    previous_tab_action: QAction
    undo_action: QAction
    redo_action: QAction
    find_action: QAction
    copy_action: QAction
    select_all_action: QAction
    delete_annotation_action: QAction
    # Built by _build_annotate_menu via setattr(), so mypy cannot infer
    # them from an assignment - declared here for the same reason as the
    # rest (see the note above).
    highlight_action: QAction
    underline_action: QAction
    strikeout_action: QAction
    squiggly_action: QAction
    select_tool_action: QAction
    rect_tool_action: QAction
    circle_tool_action: QAction
    line_tool_action: QAction
    ink_tool_action: QAction
    note_tool_action: QAction
    redact_tool_action: QAction
    edit_text_tool_action: QAction
    toggle_history_action: QAction
    toggle_theme_action: QAction
    command_palette_action: QAction
    tool_group: QActionGroup
    build_workflow_action: QAction
    run_workflow_action: QAction
    zoom_in_action: QAction
    zoom_out_action: QAction
    reset_zoom_action: QAction
    fit_width_action: QAction
    fit_page_action: QAction
    larger_thumbnails_action: QAction
    smaller_thumbnails_action: QAction
    reset_thumbnails_action: QAction
    toggle_sidebar_action: QAction
    toggle_toolbar_action: QAction
    toggle_statusbar_action: QAction
    full_screen_action: QAction
    recent_files_menu: QMenu
    toolbar: QToolBar

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_APP_NAME)
        self.resize(900, 700)

        # App-wide, shared by every tab's AppController: one plugin scan
        # and one append-only audit trail for the whole process, rather
        # than one of each per open document.
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

        self.ui_state = load_ui_state()
        self.theme = self.ui_state.theme

        # The undo stack has been describable since Phase 0 and invisible
        # ever since; this is the first time it is shown.
        self.history_panel = HistoryPanel()
        self.history_panel.step_requested.connect(self._step_history)
        self.history_dock = QDockWidget(self.tr("History"), self)
        self.history_dock.setObjectName("historyDock")
        self.history_dock.setWidget(self.history_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.history_dock)
        self.history_dock.setVisible(False)

        self.tool_actions: dict[str, QAction] = {}
        self.markup_actions: dict[str, QAction] = {}
        self.canvas_tool_actions: dict[str, QAction] = {}
        build_actions(self)
        self._refresh()

    # --- current-tab views ------------------------------------------------
    #
    # Read-only conveniences so the rest of this class (and the tests)
    # can say "the document being edited" without repeating the tab
    # lookup. All three are None when no tab is open - every caller has
    # to handle that, which is exactly the state the empty-state welcome
    # screen represents.

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
        """Branded welcome screen shown in place of the tab area when no
        document is open."""
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

    # --- view menu: thumbnail zoom / toolbar / status bar / full screen --

    def _set_thumbnail_zoom(self, width: int) -> None:
        width = max(_THUMBNAIL_ZOOM_MIN_WIDTH, min(_THUMBNAIL_ZOOM_MAX_WIDTH, width))
        # Derived from the *original* _THUMBNAIL_SIZE ratio each call,
        # not from self.thumbnail_size's current value - repeated
        # zoom-in/zoom-out calls can't compound rounding error and drift
        # the aspect ratio away from the source.
        height = round(width * _THUMBNAIL_SIZE.height() / _THUMBNAIL_SIZE.width())
        self.thumbnail_size = QSize(width, height)
        # Window-level, so every tab's grid gets the new icon size, not
        # just the visible one. Only the current tab is re-rendered here
        # (see _refresh); a background tab re-renders when it's next
        # activated, which _on_current_tab_changed handles.
        for tab in self.tabs():
            tab.thumbnail_list.setIconSize(self.thumbnail_size)
        # Existing thumbnail pixmaps were rendered at the old size -
        # re-render from the PDF at the new size rather than letting Qt
        # stretch/shrink the old QIcon blurrily.
        self._refresh()

    def _zoom_in(self) -> None:
        self._set_thumbnail_zoom(self.thumbnail_size.width() + _THUMBNAIL_ZOOM_STEP)

    def _zoom_out(self) -> None:
        self._set_thumbnail_zoom(self.thumbnail_size.width() - _THUMBNAIL_ZOOM_STEP)

    def _reset_zoom(self) -> None:
        self._set_thumbnail_zoom(_THUMBNAIL_SIZE.width())

    # --- view menu: page zoom (the primary view) --------------------------

    def _page_zoom_in(self) -> None:
        tab = self.current_tab
        if tab is not None:
            tab.canvas.zoom_in()

    def _page_zoom_out(self) -> None:
        tab = self.current_tab
        if tab is not None:
            tab.canvas.zoom_out()

    def _page_reset_zoom(self) -> None:
        tab = self.current_tab
        if tab is not None:
            tab.canvas.reset_zoom()

    def _fit_width(self) -> None:
        tab = self.current_tab
        if tab is not None:
            tab.canvas.fit_width()

    def _fit_page(self) -> None:
        tab = self.current_tab
        if tab is not None:
            tab.canvas.fit_page()

    def _find(self) -> None:
        tab = self.current_tab
        if tab is not None:
            tab.find_bar.activate()

    def _copy_selection(self) -> None:
        tab = self.current_tab
        if tab is None:
            return
        if tab.canvas.copy_selection():
            self.statusBar().showMessage(self.tr("Copied selection"), 3000)

    def _select_all_on_page(self) -> None:
        tab = self.current_tab
        if tab is not None:
            tab.canvas.select_all_on_page(tab.canvas.current_page)

    def _on_link_activated(self, tab: DocumentTab, page: int, url: str) -> None:
        """Internal links navigate. External ones are *shown, not
        opened*: SPEC.md section 1 forbids network access anywhere in
        this app, and handing a URL to the user's browser would start
        outbound traffic from a confidential-documents tool on nothing
        more than a click on an untrusted document's link. The address
        is surfaced so the user can act on it deliberately."""
        if page > 0:
            tab.canvas.scroll_to_page(page)
        elif url:
            self.statusBar().showMessage(self.tr("Link: {0}").format(url), 8000)

    def _toggle_history(self, checked: bool) -> None:
        self.history_dock.setVisible(checked)
        if checked:
            self._refresh_history()

    def _refresh_history(self) -> None:
        tab = self.current_tab
        if tab is None:
            self.history_panel.update_history([], [])
            return
        doc = tab.controller.doc
        self.history_panel.update_history(
            [operation.describe() for operation in doc.operation_log],
            # redo_stack is newest-first (undo appends), so it is
            # reversed to read in the order redo would replay it.
            [operation.describe() for operation in reversed(doc.redo_stack)],
        )

    def _step_history(self, steps: int) -> None:
        """Undo or redo `steps` places, one operation at a time.

        Stepping rather than jumping: DocumentSession has no notion of a
        history position, and inventing one here would put a second idea
        of "current state" next to the one it already owns.
        """
        for _ in range(abs(steps)):
            if steps < 0:
                self._undo()
            else:
                self._redo()

    def _toggle_theme(self) -> None:
        self.apply_theme("light" if self.theme == "dark" else "dark")

    def apply_theme(self, theme: str) -> None:
        """Repaint the whole app in `theme`. The icons are drawn rather
        than loaded, so they re-theme by being redrawn."""
        self.theme = theme
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.setPalette(build_palette(theme))
            # The palette alone is not the theme: styles.qss paints most
            # of the chrome and is authored dark, so switching without
            # this leaves a light palette under a dark stylesheet.
            application.setStyleSheet(build_stylesheet(load_stylesheet(), theme))
        apply_action_icons(self)
        self.toggle_theme_action.setText(
            self.tr("Switch to &Dark Theme")
            if theme == "light"
            else self.tr("Switch to &Light Theme")
        )

    def _show_command_palette(self) -> None:
        palette = CommandPalette(self._build_commands(), self)
        if palette.exec() == QDialog.DialogCode.Accepted and palette.chosen is not None:
            palette.chosen.run()

    def _build_commands(self) -> list[Command]:
        """Every launchable action, flattened for search."""
        commands: list[Command] = []
        for tool_id, action in self.tool_actions.items():
            plugin = self.registry.get(tool_id)
            commands.append(
                Command(plugin.display_name, self.tr("Tool"), action.trigger, tool_id)
            )
        for kind, action in self.markup_actions.items():
            commands.append(Command(kind.title(), self.tr("Annotate"), action.trigger))
        for name, action in (
            (self.tr("Open"), self.open_action),
            (self.tr("Save As"), self.save_as_action),
            (self.tr("Undo"), self.undo_action),
            (self.tr("Redo"), self.redo_action),
            (self.tr("Find"), self.find_action),
            (self.tr("Fit Width"), self.fit_width_action),
            (self.tr("Fit Page"), self.fit_page_action),
            (self.tr("Show History"), self.toggle_history_action),
            (self.tr("Switch Theme"), self.toggle_theme_action),
            (self.tr("Build Workflow"), self.build_workflow_action),
            (self.tr("Run Workflow"), self.run_workflow_action),
        ):
            commands.append(Command(name, self.tr("Command"), action.trigger))
        return commands

    def _capture_ui_state(self) -> UiState:
        state = self.ui_state
        state.theme = self.theme
        state.window_width = self.width()
        state.window_height = self.height()
        state.show_toolbar = self.toolbar.isVisible()
        state.show_statusbar = self.statusBar().isVisible()
        state.show_history = self.history_dock.isVisible()
        state.thumbnail_width = self.thumbnail_size.width()
        tab = self.current_tab
        if tab is not None:
            state.zoom = tab.canvas.zoom
        state.open_documents = [
            str(t.controller.doc.source_path)
            for t in self.tabs()
            if t.controller.doc.source_path is not None
        ]
        return state

    def restore_ui_state(self) -> None:
        """Apply the saved layout, and reopen documents if allowed.

        Called from gui/main.py after the window is shown, for the same
        reason restore_autosaved_session is: a constructor that can
        block or open files is both bad practice and untestable.
        """
        state = self.ui_state
        if state.window_width > 0 and state.window_height > 0:
            self.resize(state.window_width, state.window_height)
        self.toggle_toolbar_action.setChecked(state.show_toolbar)
        self.toggle_statusbar_action.setChecked(state.show_statusbar)
        self.toggle_history_action.setChecked(state.show_history)
        if state.thumbnail_width > 0:
            self._set_thumbnail_zoom(state.thumbnail_width)
        if not state.reopen_documents:
            return
        for path_str in state.open_documents:
            path = Path(path_str)
            if path.exists():
                self._open_document_path(path, PLACEMENT_NEW_TAB)
        if state.zoom > 0:
            for tab in self.tabs():
                tab.canvas.set_zoom(state.zoom)

    def _toggle_sidebar(self, checked: bool) -> None:
        for tab in self.tabs():
            tab.sidebar.setVisible(checked)

    def _toggle_toolbar(self, checked: bool) -> None:
        self.toolbar.setVisible(checked)

    def _toggle_statusbar(self, checked: bool) -> None:
        self.statusBar().setVisible(checked)

    def _toggle_full_screen(self, checked: bool) -> None:
        if checked:
            self.showFullScreen()
        else:
            self.showNormal()

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
            # until it actually has a document - see _add_tab's docstring
            # for the black-empty-tab bug this avoids.
            tab = self._add_tab(activate=False)
            opened_new_tab = True

        try:
            tab.controller.open_document(path)
            # A *different document* is now in this tab. The cache is
            # keyed by (page, size) and not by the working file's path -
            # it has to be, since allocate_working_path mints a new name
            # for every operation and a path-keyed cache would never hit
            # after an edit. The cost of that choice is that the
            # renderer cannot tell "next revision of the same document"
            # from "an entirely different document", so the identity
            # change has to be declared here. Without this, Replace
            # Current Tab showed the previous document's thumbnails
            # (confirmed against real colour-sampled pages, and covered
            # by test_replacing_a_tabs_document_does_not_show_the_old_pages).
            # A no-op for a brand-new tab, whose cache is already empty.
            tab.renderer.invalidate(None)
            tab.canvas.invalidate(None)
        except PDFEditorError as exc:
            # A recent-file entry that fails to open (moved/deleted since
            # last time) is stale - drop it so it doesn't keep
            # reappearing in the menu instead of just erroring forever.
            self.recent_files.remove(path)
            if opened_new_tab:
                # Don't strand an empty tab for a document that never
                # opened; a replaced tab keeps its existing document,
                # which open_document() leaves untouched on failure. It
                # was never activated, so nothing was ever shown.
                self._discard_tab(tab)
            self._show_error(exc)
            self._refresh()
            return
        if opened_new_tab:
            # Now that it actually has a document: setCurrentWidget
            # handles activation for a second-or-later tab (firing
            # _refresh() itself); the explicit _refresh() below is still
            # needed for the very first tab, where Qt already silently
            # made it current inside addTab and this is a no-op that
            # fires no signal (see _add_tab's docstring).
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
        """Returns True if the document was actually saved (used by the
        unsaved-changes prompt to know whether to proceed). Defaults to
        the current tab; the prompt passes the specific tab it's asking
        about, which during a multi-tab close may not be the one that
        was active when the close started."""
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
        self._captured_state = self._capture_ui_state()
        if not self._close_all_tabs():
            event.ignore()
            return
        # Captured *before* the tabs are gone - closing them first
        # would record an empty document list every time.
        save_ui_state(self._captured_state)
        # A clean shutdown leaves nothing to recover - drop the pointer
        # so the next launch doesn't offer a stale session.
        mark_active_session(None)
        super().closeEvent(event)

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
            # activate=False: see _add_tab's docstring - don't switch to
            # (and render) the new tab until it actually has a document.
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
        # Either way the offer is consumed: the crashed session's journal
        # is wiped (its data is either now in a live tab or explicitly
        # declined) so it can't be offered again next launch.
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
        # Conservative by design: undo/redo restores a whole prior
        # document state, and the inverse of most operations is a
        # snapshot restore, which reports no affected pages.
        tab = self.current_tab
        if tab is not None:
            tab.renderer.invalidate(None)
            tab.canvas.invalidate(None)
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
        # Conservative by design: undo/redo restores a whole prior
        # document state, and the inverse of most operations is a
        # snapshot restore, which reports no affected pages.
        tab = self.current_tab
        if tab is not None:
            tab.renderer.invalidate(None)
            tab.canvas.invalidate(None)
        self._refresh()

    # --- rendering ------------------------------------------------------------

    def _refresh(self) -> None:
        tab = self.current_tab
        if tab is not None:
            self._render_tab(tab)
            self.setWindowTitle(f"{_APP_NAME} - {tab.document_name()}")
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
        # The action's checked state, not history_dock.isVisible(): a
        # child widget of a window that has never been shown always
        # reports invisible, so visibility would mean the panel never
        # populated under test - and it is the user's intent anyway.
        if self.toggle_history_action.isChecked():
            self._refresh_history()

    def _render_tab(self, tab: DocumentTab) -> None:
        working_path = tab.controller.doc.working_path
        if tab.controller.is_open and working_path is not None:
            # Both return as soon as their geometry exists; pixels stream
            # in (gui/rendering.py, gui/page_canvas.py).
            tab.renderer.render(working_path, self.thumbnail_size)
            tab.set_document(working_path)
        else:
            tab.thumbnail_list.clear()
            tab.clear_document()

    def _update_action_state(self) -> None:
        controller = self.controller
        is_open = controller is not None and controller.is_open
        tab_count = self.tab_widget.count()
        self.save_as_action.setEnabled(is_open)
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
