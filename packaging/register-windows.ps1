# Put RiffTag in the Windows "Open with" list for audio files.
#
#   powershell -ExecutionPolicy Bypass -File packaging\register-windows.ps1 -Exe C:\RiffTagApp\RiffTag.exe
#   powershell -ExecutionPolicy Bypass -File packaging\register-windows.ps1 -Unregister
#
# Everything is written under HKCU, so this needs no administrator and
# touches nothing for other users on the machine.
#
# What it does NOT do, deliberately: it does not make RiffTag the default
# handler for .mp3. Taking the double-click away from someone's music player
# is not what "I want to right-click a file and tag it" means, and Windows 10
# and 11 refuse to let an installer seize a default anyway — the user has to
# pick it in Settings. Registering an OpenWithProgids association is the part
# that is actually ours to do: it adds RiffTag to the right-click "Open with"
# menu, with its icon and proper name, and leaves the default alone.

[CmdletBinding()]
param(
    # Path to RiffTag.exe. Defaults to the build output in this tree.
    [string]$Exe,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$progId  = "RiffTag.AudioFile"
$classes = "HKCU:\Software\Classes"

# GENERATED from rifftag/desktopfile.py - the single list of what RiffTag
# claims, shared with the Linux .desktop entries. Do not edit it here; edit
# that module and re-run:
#   PYTHONPATH=. python3 -c "from rifftag.desktopfile import extensions; print(extensions())"
# A test (tests/test_desktopfile.py) fails if this block and the module
# disagree, because they already had: the Linux side claimed audio/x-wav
# while the desktop calls those files audio/vnd.wave, and RiffTag was simply
# absent from the "Open with" menu for a .wav.
$extensions = @(
    ".aif", ".aifc", ".aiff", ".ape", ".asf", ".dsd", ".dsf", ".flac",
    ".m4a", ".m4b", ".m4p", ".m4r", ".mp+", ".mp3", ".mpc", ".ofr",
    ".ofs", ".oga", ".ogg", ".opus", ".spx", ".tta", ".wav", ".wma",
    ".wv"
)

if ($Unregister) {
    foreach ($ext in $extensions) {
        $key = "$classes\$ext\OpenWithProgids"
        if (Test-Path $key) {
            Remove-ItemProperty -Path $key -Name $progId -ErrorAction SilentlyContinue
        }
    }
    Remove-Item "$classes\$progId" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "RiffTag removed from the Open with list." -ForegroundColor Green
    exit 0
}

if (-not $Exe) {
    $Exe = Join-Path $root "dist\windows\RiffTag\RiffTag.exe"
}
$Exe = [System.IO.Path]::GetFullPath($Exe)
if (-not (Test-Path $Exe)) {
    Write-Host "No RiffTag.exe at $Exe" -ForegroundColor Red
    Write-Host "Build it first (packaging\build-windows.ps1), or pass -Exe <path>."
    exit 1
}

# The ProgID: what the shell shows and what it runs. "%1" must stay quoted -
# without the quotes a path with a space in it, which is most music, arrives
# as several arguments.
New-Item -Path "$classes\$progId\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "$classes\$progId" -Name "(Default)" -Value "Audio file"
Set-ItemProperty -Path "$classes\$progId" -Name "FriendlyTypeName" -Value "Audio file"
New-Item -Path "$classes\$progId\DefaultIcon" -Force | Out-Null
Set-ItemProperty -Path "$classes\$progId\DefaultIcon" -Name "(Default)" -Value "`"$Exe`",0"
Set-ItemProperty -Path "$classes\$progId\shell\open\command" -Name "(Default)" `
    -Value "`"$Exe`" `"%1`""

# Applications\RiffTag.exe is what makes the entry read "RiffTag" in the
# Open with list instead of the bare file name.
$appKey = "$classes\Applications\RiffTag.exe"
New-Item -Path "$appKey\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $appKey -Name "FriendlyAppName" -Value "RiffTag"
Set-ItemProperty -Path "$appKey\shell\open\command" -Name "(Default)" `
    -Value "`"$Exe`" `"%1`""
New-Item -Path "$appKey\SupportedTypes" -Force | Out-Null
foreach ($ext in $extensions) {
    Set-ItemProperty -Path "$appKey\SupportedTypes" -Name $ext -Value ""
}

foreach ($ext in $extensions) {
    New-Item -Path "$classes\$ext\OpenWithProgids" -Force | Out-Null
    # Type is REG_NONE for this key by convention; an empty string is what
    # every other application writes and what the shell expects.
    Set-ItemProperty -Path "$classes\$ext\OpenWithProgids" -Name $progId -Value ""
}

# The shell caches associations; without this the menu does not change until
# the next sign-in.
$sig = @'
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int eventId, uint flags, IntPtr item1, IntPtr item2);
'@
try {
    $shell = Add-Type -MemberDefinition $sig -Name "RiffTagShell" -Namespace "RiffTag" -PassThru
    $shell::SHChangeNotify(0x08000000, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero)
} catch {
    Write-Host "Could not refresh the shell cache; sign out and back in if the menu looks stale." -ForegroundColor Yellow
}

Write-Host "RiffTag registered for $($extensions.Count) audio extensions." -ForegroundColor Green
Write-Host "Right-click a song -> Open with -> RiffTag."
Write-Host "Re-run this after moving or rebuilding the exe: the path is baked into the registry."
