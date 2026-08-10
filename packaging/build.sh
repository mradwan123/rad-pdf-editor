#!/usr/bin/env bash
# Build a standalone Rad PDF Editor binary via PyInstaller.
# Linux/macOS. Verified on Linux; not yet verified on macOS (the spec
# is the same file either way - see packaging/README.md).
set -euo pipefail

cd "$(dirname "$0")/.."

if ! .venv/bin/python -c "import PyInstaller" 2>/dev/null; then
    echo "Installing pyinstaller (pip install -e '.[packaging]')..."
    .venv/bin/python -m pip install -e ".[packaging]"
fi

.venv/bin/pyinstaller --noconfirm --clean packaging/pdf-editor.spec

echo
echo "Built: dist/rad-pdf-editor (a single-file executable)"
echo "Run it directly: dist/rad-pdf-editor"
