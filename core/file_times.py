"""Filesystem creation ("birth") time, including on Linux.

`os.stat()` exposes `st_birthtime` on macOS and Windows, but **not on
Linux** - not because Linux lacks the information, but because CPython
has never adopted the syscall that returns it. Linux 4.11+ records a
birth time on ext4, Btrfs, XFS (v5), F2FS and others, and `stat(1)`
prints it as "Birth:"; the only way to read it from Python is the
`statx()` syscall, which `os.stat()` does not use.

Since a "Created" row that permanently reads "unavailable" on the
platform this app is most often run on is a real gap in the Document
Properties report (`core/document_info.py`), this module calls
`statx()` directly through `ctypes` - no third-party dependency, no
subprocess, no parsing of `stat(1)` output.

`birth_time()` is the single entry point and answers for every
platform:

1. `st_birthtime` when the stdlib has it (macOS, Windows, and any
   future Python that adds it on Linux) - always preferred, so this
   module's own code path retires itself automatically if CPython
   catches up.
2. `statx(STATX_BTIME)` on Linux.
3. `None` when the information genuinely does not exist.

**`None` is a real answer, not only a failure.** A filesystem can
decline to record a birth time even on a kernel that supports asking:
procfs and some tmpfs/NFS/FAT mounts return success with the
`STATX_BTIME` bit *clear* in the result mask, and an ext4 filesystem
made with 128-byte inodes has nowhere to store one. That case is
checked explicitly (verified against `/proc/cpuinfo`, which really does
return an unset mask bit here) and is why the caller distinguishes "no
creation time recorded" from a plain error.

Never raises: every failure - missing libc, a glibc too old to export
`statx`, a kernel older than 4.11 (`ENOSYS`), a file that vanished -
comes back as `None`.
"""

from __future__ import annotations

import ctypes
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from core.logging_config import get_logger

if TYPE_CHECKING:
    # typeshed's name for a ctypes function pointer. It exists only
    # for type checkers - at runtime CDLL builds a per-library _FuncPtr
    # class instead - so it is imported under TYPE_CHECKING and every
    # use of it is a (lazy, PEP 563) annotation, never a runtime
    # expression.
    from ctypes import _NamedFuncPointer as _StatxFunction

log = get_logger(__name__)

#: linux/stat.h: ask for stx_btime, and resolve like plain stat(2).
_STATX_BTIME = 0x00000800
_AT_STATX_SYNC_AS_STAT = 0x0000
#: fcntl.h. Path is absolute in practice, so this is only a formality.
_AT_FDCWD = -100

#: The kernel rejects a buffer it doesn't recognise, so the struct
#: below must match linux/stat.h exactly - asserted at import time
#: rather than trusted (see _STATX_STRUCT_SIZE below).
_STATX_STRUCT_SIZE = 256


class _StatxTimestamp(ctypes.Structure):
    """linux/stat.h `struct statx_timestamp`."""

    _fields_ = (
        ("tv_sec", ctypes.c_int64),
        ("tv_nsec", ctypes.c_uint32),
        ("__reserved", ctypes.c_int32),
    )

    tv_sec: int
    tv_nsec: int


class _Statx(ctypes.Structure):
    """linux/stat.h `struct statx`.

    Reproduced in full, not truncated to the fields used: the kernel
    writes the whole 256-byte structure, so a short buffer would be a
    memory-corrupting bug rather than a harmless simplification.
    """

    _fields_ = (
        ("stx_mask", ctypes.c_uint32),
        ("stx_blksize", ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink", ctypes.c_uint32),
        ("stx_uid", ctypes.c_uint32),
        ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16),
        ("__spare0", ctypes.c_uint16 * 1),
        ("stx_ino", ctypes.c_uint64),
        ("stx_size", ctypes.c_uint64),
        ("stx_blocks", ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _StatxTimestamp),
        ("stx_btime", _StatxTimestamp),
        ("stx_ctime", _StatxTimestamp),
        ("stx_mtime", _StatxTimestamp),
        ("stx_rdev_major", ctypes.c_uint32),
        ("stx_rdev_minor", ctypes.c_uint32),
        ("stx_dev_major", ctypes.c_uint32),
        ("stx_dev_minor", ctypes.c_uint32),
        ("stx_mnt_id", ctypes.c_uint64),
        ("stx_dio_mem_align", ctypes.c_uint32),
        ("stx_dio_offset_align", ctypes.c_uint32),
        ("__spare3", ctypes.c_uint64 * 12),
    )

    stx_mask: int
    stx_btime: _StatxTimestamp


