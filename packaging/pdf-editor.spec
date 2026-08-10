# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Rad PDF Editor.

Built and verified on Linux (this project's dev machine) via
`packaging/build.sh`. `packaging/build.ps1` (Windows) runs this exact
same spec - PyInstaller specs are cross-platform by design - but has
**not** been run or verified on Windows or macOS; see
`packaging/README.md`.

No bundled icon asset: the app icon is drawn programmatically at
runtime (`gui/resources.py`'s `build_app_icon`), not a checked-in
binary file, so there's nothing to add to `datas` for it. `gui/styles.qss`
*is* a real runtime data file (loaded via a path relative to `gui/`'s
own location) and must be bundled explicitly, or the packaged app
would silently lose all theming (gui/main.py's `_load_stylesheet`
degrades to an empty stylesheet on a missing file rather than
crashing - real regression, not a crash, still worth avoiding).
"""

import os

from PyInstaller.utils.hooks import collect_all

# SPECPATH is a PyInstaller-provided global (the directory containing
# this .spec file) - paths inside a spec resolve relative to it, not
# to wherever `pyinstaller` was invoked from. Confirmed by hand: a
# plain "gui/main.py" here resolved to packaging/gui/main.py and
# failed with "script ... not found" when this spec was correctly run
# from the repo root.
_REPO_ROOT = os.path.join(SPECPATH, "..")

datas = [(os.path.join(_REPO_ROOT, "gui", "styles.qss"), "gui")]
binaries = []
hiddenimports = []

# PyInstaller's static import analysis can miss dependencies these
# packages pull in dynamically (C-extension modules, data files) -
# collect_all is the standard PyInstaller-recommended way to bundle a
# package's full footprint rather than guessing at hiddenimports by
# hand.
for _package in (
    "pikepdf",
    "fitz",
    "ocrmypdf",
    "reportlab",
    "pdfplumber",
    "deskew",
    "skimage",
    "pyhanko",
):
    _pkg_datas, _pkg_binaries, _pkg_hiddenimports = collect_all(_package)
    datas += _pkg_datas
    binaries += _pkg_binaries
    hiddenimports += _pkg_hiddenimports

a = Analysis(
    [os.path.join(_REPO_ROOT, "gui", "main.py")],
    pathex=[_REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="rad-pdf-editor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
