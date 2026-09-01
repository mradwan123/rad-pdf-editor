"""Tab lifecycle for `MainWindow`: create, label, close, discard.

Split out of `gui/main_window.py` in Phase 6a (docs/GUI_PLAN.md §3.1).
See `gui/window_parts.py` for why this is a mixin rather than a
collaborator object.

Each tab is an independently editable document backed by its own
`AppController` (`gui/document_tab.py`) - its own session temp dir,
undo/redo stack and dirty flag - so closing one tab wipes that
document's working files and nothing else.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu, QMessageBox

from gui.controller import AppController
from gui.document_tab import DocumentTab
from gui.window_parts import WindowPart

#: Prefix marking a tab whose document has unsaved changes.
DIRTY_TAB_MARKER = "• "


class TabManagementMixin(WindowPart):
    """Tab creation, labelling, closing and the unsaved-changes gate."""

    def _add_tab(
        self, controller: AppController | None = None, *, activate: bool = True
    ) -> DocumentTab:
        """Create and wire up a new document tab, with no document in
        it yet - the caller loads or builds one immediately afterward.

        `activate=True` (the default) makes the new tab current right
        away. Every real caller passes `activate=False` instead and
        activates the tab itself only once it actually has a document,
        via `self.tab_widget.setCurrentWidget(tab)` (or an explicit
        `_refresh()` - see below for why both are needed). `activate=True`
        is kept as the default only for callers (e.g. tests) that don't
        need that care.

        Found and fixed here, confirmed by grab()ing the real window,
        not just reasoned about: `setCurrentIndex` makes the tab current
        *synchronously*, which fires `currentChanged` ->
        `_on_current_tab_changed` -> `_refresh()` immediately - before
        the caller has had a chance to open/build a document in it.
        `_refresh()` at that point renders a real, capturable frame: an
        empty "Untitled" tab with a plain dark thumbnail grid (the
        grid's own background color, since there are zero items) and
        "0 page(s)" in the status bar - exactly the "black, empty tab,
        the PDF content never appears" bug report. A later explicit
        `_refresh()` call (once the document is actually loaded)
        overwrites this in the same call stack with no event-loop turn
        in between, so a purely synchronous script self-heals too fast
        to see it - but any real repaint trigger in between (a slow file
        copy, a window-manager-driven redraw) can expose the empty
        frame, and grab() proves it exists as a real renderable state,
        not just a timing curiosity.

        `activate=False` blocks the tab widget's signals for the
        `addTab` call too, not just skips `setCurrentIndex` - adding the
        very *first* tab to an empty `QTabWidget` makes Qt select it
        automatically (confirmed directly: `addTab` alone, with no
        `setCurrentIndex` call at all, still fires `currentChanged`), so
        `activate=False` has to suppress that emission as well or the
        first-tab-ever case would hit the exact same premature render
        this exists to prevent. The tab's *actual* current-ness
        (`tab_widget.currentIndex()`/`currentWidget()`) is unaffected by
        blocking signals - only our own signal-driven refresh is - so a
        caller opening the very first document still needs an explicit
        `_refresh()` once loading succeeds: `setCurrentWidget` would be
        a no-op there (Qt already made it current, silently, during
        `addTab`) and wouldn't re-fire `currentChanged` on its own.
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
        # Before close_session(): the renderer holds an open
        # QPdfDocument on the working file, and Windows refuses to
        # overwrite or unlink an open file, which would defeat the
        # secure wipe (the same trap gui/placement_canvas.py hit).
        tab.renderer.release()
        tab.canvas.release()
        tab.controller.close_session()
        tab.deleteLater()
        self._mark_active_session()

    def _tab_label(self, tab: DocumentTab) -> str:
        name = tab.document_name()
        return f"{DIRTY_TAB_MARKER}{name}" if tab.controller.is_dirty else name

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
