# RiffTag on Windows — start here

Written 2026-11-21 on the Linux side, for a **cold Claude session running inside
Dave's Windows 11 VM**. Read this first, then `../README.md` for the app itself.
Nothing here has been tested on Windows yet; that is the job.

## Where the code lives

The project is on the Samba share `\\192.168.50.26\claude-brain`, at
`\\192.168.50.26\claude-brain\apps\rifftag`. Map it as a drive in the VM, or
copy the folder to `C:\RiffTag`.

**Build on a local disk, not on the share.** PyInstaller writes thousands of
small files and the share is already slow enough that it caused a stale
`__pycache__` bug in the .deb build (see CONTEXT_APPS.md). Suggested: edit on
the share, `robocopy` to `C:\RiffTag` before building — or ask Dave to put the
project under git first, which would be cleaner for round-tripping changes.

## What to install in the VM

```powershell
winget install Python.Python.3.12        # tick "Add to PATH"
winget install Gyan.FFmpeg               # convert + the loudness check need it
pip install PyQt6 mutagen pytest pyinstaller
```

No venv is needed on Windows — the PEP 668 restriction that forces apt packages
on the Linux side does not apply.

## First thing to do

Run the suite. It is 555 tests and the engine half has no business failing:

```powershell
cd C:\RiffTag
$env:PYTHONPATH="."
python -m pytest tests -q
```

Expect failures only in the GUI tests that touch Linux paths. **Report what
actually fails before changing anything** — the point of having a Windows box
is to stop guessing.

## Status, 2026-11-21 evening — the suite is green on Windows

Run from `B:\apps\rifftag` in the Windows 11 VM (Python 3.12.10, PyQt6 from
pip, ffmpeg from winget):

| | |
|---|---|
| Engine (`--ignore=tests/test_gui.py`) | **306 passed, 0 failed, 11s** |
| GUI (`tests/test_gui.py`) | **248 passed, 1 skipped, 37s** |

**One real bug was found and fixed** — `Path.__eq__` ignores case on Windows,
so the renamer's planner treated `lithium.mp3` -> `Lithium.mp3` as "unchanged"
and silently did nothing. `renamer._is_same_path()` now compares the text.
The *apply* step was already correct (it hops via a temp name), so this was
purely the plan refusing to act. See `test_a_case_only_change_is_never_called_unchanged`.

Everything else that failed was the tests being Linux-shaped, not the app:

* `QUrl.toLocalFile()` returns **forward slashes** on Windows, so comparing it
  against `str(Path)` fails for reasons unrelated to what was opened. Four
  tests now compare as `Path` objects.
* `st_mode & 0o111` — no execute bit on Windows; that test skips there.
* `Albums/Nevermind` — the label is built with `Path`, so the *test* was the
  thing hardcoding a separator.
* Two artwork checks need Pillow, which is not an app dependency; they skip
  when it is absent.

**Do not tee verbose output to the share.** A `-v` run piped through
`Tee-Object` to a file on `B:` crawled to 64% in half an hour — every line
flushed across SMB in UTF-16. The same suite runs in 37 seconds writing
nowhere. That wasted an evening; it was the instrumentation, not the app.

## What the first real build showed (and what was done about it)

The exe runs. The logo, the hand-drawn transport glyphs, the two-row layout,
the drop targets, the totals bar and the drive enumeration all came through
untouched. Three things did not:

1. **Half the toolbar rendered as text.** `QIcon.fromTheme` finds nothing on
   Windows, so every button fell back to its label, the strip outgrew the
   window and real tools disappeared into an overflow menu.
   `theme.themed_icon(..., fallback=QStyle.StandardPixmap.SP_*)` now falls
   through to Qt's own icon set, which exists on every platform.
   `main.TOOLBAR_FALLBACK` maps each action; the folder header does the same.
2. **The rename crib sheet rendered in a heavy Courier.** `QFont("monospace")`
   is a fontconfig alias with no meaning on Windows. It now names real
   families in order (Cascadia Mono, Consolas, Segoe UI Mono, DejaVu Sans
   Mono, Liberation Mono, Menlo) and keeps the alias last.
3. **Dark mode gave dark text on dark fields** — the mask combo and the
   result preview were illegible. The native windows11 style only partly
   consults the QPalette, and *everything* in this app is palette-derived.
   `theme.use_portable_style()` switches to **Fusion on Windows only**,
   before the system palette is captured. Linux keeps its native style.

## PyInstaller: two traps, both already worked around

