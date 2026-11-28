# Build, install and package RiffTag for Windows, in one go.
#
# Driven by build_windows.bat in the WinBoat shared folder, which only exists
# to launch this and keep the window open. The work is in PowerShell because
# the batch version kept dying silently in its own quoting: a `for /f` around
# a quoted PowerShell one-liner with a `^` continuation is three escaping
# rules deep, and when it broke it broke without a message.
#
# EVERYTHING IS LOGGED to the share. Two runs failed with nothing to read
# afterwards; that is what this is for. The log is the first thing to ask for.

[CmdletBinding()]
param(
    [string]$Share = "B:\apps\rifftag",
    [string]$Work  = "C:\RiffTag",
    [string]$AppDir = "C:\RiffTagApp",
    # Reuse what is already built instead of rebuilding. The pipeline runs
    # end to end by default, which is right for a release but wasteful when
    # only the last step is in question: it cost a 119 MB rebuild to retry
    # the installer once.
    [switch]$SkipBuild,
    # Only step 7. Implies -SkipBuild and leaves the zip alone.
    [switch]$InstallerOnly
)
if ($InstallerOnly) { $SkipBuild = $true }

$ErrorActionPreference = "Continue"
$ok = $true

# The script logs itself. The first version relied on the caller piping to
# Tee-Object, and the command was long enough that it wrapped when pasted
# and lost its own -File argument. A log nobody has to remember to ask for
# is the whole point.
$logDir = if (Test-Path "$Share\dist") { "$Share\dist" } else { $env:TEMP }
try { Start-Transcript -Path "$logDir\build_log.txt" -Force | Out-Null } catch { }

function Step($n, $text) { Write-Host "`n[$n] $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "  *** $text" -ForegroundColor Red; $script:ok = $false }
function Good($text) { Write-Host "  $text" -ForegroundColor Green }

Write-Host "RiffTag Windows build - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ("=" * 58)

# --- the share -------------------------------------------------------
if (-not (Test-Path "$Share\packaging\build-windows.ps1")) {
    Fail "$Share is not reachable. Run "1 - Connect drives.bat" first."
    exit 1
}
Good "Share OK: $Share"

# --- python ----------------------------------------------------------
# Not assumed to be on PATH. run_tests_locally.bat learned this the hard way.
Step 1 "Finding Python"
$py = $null
foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
                 "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                 "C:\Python312\python.exe")) {
    if (Test-Path $c) { $py = $c; break }
}
if (-not $py) {
    foreach ($c in @("py", "python")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
    }
}
if (-not $py) { Fail "No Python found."; exit 1 }
Good "Python: $py  ($(& $py --version 2>&1))"

# --- copy to a local disk --------------------------------------------
# Never build on the share: PyInstaller writes thousands of small files, and
# a stale __pycache__ there once shipped the wrong version number.
Step 2 "Copying $Share -> $Work"
Get-Process RiffTag -ErrorAction SilentlyContinue | Stop-Process -Force
# The copy stays even with -SkipBuild: packaging/ and rifftag/gui/about.py
# are read from $Work, so skipping it would build an installer from stale
# scripts while claiming the version in the fresh ones.
robocopy $Share $Work /MIR /NFL /NDL /NJH /NJS /NP /XD build dist .git web .pytest_cache __pycache__ .wrangler | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "robocopy failed ($LASTEXITCODE)"; exit 1 }
Good "Copied"

# --- version ---------------------------------------------------------
Step 3 "Reading the version"
$about = Get-Content "$Work\rifftag\gui\about.py" -Raw
$m = [regex]::Match($about, 'VERSION\s*=\s*"([^"]+)"')
if (-not $m.Success) { Fail "No VERSION in about.py"; exit 1 }
$ver = $m.Groups[1].Value
Good "Version: $ver"

# --- build -----------------------------------------------------------
$exe = "$Work\dist\windows\RiffTag\RiffTag.exe"
if ($SkipBuild -and (Test-Path $exe)) {
    Step 4 "Reusing the existing build (-SkipBuild)"
    Good "Reusing: $exe  ($((Get-Item $exe).LastWriteTime))"
} else {
    if ($SkipBuild) { Write-Host "  Nothing built yet; building anyway." -ForegroundColor Yellow }
    Step 4 "Building RiffTag.exe (slow - PyInstaller)"
    Push-Location $Work
    & powershell -ExecutionPolicy Bypass -File "$Work\packaging\build-windows.ps1"
    Pop-Location
    if (-not (Test-Path $exe)) { Fail "No exe at $exe"; exit 1 }
    Good "Built: $exe"
}

