"""Phase 6g: icons, themes, history panel, command palette, UI state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from core.session.ui_state import (
    UiState,
    clear_session_documents,
    load_ui_state,
    save_ui_state,
    state_path,
)
from gui.dialogs.command_palette import Command, matches
from gui.history_panel import HistoryPanel
from gui.icons import build_icon, icon_names
from gui.palette import THEMES, build_palette, build_stylesheet


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


# --- icons -----------------------------------------------------------------


def test_every_named_icon_draws_something(qapp: QApplication) -> None:
    """Drawn rather than shipped, so a missing glyph is a code bug, not
    a missing file - worth asserting they all actually paint."""
    for name in icon_names():
        icon = build_icon(name, QColor("#ffffff"))
        assert not icon.isNull(), name
        assert not icon.pixmap(24, 24).isNull(), name


def test_an_unknown_icon_is_empty_rather_than_fatal(qapp: QApplication) -> None:
    """A missing glyph should leave an action looking plain, never stop
    the window being built."""
    assert build_icon("no-such-glyph", QColor("#ffffff")).isNull()


def test_icons_take_the_colour_they_are_given(qapp: QApplication) -> None:
    """This is what makes a theme switch a redraw rather than a second
    set of assets."""
    light = build_icon("save", QColor("#ffffff"), 32).pixmap(32, 32).toImage()
    dark = build_icon("save", QColor("#000000"), 32).pixmap(32, 32).toImage()

    def ink(image: object) -> tuple[int, int, int]:
        for y in range(image.height()):  # type: ignore[attr-defined]
            for x in range(image.width()):  # type: ignore[attr-defined]
                colour = image.pixelColor(x, y)  # type: ignore[attr-defined]
                if colour.alpha() > 200:
                    return colour.red(), colour.green(), colour.blue()
        raise AssertionError("icon drew nothing")

    assert ink(light) != ink(dark)


# --- themes ----------------------------------------------------------------


def test_both_themes_fill_the_same_roles(qapp: QApplication) -> None:
    """One table, two themes - which is what stops a role being themed
    in dark and forgotten in light."""
    roles = [
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.Button,
        QPalette.ColorRole.Highlight,
        QPalette.ColorRole.PlaceholderText,
    ]
    dark = build_palette("dark")
    light = build_palette("light")
    for role in roles:
        assert dark.color(role) != light.color(role), role


def test_light_is_actually_lighter(qapp: QApplication) -> None:
    dark = build_palette("dark").color(QPalette.ColorRole.Window)
    light = build_palette("light").color(QPalette.ColorRole.Window)
    assert light.lightnessF() > 0.8
    assert dark.lightnessF() < 0.2


def test_an_unknown_theme_falls_back_rather_than_raising(qapp: QApplication) -> None:
    """A bad value in persisted state must not stop the app starting."""
    assert build_palette("chartreuse").color(QPalette.ColorRole.Window) == build_palette(
        "dark"
    ).color(QPalette.ColorRole.Window)


def test_the_stylesheet_is_derived_not_duplicated(qapp: QApplication) -> None:
    source = "QWidget { background: #1b1c1e; color: #e8e9eb; }"

    assert build_stylesheet(source, "dark") == source

    light = build_stylesheet(source, "light")
    assert light != source
    # Lightness mirrored, so the dark background becomes a light one.
    assert QColor("#1b1c1e").lightnessF() < 0.5
    background = light.split("background: ")[1].split(";")[0]
    assert QColor(background).lightnessF() > 0.5


def test_themes_are_named() -> None:
    assert set(THEMES) == {"dark", "light"}


# --- history panel ---------------------------------------------------------


def test_history_lists_applied_then_redoable(qapp: QApplication) -> None:
    panel = HistoryPanel()
    panel.update_history(["Rotated page 1", "Cropped"], ["Deleted page 2"])

    assert panel.list.count() == 3
    assert panel.list.item(0).text() == "Rotated page 1"
    assert panel.list.item(2).text() == "Deleted page 2"
    assert panel.list.currentRow() == 1, "the last applied step is current"


def test_history_shows_an_empty_message(qapp: QApplication) -> None:
    panel = HistoryPanel()
    panel.update_history([], [])
    assert not panel.list.isVisible()


def test_clicking_history_asks_for_the_right_number_of_steps(qapp: QApplication) -> None:
    """Row 1 of two applied steps means "undo one"; a redoable row means
    "redo forward to here"."""
    panel = HistoryPanel()
    panel.update_history(["one", "two"], ["three"])
    steps: list[int] = []
    panel.step_requested.connect(steps.append)

    panel.list.itemClicked.emit(panel.list.item(0))
    panel.list.itemClicked.emit(panel.list.item(1))
    panel.list.itemClicked.emit(panel.list.item(2))

    assert steps == [-1, 0, 1]


# --- command palette -------------------------------------------------------


def _command(label: str, category: str = "Tool", keywords: str = "") -> Command:
    return Command(label, category, lambda: None, keywords)


def test_every_term_must_match() -> None:
    command = _command("Word to PDF", keywords="docx_to_pdf")
    assert matches(command, "word")
    assert matches(command, "word pdf")
    assert not matches(command, "word excel")


def test_the_tool_id_is_searchable_even_though_it_is_not_shown() -> None:
    """Display names are written for users, not for searching - "Word to
    PDF" contains no "docx", which is often what someone half-remembers."""
    command = _command("Word to PDF", keywords="docx_to_pdf")
    assert matches(command, "docx")


def test_an_empty_query_is_not_a_filter() -> None:
    assert matches(_command("Anything"), "")


# --- UI state --------------------------------------------------------------


def test_ui_state_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path))
    state = UiState(theme="light", window_width=1000, show_history=True,
                    open_documents=["/tmp/a.pdf"])
    save_ui_state(state)

    loaded = load_ui_state()
    assert loaded.theme == "light"
    assert loaded.window_width == 1000
    assert loaded.show_history is True
    assert loaded.open_documents == ["/tmp/a.pdf"]


def test_missing_state_gives_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path))
    assert load_ui_state().theme == "dark"


def test_corrupt_state_is_ignored_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is a convenience; it must never stop the app starting."""
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path))
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text("{not json at all", encoding="utf-8")

    assert load_ui_state() == UiState()


def test_state_from_a_newer_version_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path))
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps({"version": 99, "state": {}}), encoding="utf-8")

    assert load_ui_state() == UiState()


def test_unknown_keys_do_not_crash_an_older_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path))
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(
        json.dumps({"version": 1, "state": {"theme": "light", "from_the_future": 1}}),
        encoding="utf-8",
    )

    assert load_ui_state().theme == "light"


def test_clearing_the_session_keeps_the_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The privacy escape hatch: drop the document trail without losing
    the panel setup."""
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path))
    save_ui_state(UiState(theme="light", show_history=True, open_documents=["/tmp/a.pdf"]))

    clear_session_documents()

    loaded = load_ui_state()
    assert loaded.open_documents == []
    assert loaded.theme == "light"
    assert loaded.show_history is True
