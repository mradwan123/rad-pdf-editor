"""Construction of `MainWindow`'s actions, menus and toolbar.

Split out of `gui/main_window.py` in Phase 6a (docs/GUI_PLAN.md §3.1):
this is ~200 lines of pure setup that the window ran once at
construction and never touched again, and Phase 6 adds a whole toolbar
of editing tools to it.

These are free functions taking the window rather than a mixin,
because nothing here is called after construction - there is no
behaviour to inherit, only wiring to perform. Every `QAction` is still
assigned onto the window (`window.undo_action`, ...) exactly as
before, so every existing caller and test keeps working unchanged.

`window.tr()` is used rather than a module-level helper so the
translation context stays "MainWindow" for strings that were already
collected under it (SPEC.md 6.2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QPalette
from PySide6.QtWidgets import QToolBar

from gui.dialogs.tool_dialog_registry import TOOL_DIALOGS
from gui.icons import build_icon

if TYPE_CHECKING:
    from gui.main_window import MainWindow


def build_actions(window: MainWindow) -> None:
    """Build every action, menu and the toolbar, and attach them to
    `window`. Called once from `MainWindow.__init__`."""
    _build_file_menu(window)
    _build_edit_menu(window)
    _build_tools_menu(window)
    _build_annotate_menu(window)
    _build_workflows_menu(window)
    build_view_menu(window)
    # After every action exists: the toolbar draws from all of them.
    _build_toolbar(window)
    apply_action_icons(window)


def _build_file_menu(window: MainWindow) -> None:
    window.open_action = QAction(window.tr("&Open..."), window)
    window.open_action.setShortcut("Ctrl+O")
    window.open_action.triggered.connect(window._open_document)

    # Lambdas, not bare bound methods, for every slot whose first
    # parameter is optional: QAction.triggered carries a `checked`
    # bool, which PySide6 would happily bind to `_save_as(tab=...)` /
    # `_close_other_tabs(index=...)` as a positional argument.
    window.save_as_action = QAction(window.tr("&Save As..."), window)
    window.save_as_action.setShortcut("Ctrl+S")
    window.save_as_action.triggered.connect(lambda: window._save_as())

    window.close_action = QAction(window.tr("&Close Tab"), window)
    window.close_action.setShortcut("Ctrl+W")
    window.close_action.triggered.connect(window._close_document)

    window.close_other_tabs_action = QAction(window.tr("Close Ot&her Tabs"), window)
    window.close_other_tabs_action.triggered.connect(lambda: window._close_other_tabs())

    window.close_all_tabs_action = QAction(window.tr("Close &All Tabs"), window)
    window.close_all_tabs_action.triggered.connect(lambda: window._close_all_tabs())

    # QTabWidget has no built-in tab cycling - these are wired
    # explicitly (and live in the File menu so their shortcuts are
    # actually registered with the window, not just declared).
    window.next_tab_action = QAction(window.tr("&Next Tab"), window)
    window.next_tab_action.setShortcut("Ctrl+Tab")
    window.next_tab_action.triggered.connect(lambda: window._cycle_tab(1))

    window.previous_tab_action = QAction(window.tr("&Previous Tab"), window)
    window.previous_tab_action.setShortcut("Ctrl+Shift+Tab")
    window.previous_tab_action.triggered.connect(lambda: window._cycle_tab(-1))

    file_menu = window.menuBar().addMenu(window.tr("&File"))
    file_menu.addAction(window.open_action)
    window.recent_files_menu = file_menu.addMenu(window.tr("Open &Recent"))
    window.recent_files_menu.aboutToShow.connect(window._populate_recent_files_menu)
    file_menu.addAction(window.save_as_action)
    file_menu.addSeparator()
    file_menu.addAction(window.close_action)
    file_menu.addAction(window.close_other_tabs_action)
    file_menu.addAction(window.close_all_tabs_action)
    file_menu.addSeparator()
    file_menu.addAction(window.next_tab_action)
    file_menu.addAction(window.previous_tab_action)


def _build_edit_menu(window: MainWindow) -> None:
    window.undo_action = QAction(window.tr("&Undo"), window)
    window.undo_action.setShortcut("Ctrl+Z")
    window.undo_action.triggered.connect(window._undo)

    window.redo_action = QAction(window.tr("&Redo"), window)
    window.redo_action.setShortcut("Ctrl+Shift+Z")
    window.redo_action.triggered.connect(window._redo)

    window.copy_action = QAction(window.tr("&Copy"), window)
    window.copy_action.setShortcut(QKeySequence.StandardKey.Copy)
    window.copy_action.triggered.connect(window._copy_selection)

    window.select_all_action = QAction(window.tr("Select &All on Page"), window)
    window.select_all_action.setShortcut(QKeySequence.StandardKey.SelectAll)
    window.select_all_action.triggered.connect(window._select_all_on_page)

    window.find_action = QAction(window.tr("&Find..."), window)
    window.find_action.setShortcut(QKeySequence.StandardKey.Find)
    window.find_action.triggered.connect(window._find)

    edit_menu = window.menuBar().addMenu(window.tr("&Edit"))
    edit_menu.addAction(window.undo_action)
    edit_menu.addAction(window.redo_action)
    edit_menu.addSeparator()
    edit_menu.addAction(window.copy_action)
    edit_menu.addAction(window.select_all_action)
    edit_menu.addSeparator()
    edit_menu.addAction(window.find_action)


def _tool_categories(window: MainWindow) -> list[tuple[str, list[str]]]:
    """(submenu label, ordered tool_ids) - grouped so the Tools menu
    doesn't grow into one flat 30+-item list as new tool_ids are added.
    Every TOOL_DIALOGS key must appear in exactly one group (checked by
    the caller) so a forgotten category can't silently drop a tool."""
    return [
        (
            window.tr("&Organize Pages"),
            ["merge", "extract_pages", "reorder_pages", "rotate_pages", "delete_pages", "flip"],
        ),
        (
            window.tr("&Edit and Design"),
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
            window.tr("F&orms and Signatures"),
            ["fill_form", "sign", "create_form_field", "flatten", "remove_annotations"],
        ),
        (window.tr("&Security"), ["protect", "unlock", "redact"]),
        (window.tr("&Document Properties"), ["set_metadata", "rename", "compress"]),
        (
            window.tr("Convert &from PDF"),
            ["pdf_to_docx", "pdf_to_pptx", "pdf_to_xlsx", "pdf_to_html", "pdf_to_jpg"],
        ),
        (
            window.tr("Convert &to PDF"),
            ["docx_to_pdf", "pptx_to_pdf", "xlsx_to_pdf", "html_to_pdf", "jpg_to_pdf"],
        ),
        # Phase 4 (Scans) didn't exist when the seven categories above
        # were first drawn up, and none of them is a clean fit -
        # OCR/Deskew/Repair operate on a whole scanned/damaged
        # document, not page layout, form/security, or format
        # conversion. An eighth category, rather than forcing one of
        # these into a group it doesn't belong in.
        (window.tr("Scans &and Repair"), ["ocr", "deskew", "repair"]),
    ]


