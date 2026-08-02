"""Integration test for the cli.main scripting entry point (SPEC.md's
/cli row - "reuses /core directly")."""

from __future__ import annotations

from pathlib import Path

import pikepdf

from cli.main import main


def _make_pdf(path: Path, num_pages: int) -> Path:
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.add_blank_page(page_size=(300, 400))
    pdf.save(path)
    return path


def test_merge_then_rotate_then_watermark_roundtrip(tmp_path: Path) -> None:
    a = _make_pdf(tmp_path / "a.pdf", 1)
    b = _make_pdf(tmp_path / "b.pdf", 2)
    merged = tmp_path / "merged.pdf"
    rotated = tmp_path / "rotated.pdf"
    watermarked = tmp_path / "watermarked.pdf"

    assert main(["merge", str(a), str(b), "-o", str(merged)]) == 0
    assert main(["rotate_pages", str(merged), "-o", str(rotated), "--angle", "90"]) == 0
    assert main(["watermark", str(rotated), "-o", str(watermarked), "--text", "DRAFT"]) == 0

    with pikepdf.Pdf.open(watermarked) as pdf:
        assert len(pdf.pages) == 3
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90


def test_protect_then_unlock_roundtrip(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 1)
    protected = tmp_path / "protected.pdf"
    unlocked = tmp_path / "unlocked.pdf"

    assert main(["protect", str(src), "-o", str(protected), "--user-password", "secret"]) == 0
    with pikepdf.Pdf.open(protected, password="secret") as pdf:
        assert pdf.is_encrypted

    assert main(["unlock", str(protected), "-o", str(unlocked), "--password", "secret"]) == 0
    with pikepdf.Pdf.open(unlocked) as pdf:
        assert not pdf.is_encrypted


def test_operating_on_encrypted_document_without_password_fails_cleanly(tmp_path: Path) -> None:
    src = _make_pdf(tmp_path / "in.pdf", 1)
    protected = tmp_path / "protected.pdf"
    out = tmp_path / "out.pdf"

    assert main(["protect", str(src), "-o", str(protected), "--user-password", "secret"]) == 0
    exit_code = main(["set_metadata", str(protected), "-o", str(out), "--title", "X"])
    assert exit_code == 1
