# Build RiffTag.exe. Run from the project root in the Windows VM:
#   powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
#
# Output goes to dist\windows\RiffTag\ — kept apart from dist\*.deb so the
# Linux and Windows artifacts cannot tread on each other.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Building from $root" -ForegroundColor Cyan

# Absolute paths throughout: PyInstaller resolves a relative --add-data
# against the SPEC file's directory, not the working directory, so with
# --specpath build\windows it went hunting for build\windows\assets.
pyinstaller --noconfirm --clean --windowed `
    --name RiffTag `
    --icon "$root\assets\rifftag.ico" `
    --add-data "$root\assets;assets" `
    --hidden-import PyQt6.QtMultimedia `
    --paths "$root" `
    --distpath "$root\dist\windows" `
    --workpath "$root\build\windows" `
    --specpath "$root\build\windows" `
    "$root\packaging\rifftag_win.py"

$exe = Join-Path $root "dist\windows\RiffTag\RiffTag.exe"
if (-not (Test-Path $exe)) { Write-Host "No exe produced." -ForegroundColor Red; exit 1 }

$size = (Get-ChildItem (Join-Path $root "dist\windows\RiffTag") -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ""
Write-Host ("Built {0} - {1:N0} MB total" -f $exe, $size) -ForegroundColor Green
if ($size -lt 60) {
    Write-Host "That is suspiciously small - Qt is probably missing again." -ForegroundColor Yellow
} else {
    Write-Host "Size looks right (Qt is bundled)." -ForegroundColor Green
}
Write-Host ""
Write-Host "Running it straight off the share is slow - 118 MB of DLLs over SMB"
Write-Host "makes the first launch look like a hang. Copy it local first:"
Write-Host "  robocopy $root\dist\windows\RiffTag C:\RiffTagApp /MIR /NFL /NDL /NJH /NJS /NP"
Write-Host "  C:\RiffTagApp\RiffTag.exe"