def _build_tools_menu(window: MainWindow) -> None:
    tools_menu = window.menuBar().addMenu(window.tr("&Tools"))
    categorized_tool_ids: set[str] = set()
    for category_label, tool_ids in _tool_categories(window):
        category_menu = tools_menu.addMenu(category_label)
        for tool_id in tool_ids:
            dialog_cls = TOOL_DIALOGS[tool_id]
            plugin = window.registry.get(tool_id)
            action = QAction(plugin.display_name, window)
            action.triggered.connect(window._make_tool_handler(tool_id, dialog_cls))
            category_menu.addAction(action)
            window.tool_actions[tool_id] = action
            categorized_tool_ids.add(tool_id)
    if categorized_tool_ids != set(TOOL_DIALOGS):
        missing = sorted(set(TOOL_DIALOGS) - categorized_tool_ids)
        raise ValueError(f"Tools menu categories missing tool_id(s): {missing}")


#: (action attribute, label, annotation kind). Text markup acts on the
#: current text selection rather than a dragged rect - the familiar
#: gesture, and a reuse of 6c's selection machinery.
_MARKUP_TOOLS = [
    ("highlight_action", "&Highlight", "highlight"),
    ("underline_action", "&Underline", "underline"),
    ("strikeout_action", "&Strikeout", "strikeout"),
    ("squiggly_action", "S&quiggly", "squiggly"),
]

#: (action attribute, label, canvas tool). These are drawn on the page.
_DRAW_TOOLS = [
    ("select_tool_action", "&Select Text", "select"),
    ("rect_tool_action", "&Rectangle", "rect"),
    ("circle_tool_action", "&Ellipse", "circle"),
    ("line_tool_action", "&Line", "line"),
    ("ink_tool_action", "&Freehand", "ink"),
    ("note_tool_action", "Sticky &Note", "note"),
    ("redact_tool_action", "&Redact Region", "redact"),
    ("edit_text_tool_action", "&Edit Text (experimental)", "edit_text"),
]


