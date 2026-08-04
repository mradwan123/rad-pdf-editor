"""Unit tests for core/session/recent_files.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.session.recent_files import RecentFiles


@pytest.fixture(autouse=True)
def _isolated_app_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDFEDITOR_APP_DATA_DIR", str(tmp_path / "appdata"))


def test_starts_empty() -> None:
    assert RecentFiles().list() == []


def test_add_then_list_returns_most_recent_first() -> None:
    recent = RecentFiles()
    recent.add(Path("/docs/a.pdf"))
    recent.add(Path("/docs/b.pdf"))

    assert recent.list() == [Path("/docs/b.pdf"), Path("/docs/a.pdf")]


def test_re_adding_an_existing_path_moves_it_to_front_without_duplicating() -> None:
    recent = RecentFiles()
    recent.add(Path("/docs/a.pdf"))
    recent.add(Path("/docs/b.pdf"))
    recent.add(Path("/docs/a.pdf"))

    assert recent.list() == [Path("/docs/a.pdf"), Path("/docs/b.pdf")]


def test_list_is_capped_at_max_entries() -> None:
    recent = RecentFiles()
    for i in range(15):
        recent.add(Path(f"/docs/{i}.pdf"))

    entries = recent.list()
    assert len(entries) == 10
    assert entries[0] == Path("/docs/14.pdf")


def test_remove_drops_a_single_entry() -> None:
    recent = RecentFiles()
    recent.add(Path("/docs/a.pdf"))
    recent.add(Path("/docs/b.pdf"))

    recent.remove(Path("/docs/a.pdf"))

    assert recent.list() == [Path("/docs/b.pdf")]


def test_remove_of_an_absent_path_does_not_raise() -> None:
    recent = RecentFiles()
    recent.remove(Path("/docs/never-added.pdf"))
    assert recent.list() == []


def test_clear_empties_the_list() -> None:
    recent = RecentFiles()
    recent.add(Path("/docs/a.pdf"))

    recent.clear()

    assert recent.list() == []


def test_clear_with_nothing_saved_does_not_raise() -> None:
    RecentFiles().clear()


def test_state_persists_across_separate_instances() -> None:
    RecentFiles().add(Path("/docs/a.pdf"))
    assert RecentFiles().list() == [Path("/docs/a.pdf")]
