"""Unit tests for core/security/secure_delete.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.errors import SecurityError
from core.security.secure_delete import secure_delete_dir, secure_delete_file


def test_secure_delete_file_removes_the_file(tmp_path: Path) -> None:
    f = tmp_path / "secret.txt"
    f.write_bytes(b"confidential" * 100)
    secure_delete_file(f)
    assert not f.exists()


def test_secure_delete_file_is_noop_for_missing_file(tmp_path: Path) -> None:
    secure_delete_file(tmp_path / "does-not-exist.txt")  # must not raise


def test_secure_delete_file_rejects_zero_passes(tmp_path: Path) -> None:
    f = tmp_path / "secret.txt"
    f.write_bytes(b"x")
    with pytest.raises(SecurityError):
        secure_delete_file(f, passes=0)


def test_secure_delete_dir_removes_tree(tmp_path: Path) -> None:
    d = tmp_path / "session"
    (d / "nested").mkdir(parents=True)
    (d / "a.pdf").write_bytes(b"file a")
    (d / "nested" / "b.pdf").write_bytes(b"file b")

    secure_delete_dir(d)
    assert not d.exists()


def test_secure_delete_dir_is_noop_for_missing_dir(tmp_path: Path) -> None:
    secure_delete_dir(tmp_path / "does-not-exist")  # must not raise


def test_secure_delete_dir_handles_empty_dir(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    secure_delete_dir(d)
    assert not d.exists()