def _build_annotate_menu(window: MainWindow) -> None:
    menu = window.menuBar().addMenu(window.tr("&Annotate"))

    for attribute, label, kind in _MARKUP_TOOLS:
        action = QAction(window.tr(label), window)
        action.triggered.connect(window._make_markup_handler(kind))
        setattr(window, attribute, action)
        menu.addAction(action)
        window.markup_actions[kind] = action

    menu.addSeparator()

    # Exactly one draw tool is active at a time.
    window.tool_group = QActionGroup(window)
    window.tool_group.setExclusive(True)
    for attribute, label, tool in _DRAW_TOOLS:
        action = QAction(window.tr(label), window)
        action.setCheckable(True)
        action.setChecked(tool == "select")
        action.triggered.connect(window._make_canvas_tool_handler(tool))
        window.tool_group.addAction(action)
        setattr(window, attribute, action)
        menu.addAction(action)
        window.canvas_tool_actions[tool] = action

    menu.addSeparator()
    window.delete_annotation_action = QAction(window.tr("&Delete Annotation"), window)
    window.delete_annotation_action.triggered.connect(window._delete_annotation_under_cursor)
    menu.addAction(window.delete_annotation_action)


def _build_workflows_menu(window: MainWindow) -> None:
    # Building/running a workflow is document-independent (Build
    # doesn't touch any open document at all; Run works against a
    # standalone input/output pair), so these two actions are hand-wired
    # here rather than going through TOOL_DIALOGS / the Tools-menu loop,
    # and are never added to window.tool_actions (which
    # _update_action_state disables when no document is open).
    window.build_workflow_action = QAction(window.tr("&Build Workflow..."), window)
    window.build_workflow_action.triggered.connect(window._build_workflow)

    window.run_workflow_action = QAction(window.tr("&Run Workflow..."), window)
    window.run_workflow_action.triggered.connect(window._run_workflow)

    workflows_menu = window.menuBar().addMenu(window.tr("&Workflows"))
    workflows_menu.addAction(window.build_workflow_action)
    workflows_menu.addAction(window.run_workflow_action)


#: (action attribute, icon name). Phase 6g - before this the toolbar
#: was four text labels and the app had no iconography at all.
_ACTION_ICONS = [
    ("open_action", "open"),
    ("save_as_action", "save"),
    ("undo_action", "undo"),
    ("redo_action", "redo"),
    ("zoom_in_action", "zoom_in"),
    ("zoom_out_action", "zoom_out"),
    ("fit_width_action", "fit_width"),
    ("fit_page_action", "fit_page"),
    ("find_action", "find"),
    ("select_tool_action", "select"),
    ("rect_tool_action", "rect"),
    ("circle_tool_action", "circle"),
    ("line_tool_action", "line"),
    ("ink_tool_action", "ink"),
    ("note_tool_action", "note"),
    ("redact_tool_action", "redact"),
    ("highlight_action", "highlight"),
    ("delete_annotation_action", "delete"),
    ("toggle_history_action", "history"),
]


def apply_action_icons(window: MainWindow) -> None:
    """(Re)draw every action's icon in the current palette.

    Called again on a theme change - the icons are painted, not loaded,
    so re-theming is a redraw rather than a second set of assets.
    """
    colour = window.palette().color(QPalette.ColorRole.ButtonText)
    for attribute, name in _ACTION_ICONS:
        action = getattr(window, attribute, None)
        if action is not None:
            action.setIcon(build_icon(name, colour))


def _build_toolbar(window: MainWindow) -> None:
    window.toolbar = QToolBar(window.tr("Main"))
    window.toolbar.setAccessibleName(window.tr("Main toolbar"))
    window.toolbar.setIconSize(QSize(20, 20))
    window.addToolBar(window.toolbar)
    # Grouped by what the user is doing, not by which menu the action
    # happens to live in.
    for group in (
        [window.open_action, window.save_as_action],
        [window.undo_action, window.redo_action],
        [window.zoom_out_action, window.zoom_in_action, window.fit_width_action,
         window.fit_page_action],
        [window.find_action],
        [window.select_tool_action, window.highlight_action, window.rect_tool_action,
         window.ink_tool_action, window.note_tool_action, window.redact_tool_action],
        [window.toggle_history_action],
    ):
        for action in group:
            window.toolbar.addAction(action)
        window.toolbar.addSeparator()


