"""Unit tests for core/session/session_dir.py."""

from __future__ import annotations

from pathlib import Path

from core.session.session_dir import SessionTempDir


def test_creates_directory_under_root(tmp_path: Path) -> None:
    session = SessionTempDir(root=tmp_path)
    assert session.path.exists()
    assert session.path.parent == tmp_path
    session.close()


def test_close_securely_wipes_directory_and_contents(tmp_path: Path) -> None:
    session = SessionTempDir(root=tmp_path)
    (session.path / "working.pdf").write_bytes(b"confidential content")

    session.close()

    assert not session.path.exists()


def test_close_is_idempotent(tmp_path: Path) -> None:
    session = SessionTempDir(root=tmp_path)
    session.close()
    session.close()  # must not raise


def test_context_manager_closes_on_exit(tmp_path: Path) -> None:
    with SessionTempDir(root=tmp_path) as session:
        path = session.path
        assert path.exists()
    assert not path.exists()


def test_context_manager_closes_on_exception(tmp_path: Path) -> None:
    path = None
    try:
        with SessionTempDir(root=tmp_path) as session:
            path = session.path
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert path is not None
    assert not path.exists()


def test_distinct_sessions_get_distinct_directories(tmp_path: Path) -> None:
    a = SessionTempDir(root=tmp_path)
    b = SessionTempDir(root=tmp_path)
    assert a.path != b.path
    a.close()
    b.close()
