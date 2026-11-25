# RiffTag — development notes

How the program is put together, and the reasoning behind decisions that
would otherwise look arbitrary. Written for whoever picks this up next.
User-facing documentation is in the [README](../README.md).

## Architecture

Engine and GUI are strictly separated. Everything in `rifftag/` except
`rifftag/gui/` is headless and unit-tested; the GUI only wires widgets to it.

```
rifftag/
  fields.py    canonical field model — ~50 fields, the ONLY vocabulary
               the rest of the code uses. Also the %-variable tables.
  casing.py    the four case modes + word replacements + keep-case list
  masks.py     the mask engine, both directions:
                 render()  tags -> path   ("Rename Files")
                 parse()   path -> tags   ("Get Tags from File Name")
  tagio.py     the ONLY module that knows about ID3 frames, MP4 atoms,
               Vorbis keys, APE keys and ASF attributes
  renamer.py   plan / apply / undo as separate steps
  convert.py   format conversion: the format/quality tables, plan() and
               the ffmpeg call. Headless; the only module that shells out
  lyrics.py    lyrics cleanup for streaming delivery, plus matching a
               track to a .txt/.lrc sitting beside it
  preflight.py the release check: every rule, plus loudness via ffmpeg
  lrc.py       the LRC format — stamps, metadata, parse and build
  tracknum.py  the track # format menu and auto fill
  webimport.py MusicBrainz and Discogs lookup (stdlib urllib, no deps)
  gui/
    model.py     the folder-tree file list, the filter, the background scan
                 worker and the tag-section shading delegate
    folderpanel.py  the Folders column: header bar (home / favourites /
                 recent / new / refresh / hide) and the horizontal scroll
                 bar settings a deep tree needs
    filterbar.py the Address line and the Filter by row above the panes
    player.py    the top-left block: the lockup plus real transport
                 (QMediaPlayer), with a hand-off fallback when Qt
                 Multimedia is not installed
    statusbar.py the totals strip (files / time / size, listed and selected)
    convert.py   the Convert audio format dialog and its worker thread
    lyrics.py    Clean lyrics for streaming, and Import lyrics from files
    preflight.py the Release Check report and its loudness worker
    lrcsync.py   tap-to-sync: time the lyrics against the playing track
    art.py       the cover-art drop target and the picture decoding
    webimport.py the Load Album Titles from Web dialog
    dialogs.py   the small prompts (Auto Fill Track #)
    theme.py     light / dark / follow-the-desktop palettes
    splash.py    startup splash, app icon, wordmark and emblem pixmaps
    about.py     About RiffTag, including the live format table
    shortcuts.py first-run desktop/panel shortcut offer
assets/        the logo and every pre-rendered icon size
    tabs.py      the three work tabs
    tageditor.py the full "Edit All Supported Tag Fields" dialog
    main.py      the window
```

The one rule that holds the design together: **`write()` only touches keys
present in the dict you pass it.** An absent key is left alone, a key mapped to
`""` is cleared. That is what makes checkbox-driven multi-file editing safe —
ticking Album and Year across 33 files cannot flatten their Titles. There is a
test for exactly that, per format.

### Mask grammar

Identical to Tag&Rename's, so saved masks and muscle memory carry over:

```
%1 Artist   %2 Title    %3 Album      %4 Year       %5 Genre
%6 Track #  %7 Filename %8 File no.   %9 Comment    %0 Bitrate
%t Playtime %b BPM      %cp Composer  %aa AlbumArt. %co Conductor
%# Disc #   %rt RelTime %md Mood      %lb Label     %rb Remixer
%key Key    %swt Show   %eid EpisodeID %snr Season  %enr Episode
%rnd Random         %d  (parse only) skip this part
```