* **Never point it at `rifftag/__main__.py`.** That file imports with
  `from .gui.main import run`, and PyInstaller analyses whatever it is given
  as a loose script, so the relative import resolves to nothing and the whole
  application — PyQt6 included — silently drops out of the graph. The build
  then "succeeds" at 23 MB. `packaging/rifftag_win.py` exists for this, with
  absolute imports. A correct build is ~118 MB; `build-windows.ps1` checks
  the size and complains under 60 MB.
* **`--add-data` resolves relative paths against the spec file's directory,**
  not the working directory. With `--specpath build\windows` it went looking
  for `build\windows\assets`. Every path in the build script is absolute now.

Build and run:

```powershell
cd B:\apps\rifftag
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
robocopy B:\apps\rifftag\dist\windows\RiffTag C:\RiffTagApp /MIR /NFL /NDL /NJH /NJS /NP
C:\RiffTagApp\RiffTag.exe
```

Run it from a local disk, not the share: 118 MB of DLLs over SMB makes the
first launch look like a hang.

## Getting at the files from the VM (settled 2026-11-22)

Windows allows **one user account per file server**. The server denies
`winmusic` access to `claude-brain`; `dave` can read Music, DaveShared and
claude-brain. So everything maps as `dave`, and the scripts in the WinBoat
shared folder (`~/.winboat/shared` on Linux) reuse a live session rather than
asking again: `1 - Connect drives.bat` maps `B:`, `M:` and `S:` in one go (run it
every time the VM starts; it saves the password in the VM's Credential Manager on
first run). Never reintroduce the winmusic credentials — that is what kept knocking
`B:` offline.

## The three known Linux-isms

Everything else was written cross-platform on purpose: the mask grammar already
normalises `\` and `/`, the renamer already hops via a temp name for case-only
renames (it was written for NTFS and Samba), and filenames are already
sanitised against the Windows rules by default.

1. **`rifftag/gui/shortcuts.py` is wholly Linux.** `.desktop` files,
   `xdg-user-dir`, and Cinnamon panel applets driven by `gsettings`. On Windows
   this wants Start Menu / Desktop `.lnk` files, or the whole feature disabled.
   Guard it behind a platform check rather than deleting it — the Linux build
   still ships.
2. **ffmpeg is found with `shutil.which("ffmpeg")`** (`convert.py`,
   `preflight.py`). That works on Windows once ffmpeg is on PATH. For a
   distributable build, either ship `ffmpeg.exe` beside the app and look for it
   next to `sys.executable` first, or keep the current graceful degradation —
   both dialogs already say what is missing instead of failing.
3. **`QIcon.fromTheme` returns nothing on Windows**, so every toolbar button
   falls back to text (that fallback already exists — `theme.themed_icon`). It
   will *work* and look poor. Options: bundle a small icon set, or map the
   common ones to `QStyle.StandardPixmap`.

Also check, because they are untested rather than known-bad:

* `splash._find_assets()` — the `/usr/share/rifftag/assets` candidate is a
  no-op on Windows, and the "beside the package" fallback is what a PyInstaller
  bundle gives. Verify the logo actually appears.
* Qt Multimedia playback — PyQt6's Windows wheels carry their own backend, so
  `player.AVAILABLE` should be true without the separate package Linux needs.
* `QDesktopServices.openUrl` for the hand-off-on-quit prompt.

## Packaging

PyInstaller first, installer second:

```powershell
pyinstaller --noconfirm --windowed --name RiffTag `
  --add-data "assets;assets" `
  --icon assets\icon-256.png `
  -m rifftag
```

Expect 100–150 MB because Qt is bundled rather than borrowed from the distro.

### The installer

A PyInstaller folder in a zip has no Start Menu entry, no Add/Remove Programs
entry and no uninstaller — uninstalling is deleting the folder. That is a
worse first impression than it needs to be, so there is an **Inno Setup**
installer as well. Both artifacts ship: the zip for people who want portable,
`RiffTag-<ver>-Setup.exe` for people who want it installed.

The `.iss` is **generated** by `packaging/make_iss.py`, never hand-written —
the file-type list has to come from `rifftag/desktopfile.py` or it drifts from
the Linux one, which is exactly the bug that hid RiffTag from the .wav
"Open with" menu. `tests/test_make_iss.py` covers it, because the installer
is compiled on a machine the test suite never runs on.

```powershell
python packaging\make_iss.py C:\RiffTag\build\rifftag.iss 0.3
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Q C:\RiffTag\build\rifftag.iss
```

`build_windows.bat` does both, and **skips the step with a message if Inno
Setup is not installed** rather than failing the build. Install it once from <https://jrsoftware.org/isdl.php> — **take the current
stable, 7.x**. The floor is 6.3, because `x64compatible` does not exist
before that, but 7 is what upstream recommends and the generated script is
already compatible with it: nothing it emits uses `WizardResizable`,
`WindowVisible` or Pascal scripting, all of which 7 dropped, and it already
uses `x64compatible` rather than the plain `x64` that 7 deprecates.

