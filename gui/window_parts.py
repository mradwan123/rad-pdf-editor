"""Typing base for `MainWindow`'s behaviour mixins.

Phase 6a (docs/GUI_PLAN.md §3.1) split `MainWindow`'s ~1160 lines into
focused modules. Tab management and tool execution moved out as
*mixins* rather than collaborator objects, deliberately: roughly twenty
of those methods are called directly on the window by the existing test
suite (`window._close_tab(...)`, `patch.object(MainWindow, "_add_tab")`,
...), and a mixin keeps every one of them resolving on `MainWindow`
exactly as before. A collaborator object would have needed a delegating
shim per method to achieve the same thing.

The mixins still have to type-check under `mypy --strict` while
referring to state that `MainWindow` owns (`self.tab_widget`,
`self.registry`) and to methods that live in a *different* part
(`self._refresh`, `self._show_error`). `WindowPart` declares that
shared surface in one place.

It exists only for the type checker:

- **To mypy** it is a `QMainWindow` carrying every shared attribute and
  cross-part method signature, so a mixin referring to `self.tr()`,
  `self.statusBar()` or `self._refresh()` checks cleanly.
- **At runtime** it is an empty plain class. Nothing is inherited,
  nothing can be shadowed, and `MainWindow` mixes plain-Python classes
  into a single `QMainWindow` base rather than inheriting from two
  QObject-derived classes - which PySide6 does not support.

That split matters: a stub with a real body would sit ahead of
`QMainWindow` in the MRO and could silently shadow a genuine Qt method.
Guarding the whole class behind `TYPE_CHECKING` makes that impossible.

Only methods a mixin calls on *another* part are declared here. A
method both declared here and defined in a mixin is checked as an
override, so the signatures must match exactly - which is why nothing
is declared more loosely than it is implemented.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from PySide6.QtCore import QPoint, QSize
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMainWindow, QStackedWidget, QTabWidget, QToolBar, QWidget

    from core.registry.registry import Registry
    from core.session.audit_log import AuditLog
    from core.session.recent_files import RecentFiles
    from gui.controller import AppController
    from gui.document_tab import DocumentTab

    class WindowPart(QMainWindow):
        """The `MainWindow` surface every behaviour mixin relies on."""

        # --- app-wide state, built in MainWindow.__init__ ---
        registry: Registry
        audit_log: AuditLog
        recent_files: RecentFiles
        tool_actions: dict[str, QAction]

        # --- widgets ---
        tab_widget: QTabWidget
        stack: QStackedWidget
        empty_state: QWidget
        toolbar: QToolBar
        thumbnail_size: QSize

        # --- current-tab views (properties on MainWindow) ---
        @property
        def current_tab(self) -> DocumentTab | None: ...

        @property
        def controller(self) -> AppController | None: ...

        def tabs(self) -> list[DocumentTab]: ...

        # --- implemented by MainWindow itself ---
        def _refresh(self) -> None: ...
        def _mark_active_session(self) -> None: ...
        def _show_error(self, exc: Exception) -> None: ...
        def _show_error_message(self, message: str) -> None: ...
        def _save_as(self, tab: DocumentTab | None = None) -> bool: ...
        def _open_document(self) -> None: ...
        def _open_document_path(self, path: Path, placement: str | None = None) -> None: ...
        def _populate_recent_files_menu(self) -> None: ...
        def _undo(self) -> None: ...
        def _redo(self) -> None: ...
        def _close_document(self) -> None: ...
        def _zoom_in(self) -> None: ...
        def _zoom_out(self) -> None: ...
        def _reset_zoom(self) -> None: ...
        def _page_zoom_in(self) -> None: ...
        def _page_zoom_out(self) -> None: ...
        def _page_reset_zoom(self) -> None: ...
        def _fit_width(self) -> None: ...
        def _fit_page(self) -> None: ...
        def _toggle_sidebar(self, checked: bool) -> None: ...
        def _toggle_toolbar(self, checked: bool) -> None: ...
        def _toggle_statusbar(self, checked: bool) -> None: ...
        def _toggle_full_screen(self, checked: bool) -> None: ...

        # --- implemented by the *other* mixin ---
        # TabManagementMixin needs these two from ToolRunnerMixin (it
        # connects each new tab's signals to them in _add_tab).
        def _on_thumbnails_reordered(self, tab: DocumentTab) -> None: ...
        def _show_thumbnail_context_menu(self, tab: DocumentTab, pos: QPoint) -> None: ...

        # ToolRunnerMixin needs these three from TabManagementMixin.
        def _add_tab(
            self, controller: AppController | None = None, *, activate: bool = True
        ) -> DocumentTab: ...
        def _discard_tab(self, tab: DocumentTab) -> None: ...
        def _update_tab_labels(self) -> None: ...

else:

    class WindowPart:
        """Empty at runtime - see the module docstring."""