`\` is a folder boundary: `%1 - %3\%6 %2` renames *and* files the track under
an `Artist - Album` directory. `/` is accepted too, `\` is what gets stored.

---

## Format support

Full parity with Tag&Rename's own list. Every one is round-trip tested against
a real ffmpeg encode, not a synthetic file.

| Family | Extensions | Standard |
|---|---|---|
| id3 | mp3 aiff wav dsf | ID3v2.3 / v2.4 |
| mp4 | m4a m4b m4r m4p m4v mp4 | iTunes atoms |
| vorbis | ogg oga opus spx flac | Vorbis comments |
| ape | mpc ape ofr wv tta | APEv2 |
| asf | wma asf wmv | Windows Media |

---

## Gotchas already paid for

Each of these cost real debugging. They are settings or guarded code, not
assumptions, and each has a test.

* **mutagen silently upgrades ID3 on load.** `ID3(path)` re-translates a v2.3
  tag to v2.4 in memory, so a test asserting `TYER` exists must pass
  `translate=False` or it will never see it.
* **`update_to_v23()` throws frames away.** Verified by probing, not by reading
  the spec: it drops `TMOO TDRL TSOA TSOP TSOT TSST`. Saving as v2.3 without
  care loses Mood and every sort field. `_V24_ONLY_TO_PRESERVE` in `tagio.py`
  re-adds them after the downgrade. `TDRC` is deliberately *not* in that list —
  the downgrade correctly rewrites it as `TYER`.
* **Grouping has two homes.** iTunes moved `TIT1` → `GRP1`; Tag&Rename 3.9.12
  added GRP1 and 3.9.13 changed its default. We read both (GRP1 wins), write
  whichever `WriteOptions.id3_grouping_frame` says, and clear the other so they
  can never disagree.
* **Album Artist and Label have two spellings each** in Vorbis/APE
  (`ALBUMARTIST` vs `ALBUM ARTIST`, `LABEL` vs `ORGANIZATION`). Read both,
  write the configured one. Both are in the Options menu.
* **Case-only renames fail on NTFS and Samba.** `lithium.mp3` → `Lithium.mp3`
  is a rename onto itself there. `renamer._move` detects it and hops via a
  temporary name. This matters here specifically — Dave's music lives on NTFS
  and Samba shares.
* **Filenames are sanitised for Windows by default**, not just Linux, for the
  same reason: `:` `?` `*` `"` `<` `>` `|` and a trailing dot would all produce
  a name a share cannot store.
* **`VCommentDict.pop()` takes no default argument**, unlike a dict. Use
  `if key in tags: del tags[key]`.
* **`QFileSystemModel` is in `QtGui` in Qt6**, not `QtWidgets` as in Qt5.
* **`QFileSystemModel` is still moving long after the window is up**, and it
  moves in two different ways. It lists each directory on a worker thread
  (`directoryLoaded`, `rowsInserted`) *and* re-sorts a directory once the
  listing finishes — and a re-sort emits only `layoutChanged`, no row
  signals at all. Scrolling to a folder nine levels down therefore has to be
  retried on all three signals, not just the obvious one: the second attempt
  parked the target row correctly at y=144 and the later re-sort of
  `/home/dave` slid it to y=1098 in a 310px viewport, which read as "the
  folder panel is empty". `MainWindow._reveal_when_loaded` /
  `_directory_loaded` / `_reveal_pending`, and the retry is queued through
  `QTimer.singleShot(0, ...)` because the signals arrive *before* the view
  lays the new rows out. It gives up after `REVEAL_WINDOW_MS` or as soon as
  the user clicks a folder, so nothing yanks the tree around mid-browse.
* **`_reveal` must expand every ancestor, from the root down.** A row inside
  a collapsed parent has no position, so `scrollTo` does nothing — while the
  sideways nudge in `show_current_folder` still runs and drags the column off
  to the indent of a row nobody can see.
* **A desktop entry in `~/.local/share/applications` REPLACES the packaged
  one of the same name.** It does not merge with it. RiffTag's own
  "Create shortcuts" feature used to write one with no `MimeType` line at
  all, so once the .deb was installed its associations never took effect and
  nothing done to the package could fix it. `shortcuts.repair_shadowed_entry()`
  removes a stale one (keeping a `.bak`) at startup; `desktop_entry_text()`
  now always carries the MIME list and `%F`. Check for this first if "Open
  with" ever looks wrong again:

  ```bash
  ls -la ~/.local/share/applications/rifftag.desktop
  gio mime audio/vnd.wave | grep rifftag
  ```

