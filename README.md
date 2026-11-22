# RiffTag

**Tag, rename and check your music before you release it.**

A music tag editor for people who manage their own libraries and release their
own records. It shows your files in a real folder structure and lets you edit
them like a spreadsheet — then tells you what a distributor would send back.

* Rename files and folders from their tags with templates, previewing every
  change before anything moves, and undo in one click
* Read tags back out of existing file names
* Edit a whole album at once, touching only the fields you tick
* Cover art by drag and drop, in the tag or as `folder.jpg`
* Album details from MusicBrainz or Discogs
* Lyrics cleanup for streaming delivery, and tap-to-sync to an `.lrc`
* Convert to MP3, FLAC, WAV or M4A without touching the originals
* Play your files while you work
* A **release check** that reports undersized artwork, inconsistent album
  artists, gaps in track numbering, loudness and true peak, and more

Formats: ID3v2.3/2.4, iTunes MP4, Vorbis comments, APEv2 and Windows Media —
mp3, flac, wav, aiff, ogg, opus, m4a, wma, dsf, ape, wv, mpc.

**Website:** <https://rifftag.nobody777.com> · **Support:**
<https://ko-fi.com/nobody777thankyou>

Runs on Linux and Windows from one codebase. Free, and free to keep.

## Where it came from

RiffTag started because the Linux tag editors on offer — EasyTAG, Kid3,
Picard, puddletag — each missed something I use, and the Windows editor I had
been running under Wine did not run well there. So the first version set out
to match that workflow on Linux: the folder-tree file list, the template
grammar, the multi-file editor.

It has since grown past that. The player, the format converter, the lyrics
tools, tap-to-sync and the release check have no counterpart in what inspired
it.

The original's workflow was learned from its own published help file, never
from its code — see [docs/ORIGIN.md](docs/ORIGIN.md). *RiffTag is not affiliated
with, endorsed by, or derived from the source code of any other tagging
product.*

## Licence

**GPL-3.0-or-later** (see `LICENSE`). Chosen deliberately in November 2026,
replacing an MIT line that had only ever been a packaging default: GPL keeps
forks open, which is what protects a free-with-donations model, and it is what
this audience expects. It is also the only coherent choice while the app links
**PyQt6** and **mutagen**, both of which are GPL — shipping a closed-source
build would require a commercial PyQt licence from Riverbank, or a move to
PySide6 (LGPL). Selling *builds* of GPL software is perfectly allowed;
withholding the source from the people you sell to is not.

## Running it

```bash
cd /mnt/claude-brain/apps/rifftag
PYTHONPATH=. python3 -m rifftag          # or ./rifftag.sh
PYTHONPATH=. python3 -m pytest tests -q   # 575 tests, ~20s
```

Needs `python3-pyqt6` and `python3-mutagen`, plus `python3-pytest` and
`ffmpeg` to run the tests. Packaging lives in `packaging/`: a `.deb`, a
Windows build and a Flatpak manifest.

To build a shareable package:

```bash
./packaging/build-deb.sh                 # -> dist/rifftag_0.2_all.deb  (~5.6 MB)
sudo dpkg -i dist/rifftag_0.2_all.deb
sudo dpkg -r rifftag
```

Verified end to end: installs, runs from `/usr/bin/rifftag` with no `PYTHONPATH`,
finds its artwork under `/usr/share/rifftag/assets`, and removes with no
leftovers. `Architecture: all` with PyQt6 and mutagen taken from the distro,
which is why it is 5.6 MB rather than the ~80 MB a bundled runtime would cost.

**Build gotcha:** `du -sk` returns **0** for a tree just written to the CIFS
brain share, which silently shipped `Installed-Size: 0`. The script sums
`os.path.getsize` in Python instead.

**Tell people to double-click the .deb**, not `dpkg -i`. On Mint that opens
Captain (or GDebi elsewhere), which pulls `python3-pyqt6` automatically;
`dpkg -i` just fails on the missing dependency and needs a follow-up
`sudo apt -f install`.

### Shortcuts

The package installs the **menu entry** only. A `postinst` runs as root,
without a session and often with no GUI — it cannot know which human is
installing, cannot write to their Desktop with the right ownership, and must
not touch their panel, which is per-user session state. So the desktop and
panel shortcuts are offered by the app itself on first run (`shortcuts.py`),
as the user, with checkboxes. Also available any time from
**Options → Create shortcuts…**; the first-run prompt is shown once and
remembered in `QSettings` under `shortcuts_offered`.

The panel half is Cinnamon-only and deliberately cautious, because this is
exactly where a Plex launcher got lost during development:

* it **appends**, never replaces — dropping a launcher *onto* an existing one
  is what replaces it;
* it backs the applet's JSON up before editing;
* if there is no launcher applet at all it adds one at the **end** of the
  zone, so no existing applet has to be renumbered.

Tests cover all three, including one that starts from a three-Plex-button
applet and asserts all three survive.

---

## Documentation

| | |
|---|---|
| [docs/FEATURES.md](docs/FEATURES.md) | Every feature in detail, and why it behaves as it does |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Architecture, format support, and the gotchas already paid for |
| [docs/ORIGIN.md](docs/ORIGIN.md) | How the project was specified, and why not by decompiling anything |
| [docs/WINDOWS_PORT.md](docs/WINDOWS_PORT.md) | Building and testing the Windows version |
| [packaging/flatpak/README.md](packaging/flatpak/README.md) | Building the Flatpak, and the three things that were not obvious |

## Contributing

Bug reports and patches are welcome. The test suite is the contract:

```bash
PYTHONPATH=. python3 -m pytest tests -q
```

575 tests, about 20 seconds. Anything that changes behaviour wants a test that
fails without the change — most of the awkward lessons in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) are pinned by one.