Decisions baked into the script, each for a reason:

* **`AppId` is a fixed GUID and must never change.** It is the identity
  Windows stores for the installed program; changing it makes an upgrade
  install a second copy beside the first and orphans the old uninstaller.
* **Per-user by default** (`PrivilegesRequired=lowest`), so there is no UAC
  prompt — the same choice the HKCU associations make. The dialog still lets
  anyone pick a machine-wide install.
* **`uninsdeletevalue`, never `uninsdeletekey`, on the extension keys.**
  `.mp3\OpenWithProgids` belongs to the file type and other programs keep
  their own values there; deleting the key on uninstall would take theirs.
* **The associations are an optional task**, and the installer never takes a
  default handler. Installing a tag editor must not take the double-click
  away from someone's music player.
* **Unsigned.** Windows will show "Windows protected your PC" until the
  installer earns SmartScreen reputation. A code-signing certificate is the
  fix and costs real money every year — see the money plan in
  `../../CONTEXT_APPS.md`, where a signed installer is named as one of the
  few things genuinely worth charging for.

### File associations, until there is an installer

```powershell
powershell -ExecutionPolicy Bypass -File packaging\register-windows.ps1 -Exe C:\RiffTagApp\RiffTag.exe
powershell -ExecutionPolicy Bypass -File packaging\register-windows.ps1 -Unregister
```

Everything goes under `HKCU\Software\Classes`, so no administrator is
needed and no other user on the machine is touched. It registers a
`RiffTag.AudioFile` ProgID, an `Applications\RiffTag.exe` entry (that is
what makes the menu say "RiffTag" rather than the bare exe name) and an
`OpenWithProgids` value for every audio extension RiffTag claims.

**The extension list in that script is generated**, from
`rifftag/desktopfile.py`, which is the same list the Linux `.desktop` entries
are built from. Do not edit it in the `.ps1`; edit the module and regenerate,
or `tests/test_desktopfile.py` will fail. The two lists had drifted apart
once and the result was RiffTag missing from the Open-with menu for `.wav`.

It does **not** make RiffTag the default for `.mp3`, deliberately: taking the
double-click away from someone's music player is not what "let me right-click
a file and tag it" means, and Windows 10/11 will not let a program seize a
default anyway — the user has to pick it in Settings.

Two things that will bite a future edit: `"%1"` must stay quoted in the
command, or a path with a space in it (which is most music) arrives as
several arguments; and the exe path is baked into the registry, so the script
has to be re-run after the exe is moved or rebuilt elsewhere.

The app end of this is `rifftag/cli.py` — it is shared with Linux and has no
Windows-specific code.

## What not to re-decide

The Linux side has paid for these already; they are in `../README.md` with the
reasoning, and none of them are Windows-specific:

* `tagio.write()` only touches keys present in the dict it is given.
* Row colours, zebra stripes and the transport glyphs are all derived from the
  live `QPalette`; never hardcode a colour and never use a stylesheet on a
  colour-bearing widget.
* Tests must never write to the user's real `QSettings` — `tests/conftest.py`
  redirects `XDG_CONFIG_HOME`, and the Windows equivalent needs the same
  treatment (`QSettings` goes to the registry there, so expect to redirect
  `HKCU\Software\rifftag` instead — check this before running the GUI tests on
  a machine Dave cares about).

## Matroska on Windows (added 2026-11-27, verified in the VM 2026-11-28)

`.mkv/.mka/.webm` need MKVToolNix. `rifftag/matroska.py` looks on PATH, then
in a `mkvtoolnix\` folder beside `RiffTag.exe`, then in
`%ProgramFiles%\MKVToolNix` — so the ordinary MKVToolNix installer is enough,
no PATH edit. The installer is **not** bundled (same choice as ffmpeg). To
bundle later, copy `mkvmerge.exe`, `mkvextract.exe`, `mkvpropedit.exe` into
`dist\RiffTag\mkvtoolnix\` (GPL-2+, compatible with RiffTag's GPL-3).
Subprocesses use `CREATE_NO_WINDOW` so no console flashes.

Verified 2026-11-28: full suite (711) passes in the VM with MKVToolNix
installed by `winget install --id MoritzBunkus.MKVToolNix -e`, non-ASCII test
included. The first run's six failures were test helpers calling `mkvextract`
by bare name with `PATH=/usr/bin:/bin` — test code must find tools through
`matroska.tool()` exactly as the app does, never by bare name.
