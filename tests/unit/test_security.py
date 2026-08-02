"""Unit tests for core/ops/security.py (Protect, Unlock)."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest

from core.errors import OperationError, SecurityError
from core.model.document import DocumentSession
from core.ops.security import ProtectOperation, UnlockOperation


def _make_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.save(path)
    return path


def _session(tmp_path: Path) -> DocumentSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    working = _make_pdf(session_dir / "working.pdf")
    return DocumentSession(working_path=working, source_path=None)


def _is_encrypted(path: Path) -> bool:
    with pikepdf.Pdf.open(path, password="secret", suppress_warnings=True) as pdf:
        return pdf.is_encrypted


def test_protect_encrypts_document(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(ProtectOperation(user_password="secret"))
    assert _is_encrypted(result.working_path)


def test_protect_wrong_password_fails_to_open(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(ProtectOperation(user_password="secret"))
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.Pdf.open(result.working_path, password="wrong")


def test_protect_requires_nonempty_password(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    with pytest.raises(OperationError):
        doc.apply(ProtectOperation(user_password=""))


def test_protect_undo_restores_unencrypted_document(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    result = doc.apply(ProtectOperation(user_password="secret"))
    restored = result.undo()
    with pikepdf.Pdf.open(restored.working_path) as pdf:
        assert not pdf.is_encrypted


def test_unlock_removes_encryption(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    protected = doc.apply(ProtectOperation(user_password="secret"))
    unlocked = protected.apply(UnlockOperation(password="secret"))
    with pikepdf.Pdf.open(unlocked.working_path) as pdf:
        assert not pdf.is_encrypted


def test_unlock_wrong_password_raises_security_error(tmp_path: Path) -> None:
    doc = _session(tmp_path)
    protected = doc.apply(ProtectOperation(user_password="secret"))
    with pytest.raises(SecurityError):
        UnlockOperation(password="wrong").apply(protected)


def test_serialize_never_includes_password(tmp_path: Path) -> None:
    op = ProtectOperation(user_password="topsecret", owner_password="alsosecret")
    serialized = op.serialize()
    assert "topsecret" not in str(serialized)
    assert "alsosecret" not in str(serialized)
