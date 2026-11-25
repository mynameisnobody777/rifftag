# What RiffTag does, in detail

The long version: every feature, and why it behaves the way it does.
For a summary, see the [README](../README.md).

## State

**Working now:** folder tree, recursive scan with background tag reading, the
folder-structure file list with colour-coded rows and in-grid tag editing, all
three tabs (Rename / Multi File Tag Editor / Get Tags from File Name), the full
5-tab Tag Editor dialog with cover art, Test/Rename/Undo, folder.jpg embed and
extract, and the Tools menu (auto-fill track #, Artist→Album Artist, strip
leading "The", trim spaces).

The Tag Editor matches the original's single-file workflow: an **editable File
box** at the top that renames on save, a two-column General tab with the File
information panel beside it, **Save and Prev / Save / Cancel / Remove Tag /
Save and Next** along the bottom so an album can be worked through without the
dialog closing, and a Play button that hands the track to the desktop's default
player. Double-clicking a row opens single-file mode; "Edit All Supported Tag
Fields" opens the whole selection at once. Prev/Next walk every file on screen,
not just the selection.

A **quick-tools toolbar** carries the strip Dave uses from the original:
refresh, search-subfolders, select all/none/invert, the check-box mode toggle,
move up/down, edit tags, remove tags, play, folder.jpg in and out, the
**track #** format dropdown and **Auto Fill Track # / Episode #**.

**Selection works both ways.** Ctrl/Shift highlighting by default; View → "Use
check boxes to select files" switches to ticks, which survive scrolling and
clicking elsewhere, with a tri-state tick on each folder row. In tick mode the
ticks are what every action uses, and the highlight is ignored.

**Online album lookup** (the globe button, Ctrl+W) ports the feature, not the
sources. freedb shut down in 2020 and Amazon's product API is gone, so:
**MusicBrainz** is the default and needs no account, with cover art from the
Cover Art Archive; **Discogs** needs a free personal access token from
discogs.com/settings/developers, pasted into Options → Discogs access token.
The dialog prefills from the tags the files already carry (or the folder name
when they have none), searches, lists the releases, then lines the fetched
titles up against the files positionally — with Move title ↑/↓ to fix an
offset — and writes only the fields you tick.

The folder tree renames in place: click, pause, click again (or F2), as in the
original. The three tabs sit **above** the file list, with the **colour key**
beside them.

**The top-left corner is a logo-and-player block** and the folder tree starts
*below* it, level with the file list — Tag&Rename gives its whole top strip to
the tag block, and this is the same visual separation without moving the tabs.
The block carries the lockup, a now-playing line, a seek bar with elapsed and
total, previous / play-pause / stop / next, a volume slider with a mute
button, and **Play all** (the whole list, starting at the selected row) and
**Play selected**. View → Player panel hides it.

The artwork is **painted as the block's backdrop**, not put in a label: Dave
composed Logo-05 with the mark in the upper part of a square and clear space
beneath it for exactly these controls, so the asset is cropped to the art
(rows 0–800 of 1254 — the last visible row is 758) and its own aspect ratio
becomes the art block. The panel scales it to its width, an empty
`logo_area` widget reserves that height in the layout, and the controls start
directly underneath. One asset covers both themes: it is a cut-out with no
backdrop, the same call as the emblem and the wordmark.

The **transport glyphs are drawn, not themed**. Desktop icon sets are the
wrong source for these: Dave's reads near-white, which all but vanished
against the light theme's window. `transport_icon()` paints the five shapes in
the palette's own `WindowText` colour (cached per colour and size), so they are
always exactly as legible as the text beside them, on any theme and any icon
set, and `refresh_theme()` redraws them on a switch.

**Quitting mid-track** asks whether to hand the song to the desktop's own
player, since RiffTag's player stops when RiffTag does. It restarts the track
rather than resuming it — there is no portable way to tell another player
where to start — and the prompt says so, naming the position you are losing.

Playback is **real and in-process** — `QMediaPlayer` from Qt Multimedia — which
is the only way stop, skip, seek and volume can mean anything; handing the file
to the desktop's player (what the toolbar's Play button used to do) can start a
track but never control it. Finishing a track advances the queue. Volume is
remembered. `python3-pyqt6.qtmultimedia` is a **separate distro package** and
therefore an optional import: without it the panel still builds, the transport
greys out with an explanatory tooltip, and Play falls back to the old hand-off.
It is a `Recommends:` in the .deb, not a `Depends:`, for exactly that reason.
mp3, wav and flac were verified decoding through it on this machine.