# --- install ---------------------------------------------------------
Step 5 "Installing to $AppDir"
if ($InstallerOnly) { Write-Host "  Skipped (-InstallerOnly)." -ForegroundColor Yellow } else {
robocopy "$Work\dist\windows\RiffTag" $AppDir /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "install robocopy failed ($LASTEXITCODE)" } else { Good "Installed" }
}

# --- zip -------------------------------------------------------------
# Zip LOCALLY then copy. Compress-Archive straight to the mapped B: drive is
# what failed on 2026-11-26: build and install both worked, no zip appeared.
Step 6 "Zipping (118 MB - takes a minute)"
if ($InstallerOnly) { Write-Host "  Skipped (-InstallerOnly)." -ForegroundColor Yellow } else {
$localZip = "$Work\rifftag-$ver-windows.zip"
if (Test-Path $localZip) { Remove-Item $localZip -Force }
try {
    Compress-Archive -Path "$Work\dist\windows\RiffTag" -DestinationPath $localZip -CompressionLevel Optimal -Force
} catch {
    Fail "Compress-Archive: $_"
}
if (Test-Path $localZip) {
    Copy-Item $localZip "$Share\dist\rifftag-$ver-windows.zip" -Force
    if (Test-Path "$Share\dist\rifftag-$ver-windows.zip") { Good "Zip on the share" }
    else { Fail "Zip made locally but the copy to the share failed" }
} else { Fail "No zip produced" }
}

# --- installer -------------------------------------------------------
# Optional: without Inno Setup the zip above is still a complete result.
Step 7 "Building the installer"
# Two fixed paths were not enough: Dave had Inno Setup installed and the
# script still said it was missing. It can be per-user, and the folder is
# version-named. Ask the registry first - that is where the installer
# records itself - then wildcard the three plausible roots, then PATH.
$iscc = $null
foreach ($key in @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup*_is1",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup*_is1",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup*_is1")) {
    foreach ($entry in (Get-ItemProperty $key -ErrorAction SilentlyContinue)) {
        $candidate = Join-Path $entry.InstallLocation "ISCC.exe"
        if ($entry.InstallLocation -and (Test-Path $candidate)) {
            $iscc = $candidate; break
        }
    }
    if ($iscc) { break }
}
if (-not $iscc) {
    foreach ($root in @("${env:ProgramFiles(x86)}", "$env:ProgramFiles",
                        "$env:LOCALAPPDATA\Programs")) {
        if (-not $root) { continue }
        $hit = Get-ChildItem -Path $root -Filter "ISCC.exe" -Recurse -Depth 2 `
                   -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { $iscc = $hit.FullName; break }
    }
}
if (-not $iscc) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) {
    Write-Host "  Inno Setup not found - skipping." -ForegroundColor Yellow
    Write-Host "  Looked in the registry, Program Files, Program Files (x86),"
    Write-Host "  $env:LOCALAPPDATA\Programs and PATH. If it IS installed, say"
    Write-Host "  where ISCC.exe lives and that path goes in the list."
    Write-Host "  Otherwise: https://jrsoftware.org/isdl.php - take the"
    Write-Host "  current stable (7.x). This script is compatible with it:"
    Write-Host "  nothing it generates uses anything 7 dropped."
} else {
    Write-Host "  ISCC: $iscc"
    $iss = "$Work\build\rifftag.iss"
    & $py "$Work\packaging\make_iss.py" $iss $ver
    if (-not (Test-Path $iss)) {
        Fail "make_iss.py wrote nothing"
    } else {
        & $iscc /Q $iss
        $setup = "$Work\dist\RiffTag-$ver-Setup.exe"
        if (Test-Path $setup) {
            Copy-Item $setup "$Share\dist\RiffTag-$ver-Setup.exe" -Force
            Good "Installer on the share: RiffTag-$ver-Setup.exe"
        } else {
            Fail "ISCC produced no Setup.exe"
        }
    }
}

Write-Host ""
Write-Host ("=" * 58)
if ($ok) {
    Write-Host "Done. RiffTag $ver is at $AppDir, and the share has:" -ForegroundColor Green
    Get-ChildItem "$Share\dist" -Filter "*$ver*" |
        ForEach-Object { Write-Host ("   {0}  {1:N0} KB" -f $_.Name, ($_.Length / 1KB)) }
} else {
    Write-Host "Finished WITH ERRORS - see the *** lines above." -ForegroundColor Red
    Write-Host "The full log is on the share at apps\rifftag\dist\build_log.txt"
}
Write-Host ("=" * 58)
Write-Host "Log: $logDir\build_log.txt"
try { Stop-Transcript | Out-Null } catch { }
