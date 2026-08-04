"""Unit tests for core/ops/convert_common.py."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pikepdf
import pytest

from core.errors import ConversionError
from core.ops.convert_common import (
    extract_pdf_tables_by_page,
    extract_pdf_text_by_page,
    libreoffice_binary,
    render_pdf_page_to_image,
    run_libreoffice_convert,
)

_HAS_LIBREOFFICE = libreoffice_binary() is not None


def _make_text_pdf(path: Path, text: str = "hello convert_common") -> Path:
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    pdf.save(path)
    return path


# --- libreoffice_binary -------------------------------------------------


def test_libreoffice_binary_returns_none_when_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert libreoffice_binary() is None


def test_libreoffice_binary_finds_soffice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None)
    assert libreoffice_binary() == "/usr/bin/soffice"


# --- run_libreoffice_convert ---------------------------------------------


def test_run_libreoffice_convert_raises_when_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.ops.convert_common.libreoffice_binary", lambda: None)
    with pytest.raises(ConversionError, match="not installed"):
        run_libreoffice_convert(tmp_path / "in.txt", "pdf", tmp_path)


def test_run_libreoffice_convert_raises_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.ops.convert_common.libreoffice_binary", lambda: "/usr/bin/soffice")

    def _raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="soffice", timeout=1.0)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    source = tmp_path / "in.txt"
    source.write_text("hi")
    with pytest.raises(ConversionError, match="timed out"):
        run_libreoffice_convert(source, "pdf", tmp_path, timeout=1.0)


def test_run_libreoffice_convert_raises_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.ops.convert_common.libreoffice_binary", lambda: "/usr/bin/soffice")

    class _FakeResult:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeResult())
    source = tmp_path / "in.txt"
    source.write_text("hi")
    with pytest.raises(ConversionError, match="boom"):
        run_libreoffice_convert(source, "pdf", tmp_path)


def test_run_libreoffice_convert_raises_when_output_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.ops.convert_common.libreoffice_binary", lambda: "/usr/bin/soffice")

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeResult())
    source = tmp_path / "in.txt"
    source.write_text("hi")
    with pytest.raises(ConversionError, match="did not produce"):
        run_libreoffice_convert(source, "pdf", tmp_path)


def test_run_libreoffice_convert_forces_dead_proxy_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Confirmed by hand (not just asserted here): without this,
    # LibreOffice's subprocess made a real outbound TCP connection
    # attempt for a remote <img src> in a converted HTML file (an 11s
    # conversion vs. the ~1s local-only baseline, against an RFC 5737
    # black-hole address) - network_lockdown()'s socket patch never
    # reaches this subprocess at all. This test only checks the env
    # dict actually handed to subprocess.run; the real network
    # behavior was verified manually, not re-proven on every test run.
    monkeypatch.setattr("core.ops.convert_common.libreoffice_binary", lambda: "/usr/bin/soffice")
    captured_env: dict[str, str] = {}

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(*_args: object, **kwargs: object) -> _FakeResult:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        captured_env.update(env)
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    source = tmp_path / "in.html"
    source.write_text("<html></html>")
    out_path = tmp_path / f"{source.stem}.pdf"
    out_path.touch()  # satisfy the "did LibreOffice produce output" check

    run_libreoffice_convert(source, "pdf", tmp_path)

    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        assert captured_env[var] == "http://127.0.0.1:1"
    assert captured_env["no_proxy"] == ""
    assert captured_env["NO_PROXY"] == ""


@pytest.mark.skipif(not _HAS_LIBREOFFICE, reason="LibreOffice not installed on this machine")
def test_run_libreoffice_convert_real_conversion(tmp_path: Path) -> None:
    source = tmp_path / "in.txt"
    source.write_text("real conversion test")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = run_libreoffice_convert(source, "pdf", out_dir)
    assert result == out_dir / "in.pdf"
    with pikepdf.Pdf.open(result) as pdf:
        assert len(pdf.pages) >= 1


# --- text/table extraction + image rendering -----------------------------


def test_extract_pdf_text_by_page_returns_page_text(tmp_path: Path) -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((50, 50), "extract me")
    path = tmp_path / "text.pdf"
    doc.save(path)
    doc.close()

    pages = extract_pdf_text_by_page(path)
    assert len(pages) == 1
    assert "extract me" in pages[0]


def test_extract_pdf_text_by_page_empty_page_is_empty_string(tmp_path: Path) -> None:
    path = _make_text_pdf(tmp_path / "blank.pdf")
    pages = extract_pdf_text_by_page(path)
    assert pages == [""]


def test_extract_pdf_tables_by_page_returns_empty_for_no_tables(tmp_path: Path) -> None:
    path = _make_text_pdf(tmp_path / "blank.pdf")
    tables = extract_pdf_tables_by_page(path)
    assert tables == [[]]


def test_render_pdf_page_to_image_writes_a_real_image(tmp_path: Path) -> None:
    path = _make_text_pdf(tmp_path / "in.pdf")
    out_path = tmp_path / "page.png"
    result = render_pdf_page_to_image(path, 0, dpi=100, out_path=out_path)
    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 0