**The window is two real rows**, not two columns: `top_row` holds the player
block and the tabs, `bottom_row` holds the folder tree and the file list, and
a vertical splitter stacks them with the filter strip travelling with the
bottom row. The tree is therefore level with the list *by construction* — an
earlier arrangement used two vertical splitters pretending to be rows and had
to push their heights back and forth to stay in line.

What still needs keeping in step is the **column divider**: each row pushes
its widths onto the other (guarded against the signal bouncing back) and then
adopts whatever the other could actually take, because the two rows have
different minimum widths. One pass only — if a very narrow window leaves no
position that suits both, they settle as close as the constraints allow
rather than oscillating. The first alignment happens in `showEvent`, because
`setSizes()` before layout is only a hint.

The folder panel carries an **explicit** `setMinimumWidth(170)`: a QSplitter
clamps to each pane's `minimumSizeHint()`, and the header strip's buttons push
that hint to ~380px on a desktop whose icon theme has no media icons (every
button falls back to a word). An explicit minimum overrides the hint, so the
column can still be dragged narrow.

**The folder column slides to the folder you are in.** Indentation is the
whole problem: four levels down, a name starts past the right edge of a 260px
column, and before this you had to drag the scroll bar every time to see
where you were. `show_current_folder()` nudges the column sideways only when
the name is not already readable, parks the *start* of the name 24px inside
the left edge (a folder wider than the column shows its beginning, not its
tail), and runs on selection change, on expand, and once from `showEvent` —
that last one matters, because `_reveal()` runs during `_restore()` while the
tree still has no real width, and `visualRect()` is meaningless until it
does. Qt's own `scrollTo` is no help here: it either leaves the row alone or
parks the end of the name against the right edge.

**The Folders column** carries the original's header strip — **up one
folder**, home, favourites
(add/remove the current folder, then pick one), recent folders, new subfolder,
refresh, and a close box (View → Folder panel, Ctrl+L, brings it back) — and a
**horizontal scroll bar** along the bottom so a path several levels deep can be
read without dragging the splitter. Favourites and the recent list are JSON in
QSettings (`folder_favourites`, `folder_recent`, capped at 15). The word
"Folders:" hides itself when the column is too narrow to hold it *and* the
buttons — a QLabel does not elide, it just gets cut off mid-word, which reads
as a bug rather than as a narrow column.

**The Address and Filter bar** sits between the two rows, directly above the
pair it acts on — Tag&Rename's position. The address
line shows the open folder, accepts a typed path and keeps a drop-down of the
last 15. **Filter by** takes File Name, Any field or any of nine tag fields,
matches as a case-insensitive substring — or as an MS-DOS style wildcard the
moment the text contains `*` or `?` — and `Filter!` narrows the list to the
rows that match. **Hide Selected Files** takes rows out of the way without
touching the disk; **Show All** clears both. Filtering is done in the model:
each folder node keeps `all_children` (everything scanned) and `children`
(what the view sees), and with no filter in force they are *the same list
object*, so the unfiltered path is exactly what it always was. Rows arriving
from the background scan while a filter is on are filtered as they land, and a
folder with no matches is dropped rather than left as an empty heading.

**Rows carry `ROW_PADDING` (4px) of extra height.** The grid is deliberately
dense — fitting a whole album on screen is the point — but at the style's own
row height the lines ran together and were tiring to read. Four pixels
separates them without costing a row on a typical album.

**The shading division carries on past the last row.** `FileListView`
(via `_SectionBackgroundMixin`) paints the tag half of the *empty* pane below
the list in the same shade, so the division runs the full height. Without it
the table stops dead halfway down the pane and reads as unfinished rather
than empty. `empty_section_rect()` is the testable part: it returns the slab,
or None when the rows already fill the pane or no tag column is shown.

**The file list is shaded in two halves** — File Name on the left, every tag
column from the first one rightward in a slightly lifted shade — which is how
the original lets you find where the tags start without counting headers. See
the delegate note in the gotchas.

