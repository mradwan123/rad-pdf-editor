# Build a standalone Rad PDF Editor binary via PyInstaller. Windows.
#
# NOT VERIFIED ON WINDOWS - see packaging/README.md. This uses the
# exact same packaging/pdf-editor.spec the (verified, Linux) build.sh
# does; PyInstaller specs are cross-platform by design, but this
# script itself has not been run on a real Windows machine.

Set-Location (Join-Path $PSScriptRoot "..")

$pyinstallerInstalled = & .venv\Scripts\python.exe -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing pyinstaller (pip install -e '.[packaging]')..."
    & .venv\Scripts\python.exe -m pip install -e ".[packaging]"
}

& .venv\Scripts\pyinstaller.exe --noconfirm --clean packaging\pdf-editor.spec

Write-Host ""
Write-Host "Built: dist\rad-pdf-editor.exe (a single-file executable)"
Write-Host "Run it directly: dist\rad-pdf-editor.exe"
