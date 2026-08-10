# Packaging / installers

Standalone builds via [PyInstaller](https://pyinstaller.org/), from
`packaging/pdf-editor.spec` (one spec, used on every OS — PyInstaller
specs are cross-platform by design).

## Verification status

| Platform | Script | Status |
|---|---|---|
| Linux | `packaging/build.sh` | **Verified** — real build via `pyinstaller`, real launch (`QT_QPA_PLATFORM=offscreen`, stayed running 5+s with an empty log - the full app, all `core`/`gui` imports, actually initialized) |
| macOS | `packaging/build.sh` (same script) | Not yet verified on real macOS hardware |
| Windows | `packaging/build.ps1` | Not yet verified on a real Windows machine |

Only the Linux build has actually been run and checked. The Windows/
macOS paths are provided because the spec itself is genuinely
cross-platform (nothing in `pdf-editor.spec` is Linux-specific), but
don't take "not yet verified" as "definitely broken" or "definitely
fine" — it means exactly what it says: nobody has run it there yet. If
you're the first to build on Windows or macOS, please update this
table with what you found.

## Build

```bash
# Linux / macOS
packaging/build.sh
```
```powershell
# Windows
packaging\build.ps1
```

Both install `pyinstaller` (the `packaging` extra — `pip install -e
".[dev,packaging]"` gets you dev tools + packaging in one go) if it's
not already present, then run PyInstaller against the shared spec.
Output is a **single-file executable** at `dist/rad-pdf-editor` (Linux/
macOS) or `dist\rad-pdf-editor.exe` (Windows) — not a folder, since the
spec's `EXE(...)` call is given `a.binaries`/`a.datas` directly rather
than routed through a separate `COLLECT(...)` step (the standard
PyInstaller pattern for one-folder output instead of one-file). One
file was picked deliberately: simpler to hand to a teammate than a
folder they have to keep intact.

## What's bundled, and what isn't

The spec (`packaging/pdf-editor.spec`) bundles every Python dependency
via `PyInstaller.utils.hooks.collect_all` for the packages whose
dynamic imports/data files PyInstaller's static analysis tends to
miss (`pikepdf`, `fitz`, `ocrmypdf`, `reportlab`, `pdfplumber`,
`deskew`, `skimage`, `pyhanko`), plus `gui/styles.qss` (a genuine
runtime data file, not code — the app icon has no equivalent bundled
asset since it's drawn programmatically at runtime, see
`gui/resources.py`).

**Not bundled — external system binaries, same as the source checkout
requires**:
- `tesseract` — **required** for the OCR tool. No pure-Python
  fallback exists; see the main `README.md`'s Setup section.
- LibreOffice — optional; Word/PowerPoint/Excel/HTML conversion falls
  back to a pure-Python path automatically when it's absent.
- Ghostscript — optional; used only as Repair's fallback engine for
  corruption `pikepdf`'s own recovery can't handle.

A built installer does not change any of this — these are still
system-level prerequisites the *user's machine* needs, exactly as they
are for a source checkout. Document this for whoever you hand a built
binary to; discovering it via a mid-task failure is a worse experience
than reading it up front.

## Real issues hit and fixed while building the (verified) Linux binary

- **`SPECPATH`, not the invocation directory.** A first version of the
  spec used a plain `"gui/main.py"` for the entry script and a plain
  `"gui/styles.qss"` for the bundled data file. PyInstaller resolves
  relative paths inside a `.spec` file against the spec file's own
  directory, not wherever `pyinstaller` was run from — this failed
  with `script '.../packaging/gui/main.py' not found` even though the
  build was correctly invoked from the repo root. Fixed with
  PyInstaller's `SPECPATH` builtin (see the spec file's own comments).
  `pathex=[_REPO_ROOT]` was also needed so `gui/main.py`'s own
  `from core... import ...` absolute imports resolve.

## Known warnings (non-fatal, seen on the real Linux build)

- `Library not found: could not resolve 'libtiff.so.5'` — a Qt
  `imageformats` plugin (`libqtiff.so`) has an optional TIFF-decoding
  dependency not present on the build machine. Doesn't affect PDF
  handling (fitz/PyMuPDF, not Qt, does all this app's actual PDF
  rendering); would only matter if some future feature opened a raw
  `.tiff` through Qt's own image loader directly.
- `Hidden import "pycparser.lextab"/"pycparster.yacctab" not found` and
  `Hidden import "scipy.special._cdflib" not found` — both well-known,
  commonly-seen PyInstaller false-positive warnings for these specific
  packages (generated/optional submodules their own build process
  doesn't always produce), not indicative of an actual missing
  dependency; the app launched and ran cleanly regardless.