* **MIME aliases are not resolved when the "Open with" menu is built.**
  shared-mime-info records `audio/x-wav` as an alias of `audio/vnd.wave`, and
  claiming only the alias left RiffTag out of the menu for every .wav on the
  machine. Every spelling has to be listed. Find out what a given machine
  really calls a file:

  ```bash
  printf xxxxxxxxxxxxxxxx > /tmp/probe.wav && xdg-mime query filetype /tmp/probe.wav
  ```

  The list lives in `rifftag/desktopfile.py` and is **generated** into the
  packaged entry, the Flatpak entry, the development entry and the Windows
  registry script. `tests/test_desktopfile.py` fails if any of them drifts,
  or if a format `tagio` can open has no MIME decision at all.

* **Two installs of RiffTag are indistinguishable in the menu.** A Flatpak
  and a .deb both present an entry called "RiffTag", and picking the stale
  one looks exactly like the fix not working — which is what happened on
  2026-11-24. `flatpak list --app | grep -i rifftag` is the one-line check.

* **"Follow desktop" cannot be read off the startup palette.** That was the
  whole of the light-theme bug. On Windows the app runs under Fusion, whose
  palette is light *always* — it is a self-contained style, not a native one.
  On Linux the palette only comes back dark if the XDG settings portal
  answered over D-Bus, and a process started without
  `DBUS_SESSION_BUS_ADDRESS` gets light. Reproduce it exactly:

  ```bash
  env -i DISPLAY=:0 HOME=$HOME XDG_RUNTIME_DIR=/run/user/$(id -u) \
      PATH=/usr/bin:/bin /usr/bin/rifftag
  ```

  `theme.desktop_prefers_dark()` asks instead — Qt 6.5's `colorScheme()`,
  then the Windows registry or the portal, then GTK — and
  `theme.system_palette()` keeps the captured palette when it agrees (so the
  desktop's own accent colours survive) and substitutes `_DARK`/`_LIGHT`
  when it does not.
* **`color-scheme` alone is not enough on Mint.** It sits at `default` while
  a dark theme is running, so the *theme name* has to be checked too — and
  Dave's is `Mint-L-Darker-Aqua`, which is why the match is a `dark`
  substring rather than a `-dark` suffix. `gsettings` is read by subprocess
  rather than by importing Gio: dconf reads the user's database file
  directly when there is no session bus, so it still answers in exactly the
  stripped environment where the portal does not.
* **Anything that can raise or block in `closeEvent` must come after
  `_stop_scan()`.** A QThread still running when its window is destroyed
  aborts the whole process, so an exception in the quit-time prompt used to
  take the interpreter down rather than just fail. The test suite has the
  matching guard: an autouse fixture answers every `QMessageBox.question`
  with No, because a modal dialog nobody can click blocks the run forever.
* **A failed encode must delete its own output.** ffmpeg leaves a zero-or-part
  length file behind when it errors out, and the next run would see that as
  "destination exists" and skip the source *for good*. `convert()` unlinks it
  before raising.
* **Re-planning after a run wipes the result report.** The convert dialog has
  to re-plan when the batch finishes (the destinations exist now, so Convert
  must not offer to redo them) — but the first version called the same
  `refresh()` the controls use, and the summary and the per-file errors
  vanished the instant they appeared. `refresh(status=False)` exists for
  exactly that call site.
* **A `QTreeView` can never scroll sideways while its last section
  stretches.** That is the default, and it is why the folder column had no
  horizontal scroll bar: the single visible column was always *exactly* the
  viewport width, so there was nothing to scroll to and a path four levels
  deep simply vanished behind the indentation. The fix is four settings
  together, all of them necessary — `setStretchLastSection(False)`,
  `ResizeToContents`, `ElideNone` (an elided name fits by definition) and
  `ScrollPerPixel`. They live in `folderpanel.configure_tree()`.
* **`header.setSectionResizeMode(0, mode)` segfaults on a view with no
  model.** It indexes into the header's sections, and there are none yet.
  The all-sections overload `setSectionResizeMode(mode)` is safe and, with
  only column 0 visible, identical in effect. Found by a test that built a
  bare `QTreeView`; it took the interpreter down, not just the test.
* **A model cannot know whether the view is painting a row as the alternating
  stripe.** Two folders of three files each do not alternate on the model's
  own row numbers, so deriving the tag-section shade inside
  `data(BackgroundRole)` produced a chequerboard. `SectionShadeDelegate`
  reads `QStyleOptionViewItem.features & Alternate` instead — by the time the
  delegate sees the option the real background is known, and the shade is
  blended on top of it, stripe or row tint alike. Selected rows are left
  alone: shading a highlight only mutes it.
* **Never hardcode row colours.** The first build used fixed pastel tints and
  they were unreadable the moment Dave opened it — he runs a dark theme, and
  pale green behind near-white text is almost no contrast at all. `RowPalette`
  now blends each tint out of the palette's *own* `Base` colour and keeps the
  palette's `Text` on top, so it adapts to any theme without knowing which one
  is in use. `MainWindow.changeEvent` re-derives them on a theme switch.
  Tests lock every tint to >= 4.5:1 (WCAG AA) in light, dark and very-dark
  palettes, and lock the three tints to being distinguishable from each other.
  The error tint is deliberately stronger than the rest — amber and red are
  close in hue, and an unreadable file must not look merely incomplete.
* **mutagen has no class for the iTunes TV frames** (`TVSH TVEN TVSE TVES`), so
  they would be dropped on save. `_register_tv_frames()` registers them.
* **`QLineEdit.setText()` does not emit `textEdited`.** The auto-tick on the
  field checkboxes fires on real keystrokes only — which is correct, and which
  a test must account for.
* **Never style text with a bare `palette(mid)` stylesheet.** The first Tag
  Editor header did, and it rendered as dark-on-dark. Let the palette supply
  the colour, or set `Text` explicitly — same lesson as the row tints.
* **Save and Next re-reads from disk** rather than trusting the in-memory
  `Track`. After a save the file is the source of truth, and the next file may
  have been changed by something else since the folder was scanned.
* **`selectedRows()` returns click order, not display order.** Auto Fill Track
  # numbers by list position, so a ctrl-click selection built bottom-up would
  have numbered backwards. `selected_tracks()` sorts by `model.position_of()`.
  There is a test that selects rows 2, 0, 1 and expects 01, 02, 03.
* **An icon-only toolbar button with a missing icon is invisible.** Mint-L-
  Yellow has no `edit-select-none`, `edit-select-invert`, `format-number` or
  `chronometer`, so `TOOLBAR_ICONS` gives every action a fallback chain and
  anything still unresolved is switched to a text button. Note that under
  `QT_QPA_PLATFORM=offscreen` the icon theme name is empty and *every* lookup
  fails — do not conclude from a headless run that icons are missing.
* **Scale art at `devicePixelRatioF()`, not at logical size.** `wordmark()`
  and `emblem()` render `height * ratio` real pixels and then set the pixmap's
  `devicePixelRatio`; without that, Qt upscales a small pixmap on a HiDPI
  screen and the lettering turns to mush.
* **Do not key the emblem's dark field to transparency yourself.** It is drawn
  as a glow on dark, so an alpha key computed from colour distance leaves a
  pale halo at every size. The rounded-tile workaround that replaced it is
  also obsolete: Dave supplied a properly cut-out emblem
  (`Logo-02-forLightBackground.png`), which is what `assets/` is built from
  now. If the artwork is ever revised, ask for a cut-out rather than keying.
* **A stylesheet freezes a widget's colours at the theme that was active when
  it was set.** Any `setStyleSheet` switches that widget to `QStyleSheetStyle`,
  which resolves `palette(...)` and inherited colours once and caches them —
  so after a theme switch it renders with the old palette and looks washed
  out. Use `setFont` and `setFrameShape` instead. The only stylesheet left is
  on the legend swatches, which are re-set on every theme change. A test
  asserts no other widget carries one.
* **`apply_zebra` must read the APPLICATION palette, not the widget's.** Once
  it has run, the widget carries an overridden palette; re-deriving the stripe
  from that after a theme switch blends the new stripe out of the *old* Base,
  which made every other row solid black in light mode.
* **Capture the desktop palette before applying any theme.** Once
  `setPalette` has overridden it there is no way to ask Qt for it again, so
  "Follow desktop" would have nothing to go back to. `run()` calls
  `capture_system_palette()` first.
* **MusicBrainz signals a rate limit with 503, not 429.** It showed up as
  "Server returned 503 Service Temporarily Unavailable" on an ordinary search
  and looked like an outage; the response carries `Retry-After: 1`. `_get()`
  now retries both 429 and 503 up to `MAX_RETRIES`, honouring `Retry-After`
  and falling back to exponential backoff, and only reports failure if the
  limiter never lets up — with wording that says it is throttling.
* **Keep the MusicBrainz throttle module-level, not per-instance.** A fresh
  `MusicBrainz()` is built every time the web-import dialog opens, so a
  per-instance clock starts at zero and the new dialog's first request lands
  inside the previous one's one-request-per-second window. `_mb_clock` is
  shared and lock-guarded.
* **Patch `time.sleep` in web-import tests.** Without it the retry and
  throttle paths sleep for real and the suite goes from 0.1s to several
  seconds. `tests/test_webimport.py` has an autouse fixture for this.
* **`QHeaderView.ResizeMode.Stretch` locks a column.** A stretched column
  cannot be dragged, which made the web-import results unreadable when one
  album title was long. Every table uses `Interactive` plus
  `setStretchLastSection(True)`, sizes to contents after a fill, and caps any
  single column at `MAX_AUTO_WIDTH` so one 600-character title cannot push the
  rest off screen.
* **A bare max-width widget caps its whole grid column.** `setMaximumWidth(110)`
  on the Year combo capped column 1 at ~247px, and QGridLayout dumped the
  leftover ~500px into the *label* column — which is why the labels sat miles
  from their boxes. Wrap a width-limited field in an HBox with a trailing
  stretch so the cell can still expand. Reproduce with
  `QGridLayout` + one capped widget + `setColumnStretch(1, 1)`.
* **Qt's own AlternateBase is unreliable on a dark theme** — often invisible or
  far too loud. `gui.model.apply_zebra()` derives the stripe from the view's
  own Base colour (blended 5.5% toward Text) and a test pins it to a 3..25
  lightness delta.
* **Never let a QThread be destroyed while it runs** — it aborts the whole
  process, and a blocking `urlopen` cannot be cancelled. The web-import worker
  threads are held in `gui/webimport._LIVE` from start to finish, so a dialog
  that is closed *or simply garbage-collected* cannot take one down with it;
  the dialog separately disconnects the job's signals so a late answer is
  ignored rather than delivered to a dead widget. Closing never blocks.
* **The test suite must never touch the network.** The `no_network` autouse
  fixture in `tests/test_gui.py` refuses `urlopen` and stubs each provider's
  `cover()`. Without it, opening the album page fired a real Cover Art Archive
  request on a worker thread and crashed the run.
* **The track # menu's five entries are read literally** (`0x` = a zero then
  the number, so track 10 becomes `010`; `xx` = two digits, so 10 stays `10`).
  The first attempt read `xx` as "pad to the widest number in the selection"
  and a test caught that this makes `xx` and `0x` identical for any album of
  10+ tracks. Unverified against the original — every menu entry shows a live
  example so a wrong guess is visible. `tracknum.FORMATS` is the one place to
  change it.

---