def build_view_menu(window: MainWindow) -> None:
    # Phase 6c: the page view is the primary pane, so the standard zoom
    # shortcuts act on it. Thumbnail sizing keeps its own actions under
    # Ctrl+Shift - it is now sidebar navigation, not the main view.
    window.zoom_in_action = QAction(window.tr("Zoom &In"), window)
    # QKeySequence.StandardKey.ZoomIn resolves to the literal "Ctrl++"
    # on this platform (confirmed via QKeySequence.keyBindings), but
    # '+' isn't its own physical key on most keyboard layouts - it's
    # Shift+'=' on a US layout, and varies further on non-US ones.
    # Relying on the standard key alone means a user who presses the
    # unshifted "Ctrl+=" (the binding browsers/editors also accept for
    # exactly this reason) sees nothing happen. setShortcuts (plural)
    # keeps every platform alternate StandardKey.ZoomIn already
    # provides and adds "Ctrl+=" explicitly, rather than replacing the
    # standard binding outright.
    window.zoom_in_action.setShortcuts(
        [*QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomIn), QKeySequence("Ctrl+=")]
    )
    window.zoom_in_action.triggered.connect(window._page_zoom_in)

    window.zoom_out_action = QAction(window.tr("Zoom &Out"), window)
    window.zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
    window.zoom_out_action.triggered.connect(window._page_zoom_out)

    window.reset_zoom_action = QAction(window.tr("&Reset Zoom"), window)
    window.reset_zoom_action.setShortcut("Ctrl+0")
    window.reset_zoom_action.triggered.connect(window._page_reset_zoom)

    window.fit_width_action = QAction(window.tr("Fit &Width"), window)
    window.fit_width_action.setShortcut("Ctrl+1")
    window.fit_width_action.triggered.connect(window._fit_width)

    window.fit_page_action = QAction(window.tr("Fit &Page"), window)
    window.fit_page_action.setShortcut("Ctrl+2")
    window.fit_page_action.triggered.connect(window._fit_page)

    window.larger_thumbnails_action = QAction(window.tr("&Larger Thumbnails"), window)
    window.larger_thumbnails_action.setShortcut("Ctrl+Shift+=")
    window.larger_thumbnails_action.triggered.connect(window._zoom_in)

    window.smaller_thumbnails_action = QAction(window.tr("&Smaller Thumbnails"), window)
    window.smaller_thumbnails_action.setShortcut("Ctrl+Shift+-")
    window.smaller_thumbnails_action.triggered.connect(window._zoom_out)

    window.reset_thumbnails_action = QAction(window.tr("Reset &Thumbnail Size"), window)
    window.reset_thumbnails_action.setShortcut("Ctrl+Shift+0")
    window.reset_thumbnails_action.triggered.connect(window._reset_zoom)

    window.toggle_toolbar_action = QAction(window.tr("Show &Toolbar"), window)
    window.toggle_toolbar_action.setCheckable(True)
    window.toggle_toolbar_action.setChecked(True)
    window.toggle_toolbar_action.toggled.connect(window._toggle_toolbar)

    window.toggle_sidebar_action = QAction(window.tr("Show &Sidebar"), window)
    window.toggle_sidebar_action.setCheckable(True)
    window.toggle_sidebar_action.setChecked(True)
    window.toggle_sidebar_action.toggled.connect(window._toggle_sidebar)

    window.toggle_statusbar_action = QAction(window.tr("Show Status &Bar"), window)
    window.toggle_statusbar_action.setCheckable(True)
    window.toggle_statusbar_action.setChecked(True)
    window.toggle_statusbar_action.toggled.connect(window._toggle_statusbar)

    window.toggle_history_action = QAction(window.tr("Show &History"), window)
    window.toggle_history_action.setCheckable(True)
    window.toggle_history_action.toggled.connect(window._toggle_history)

    window.toggle_theme_action = QAction(window.tr("Switch to &Light Theme"), window)
    window.toggle_theme_action.triggered.connect(window._toggle_theme)

    window.command_palette_action = QAction(window.tr("&Command Palette..."), window)
    window.command_palette_action.setShortcut("Ctrl+Shift+P")
    window.command_palette_action.triggered.connect(window._show_command_palette)

    window.full_screen_action = QAction(window.tr("&Full Screen"), window)
    window.full_screen_action.setShortcut("F11")
    window.full_screen_action.setCheckable(True)
    window.full_screen_action.toggled.connect(window._toggle_full_screen)

    view_menu = window.menuBar().addMenu(window.tr("&View"))
    view_menu.addAction(window.zoom_in_action)
    view_menu.addAction(window.zoom_out_action)
    view_menu.addAction(window.reset_zoom_action)
    view_menu.addSeparator()
    view_menu.addAction(window.fit_width_action)
    view_menu.addAction(window.fit_page_action)
    view_menu.addSeparator()
    view_menu.addAction(window.larger_thumbnails_action)
    view_menu.addAction(window.smaller_thumbnails_action)
    view_menu.addAction(window.reset_thumbnails_action)
    view_menu.addSeparator()
    view_menu.addAction(window.toggle_toolbar_action)
    view_menu.addAction(window.toggle_sidebar_action)
    view_menu.addAction(window.toggle_statusbar_action)
    view_menu.addAction(window.toggle_history_action)
    view_menu.addSeparator()
    view_menu.addAction(window.toggle_theme_action)
    view_menu.addAction(window.command_palette_action)
    view_menu.addSeparator()
    view_menu.addAction(window.full_screen_action)