#: Resolved once, lazily, and cached - including the negative answer,
#: so a platform without statx() doesn't re-dlopen libc per file.
#: `_statx_looked_up` is what distinguishes "not asked yet" from
#: "asked, and the answer was no".
_statx_function: _StatxFunction | None = None
_statx_looked_up = False


def _load_statx() -> _StatxFunction | None:
    """The libc `statx` symbol, or None if it cannot be used here.

    glibc has exported `statx` since 2.28 (2018); older glibc, musl
    builds without it, and non-Linux platforms all land on None.
    """
    global _statx_function, _statx_looked_up
    if _statx_looked_up:
        return _statx_function
    _statx_looked_up = True

    if sys.platform != "linux":
        return None
    if ctypes.sizeof(_Statx) != _STATX_STRUCT_SIZE:  # pragma: no cover - layout bug
        log.warning(
            "struct statx layout mismatch; not reading birth times",
            extra={"context": f"{ctypes.sizeof(_Statx)} bytes, expected {_STATX_STRUCT_SIZE}"},
        )
        return None
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        function = libc.statx
    except (OSError, AttributeError) as exc:
        log.info("statx() unavailable; no filesystem birth times", extra={"context": str(exc)})
        return None

    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(_Statx),
    )
    function.restype = ctypes.c_int
    _statx_function = function
    return function


def _statx_birth_time(path: Path) -> datetime | None:
    """`path`'s birth time via statx(2), or None if not recorded."""
    function: _StatxFunction | None = _load_statx()
    if function is None:
        return None

    buffer = _Statx()
    try:
        result = function(
            _AT_FDCWD,
            os.fsencode(str(path)),
            _AT_STATX_SYNC_AS_STAT,
            _STATX_BTIME,
            ctypes.byref(buffer),
        )
    except (OSError, ValueError) as exc:  # pragma: no cover - defensive
        log.info("statx() call failed", extra={"context": f"{path}: {exc}"})
        return None

    if result != 0:
        # ENOSYS on a pre-4.11 kernel, ENOENT for a file that has just
        # gone away - both simply mean "no birth time to show".
        log.info(
            "statx() returned an error", extra={"context": f"{path}: errno {ctypes.get_errno()}"}
        )
        return None
    if not buffer.stx_mask & _STATX_BTIME:
        # Succeeded, but this filesystem records no birth time
        # (procfs, some tmpfs/NFS/FAT, ext4 with 128-byte inodes).
        return None

    return _to_local_datetime(buffer.stx_btime.tv_sec + buffer.stx_btime.tv_nsec / 1_000_000_000)


def birth_time(path: Path, stat_result: os.stat_result | None = None) -> datetime | None:
    """`path`'s filesystem creation time, or None if there isn't one.

    Pass `stat_result` when the caller has already stat()ed the file,
    to save a second syscall on the platforms where the stdlib answers
    directly.
    """
    if stat_result is None:
        try:
            stat_result = path.stat()
        except OSError:
            return None

    # macOS, Windows, and any future Python exposing this on Linux.
    stdlib_birthtime = getattr(stat_result, "st_birthtime", None)
    if stdlib_birthtime is not None:
        return _to_local_datetime(stdlib_birthtime)
    return _statx_birth_time(path)


def _to_local_datetime(timestamp: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(timestamp).astimezone()
    except (OSError, OverflowError, ValueError):  # pragma: no cover - absurd timestamps only
        return None
