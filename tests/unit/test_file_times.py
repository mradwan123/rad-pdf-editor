"""Unit tests for core/file_times.py - filesystem creation time,
including the Linux `statx()` path `os.stat()` cannot provide.

The Linux tests check against `stat(1)`'s own "Birth:" field rather
than only against themselves: a birth time that is merely *a*
plausible datetime would still pass a self-consistent test while
silently reporting the wrong field (`st_ctime`, the inode change time,
is the classic wrong answer here and is usually close enough to look
right).
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from core import file_times
from core.file_times import birth_time

_LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux", reason="statx() is Linux-specific")


@pytest.fixture(autouse=True)
def _reset_statx_cache() -> object:
    """`_load_statx` caches its lookup process-wide; tests that
    monkeypatch it must not leak that into the next test."""
    yield
    file_times._statx_function = None
    file_times._statx_looked_up = False


def _stat_cli_birth(path: Path) -> str | None:
    """`stat(1)`'s Birth field, or None if it reports none ("-")."""
    result = subprocess.run(["stat", str(path)], capture_output=True, text=True, check=False)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Birth:"):
            value = stripped.removeprefix("Birth:").strip()
            return None if value in {"-", ""} else value
    return None


def test_a_freshly_created_file_reports_a_creation_time_of_about_now(tmp_path: Path) -> None:
    # Deliberately a window, not a strict before <= created <= after
    # ordering: inode timestamps come from the kernel's *coarse*
    # tick-based clock, which does not order strictly against
    # CLOCK_REALTIME as datetime.now() reads it. Measured here, a
    # freshly created file's birth time landed 1.5ms *ahead* of a
    # now() taken after the write - so a strict assertion is flaky by
    # construction, not by bad luck.
    tolerance = 5.0
    target = tmp_path / "fresh.pdf"
    before = datetime.now().astimezone()
    target.write_bytes(b"%PDF-1.7\n")

    created = birth_time(target)
    if created is None:
        pytest.skip("this filesystem records no creation time")

    after = datetime.now().astimezone()
    assert (before - created).total_seconds() < tolerance
    assert (created - after).total_seconds() < tolerance


@_LINUX_ONLY
def test_linux_birth_time_matches_what_stat_reports(tmp_path: Path) -> None:
    """The ground-truth check: our value against the same value read by
    a completely independent implementation."""
    target = tmp_path / "fresh.pdf"
    target.write_bytes(b"%PDF-1.7\n")

    reference = _stat_cli_birth(target)
    created = birth_time(target)
    if reference is None:
        assert created is None, "reported a birth time where stat(1) sees none"
        pytest.skip("this filesystem records no creation time")

    assert created is not None
    # stat(1) prints e.g. "2026-08-20 14:09:51.312369436 +0200"; compare
    # to whole seconds, since it keeps nanoseconds and datetime does not.
    assert created.strftime("%Y-%m-%d %H:%M:%S") == reference[:19]


@_LINUX_ONLY
def test_creation_time_differs_from_the_inode_change_time(tmp_path: Path) -> None:
    """The mistake this module exists to avoid: `st_ctime` moves when
    the file is merely *modified*, a creation time does not."""
    target = tmp_path / "fresh.pdf"
    target.write_bytes(b"%PDF-1.7\n")
    created = birth_time(target)
    if created is None:
        pytest.skip("this filesystem records no creation time")

    time.sleep(0.01)
    target.write_bytes(b"%PDF-1.7\n% changed\n")

    assert birth_time(target) == created, "birth time moved when the file was modified"
    changed = datetime.fromtimestamp(target.stat().st_ctime).astimezone()
    assert changed > created, "st_ctime did not move, so this test proves nothing"


@_LINUX_ONLY
def test_a_filesystem_that_records_no_birth_time_reports_none() -> None:
    """procfs answers statx() successfully but leaves the STATX_BTIME
    bit clear in the result mask - the "succeeded, but there is no such
    time" case, which must not be mistaken for a value."""
    assert _stat_cli_birth(Path("/proc/cpuinfo")) is None, "fixture assumption changed"
    assert birth_time(Path("/proc/cpuinfo")) is None


def test_a_missing_file_reports_none_rather_than_raising(tmp_path: Path) -> None:
    assert birth_time(tmp_path / "does-not-exist.pdf") is None


def test_the_stdlib_is_preferred_when_it_has_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On macOS/Windows (and any future Python exposing it on Linux)
    `st_birthtime` must be used directly, with the syscall path not
    consulted at all."""
    target = tmp_path / "fresh.pdf"
    target.write_bytes(b"%PDF-1.7\n")

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("statx() called even though st_birthtime was available")

    monkeypatch.setattr(file_times, "_statx_birth_time", _fail)

    class _StatWithBirthtime:
        st_birthtime = 1_700_000_000.0

    created = birth_time(target, _StatWithBirthtime())  # type: ignore[arg-type]

    assert created == datetime.fromtimestamp(1_700_000_000.0).astimezone()


def test_no_birth_time_where_neither_source_can_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform where neither source has the answer (musl, glibc <
    2.28, kernel < 4.11) degrades to "unknown" rather than raising.

    Both sources have to be denied for this to mean anything: on
    macOS/Windows the stdlib answers first and *should*, so disabling
    only the syscall proves nothing there - it just exercises the
    stdlib path under a misleading name. (Caught by CI on macOS, where
    an earlier version of this test asserted None and got a perfectly
    correct st_birthtime.)
    """
    target = tmp_path / "fresh.pdf"
    target.write_bytes(b"%PDF-1.7\n")
    monkeypatch.setattr(file_times, "_load_statx", lambda: None)

    class _StatWithoutBirthtime:
        """A stat result from a platform whose stdlib has no birth time."""

    assert birth_time(target, _StatWithoutBirthtime()) is None  # type: ignore[arg-type]


def test_the_libc_lookup_is_only_performed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The negative answer is cached too - otherwise every file in a
    report would re-dlopen libc on a platform that cannot answer."""
    calls = 0

    def _counting_cdll(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise OSError("no libc here")

    monkeypatch.setattr(file_times.ctypes, "CDLL", _counting_cdll)
    file_times._statx_function = None
    file_times._statx_looked_up = False

    for _ in range(3):
        file_times._load_statx()

    assert calls <= 1


@_LINUX_ONLY
def test_the_statx_struct_matches_the_kernels_layout() -> None:
    """The kernel writes the whole structure, so a wrong size would be
    a memory-corrupting bug rather than a wrong number. 256 bytes is
    linux/stat.h's `struct statx`."""
    assert ctypes.sizeof(file_times._Statx) == 256
    assert ctypes.sizeof(file_times._StatxTimestamp) == 16