**Cover art takes a drop.** Drag a picture onto **File cover art** and it is
embedded in every selected file; drop one on **Folder cover art** and it is
written as `folder.jpg` beside the music; the Tag Editor's art box takes a
drop too (saved with the rest of that dialog's edits). Three sources work,
because all three are how people move a picture around: a file from the file
manager, an image dragged straight out of a browser (no file involved — the
pixels arrive on the clipboard), and a path as plain text.

Two rules in `gui/art.py`:

* **JPEG and PNG pass through byte for byte.** Re-encoding a cover someone
  already exported at 3000×3000 would cost a generation of quality for
  nothing. WebP, BMP, GIF and TIFF are converted to JPEG, which is what tag
  readers and stores reliably understand.
* **Folder art is always written as `folder.jpg`**, converting if need be.
  That name is what the rest of the app looks for, so writing `folder.png`
  beside an existing `folder.jpg` would leave the stale one winning the
  lookup.

The drop highlight is done with the widget's own palette, never a stylesheet
— see the note in `tabs.py` about stylesheets freezing a widget's colours at
the theme in force when they were parsed.

**Test/Preview applies in place.** The preview window carries a button that
goes ahead and does it — "Rename 17 file(s)", "Write tags to 17 file(s)" —
rather than making you close it and find the other button. There is
deliberately **no second confirmation**: the preview *is* the confirmation,
it lists every change individually, and an "are you sure?" layered on top of
a reviewed list is the kind people learn to click through without reading.
The button names the action and the count instead, and renames stay undoable.
When there is nothing applicable the button is simply absent.

**Release Check** (the leading toolbar button, Tools → Release Check, or F9)
is the feature this app has that a tagger normally does not: point it at a
finished album and it says what a distributor would send back, and what to do
about each thing.

It checks the release as a whole — album title that disagrees with itself,
album artist that differs across tracks, duplicate or missing track numbers,
gaps in the numbering, mixed title capitalisation, cover art that is not the
same on every file — and each track: missing title/artist/year, missing or
undersized or non-square cover art (3000×3000 is the published minimum), a
lossy master, a sample rate below CD, lyrics that still carry `[Verse 1]`
scaffolding, `feat.` in the title but not the artist field, stray spacing, no
ISRC. Tick **Also measure loudness** and it decodes every file on a worker
thread to report integrated LUFS and true peak, flagging anything above
−1 dBTP (lossy transcoding overshoots, so a master that touches 0 dBFS clips
on the listener's side) or far from the −14 LUFS streaming target.

Three rules keep it honest, and they are the ones to preserve when adding a
check:

* **Levels mean something.** *Problem* = will be rejected or is plainly
  wrong. *Warning* = will probably hurt you. *Note* = your call. Nothing that
  is merely taste is ever an error.
* **Anything that depends on a particular store's policy is a Note phrased as
  a question.** `feat.` in the title is the example: distributors genuinely
  disagree, so the app says "check your distributor" instead of inventing a
  verdict. The hard-coded thresholds (3000px, −1 dBTP, −14 LUFS) are the
  widely published norms and all live at the top of `preflight.py`.
* **One line per problem the whole release shares.** A 17-track album with
  the same undersized cover produced 17 identical warnings that buried
  everything which actually differed; findings identical across *every* track
  collapse to one line marked "(every track)", while anything that varies
  stays per file, because then the file name is the information.

Nothing in the check writes anything, and every finding names its fix —
several point straight at the tool that performs it ("Lyrics → Clean lyrics
for streaming", "Tools → Auto fill track #").

**Lyrics cleanup** (the **Lyrics** toolbar button, or Tools → Clean lyrics
for streaming…) strips the
scaffolding writing tools leave behind — `[Verse 1]`, `(x2)`, `Chorus:`, an
LRC timestamp, a copyright line, HTML from a web paste — and leaves the words.
Nine switches, all on by default except ALL-CAPS flattening, and a real
before/after pane per file because lyrics are the one field where a bad
automated edit is easy to make and hard to notice later. Nothing is written
until Clean is pressed; a second pass has nothing left to do.

The rule that makes it safe: **never delete a line that might be lyrics.** A
bracketed line is scaffolding by construction, so those go unconditionally. A
*bare* line like `Chorus:` is only dropped when the word is in a closed list
of section names (`lyrics.SECTION_WORDS`) — "Hold on:" is a lyric, and losing
it would be unforgivable. Smart quotes are flattened by default for a
concrete reason: an ID3v2.3 text frame falls back to UTF-16 the moment a
character leaves latin-1, and a stray smart quote is the usual cause of a
lyric sheet arriving at a distributor as mojibake.

**The Tag Editor's Lyrics tab carries its own Load and Save**, as the
original does: **Load from file…** reads a `.txt` or `.lrc` straight into the
box (any encoding — it tries UTF-8, then cp1252, then latin-1) and ticks the
Lyrics checkbox so the next Save actually writes it; **Save to file…** writes
what is in the box out to a text file. Both default to a name beside the
track (`02 In Bloom.txt`), or `lyrics.txt` in the folder when several files
are open at once. That is the single-file counterpart to the batch importer
below.

**Lyrics import** (Tools → Import lyrics from text files…) pulls in a `.txt`
or `.lrc` sitting beside each track, matched on the file name *or* the title
with punctuation, case and track numbers ignored. An ambiguous match is
refused rather than guessed. Existing lyrics are left alone unless Replace is
ticked, and the text can be cleaned on the way in — which is what makes
dropping a folder of `.lrc` files in work properly.

**Tap-to-sync** (Lyrics → Sync lyrics to the music…) times the lyrics
against the track: press **Space** on the beat of each line and it stamps the
line with the position you are hearing, then moves to the next one. Backspace
undoes a tap, double-clicking a line carries on from there, and ±0.5s shifts
every stamp at once when the whole take landed late. Save writes an ordinary
`.lrc` beside the track, optionally dropping the timed text into the lyrics
tag as well.

The clock is the point: the dialog reads the position **off the player the
main window is already using**, so what you hear and what gets stamped are
the same clock — a second player would drift against the first inside a
verse. The stamps therefore land slightly behind the wall clock, which is
correct: `position()` reports where the audio sink actually is, which is what
the listener is hearing.

Blank lines are kept as tappable lines on purpose: a timed blank is how an
LRC player clears the screen between verses. Two format details are pinned in
`lrc.py` because players are unforgiving about them — stamps are
**centiseconds, two digits** (`[01:23.456]` is rejected by enough players to
matter), and **minutes are not capped at 59**, since LRC has no hours field.
Reopening the dialog picks up an `.lrc` that is already there, so a half-done
sync is editable rather than something to start over.

**Format conversion** (Tools → Convert audio format…, or the toolbar button)
is an addition, not a Tag&Rename feature. It converts the selection — or the
whole list when nothing is selected — to **MP3** (VBR V0/V2 or 320/256/192
CBR), **FLAC**, **WAV** (16/24-bit, or 16/44.1 for CD) or **M4A/AAC**
(256/192/128). ffmpeg does the encoding; it is already a build dependency of
the test fixtures, and the dialog says `sudo apt install ffmpeg` instead of
converting if it is missing.

Three rules make it safe to press:

* **Originals are never touched.** Output goes to a subfolder of the source
  folder (`Nevermind/MP3/02 In Bloom.mp3`), so a second run is obviously
  re-runnable and a bad result is deleted by deleting one folder. An existing
  destination is reported as "destination exists" and skipped, never
  overwritten, and two sources that would land on the same name are caught in
  the plan.
* **ffmpeg encodes the audio and nothing else** — every run passes
  `-map_metadata -1` and `-vn`, and `tagio.write()` then puts the tags and the
  cover art on the new file. Letting ffmpeg copy the metadata would bypass the
  ID3v2.3-vs-2.4 choice, the GRP1/TIT1 grouping option and the ALBUMARTIST
  spelling, and would map fields by ffmpeg's vocabulary instead of ours.
* **The whole plan is shown before anything is encoded**, like the rename
  preview, and one unreadable file does not abandon the rest of the album —
  failures are collected and reported against their own row.

**The totals strip** along the bottom shows Total files, Selected files, Total
time, Selected time and Total size, as permanent status-bar widgets so a
transient "Reading tags… 12/400" message cannot wipe them. Sizes are stat'd
once per file and cached; the cache is dropped on every rescan.

**Branding** — Dave's artwork lives in `~/Pictures/logos/RiffTag/`
(`Logo-01` full lockup, `Logo-02` emblem, `Logo-04` wordmark). Derived assets
in `assets/`:

| asset | from | used for |
|---|---|---|
| `rifftag-logo.png` | Logo-01, corners rounded | splash, About on dark |
| `rifftag-logo-light.png` | Logo-01-forLightBackground, squared | About on light |
| `rifftag-emblem.png` + `icon-*.png` | Logo-02-forLightBackground, squared | app/window icon, empty-list watermark |
| `rifftag-wordmark.png` | Logo-04, trimmed to its bbox | menu-bar corner |
| `rifftag-player-logo.png` | Logo-05, cropped to row 800 of 1254 | the player block's backdrop |

It appears in four places: the splash, the **menu bar's top-right corner**
(a `QMenuBar` corner widget, which costs no vertical space — the title bar
itself is drawn by the window manager and takes only text plus the icon), a
faint **watermark** in the file list while it is empty with a "Choose a folder
on the left" hint, and **Help → About RiffTag**, whose format table is built
from `EXT_FAMILY` so it cannot drift from what the code supports.

**Only the About lockup is theme-aware**, and that was decided by comparing
each asset against both backgrounds at the size it is actually used, not by
assuming:

* **Emblem/icon** — one asset for both. The `-forLightBackground` file is
  simply the emblem *cut out*, with no backdrop to clash with anything, and it
  beat the earlier rounded-tile treatment at every icon size on light **and**
  dark. Despite its name it is the correct choice everywhere.
* **Wordmark** — one asset for both. The original dark-outline art stays crisp
  on white, while the light-background variant's white glow merges into a
  light background and *dulls* it at 20px. The light variant is unused.
* **Full lockup** — genuinely needs both, because the About dialog shows it at
  260px where the difference registers: the dark card reads as a deliberate
  card on dark, and would be a black slab on light. `logo_path(dark=...)`
  picks it. The splash always uses the dark one: it is its own window, and the
  dark treatment is the brand moment.

Icons are installed into `~/.local/share/icons/hicolor/`. Regenerate with the
snippets below. Options → "Show splash screen on startup"
turns the splash off. The window builds in about 0.2s, so without a floor the
splash just flashed: it is now held for `MIN_MS` (2s) with a timer — never a
sleep, since it has to keep painting while it waits — then the window appears.
A click skips the rest of the hold, and `MAX_MS` (6s) is a backstop so it can
never outlive the window it covers.

Regenerate the icons from a new logo:

```python
from PIL import Image, ImageDraw
L = "/home/dave/Pictures/logos/RiffTag"

def trimmed(path):
    im = Image.open(path).convert("RGBA")
    return im.crop(im.getbbox())

def squared(im):
    side = max(im.size)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(im, ((side-im.width)//2, (side-im.height)//2))
    return out

# icons + watermark: the cut-out emblem, correct on BOTH themes
emblem = squared(trimmed(f"{L}/Logo-02-forLightBackground.png"))
emblem.save("assets/rifftag-emblem.png")
emblem.save("assets/rifftag-icon.png")
for px in (16, 22, 24, 32, 48, 64, 128, 256, 512):
    emblem.resize((px, px), Image.LANCZOS).save(f"assets/icon-{px}.png")

# About on light
squared(trimmed(f"{L}/Logo-01-forLightBackground.png")).save(
    "assets/rifftag-logo-light.png")

# wordmark: trim to its own bounding box, nothing else
wm = Image.open(f"{L}/Logo-04.png").convert("RGBA")
wm.crop(wm.getbbox()).save("assets/rifftag-wordmark.png")

# splash/About logo: rounded so the dark field reads as a card in light mode
logo = Image.open(f"{L}/Logo-01.png").convert("RGBA")
m = Image.new("L", logo.size, 0)
ImageDraw.Draw(m).rounded_rectangle((0, 0, logo.width-1, logo.height-1),
                                    radius=int(min(logo.size)*0.06), fill=255)
logo.putalpha(m)
logo.save("assets/rifftag-logo.png")
```

Then reinstall the icons:

```bash
for px in 16 22 24 32 48 64 128 256 512; do
  cp assets/icon-$px.png ~/.local/share/icons/hicolor/${px}x${px}/apps/rifftag.png
done
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
```

**Theme**: View → Theme offers Light, Dark and Follow desktop, remembered
between runs. Every derived colour — row tints, zebra stripes, legend
swatches — is computed from the live palette, so a switch needs no restart.

**Toolbar placement**: View → Toolbar position puts the toolbar either at the
top of the window or directly **above the file list** (the default, and where
the original has it). The file list lives inside its own nested `QMainWindow`
purely to make that possible: Qt will only dock a toolbar to the edges of a
`QMainWindow`, so a nested one is the only way to get a *movable* toolbar
between the work tabs and the list.

**Row colours** signal **tag presence**, not field completeness: a plain
striped row is a normally tagged file, red means no tag at all, amber means
the file would not read. Colouring by completeness tinted nearly every row red
— one missing Year was enough — and left no neutral background to read
against.

**Copy a whole tag from one file to another.** Tag&Rename's workflow, and the
reason check boxes exist: ticks say *where to write*, the highlight says *what
to copy from*. Turn on View -> Use check boxes to select files, tick the
untagged track, highlight one that is already tagged, and open **Edit All
Supported Tag Fields**. Every field is already filled in and ticked — the copy
happens as the window opens, with no button to find, because that is how
Tag&Rename did it. **Copy from Focused File** is still there for re-copying
after you highlight a different file.

**Cover art comes across as well.** It is part of the tag, and an album's art
is the same on every track, which is the case this is for. It has its own
**Write cover art** tick box under the art, because art has no field to sit a
box beside: anything that changes the art in that window ticks it — a copy,
Add…, a drop, or Remove — and unticking it leaves the art on disk alone.
Unticked whenever the editor loads a file, so an ordinary edit never touches
art. A source with no art is simply not copied from, so it can never wipe art
the target already had.

``PER_TRACK_FIELDS`` — Title, Sort Title, Track # and Lyrics — are **filled
but not ticked**: visible, one click away, and not written unless you ask. They belong
to the song rather than to the release, which is the thing being copied. Sort
Title goes with Title because it is the same field; Track # is there for the
obvious reason, and the "of N" total goes with it because one tick governs
both halves of that widget.

Two more rules make it safe to use on an empty file:

* a field the source does not carry is left **unticked**, so it is not
  written and the target keeps whatever it had — blanks never flatten a tag;
* the copy is **not repeated when Prev/Next steps to another file** — a reload
  has to show that file's own tags, so it only ever happens as the window
  opens;
* the button is **disabled when the highlighted row is one of the files being
  edited**, which is every time check boxes are off, because then the
  highlight *is* the selection and there is nothing to copy from.

The Multi File Tag Editor tab has its own, smaller *Copy from Focused File*
covering the five fields on that tab (artist, album, year, genre, comment).
The one in the full editor reaches everything.

**Clean on the Lyrics tab.** The same cleaner as Tools -> Clean Lyrics, run on
the box in front of you, with a line underneath saying what it did. It edits
the box only — nothing reaches disk until Save, so Cancel is a real undo — and
it ticks the Lyrics box so Save actually writes the cleaned words. "Already
clean" and "the box is empty" are said out loud rather than looking like a
dead button. It uses the default options, which means `fix_shouting` stays
**off**: a stanza in capitals is sometimes deliberate, and that choice belongs
in the Tools dialog, which is also still the way to do many files at once.

**Not built yet,** roughly in the order they are worth doing:

1. Export/Import tags (csv / xml / html / xlsx round trip).
2. Rename Actions — saved file+folder mask pairs on a toolbar.
3. Playlist writing (m3u / m3u8).
4. A proper Mask Editor dialog; the button currently just saves the mask.
5. Column chooser UI — `OPTIONAL_COLUMNS` exists but nothing exposes it.
6. Tags synchronisation wizard (ID3v1 ↔ ID3v2).

Done since this list was written: **online album lookup** (MusicBrainz +
Discogs), the **filter bar** (wildcards over the file name, any tag field, or
any field at once), and **opening a file from the file manager** — right-click
a song, Open with -> RiffTag, and it opens that song's folder with the song
selected. That covers what the "Nemo right-click action" entry was asking
for, through the ordinary MIME association rather than a file-manager plugin,
and it works the same way on Windows (see
[../docs/WINDOWS_PORT.md](WINDOWS_PORT.md) for the one-off registration).

**Deliberately not planned:** Amazon import (Tag&Rename removed most of it in
3.9.15 anyway) and the Windows shell extension.
