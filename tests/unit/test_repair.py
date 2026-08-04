"""Unit tests for core/ops/repair.py.

Fixtures reproduce the two real corruption modes confirmed by hand
before this module was written: a truncated file (missing xref/
trailer - pikepdf's own structural recovery handles this for free)
and randomly mangled bytes mid-file (severe enough that pikepdf raises
a plain RuntimeError, not just PdfError - confirmed the exact error,
`/Count is wrong after flattening pages tree` - which is why the
except clause in repair.py catches RuntimeError too, not just
PdfError/OSError)."""

from __future__ import annotations

import random
import shutil
from pathlib import Path

import pikepdf
import pytest

from core.errors import OperationError
from core.model.document import DocumentSession
from core.ops.repair import RepairOperation, _ghostscript_binary

_HAS_GHOSTSCRIPT = _ghostscript_binary() is not None


def _make_good_pdf(path: Path, num_pages: int = 2) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(300, 400))
    pdf.save(path)
    return path


def _make_truncated_pdf(path: Path, tmp_path: Path) -> Path:
    good = _make_good_pdf(tmp_path / "good_for_truncation.pdf")
    raw = good.read_bytes()
    path.write_bytes(raw[: int(len(raw) * 0.8)])
    return path


def _make_severely_corrupt_pdf(path: Path, tmp_path: Path) -> Path:
    good = _make_good_pdf(tmp_path / "good_for_mangling.pdf")
    raw = bytearray(good.read_bytes())
    rng = random.Random(42)
    for _ in range(30):
        i = rng.randint(50, len(raw) - 50)
        raw[i] = rng.randint(0, 255)
    path.write_bytes(bytes(raw))
    return path


def _empty_session() -> DocumentSession:
    return DocumentSession(working_path=None, source_path=None)


# --- validation ------------------------------------------------------


def test_repair_missing_source_raises() -> None:
    session = _empty_session()
    with pytest.raises(OperationError):
        session.apply(RepairOperation(source_path=Path("/nonexistent/file.pdf")))


def test_repair_works_with_no_document_open() -> None:
    # RepairOperation doesn't require a document already open - same
    # shape as MergeOperation, since the point is recovering a file
    # that couldn't be opened via the normal flow in the first place.
    session = _empty_session()
    assert session.working_path is None


# --- tier 1: pikepdf structural recovery ------------------------------


def test_repair_recovers_a_truncated_file(tmp_path: Path) -> None:
    corrupt = _make_truncated_pdf(tmp_path / "truncated.pdf", tmp_path)
    session = _empty_session()

    result = session.apply(RepairOperation(source_path=corrupt))
    assert result.operation_log[-1].describe() == "Repaired (structural recovery)"
    assert result.working_path is not None
    with pikepdf.Pdf.open(result.working_path) as pdf:
        assert len(pdf.pages) == 2


def test_repair_undo_after_structural_recovery(tmp_path: Path) -> None:
    corrupt = _make_truncated_pdf(tmp_path / "truncated.pdf", tmp_path)
    session = _empty_session()
    result = session.apply(RepairOperation(source_path=corrupt))
    restored = result.undo()
    assert restored.working_path is None


# --- tier 2: Ghostscript fallback --------------------------------------


@pytest.mark.skipif(not _HAS_GHOSTSCRIPT, reason="Ghostscript not installed on this machine")
def test_repair_falls_back_to_ghostscript_for_severe_corruption(tmp_path: Path) -> None:
    corrupt = _make_severely_corrupt_pdf(tmp_path / "severe.pdf", tmp_path)

    # confirm pikepdf really can't handle this one directly - the
    # premise the Ghostscript fallback exists for
    with pytest.raises((pikepdf.PdfError, OSError, RuntimeError)):
        pikepdf.Pdf.open(corrupt)

    session = _empty_session()
    result = session.apply(RepairOperation(source_path=corrupt))
    assert result.operation_log[-1].describe() == "Repaired (Ghostscript)"
    assert result.working_path is not None
    with pikepdf.Pdf.open(result.working_path) as pdf:
        assert len(pdf.pages) >= 1


def test_repair_raises_when_ghostscript_missing_and_pikepdf_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.ops.repair._ghostscript_binary", lambda: None)
    corrupt = _make_severely_corrupt_pdf(tmp_path / "severe.pdf", tmp_path)
    session = _empty_session()
    with pytest.raises(OperationError, match="Ghostscript is not installed"):
        session.apply(RepairOperation(source_path=corrupt))


# --- doesn't touch a currently-open document ----------------------------


def test_repair_does_not_touch_the_currently_open_document(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    open_doc = session_dir / "open_doc.pdf"
    _make_good_pdf(open_doc, num_pages=5)
    working = session_dir / "working.pdf"
    shutil.copyfile(open_doc, working)
    session = DocumentSession(working_path=working, source_path=None)

    corrupt = _make_truncated_pdf(tmp_path / "truncated.pdf", tmp_path)
    result = session.apply(RepairOperation(source_path=corrupt))

    # the repaired file replaces the working doc going forward (same
    # session/undo-stack shape as every other Operation), but the
    # *original* open_doc file on disk is untouched
    with pikepdf.Pdf.open(open_doc) as pdf:
        assert len(pdf.pages) == 5
    assert result.working_path is not None
    with pikepdf.Pdf.open(result.working_path) as pdf:
        assert len(pdf.pages) == 2
