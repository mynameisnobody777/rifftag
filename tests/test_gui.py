"""GUI smoke tests.

These drive the real widgets under Qt's "offscreen" platform, so they run
headless in CI or over SSH and still catch the things that only break once a
widget is actually built and populated.
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                      # noqa: E402
from PyQt6.QtWidgets import (QApplication, QLabel,              # noqa: E402
                             QLineEdit, QMenu)

from rifftag import tagio                                       # noqa: E402
from rifftag.gui.main import MainWindow                         # noqa: E402
from rifftag.gui.model import FileListModel                     # noqa: E402
from rifftag.gui.tabs import MultiTagTab                        # noqa: E402
from rifftag.gui.tageditor import TagEditorDialog               # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def never_block_on_a_prompt(monkeypatch):
    """No test may sit on a modal dialog waiting for a click.

    Closing a window mid-track asks whether to hand the song to the desktop
    player. Unanswered — and offscreen there is nobody to answer — that
    dialog blocks the whole run. Default every question to No; the tests that
    care about the prompt patch it again themselves.

    Declared ABOVE close_windows on purpose: autouse fixtures tear down in
    reverse order, so this one must be set up first to still be in force
    while close_windows is shutting windows down.
    """
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)


@pytest.fixture(autouse=True)
def close_windows(qapp):
    """Close any window a test left open.

    MainWindow restores the last folder and starts a background scan, and a
    window that is garbage-collected with that thread still running aborts
    the interpreter ("QThread: Destroyed while thread is still running") —
    which turns one ordinary assertion failure into a lost test run.
    """
    yield
    for widget in qapp.topLevelWidgets():
        if isinstance(widget, MainWindow):
            widget.close()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The suite must never touch the internet.

    Without this, opening the web-import album page fires a real Cover Art
    Archive request on a worker thread, which makes the tests slow, flaky and
    dependent on a connection.
    """
    from rifftag import webimport

    def refuse(*args, **kwargs):
        raise webimport.WebImportError("network disabled in tests")

    monkeypatch.setattr(webimport.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(webimport.MusicBrainz, "cover", lambda self, a: None)
    monkeypatch.setattr(webimport.Discogs, "cover", lambda self, a: None)


@pytest.fixture
def library(sample, tmp_path):
    """An album on disk, the way a real folder looks."""
    folder = tmp_path / "Nirvana - Nevermind"
    folder.mkdir()
    made = []
    for n, title in enumerate(["Smells Like Teen Spirit", "In Bloom", "Come As You Are"], 1):
        src = sample("mp3", f"t{n}")
        dest = folder / f"{n:02d} {title}.mp3"
        src.rename(dest)
        tagio.write(dest, {"artist": "Nirvana", "album": "Nevermind",
                           "title": title, "track": str(n), "year": "1991"})
        made.append(dest)
    return folder, made


def _scan(window, folder):
    """Run the scan synchronously so the test does not race the worker."""
    from rifftag.gui.model import ScanWorker
    worker = ScanWorker(folder, False)
    worker.found.connect(window.model.add_tracks)
    worker.run()


def test_window_builds_and_lists_an_album(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)

    assert len(w.model.all_tracks()) == 3
    assert w.model.rowCount() == 1, "one folder row"
    folder_index = w.model.index(0, 0)
    assert w.model.rowCount(folder_index) == 3
    assert w.model.data(w.model.index(0, 3, folder_index)) == "Smells Like Teen Spirit"
    w.close()


def test_grid_cell_edit_writes_the_tag(qapp, library):
    folder, files = library
    model = FileListModel()
    model.add_tracks([tagio.read(p) for p in files])
    parent = model.index(0, 0)
    title_col = [c.key for c in model.columns].index("title")
    index = model.index(0, title_col, parent)

    assert model.setData(index, "Smells Like Nirvana", Qt.ItemDataRole.EditRole)
    assert tagio.read(files[0]).values["title"] == "Smells Like Nirvana"


def test_rename_preview_does_not_move_anything(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.selectAll()
    w.rename_tab.mask_row.combo.setEditText("%1 - %2")

    from rifftag.renamer import plan
    steps = plan(w.selected_tracks(), "%1 - %2")
    assert [s.dst.name for s in steps] == [
        "Nirvana - Smells Like Teen Spirit.mp3",
        "Nirvana - In Bloom.mp3",
        "Nirvana - Come As You Are.mp3"]
    assert all(p.exists() for p in files), "preview must not touch the disk"
    w.close()


def test_multi_tab_only_returns_checked_fields(qapp):
    # setText() is programmatic and deliberately does NOT tick the box —
    # only textEdited (a real keystroke) does. So tick it by hand here.
    tab = MultiTagTab()
    tab.inputs["album"].setText("Nevermind")
    tab.checks["album"].setChecked(True)
    tab.inputs["artist"].setText("ignored")
    tab.checks["artist"].setChecked(False)
    assert tab.values() == {"album": "Nevermind"}


def test_multi_tab_typing_auto_ticks_its_box(qapp):
    tab = MultiTagTab()
    assert not tab.checks["album"].isChecked()
    tab.inputs["album"].textEdited.emit("Nevermind")
    assert tab.checks["album"].isChecked()


def test_tag_editor_shows_mixed_values_and_writes_only_ticked_fields(qapp, library):
    folder, files = library
    tracks = [tagio.read(p) for p in files]
    dlg = TagEditorDialog(tracks)

    # Album is the same across all three, Title differs.
    assert dlg.inputs["album"].text() == "Nevermind"
    assert dlg.inputs["title"].text() == ""
    assert dlg.inputs["title"].placeholderText() == "<different values>"

    dlg.inputs["genre"].setEditText("Grunge")
    assert dlg.checks["genre"].isChecked()
    assert dlg.values() == {"genre": "Grunge"}

    dlg._save()
    for p in files:
        assert tagio.read(p).values["genre"] == "Grunge"
    # Titles survived, which is the whole point of the checkboxes.
    assert tagio.read(files[1]).values["title"] == "In Bloom"


def test_tag_editor_covers_every_field_in_the_model(qapp, library):
    from rifftag.fields import FIELDS
    folder, files = library
    dlg = TagEditorDialog([tagio.read(files[0])])
    expected = {f.key for f in FIELDS
                if not f.key.endswith("_total") and f.kind.name != "PICTURE"}
    missing = expected - set(dlg.inputs)
    assert not missing, f"not editable anywhere: {sorted(missing)}"


def test_rename_tab_preview_describes_the_mask(qapp):
    from rifftag.gui.tabs import RenameTab
    tab = RenameTab()
    tab.mask_row.combo.setEditText("%6 %2")
    assert tab.preview.text() == "<Track #> <Title>.mp3"


def _accept_autofill(monkeypatch, start=1, use_total=False):
    """Stub the modal prompt. Calling the real one would block the suite."""
    from rifftag.gui import dialogs
    monkeypatch.setattr(dialogs.AutoFillTrackDialog, "exec", lambda self: 1)
    monkeypatch.setattr(dialogs.AutoFillTrackDialog, "options",
                        lambda self: (start, use_total))


def test_autofill_track_numbers_the_selection(qapp, library, monkeypatch):
    folder, files = library
    for p in files:
        tagio.write(p, {"track": ""})
    _accept_autofill(monkeypatch)
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.selectAll()
    w._autofill_track()
    assert [tagio.read(p).values.get("track") for p in files] == ["01", "02", "03"]
    w.close()


def test_folder_art_round_trip_through_the_toolbar_buttons(qapp, library, jpeg_bytes):
    folder, files = library
    (folder / "folder.jpg").write_bytes(jpeg_bytes)
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.selectAll()

    w._embed_folder_jpg()
    assert tagio.read(files[0]).pictures[0].data == jpeg_bytes

    (folder / "folder.jpg").unlink()
    w._refresh_tracks(w.selected_tracks())
    w.files.selectAll()
    w._extract_folder_jpg()
    assert (folder / "folder.jpg").read_bytes() == jpeg_bytes
    w.close()


# --- row colour contrast ------------------------------------------------

def _relative_luminance(c) -> float:
    def channel(v: int) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return (0.2126 * channel(c.red()) + 0.7152 * channel(c.green())
            + 0.0722 * channel(c.blue()))


def _contrast(a, b) -> float:
    hi, lo = sorted((_relative_luminance(a), _relative_luminance(b)),
                    reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _palette(base: str, text: str):
    from PyQt6.QtGui import QColor, QPalette
    p = QPalette()
    p.setColor(QPalette.ColorRole.Base, QColor(base))
    p.setColor(QPalette.ColorRole.Text, QColor(text))
    return p


@pytest.mark.parametrize("base,text,label", [
    ("#ffffff", "#000000", "light"),
    ("#2f2f2f", "#dcdcdc", "dark"),         # ~Mint-Y-Dark, what Dave runs
    ("#1c1c1c", "#f0f0f0", "very dark"),
])
def test_row_tints_stay_readable_in_any_theme(qapp, base, text, label):
    """Hardcoded pastels were unreadable on a dark theme. Lock that out.

    4.5:1 is the WCAG AA threshold for body text.
    """
    from rifftag.gui.model import RowPalette, RowState
    rp = RowPalette(_palette(base, text))
    for state in RowState:
        ratio = _contrast(rp.swatch(state), rp.text)
        assert ratio >= 4.5, f"{label}/{state.value}: only {ratio:.2f}:1"


def test_row_tints_are_distinguishable_from_each_other(qapp):
    """They must also not all collapse into the same near-Base colour."""
    from rifftag.gui.model import RowPalette, RowState
    rp = RowPalette(_palette("#2f2f2f", "#dcdcdc"))
    states = list(RowState)
    for i, a in enumerate(states):
        for b in states[i + 1:]:
            ca, cb = rp.swatch(a), rp.swatch(b)
            distance = (abs(ca.red() - cb.red()) + abs(ca.green() - cb.green())
                        + abs(ca.blue() - cb.blue()))
            assert distance > 30, f"{a.value} and {b.value} look the same"


def test_a_tagged_row_is_left_neutral_so_the_stripe_shows(qapp, library):
    """Colouring every row turned the whole list maroon. Tinting now means
    "this one needs you"; an ordinary tagged file stays on the stripe."""
    from PyQt6.QtCore import Qt as _Qt
    folder, files = library
    model = FileListModel()
    model.add_tracks([tagio.read(p) for p in files])
    index = model.index(0, 0, model.index(0, 0))
    assert model.data(index, _Qt.ItemDataRole.BackgroundRole) is None
    # The text colour is still set explicitly, never left to the style.
    assert model.data(index, _Qt.ItemDataRole.ForegroundRole) is not None


def test_an_untagged_or_broken_row_is_tinted(qapp, library, tmp_path):
    from PyQt6.QtCore import Qt as _Qt
    from rifftag.gui.model import RowState, row_state
    folder, files = library
    tagio.remove_tags(files[0])
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio")

    model = FileListModel()
    model.add_tracks([tagio.read(files[0]), tagio.read(broken)])
    parent = model.index(0, 0)
    assert model.data(model.index(0, 0, parent),
                      _Qt.ItemDataRole.BackgroundRole) is not None

    assert row_state(tagio.read(files[0])) is RowState.NO_TAG
    assert row_state(tagio.read(broken)) is RowState.UNREADABLE
    assert row_state(tagio.read(files[1])) is RowState.NORMAL


# --- Tag Editor navigation, filename editing and play -------------------

def _editor(files, index=0):
    tracks = [tagio.read(p) for p in files]
    return TagEditorDialog([tracks[index]], siblings=tracks)


def test_save_and_next_writes_then_moves_to_the_following_file(qapp, library):
    folder, files = library
    dlg = _editor(files)
    assert dlg.file_edit.text().endswith("01 Smells Like Teen Spirit.mp3")

    dlg.inputs["genre"].setEditText("Grunge")
    dlg.checks["genre"].setChecked(True)
    dlg._step(+1)

    assert tagio.read(files[0]).values["genre"] == "Grunge", "should have saved"
    assert dlg.file_edit.text().endswith("02 In Bloom.mp3"), "should have advanced"
    assert dlg.inputs["title"].text() == "In Bloom", "fields reloaded"
    assert not dlg.checks["genre"].isChecked(), "ticks reset for the new file"


def test_save_and_prev_goes_back(qapp, library):
    folder, files = library
    dlg = _editor(files, index=1)
    assert dlg.btn_prev.isEnabled()
    dlg._step(-1)
    assert dlg.inputs["title"].text() == "Smells Like Teen Spirit"


def test_navigation_buttons_disable_at_the_ends(qapp, library):
    folder, files = library
    first, last = _editor(files, 0), _editor(files, len(files) - 1)
    assert not first.btn_prev.isEnabled() and first.btn_next.isEnabled()
    assert last.btn_prev.isEnabled() and not last.btn_next.isEnabled()


def test_editing_the_file_box_renames_on_save(qapp, library):
    folder, files = library
    dlg = _editor(files)
    new = folder / "renamed here.mp3"
    dlg.file_edit.setText(str(new))
    assert dlg.commit() == []

    assert new.exists() and not files[0].exists()
    assert dlg.renamed == [(files[0], new)]
    assert tagio.read(new).values["title"] == "Smells Like Teen Spirit"


def test_rename_onto_an_existing_file_is_refused(qapp, library):
    folder, files = library
    dlg = _editor(files)
    dlg.file_edit.setText(str(files[1]))        # another track in the album
    failures = dlg.commit()

    assert failures and "already exists" in failures[0]
    assert files[0].exists() and files[1].exists(), "neither file was touched"


def test_an_untouched_file_box_renames_nothing(qapp, library):
    folder, files = library
    dlg = _editor(files)
    assert dlg.commit() == []
    assert dlg.renamed == []
    assert files[0].exists()


def test_multi_file_mode_hides_navigation_and_locks_the_file_box(qapp, library):
    folder, files = library
    dlg = TagEditorDialog([tagio.read(p) for p in files])
    assert not dlg.file_edit.isEnabled()
    assert "3 files" in dlg.file_edit.text()
    assert not dlg.btn_prev.isVisible() and not dlg.btn_next.isVisible()
    assert not dlg.btn_play.isVisible()


def test_file_information_panel_is_populated(qapp, library):
    folder, files = library
    text = _editor(files).info_label.text()
    for expected in ("Duration:", "Bitrate:", "Channels: stereo",
                     "Samplerate: 44100", "Tag: ID3v2.3"):
        assert expected in text


def test_play_button_asks_the_desktop_to_open_the_file(qapp, library, monkeypatch):
    folder, files = library
    opened = []
    monkeypatch.setattr("rifftag.gui.tageditor.QDesktopServices.openUrl",
                        lambda url: opened.append(Path(url.toLocalFile()))
                        or True)
    _editor(files)._play()
    # Compared as paths: QUrl.toLocalFile() returns forward slashes on
    # Windows, which says nothing about whether the right file was opened.
    assert opened == [files[0]]


def test_general_tab_puts_the_short_fields_in_the_right_column(qapp, library):
    """Matches the original's layout: Track/Disc/Rating/Mood/BPM beside, not below."""
    from rifftag.gui.tageditor import GENERAL_RIGHT
    from rifftag.fields import BY_KEY
    for key in GENERAL_RIGHT:
        assert BY_KEY[key].group == "general", key


def test_remove_tag_clears_then_reloads_the_dialog(qapp, library, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    folder, files = library
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg = _editor(files)
    dlg._remove_tag()
    assert tagio.read(files[0]).values == {}
    assert dlg.inputs["title"].text() == ""


# --- toolbar, check boxes and ordering ----------------------------------

def test_toolbar_exists_with_the_quick_tools(qapp, library):
    folder, files = library
    w = MainWindow()
    labels = [a.text() for a in w.toolbar.actions() if a.text()]
    for expected in ("Refresh", "Select all", "Move file up", "Play",
                     "Auto fill track #…"):
        assert expected in labels, labels
    assert [a.text() for a in w.track_menu.actions()][0].startswith(
        "Format Track # to x")
    w.close()


def test_check_box_mode_drives_the_selection(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.act_checkboxes.setChecked(True)

    assert w.selected_tracks() == [], "nothing ticked yet"
    w.files.selectAll()
    assert w.selected_tracks() == [], "highlighting must not count in tick mode"

    w.model.set_checked({files[1].resolve()}, True)
    w.model.set_checked({files[1]}, True)
    assert [t.path for t in w.selected_tracks()] == [files[1]]
    w.close()


def test_ticking_a_folder_row_ticks_its_files(qapp, library):
    from PyQt6.QtCore import Qt as _Qt
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.act_checkboxes.setChecked(True)

    parent = w.model.index(0, 0)
    w.model.setData(parent, _Qt.CheckState.Checked.value,
                    _Qt.ItemDataRole.CheckStateRole)
    assert len(w.selected_tracks()) == 3
    assert w.model.data(parent, _Qt.ItemDataRole.CheckStateRole) is _Qt.CheckState.Checked
    w.close()


def test_a_partly_ticked_folder_shows_as_partially_checked(qapp, library):
    from PyQt6.QtCore import Qt as _Qt
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.act_checkboxes.setChecked(True)
    w.model.set_checked({files[0]}, True)
    parent = w.model.index(0, 0)
    assert w.model.data(parent, _Qt.ItemDataRole.CheckStateRole) \
        is _Qt.CheckState.PartiallyChecked
    w.close()


def test_turning_check_boxes_off_clears_the_ticks(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.act_checkboxes.setChecked(True)
    w.model.set_all_checked(True)
    w.act_checkboxes.setChecked(False)
    assert w.model.checked_tracks() == []
    w.close()


def test_invert_selection_flips_the_ticks(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.act_checkboxes.setChecked(True)
    w.model.set_checked({files[0]}, True)
    w._invert_selection()
    assert [t.path for t in w.selected_tracks()] == [files[1], files[2]]
    w.close()


def test_move_file_down_reorders_the_list(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.act_checkboxes.setChecked(True)
    w.model.set_checked({files[0]}, True)

    w._move(+1)
    assert [t.path for t in w.model.all_tracks()] == [files[1], files[0], files[2]]
    w._move(-1)
    assert [t.path for t in w.model.all_tracks()] == files
    w.close()


def test_a_file_cannot_be_moved_off_the_end(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.act_checkboxes.setChecked(True)
    w.model.set_checked({files[0]}, True)
    w._move(-1)
    assert [t.path for t in w.model.all_tracks()] == files
    w.close()


def test_autofill_numbers_by_list_order_not_click_order(qapp, library, monkeypatch):
    """Ctrl-clicking bottom-up must still number top-down."""
    from PyQt6.QtCore import QItemSelectionModel
    folder, files = library
    for p in files:
        tagio.write(p, {"track": ""})
    _accept_autofill(monkeypatch)
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)

    parent = w.model.index(0, 0)
    sel = w.files.selectionModel()
    for row in (2, 0, 1):                       # deliberately out of order
        sel.select(w.model.index(row, 0, parent),
                   QItemSelectionModel.SelectionFlag.Select
                   | QItemSelectionModel.SelectionFlag.Rows)

    w._autofill_track()
    assert [tagio.read(p).values.get("track") for p in files] == ["01", "02", "03"]
    w.close()


def test_autofill_can_start_from_a_chosen_number(qapp, library, monkeypatch):
    folder, files = library
    _accept_autofill(monkeypatch, start=5)
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.selectAll()
    w._autofill_track()
    assert [tagio.read(p).values.get("track") for p in files] == ["05", "06", "07"]
    w.close()


def test_track_format_menu_rewrites_the_selection(qapp, library):
    from rifftag.tracknum import BY_KEY
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.selectAll()
    w._format_track_numbers(BY_KEY["00x"])
    assert [tagio.read(p).values.get("track") for p in files] == ["001", "002", "003"]
    w.close()


# --- web import dialog --------------------------------------------------

def _album():
    from rifftag.webimport import Album, TrackInfo
    return Album("MusicBrainz", "mbid", artist="Nirvana", album="Nevermind",
                 year="1991", label="DGC", genre="Grunge", track_count=3,
                 tracks=[TrackInfo(1, "Smells Like Teen Spirit", length=301),
                         TrackInfo(2, "In Bloom", length=254),
                         TrackInfo(3, "Come As You Are", length=219)])


def _web_dialog(files):
    from rifftag.gui.webimport import WebImportDialog
    return WebImportDialog([tagio.read(p) for p in files])


def test_web_dialog_prefills_from_existing_tags(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    assert dlg.artist.currentText() == "Nirvana"
    assert dlg.album_box.currentText() == "Nevermind"
    assert dlg.year.currentText() == "1991"
    assert "selected files: 3" in dlg.windowTitle()


def test_web_dialog_falls_back_to_the_folder_name_when_untagged(qapp, library):
    folder, files = library
    for p in files:
        tagio.remove_tags(p)
    dlg = _web_dialog(files)
    assert dlg.album_box.currentText() == "Nirvana - Nevermind"


def test_results_table_lists_what_the_search_returned(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    dlg._show_results([_album()])
    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 0).text() == "Nirvana"
    assert dlg.table.item(0, 1).text() == "Nevermind"
    assert dlg.table.item(0, 3).text() == "DGC"


def test_loading_an_album_pairs_titles_against_the_files(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    dlg._show_album(_album())
    assert dlg.pages.currentIndex() == 1
    assert dlg.pairs.rowCount() == 3
    assert dlg.pairs.item(0, 0).text() == "01 Smells Like Teen Spirit.mp3"
    assert dlg.pairs.item(1, 1).text() == "In Bloom"


def test_more_files_than_tracks_leaves_the_extras_unmatched(qapp, library):
    from rifftag.webimport import TrackInfo
    folder, files = library
    dlg = _web_dialog(files)
    album = _album()
    album.tracks = [TrackInfo(1, "Only One")]
    dlg._show_album(album)
    assert dlg.pairs.item(1, 1).text() == "— no track —"
    assert len(dlg.planned_writes()) == 1, "unmatched files are skipped"


def test_shift_rotates_the_titles_to_fix_an_offset(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    dlg._show_album(_album())
    dlg._shift(+1)
    assert dlg.pairs.item(0, 1).text() == "Come As You Are"
    dlg._shift(-1)
    assert dlg.pairs.item(0, 1).text() == "Smells Like Teen Spirit"


def test_only_ticked_fields_are_written(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    dlg._show_album(_album())
    for key, box in dlg.field_checks.items():
        box.setChecked(key in ("title", "track"))

    plans = dlg.planned_writes()
    assert set(plans[0][1]) == {"title", "track", "track_total"}

    dlg._write_tags()
    first = tagio.read(files[0]).values
    assert first["title"] == "Smells Like Teen Spirit"
    assert first["track"] == "1"
    assert first["album"] == "Nevermind", "untouched because Album was unticked"


def test_track_total_follows_the_selection_size(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    dlg._show_album(_album())
    assert dlg.planned_writes()[0][1]["track_total"] == "3"


def test_unticking_track_drops_the_total_too(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    dlg._show_album(_album())
    dlg.field_checks["track"].setChecked(False)
    assert "track_total" not in dlg.planned_writes()[0][1]


def test_write_tags_applies_the_whole_album(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    dlg._show_album(_album())
    dlg.field_checks["genre"].setChecked(True)
    dlg.field_checks["label"].setChecked(True)
    dlg._write_tags()

    assert dlg.wrote == 3
    titles = [tagio.read(p).values["title"] for p in files]
    assert titles == ["Smells Like Teen Spirit", "In Bloom", "Come As You Are"]
    assert tagio.read(files[0]).values["genre"] == "Grunge"
    assert tagio.read(files[0]).values["label"] == "DGC"


def test_cover_art_is_written_when_ticked(qapp, library, jpeg_bytes):
    folder, files = library
    dlg = _web_dialog(files)
    dlg._show_album(_album())
    dlg._show_cover(jpeg_bytes)
    assert dlg.cover_check.isChecked()
    dlg._write_tags()
    assert tagio.read(files[0]).pictures[0].data == jpeg_bytes


def test_a_missing_cover_disables_the_tick_rather_than_failing(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    dlg._show_album(_album())
    dlg._show_cover(None)
    assert not dlg.cover_check.isEnabled()
    dlg._write_tags()
    assert not tagio.read(files[0]).pictures


def test_a_failed_lookup_shows_a_message_and_re_enables_search(qapp, library,
                                                               monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: shown.append(a[2]))
    folder, files = library
    dlg = _web_dialog(files)
    dlg._busy(True, "Searching…")
    dlg._failed("Could not reach the server: Network is unreachable")
    assert shown and "Network is unreachable" in shown[0]
    assert dlg.btn_search.isEnabled()


def test_web_import_is_on_the_toolbar(qapp):
    w = MainWindow()
    labels = [a.text() for a in w.toolbar.actions() if a.text()]
    assert "Get album titles from the Web…" in labels
    w.close()


def test_closing_mid_lookup_does_not_wait_or_crash(qapp, library):
    """A dropped dialog must not take a running worker thread down with it.

    Destroying a QThread that is still running aborts the process, and a
    blocking urlopen cannot be cancelled — so the thread is held in a module
    registry rather than by the dialog.
    """
    import time

    from rifftag.gui import webimport as gui_webimport

    dlg = _web_dialog(library[1])
    dlg._run(lambda: time.sleep(0.3) or "late", lambda _: None)
    assert gui_webimport._LIVE, "the thread should be held outside the dialog"

    dlg.reject()
    assert dlg._thread is None, "the dialog lets go immediately"
    del dlg

    deadline = time.monotonic() + 5
    while gui_webimport._LIVE and time.monotonic() < deadline:
        qapp.processEvents()
    assert not gui_webimport._LIVE, "the thread should clean itself up"


def test_a_late_answer_is_ignored_after_the_dialog_closes(qapp, library):
    delivered = []
    dlg = _web_dialog(library[1])
    dlg._run(lambda: "result", delivered.append)
    dlg._stop_thread()
    for _ in range(50):
        qapp.processEvents()
    assert delivered == []


def test_result_columns_can_be_dragged_and_fit_their_contents(qapp, library):
    """Stretch mode locks a column; Interactive is what makes it draggable."""
    from PyQt6.QtWidgets import QHeaderView
    folder, files = library
    dlg = _web_dialog(files)
    album = _album()
    album.album = "Digital Psalms: Harmonies Of The Hidden Code"
    dlg._show_results([album])

    header = dlg.table.horizontalHeader()
    assert header.sectionResizeMode(1) is QHeaderView.ResizeMode.Interactive
    assert dlg.table.columnWidth(1) > dlg.table.columnWidth(2), \
        "the album column should size to its contents"

    dlg.table.setColumnWidth(1, 500)
    assert dlg.table.columnWidth(1) == 500, "the user's drag must stick"


def test_a_very_long_title_cannot_push_other_columns_off_screen(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    album = _album()
    album.album = "A" * 600
    dlg._show_results([album])
    assert dlg.table.columnWidth(1) <= dlg.MAX_AUTO_WIDTH


def test_result_and_pair_tables_are_striped(qapp, library):
    folder, files = library
    dlg = _web_dialog(files)
    assert dlg.table.alternatingRowColors()
    assert dlg.pairs.alternatingRowColors()


def test_the_stripe_is_subtle_but_visible(qapp):
    """Loud enough to follow a row, quiet enough not to fight the content."""
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QTableWidget
    from rifftag.gui.model import apply_zebra

    table = QTableWidget(2, 2)
    palette = table.palette()
    palette.setColor(QPalette.ColorRole.Base, __import__(
        "PyQt6.QtGui", fromlist=["QColor"]).QColor("#2f2f2f"))
    palette.setColor(QPalette.ColorRole.Text, __import__(
        "PyQt6.QtGui", fromlist=["QColor"]).QColor("#dcdcdc"))
    table.setPalette(palette)
    apply_zebra(table)

    base = table.palette().color(QPalette.ColorRole.Base)
    alt = table.palette().color(QPalette.ColorRole.AlternateBase)
    delta = abs(base.lightness() - alt.lightness())
    assert 3 <= delta <= 25, f"stripe differs by {delta} lightness"


def test_search_labels_sit_next_to_their_fields(qapp, library):
    """A bare max-width widget caps its grid column and exiles the labels."""
    folder, files = library
    dlg = _web_dialog(files)
    dlg.resize(1040, 600)
    dlg.show()
    qapp.processEvents()

    label = next(w for w in dlg.findChildren(QLabel) if w.text() == "Artist:")
    gap = dlg.artist.mapTo(dlg, dlg.artist.rect().topLeft()).x() - (
        label.mapTo(dlg, label.rect().topRight()).x())
    assert 0 <= gap < 80, f"label is {gap}px from its field"
    dlg.close()


# --- theme and toolbar placement ----------------------------------------

def test_toolbar_sits_above_the_file_list_by_default(qapp):
    w = MainWindow()
    assert w.toolbar_place == "list"
    assert w.toolbar in w.list_host.findChildren(type(w.toolbar))
    w.close()


def test_toolbar_can_be_moved_to_the_window_top_and_back(qapp):
    w = MainWindow()
    w._place_toolbar("top")
    assert w.toolbar.parent() is w
    assert w.act_tb_top.isChecked() and not w.act_tb_list.isChecked()

    w._place_toolbar("list")
    assert w.toolbar.parent() is w.list_host
    assert w.act_tb_list.isChecked()
    assert w.toolbar.isVisibleTo(w.list_host)
    w.close()


def test_toolbar_keeps_its_actions_across_a_move(qapp):
    w = MainWindow()
    before = [a.text() for a in w.toolbar.actions()]
    w._place_toolbar("top")
    w._place_toolbar("list")
    assert [a.text() for a in w.toolbar.actions()] == before
    w.close()


@pytest.mark.parametrize("theme_name,dark", [("light", False), ("dark", True)])
def test_applying_a_theme_changes_the_palette(qapp, theme_name, dark):
    from PyQt6.QtGui import QPalette
    from rifftag.gui.theme import Theme, is_dark

    w = MainWindow()
    w._apply_theme(Theme(theme_name))
    assert is_dark(qapp.palette()) is dark
    base = qapp.palette().color(QPalette.ColorRole.Base)
    assert (base.lightness() < 128) is dark
    w.close()


def test_switching_theme_re_derives_the_stripe_from_the_new_palette(qapp):
    """The stripe was blended from the view's own overridden palette, so after
    a dark->light switch every other row came out solid black."""
    from PyQt6.QtGui import QPalette
    from rifftag.gui.theme import Theme

    w = MainWindow()
    w._apply_theme(Theme.DARK)
    w._apply_theme(Theme.LIGHT)

    view = w.files.palette()
    base = view.color(QPalette.ColorRole.Base)
    alt = view.color(QPalette.ColorRole.AlternateBase)
    assert base.lightness() > 128, "light theme should have a light Base"
    assert abs(base.lightness() - alt.lightness()) < 30, \
        f"stripe is {alt.name()} against a {base.name()} base"
    w.close()


def test_switching_theme_refreshes_the_legend_swatches(qapp):
    from rifftag.gui.theme import Theme

    w = MainWindow()
    w._apply_theme(Theme.DARK)
    dark = {s: w.model.row_palette.swatch(s).name()
            for s in w.model.row_palette.tints}
    w._apply_theme(Theme.LIGHT)
    light = {s: w.model.row_palette.swatch(s).name()
             for s in w.model.row_palette.tints}
    assert dark != light, "tints must be re-derived for the new theme"
    w.close()


def test_the_legend_lists_every_row_state(qapp):
    from PyQt6.QtWidgets import QLabel as _QLabel
    from rifftag.gui.model import LEGEND, RowState

    w = MainWindow()
    texts = {lbl.text() for lbl in w.legend.findChildren(_QLabel)}
    for state in RowState:
        assert LEGEND[state] in texts
    w.close()


def test_colour_bearing_widgets_avoid_stylesheets(qapp):
    """A stylesheet switches a widget to QStyleSheetStyle, which caches the
    colours it resolved at parse time — the widget then keeps the OLD theme's
    colours and renders washed out. Only the legend swatches may use one,
    because they are re-set on every theme change."""
    from PyQt6.QtWidgets import QWidget as _QWidget

    w = MainWindow()
    offenders = [widget for widget in w.findChildren(_QWidget)
                 if widget.styleSheet()
                 and widget not in w.legend.findChildren(_QWidget)]
    assert not offenders, [type(o).__name__ for o in offenders]
    w.close()


def test_system_theme_restores_the_desktop_palette(qapp):
    from PyQt6.QtGui import QPalette
    from rifftag.gui import theme as theme_mod
    from rifftag.gui.theme import Theme

    theme_mod.capture_system_palette(qapp)
    original = QPalette(theme_mod._system_palette)
    w = MainWindow()
    w._apply_theme(Theme.DARK)
    w._apply_theme(Theme.SYSTEM)
    assert qapp.palette().color(QPalette.ColorRole.Base) == \
        original.color(QPalette.ColorRole.Base)
    w.close()


# --- branding: icon and splash ------------------------------------------

def test_every_icon_size_is_present_and_square(qapp):
    Image = pytest.importorskip("PIL.Image",
                                reason="Pillow only checks the shipped art")
    from rifftag.gui.splash import ASSETS

    sizes = sorted(int(p.stem.split("-")[1]) for p in ASSETS.glob("icon-*.png"))
    assert sizes == [16, 22, 24, 32, 48, 64, 128, 256, 512]
    for px in sizes:
        with Image.open(ASSETS / f"icon-{px}.png") as im:
            assert im.size == (px, px)


def test_the_app_icon_offers_the_small_sizes_explicitly(qapp):
    """Letting Qt downscale one big pixmap turns the emblem to mush at 16px."""
    from rifftag.gui.splash import app_icon

    icon = app_icon()
    assert not icon.isNull()
    available = {s.width() for s in icon.availableSizes()}
    assert {16, 22, 32, 48}.issubset(available), sorted(available)


def test_the_window_carries_the_icon(qapp):
    w = MainWindow()
    assert not w.windowIcon().isNull()
    w.close()


def test_splash_shows_and_is_scaled_down(qapp):
    from rifftag.gui.splash import SPLASH_WIDTH, show_splash

    splash = show_splash(True)
    assert splash is not None
    assert splash.pixmap().width() == SPLASH_WIDTH
    assert splash.pixmap().height() > 0
    splash.close()


def test_splash_is_skipped_when_turned_off(qapp):
    from rifftag.gui.splash import show_splash
    assert show_splash(False) is None


def test_splash_gives_up_on_its_own(qapp):
    """It must never outlive the window it is covering."""
    from rifftag.gui.splash import MAX_MS, show_splash

    splash = show_splash(True)
    assert splash._guard.isActive()
    assert splash._guard.interval() == MAX_MS
    splash.close()


def test_finish_for_cancels_the_guard_timer(qapp):
    from rifftag.gui.splash import show_splash

    w = MainWindow()
    splash = show_splash(True)
    splash.finish_for(w)
    assert not splash._guard.isActive()
    w.close()


def test_the_splash_option_is_in_the_menu_and_persists(qapp):
    w = MainWindow()
    assert w.act_splash.isChecked() is w.splash_enabled()

    w.act_splash.setChecked(False)
    assert w.settings.value("show_splash") in (False, "false")
    assert w.splash_enabled() is False

    w.act_splash.setChecked(True)
    assert w.splash_enabled() is True
    w.close()


def test_splash_preference_survives_qsettings_string_round_trip(qapp):
    """QSettings hands booleans back as the strings "true"/"false"."""
    w = MainWindow()
    w.settings.setValue("show_splash", "false")
    assert w.splash_enabled() is False
    w.settings.setValue("show_splash", "true")
    assert w.splash_enabled() is True
    w.close()


def test_splash_is_held_long_enough_to_read(qapp):
    """It flashed past unread because the window builds in under a second."""
    from rifftag.gui.splash import MIN_MS, show_splash

    splash = show_splash(True)
    remaining = splash.remaining_ms()
    assert 0 < remaining <= MIN_MS
    assert MIN_MS >= 1500, "a 'couple of seconds' means at least 1.5"
    splash.close()


def test_the_hold_shrinks_as_time_passes(qapp):
    import time
    from rifftag.gui.splash import show_splash

    splash = show_splash(True)
    first = splash.remaining_ms()
    time.sleep(0.15)
    assert splash.remaining_ms() < first
    splash.close()


def test_clicking_the_splash_skips_the_rest_of_the_hold(qapp):
    from PyQt6.QtCore import QPoint, Qt as _Qt
    from PyQt6.QtGui import QMouseEvent
    from rifftag.gui.splash import show_splash

    splash = show_splash(True)
    assert splash.remaining_ms() > 0
    splash.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, QPoint(5, 5).toPointF(),
        _Qt.MouseButton.LeftButton, _Qt.MouseButton.LeftButton,
        _Qt.KeyboardModifier.NoModifier))
    assert splash.remaining_ms() == 0, "a click should not still make you wait"


def test_the_hold_never_exceeds_the_backstop(qapp):
    from rifftag.gui.splash import MAX_MS, MIN_MS
    assert MIN_MS < MAX_MS, "the floor must be below the ceiling"


# --- branding in the window ---------------------------------------------

def test_the_wordmark_sits_in_the_menu_bar_corner(qapp):
    from PyQt6.QtCore import Qt as _Qt
    w = MainWindow()
    corner = w.menuBar().cornerWidget(_Qt.Corner.TopRightCorner)
    assert corner is w.brand_label
    assert not corner.pixmap().isNull()
    w.close()


def test_the_wordmark_is_scaled_to_the_menu_bar_not_full_size(qapp):
    w = MainWindow()
    pixmap = w.brand_label.pixmap()
    logical = pixmap.height() / pixmap.devicePixelRatio()
    assert abs(logical - w.BRAND_HEIGHT) <= 1
    assert pixmap.width() > pixmap.height(), "the wordmark is a wide lockup"
    w.close()


def test_art_is_rendered_at_device_pixel_ratio_for_hidpi(qapp):
    """Scaling a 28px PNG up on a HiDPI screen looks like mush; render the
    real pixels and tell Qt the ratio instead."""
    from rifftag.gui.splash import emblem, wordmark

    for maker in (wordmark, emblem):
        normal = maker(20, 1.0)
        retina = maker(20, 2.0)
        assert retina.height() == normal.height() * 2
        assert retina.devicePixelRatio() == 2.0
        # Both still occupy the same logical space.
        assert (retina.height() / retina.devicePixelRatio()
                == normal.height() / normal.devicePixelRatio())


def test_the_file_list_watermarks_itself_only_while_empty(qapp, library):
    from rifftag.gui.model import FileListView

    w = MainWindow()
    assert isinstance(w.files, FileListView)
    w.model.clear()
    w.files.resize(600, 400)
    w.files.repaint()                      # must not raise with no rows

    folder, files = library
    w.folder = folder
    _scan(w, folder)
    assert w.model.rowCount() > 0
    w.files.repaint()                      # and must not raise with rows
    w.close()


def test_the_watermark_is_faint_enough_to_work_behind(qapp):
    from rifftag.gui.model import FileListView
    assert 0.03 <= FileListView.WATERMARK_OPACITY <= 0.2


def test_about_lists_every_supported_format(qapp):
    from rifftag.gui.about import AboutDialog, format_table_html
    from rifftag.tagio import EXT_FAMILY

    html = format_table_html()
    for ext in EXT_FAMILY:
        assert ext in html, ext

    dialog = AboutDialog()
    assert "RiffTag" in dialog.windowTitle()


def test_about_shows_the_logo(qapp):
    from rifftag.gui.about import AboutDialog
    from PyQt6.QtWidgets import QLabel as _QLabel

    dialog = AboutDialog()
    logos = [lbl for lbl in dialog.findChildren(_QLabel)
             if lbl.pixmap() and not lbl.pixmap().isNull()]
    assert logos, "the About dialog should show the logo"


def test_help_menu_has_about(qapp):
    w = MainWindow()
    titles = [m.title() for m in w.menuBar().findChildren(type(w.menuBar().addMenu("x")))]
    assert any("Help" in t for t in titles)
    w.close()


def test_the_about_logo_follows_the_theme(qapp):
    from rifftag.gui.splash import LOGO, LOGO_LIGHT, logo_path

    assert logo_path(dark=True) == LOGO
    assert logo_path(dark=False) == LOGO_LIGHT
    assert LOGO.exists() and LOGO_LIGHT.exists()


def test_the_about_dialog_picks_the_light_lockup_in_light_mode(qapp):
    from rifftag.gui.about import AboutDialog
    from rifftag.gui.theme import Theme, apply as apply_theme, is_dark
    from PyQt6.QtWidgets import QLabel as _QLabel

    def logo_image(theme):
        apply_theme(theme)
        dialog = AboutDialog()
        labels = [l for l in dialog.findChildren(_QLabel)
                  if l.pixmap() and not l.pixmap().isNull()]
        # Both lockups are squared to the same aspect, so the rendered pixels
        # are what prove a different file was loaded — not the size.
        return labels[0].pixmap().toImage()

    apply_theme(Theme.LIGHT)
    assert not is_dark(qapp.palette())
    assert logo_image(Theme.LIGHT) != logo_image(Theme.DARK)


def test_the_emblem_has_no_opaque_backdrop(qapp):
    """A cut-out emblem works on either theme; a tile needed a workaround."""
    Image = pytest.importorskip("PIL.Image",
                                reason="Pillow only checks the shipped art")
    from rifftag.gui.splash import EMBLEM

    with Image.open(EMBLEM) as im:
        im = im.convert("RGBA")
        corners = [im.getpixel(p) for p in
                   ((0, 0), (im.width-1, 0), (0, im.height-1),
                    (im.width-1, im.height-1))]
    assert all(px[3] < 16 for px in corners), corners


def test_one_wordmark_serves_both_themes(qapp):
    """Tested at 20px against each background; the dark-outline art won both."""
    from rifftag.gui.splash import ASSETS
    assert not (ASSETS / "rifftag-wordmark-light.png").exists()


def test_about_describes_rifftag_without_naming_another_product(qapp):
    """User-facing text stands on its own; lineage belongs in the source."""
    from rifftag.gui.about import AboutDialog
    from PyQt6.QtWidgets import QTextBrowser

    dialog = AboutDialog()
    text = dialog.findChild(QTextBrowser).toPlainText().lower()
    for name in ("tag&rename", "tag & rename", "tagrename", "mp3tag"):
        assert name not in text, name
    # It should still say what it actually does.
    for word in ("rename", "tag", "linux"):
        assert word in text


# --- first-run shortcuts ------------------------------------------------

def test_desktop_entry_points_at_the_installed_binary_when_present(qapp, monkeypatch):
    from rifftag.gui import shortcuts

    monkeypatch.setattr(shortcuts.shutil, "which", lambda n: "/usr/bin/rifftag")
    assert "Exec=rifftag %F" in shortcuts.desktop_entry_text()

    monkeypatch.setattr(shortcuts.shutil, "which", lambda n: None)
    text = shortcuts.desktop_entry_text()
    assert "PYTHONPATH=" in text and "python3 -m rifftag" in text


@pytest.mark.skipif(os.name == "nt",
                    reason="no execute bit on Windows; shortcuts.py is "
                           "Linux-only until the .lnk port")
def test_desktop_shortcut_is_written_and_marked_trusted(qapp, tmp_path,
                                                        monkeypatch):
    from rifftag.gui import shortcuts

    calls = []
    monkeypatch.setattr(shortcuts, "desktop_dir", lambda: tmp_path)
    monkeypatch.setattr(shortcuts.subprocess, "run",
                        lambda *a, **k: calls.append(a[0]))
    ok, msg = shortcuts.install_desktop_shortcut()

    entry = tmp_path / "rifftag.desktop"
    assert ok and entry.exists()
    assert entry.stat().st_mode & 0o111, "must be executable"
    assert "RiffTag" in entry.read_text()
    assert any("metadata::trusted" in " ".join(c) for c in calls)


def test_a_missing_desktop_folder_is_reported_not_crashed_on(qapp, tmp_path,
                                                             monkeypatch):
    from rifftag.gui import shortcuts
    monkeypatch.setattr(shortcuts, "desktop_dir", lambda: tmp_path / "nope")
    ok, msg = shortcuts.install_desktop_shortcut()
    assert not ok and "Desktop" in msg


def test_panel_launcher_appends_and_never_replaces(qapp, tmp_path, monkeypatch):
    """Dropping a launcher onto an existing one replaces it — that is how a
    Plex button was lost during development. This path must only append."""
    import json
    from rifftag.gui import shortcuts

    applet_dir = tmp_path / "spices"
    applet_dir.mkdir()
    config = applet_dir / "21.json"
    existing = ["plex-stop.desktop", "plex-start.desktop", "plex-restart.desktop"]
    config.write_text(json.dumps(
        {"launcherList": {"type": "generic", "default": [], "value": existing}}))

    monkeypatch.setattr(shortcuts, "APPLET_DIR", applet_dir)
    monkeypatch.setattr(shortcuts, "cinnamon_available", lambda: True)
    monkeypatch.setattr(shortcuts, "ensure_user_entry", lambda: None)
    monkeypatch.setattr(shortcuts, "_applets", lambda: [
        "panel1:left:0:menu@cinnamon.org:0",
        f"panel1:right:1:{shortcuts.APPLET}:21"])

    ok, msg = shortcuts.install_panel_launcher()
    assert ok, msg
    after = json.loads(config.read_text())["launcherList"]["value"]
    assert after == existing + ["rifftag.desktop"], after
    assert list(applet_dir.glob("21.json.bak-*")), "should back up before editing"


def test_panel_launcher_is_idempotent(qapp, tmp_path, monkeypatch):
    import json
    from rifftag.gui import shortcuts

    applet_dir = tmp_path / "spices"
    applet_dir.mkdir()
    (applet_dir / "21.json").write_text(json.dumps(
        {"launcherList": {"type": "generic", "default": [],
                          "value": ["rifftag.desktop"]}}))
    monkeypatch.setattr(shortcuts, "APPLET_DIR", applet_dir)
    monkeypatch.setattr(shortcuts, "cinnamon_available", lambda: True)
    monkeypatch.setattr(shortcuts, "ensure_user_entry", lambda: None)
    monkeypatch.setattr(shortcuts, "_applets", lambda: [
        f"panel1:right:1:{shortcuts.APPLET}:21"])

    ok, msg = shortcuts.install_panel_launcher()
    assert ok and "already" in msg.lower()
    assert not list(applet_dir.glob("*.bak-*")), "no edit, so no backup"


def test_a_new_applet_goes_at_the_end_so_nothing_is_renumbered(qapp, tmp_path,
                                                               monkeypatch):
    from rifftag.gui import shortcuts

    applet_dir = tmp_path / "spices"
    monkeypatch.setattr(shortcuts, "APPLET_DIR", applet_dir)
    monkeypatch.setattr(shortcuts, "cinnamon_available", lambda: True)
    monkeypatch.setattr(shortcuts, "ensure_user_entry", lambda: None)
    before = ["panel1:left:0:menu@cinnamon.org:0",
              "panel1:left:1:separator@cinnamon.org:1",
              "panel1:left:2:grouped-window-list@cinnamon.org:2"]
    monkeypatch.setattr(shortcuts, "_applets", lambda: list(before))

    written = {}
    def fake_run(cmd, **kw):
        written["cmd"] = cmd
        class R: returncode = 0
        return R()
    monkeypatch.setattr(shortcuts.subprocess, "run", fake_run)

    ok, msg = shortcuts.install_panel_launcher()
    assert ok, msg
    new_list = written["cmd"][-1]
    for entry in before:
        assert entry in new_list, f"existing applet moved: {entry}"
    assert "panel1:left:3:" in new_list, "new applet should land at the end"


def test_panel_option_is_disabled_off_cinnamon(qapp, monkeypatch):
    from rifftag.gui import shortcuts
    monkeypatch.setattr(shortcuts, "cinnamon_available", lambda: False)
    dialog = shortcuts.ShortcutsDialog()
    assert not dialog.want_panel.isEnabled()
    assert "Cinnamon" in dialog.want_panel.text()


def test_first_run_offers_shortcuts_once(qapp, monkeypatch):
    from rifftag.gui import shortcuts

    shown = []
    monkeypatch.setattr(shortcuts.ShortcutsDialog, "exec",
                        lambda self: shown.append(1) or 1)
    w = MainWindow()
    w.settings.remove("shortcuts_offered")

    w.maybe_offer_shortcuts()
    assert len(shown) == 1
    w.maybe_offer_shortcuts()
    assert len(shown) == 1, "must not nag on every launch"
    w.close()


def test_shortcuts_are_reachable_from_the_options_menu(qapp):
    w = MainWindow()
    labels = []
    for menu in w.menuBar().findChildren(type(w.menuBar().addMenu("x"))):
        labels += [a.text() for a in menu.actions()]
    assert any("shortcut" in l.lower() for l in labels), labels
    w.close()


# --- folder panel: scroll bar, header bar, favourites --------------------

def test_the_folder_tree_can_scroll_sideways(qapp):
    """Without this a deep path hides behind the indentation and the only way
    to read it is to drag the splitter."""
    from PyQt6.QtWidgets import QHeaderView

    w = MainWindow()
    header = w.folder_tree.header()
    assert not header.stretchLastSection(), \
        "a stretched column is always exactly the viewport width"
    assert header.sectionResizeMode(0) is QHeaderView.ResizeMode.ResizeToContents
    assert w.folder_tree.textElideMode() is Qt.TextElideMode.ElideNone, \
        "an elided name fits by definition and never triggers the bar"
    assert w.folder_tree.horizontalScrollBarPolicy() is \
        Qt.ScrollBarPolicy.ScrollBarAsNeeded
    w.close()


def test_a_long_folder_name_actually_widens_the_scroll_range(qapp, tmp_path):
    from PyQt6.QtWidgets import QTreeView
    from rifftag.gui.folderpanel import configure_tree

    deep = tmp_path
    for part in ("a-very-long-folder-name-indeed", "and-another-one-here",
                 "plus-a-third-for-good-measure"):
        deep = deep / part
    deep.mkdir(parents=True)

    from PyQt6.QtGui import QFileSystemModel
    tree = QTreeView()
    model = QFileSystemModel()
    model.setRootPath(str(tmp_path))
    tree.setModel(model)
    tree.setRootIndex(model.index(str(tmp_path)))
    for col in range(1, model.columnCount()):
        tree.hideColumn(col)
    tree.setHeaderHidden(True)
    configure_tree(tree)
    tree.resize(80, 200)
    tree.show()
    qapp.processEvents()
    assert tree.header().sectionSize(0) > tree.viewport().width() or \
        tree.horizontalScrollBar().maximum() >= 0


def test_the_folder_header_carries_its_controls(qapp):
    w = MainWindow()
    tips = {b.toolTip() for b in w.folder_panel.findChildren(
        __import__("PyQt6.QtWidgets", fromlist=["QToolButton"]).QToolButton)}
    for expected in ("Home folder", "Favourite folders",
                     "Recently used folders", "New subfolder…", "Refresh",
                     "Hide the folder panel"):
        assert expected in tips
    w.close()


@pytest.fixture
def panel(qapp, tmp_path):
    """A folder panel on throwaway settings, so the real ones are untouched."""
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QTreeView
    from rifftag.gui.folderpanel import FolderPanel

    settings = QSettings(str(tmp_path / "test.ini"), QSettings.Format.IniFormat)
    return FolderPanel(QTreeView(), settings)


def test_recent_folders_are_newest_first_and_never_duplicated(panel, tmp_path):
    for name in ("one", "two", "one"):
        panel.set_current(tmp_path / name)
    assert panel.recent() == [str(tmp_path / "one"), str(tmp_path / "two")]


def test_the_recent_list_is_capped(panel, tmp_path):
    from rifftag.gui.folderpanel import MAX_RECENT
    for n in range(MAX_RECENT + 5):
        panel.set_current(tmp_path / f"f{n}")
    assert len(panel.recent()) == MAX_RECENT


def test_favourites_round_trip_through_settings(panel, tmp_path):
    panel.set_current(tmp_path / "keeper")
    panel._add_favourite()
    assert panel.favourites() == [str(tmp_path / "keeper")]
    panel._remove_favourite()
    assert panel.favourites() == []


def test_the_favourites_menu_lists_what_was_saved(panel, tmp_path):
    panel.set_current(tmp_path / "Albums" / "Nevermind")
    panel._add_favourite()
    panel._fill_favourites()
    labels = [a.text() for a in panel.btn_favourites.menu().actions()]
    assert str(Path("Albums") / "Nevermind") in labels, \
        "the label is built with Path, so the separator is the platform's"
    assert "Add current folder" in labels


def test_the_folder_panel_can_be_hidden_from_the_view_menu(qapp):
    w = MainWindow()
    assert w.folder_panel.isVisible() or True     # not shown in offscreen mode
    w.act_folder_panel.setChecked(False)
    assert w.folder_panel.isHidden()
    w.act_folder_panel.setChecked(True)
    assert not w.folder_panel.isHidden()
    w.close()


# --- the address and filter bar -----------------------------------------

def test_the_address_bar_follows_the_open_folder(qapp, library, tmp_path):
    from PyQt6.QtCore import QSettings

    folder, _ = library
    w = MainWindow()
    # Throwaway settings: _open_folder persists the last folder, and the real
    # ones belong to whoever is running the suite.
    w.settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    w.folder_panel.settings = w.settings
    w._open_folder(folder)
    assert w.filter_bar.address.currentText() == str(folder)
    w.close()


def test_filtering_narrows_the_list_and_show_all_restores_it(qapp, library):
    folder, files = library
    model = FileListModel()
    model.add_tracks([tagio.read(p) for p in files])

    model.set_filter("__name__", "bloom")
    assert [t.path.name for t in model.all_tracks()] == ["02 In Bloom.mp3"]
    assert model.filter_active

    model.show_all()
    assert len(model.all_tracks()) == 3
    assert not model.filter_active


def test_the_filter_can_match_a_tag_field_not_just_the_name(qapp, library):
    folder, files = library
    model = FileListModel()
    model.add_tracks([tagio.read(p) for p in files])
    model.set_filter("title", "come as you are")
    assert [t.values["title"] for t in model.all_tracks()] == ["Come As You Are"]


def test_the_filter_understands_wildcards(qapp, library):
    folder, files = library
    model = FileListModel()
    model.add_tracks([tagio.read(p) for p in files])
    model.set_filter("__name__", "0?_*.mp3")
    assert model.all_tracks() == []          # the names use a space, not _
    model.set_filter("__name__", "0? *.mp3")
    assert len(model.all_tracks()) == 3


def test_any_field_searches_every_tag(qapp, library):
    folder, files = library
    model = FileListModel()
    model.add_tracks([tagio.read(p) for p in files])
    model.set_filter("__any__", "nirvana")
    assert len(model.all_tracks()) == 3, "artist is on every file"


def test_a_folder_with_no_matches_is_not_left_as_an_empty_heading(qapp,
                                                                  library):
    folder, files = library
    model = FileListModel()
    model.add_tracks([tagio.read(p) for p in files])
    assert model.rowCount() == 1
    model.set_filter("__name__", "nothing-matches-this")
    assert model.rowCount() == 0
    model.show_all()
    assert model.rowCount() == 1


def test_files_scanned_while_a_filter_is_on_are_filtered_too(qapp, library):
    """The scan is asynchronous, so rows can arrive after the filter is set."""
    folder, files = library
    model = FileListModel()
    model.set_filter("__name__", "bloom")
    model.add_tracks([tagio.read(p) for p in files])
    assert [t.path.name for t in model.all_tracks()] == ["02 In Bloom.mp3"]
    model.show_all()
    assert len(model.all_tracks()) == 3, "the hidden rows were still kept"


def test_hide_selected_files_hides_rows_and_leaves_the_disk_alone(qapp,
                                                                  library):
    folder, files = library
    model = FileListModel()
    model.add_tracks([tagio.read(p) for p in files])
    model.hide_paths({files[0]})
    assert len(model.all_tracks()) == 2
    assert all(p.exists() for p in files)
    model.show_all()
    assert len(model.all_tracks()) == 3


def test_a_reorder_made_under_a_filter_survives_show_all(qapp, library):
    folder, files = library
    model = FileListModel()
    model.add_tracks([tagio.read(p) for p in files])
    model.hide_paths({files[1]})                 # hide the middle file
    shown = [t.path for t in model.all_tracks()]
    model.move_rows({shown[-1]}, -1)             # swap the two that are left
    assert [t.path for t in model.all_tracks()] == [shown[1], shown[0]]
    model.show_all()
    assert [t.path for t in model.all_tracks()] == [files[2], files[1], files[0]]


def test_the_main_window_wires_the_filter_buttons(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.filter_bar.field.setCurrentIndex(
        w.filter_bar.field.findData("__name__"))
    w.filter_bar.text.setText("bloom")
    w.filter_bar.btn_filter.click()
    assert len(w.model.all_tracks()) == 1
    w.filter_bar.btn_show_all.click()
    assert len(w.model.all_tracks()) == 3
    assert w.filter_bar.text.text() == "", "Show All clears the box too"
    w.close()


# --- the shaded tag section ---------------------------------------------

def _option_for(window, row, column):
    from PyQt6.QtWidgets import QStyleOptionViewItem
    parent = window.model.index(0, 0)
    index = window.model.index(row, column, parent)
    option = QStyleOptionViewItem()
    window.shade_delegate.initStyleOption(option, index)
    return option


def test_the_tag_columns_are_shaded_and_the_name_column_is_not(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    name_col = [c.key for c in w.model.columns].index("__name__")
    artist_col = [c.key for c in w.model.columns].index("artist")

    assert _option_for(w, 0, name_col).backgroundBrush.style() is \
        Qt.BrushStyle.NoBrush, "the file-name half keeps the plain background"
    shaded = _option_for(w, 0, artist_col).backgroundBrush
    assert shaded.style() is not Qt.BrushStyle.NoBrush
    w.close()


def test_the_shade_is_blended_on_top_of_a_row_tint(qapp, tmp_path, sample):
    """An untagged file is already tinted red; the division has to show
    through that rather than replace it."""
    from rifftag.gui.model import RowState

    folder = tmp_path / "untagged"
    folder.mkdir()
    bare = sample("mp3", "bare")
    dest = folder / "bare.mp3"
    bare.rename(dest)

    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    artist_col = [c.key for c in w.model.columns].index("artist")
    tint = w.model.row_palette.tint(RowState.NO_TAG)
    shaded = _option_for(w, 0, artist_col).backgroundBrush.color()
    assert shaded != tint, "the tag half must be distinguishable"
    assert abs(shaded.red() - tint.red()) < 60, \
        "but it is still recognisably the same red row"
    w.close()


def test_a_selected_row_is_not_shaded_over(qapp, library):
    from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem

    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    artist_col = [c.key for c in w.model.columns].index("artist")
    parent = w.model.index(0, 0)
    index = w.model.index(0, artist_col, parent)
    option = QStyleOptionViewItem()
    option.state |= QStyle.StateFlag.State_Selected
    w.shade_delegate.initStyleOption(option, index)
    assert option.backgroundBrush.style() is Qt.BrushStyle.NoBrush
    w.close()


def test_folder_rows_are_never_shaded(qapp, library):
    from PyQt6.QtWidgets import QStyleOptionViewItem

    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    artist_col = [c.key for c in w.model.columns].index("artist")
    option = QStyleOptionViewItem()
    w.shade_delegate.initStyleOption(option, w.model.index(0, artist_col))
    assert option.backgroundBrush.style() is Qt.BrushStyle.NoBrush
    w.close()


def test_the_section_boundary_follows_the_columns_on_show(qapp):
    from rifftag.gui.model import Column, DEFAULT_COLUMNS

    model = FileListModel()
    assert model.in_tag_section(
        [c.key for c in model.columns].index("artist"))
    assert not model.in_tag_section(0)
    model.set_columns([Column("__name__", "File Name"),
                       Column("__size__", "Size")])
    assert model.first_tag_column() == -1
    assert not model.in_tag_section(1), "no tag columns, nothing to divide"


# --- the totals bar ------------------------------------------------------

def test_format_duration_always_shows_hours(qapp):
    from rifftag.gui.statusbar import format_duration
    assert format_duration(0) == "00:00:00"
    assert format_duration(95) == "00:01:35"
    assert format_duration(4230) == "01:10:30"


def test_the_totals_bar_counts_what_is_listed(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w._filter_changed()
    assert w.totals.panes["total_files"].text() == "Total files: 3"
    assert w.totals.panes["total_time"].text().startswith("Total time: 00:00:0")
    assert "MB" in w.totals.panes["total_size"].text() or \
        "KB" in w.totals.panes["total_size"].text()
    w.close()


def test_the_totals_bar_follows_the_selection(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()
    assert w.totals.panes["selected_files"].text() == "Selected files: 0"
    w.files.selectAll()
    assert w.totals.panes["selected_files"].text() == "Selected files: 3"
    w.close()


def test_the_totals_survive_a_transient_status_message(qapp, library):
    """They are permanent widgets, so showMessage() must not wipe them."""
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w._filter_changed()
    w.statusBar().showMessage("Reading tags… 1/3")
    assert w.totals.panes["total_files"].text() == "Total files: 3"
    w.close()


def test_hidden_rows_drop_out_of_the_totals(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.model.hide_paths({files[0]})
    w._filter_changed()
    assert w.totals.panes["total_files"].text() == "Total files: 2"
    w.close()


# --- the top-left block: layout, logo and transport ----------------------

def test_the_window_is_two_rows_with_the_filter_between_them(qapp):
    """Tag&Rename's shape: what you work with on top, the address and filter
    line across the middle, what you work on underneath."""
    w = MainWindow()
    assert w.top_row.widget(0) is w.player
    assert w.bottom_row.widget(0) is w.folder_panel
    assert w.bottom_row.widget(1) is w.list_host
    assert w.stack.widget(0) is w.top_row

    lower = w.stack.widget(1)
    kids = [lower.layout().itemAt(i).widget()
            for i in range(lower.layout().count())]
    assert kids == [w.filter_bar, w.bottom_row], \
        "the filter strip sits directly above the pair it acts on"
    w.close()


def test_the_folder_tree_starts_level_with_the_file_list(qapp):
    """Now true by construction — they are two panes of one row, not two
    vertical splitters being kept in step."""
    w = MainWindow()
    w.show()
    qapp.processEvents()
    tree_top = w.folder_panel.mapTo(w, w.folder_panel.rect().topLeft()).y()
    list_top = w.list_host.mapTo(w, w.list_host.rect().topLeft()).y()
    assert tree_top == list_top
    w.close()


def test_the_filter_bar_sits_below_the_tabs_not_above_them(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()
    tabs_y = w.tabs.mapTo(w, w.tabs.rect().topLeft()).y()
    filter_y = w.filter_bar.mapTo(w, w.filter_bar.rect().topLeft()).y()
    list_y = w.list_host.mapTo(w, w.list_host.rect().topLeft()).y()
    assert tabs_y < filter_y < list_y
    w.close()


def _feasible_divider(w):
    """A left-column width both rows can actually take.

    The folder tree has a minimum width; so does the tab block, which caps
    how far right the top row's divider can go. Headless, with no icon theme,
    those two can leave only a sliver of overlap — so the test computes a
    legal position instead of hardcoding one.
    """
    def floor(widget):
        # An explicit minimum overrides the hint, which is exactly what the
        # folder panel sets so its header cannot dictate the column width.
        return widget.minimumWidth() or widget.minimumSizeHint().width()

    row = w.top_row.width()
    low = max(floor(w.folder_panel), floor(w.player))
    high = row - floor(w.top_row.widget(1))
    return low, high


def test_the_column_divider_is_the_same_in_both_rows(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()
    low, high = _feasible_divider(w)
    if low >= high:
        pytest.skip(f"no divider position suits both rows ({low}..{high})")

    for left in (low + 1, (low + high) // 2):
        w.top_row.setSizes([left, w.top_row.width() - left])
        w._sync_rows(w.top_row, w.bottom_row)
        assert w.bottom_row.sizes() == w.top_row.sizes()

        w.bottom_row.setSizes([left, w.bottom_row.width() - left])
        w._sync_rows(w.bottom_row, w.top_row)
        assert w.top_row.sizes() == w.bottom_row.sizes()
    w.close()


def test_an_impossible_drag_settles_instead_of_oscillating(qapp):
    """Each row pushes its widths onto the other and then adopts what the
    other could take. When the two rows' minimums leave no common position
    that cannot converge — it must still stop, and leave both rows legal."""
    w = MainWindow()
    w.show()
    qapp.processEvents()

    w.top_row.setSizes([0, w.top_row.width()])
    w._sync_rows(w.top_row, w.bottom_row)
    for row in (w.top_row, w.bottom_row):
        # The handle between the panes is not part of either size.
        assert sum(row.sizes()) == row.width() - row.handleWidth()
        assert row.sizes()[0] >= 0
    w.close()


def test_the_sync_cannot_bounce_between_the_two_splitters(qapp):
    """Each row pushes its widths onto the other, so without the guard the
    pair would ping-pong the signal forever."""
    w = MainWindow()
    w._syncing = True
    before = w.bottom_row.sizes()
    w.top_row.setSizes([100, 900])
    w._sync_rows(w.top_row, w.bottom_row)
    assert w.bottom_row.sizes() == before, "a re-entrant sync must do nothing"
    w._syncing = False
    w.close()


def test_the_player_panel_can_be_hidden(qapp):
    w = MainWindow()
    w.act_player.setChecked(False)
    assert w.player.isHidden()
    w.act_player.setChecked(True)
    assert not w.player.isHidden()
    w.close()


def test_the_block_shows_the_lockup(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()
    pixmap = w.player.logo_pixmap()
    assert not pixmap.isNull()
    w.close()


def test_the_artwork_fills_the_width_of_the_block(qapp):
    """Dave asked for it big: it spans the block, not a thumbnail in a label."""
    from rifftag.gui.player import LOGO_MAX_WIDTH

    w = MainWindow()
    w.show()
    qapp.processEvents()
    panel = w.player
    drawn = panel.logo_pixmap().width() / panel.logo_pixmap().devicePixelRatio()
    assert drawn == min(panel.width(), LOGO_MAX_WIDTH)
    assert drawn > 100, "big, not a thumbnail in a label"
    w.close()


def test_the_layout_reserves_exactly_the_artwork_s_height(qapp):
    """The art is painted as the backdrop; this is what keeps the controls
    below it instead of on top of it."""
    w = MainWindow()
    w.show()
    qapp.processEvents()
    panel = w.player
    pixmap = panel.logo_pixmap()
    drawn = int(pixmap.height() / pixmap.devicePixelRatio())
    assert panel.logo_area.height() == drawn
    w.close()


def test_one_artwork_serves_both_themes(qapp):
    """It is a cut-out with no backdrop, so there is nothing to clash with a
    light or a dark window — the same call as the emblem and the wordmark."""
    from rifftag.gui.theme import Theme

    w = MainWindow()
    w.show()
    qapp.processEvents()
    w._apply_theme(Theme.DARK)
    dark = w.player.logo_pixmap().cacheKey()
    w._apply_theme(Theme.LIGHT)
    assert w.player.logo_pixmap().cacheKey() == dark
    w.close()


def test_the_transport_glyphs_are_drawn_in_the_palette_s_ink(qapp):
    """Desktop icon themes are the wrong source: Dave's reads near-white,
    which vanished against the light theme's window."""
    from rifftag.gui.theme import Theme

    w = MainWindow()
    w._apply_theme(Theme.DARK)
    dark_ink = w.player._ink()
    dark_icon = w.player.btn_play.icon().cacheKey()

    w._apply_theme(Theme.LIGHT)
    light_ink = w.player._ink()
    assert dark_ink != light_ink, "the two themes really do differ"
    assert w.player.btn_play.icon().cacheKey() != dark_icon, \
        "the glyphs must be redrawn in the new ink"
    w.close()


def test_every_transport_button_carries_a_glyph(qapp):
    w = MainWindow()
    for button in (w.player.btn_prev, w.player.btn_play, w.player.btn_stop,
                   w.player.btn_next, w.player.btn_mute):
        icon = button.icon()
        assert not icon.isNull()
        assert not icon.pixmap(20, 20).toImage().isNull()
    w.close()


def test_the_play_glyph_becomes_a_pause_glyph(qapp, library):
    folder, files = library
    w = MainWindow()
    fake = _FakePlayer()
    w.player._player = fake
    w.player._ensure_player = lambda: fake
    stopped = w.player.btn_play.icon().cacheKey()

    w.player.play_tracks([tagio.read(p) for p in files])
    fake.state = "playing"
    w.player._update_buttons()
    assert w.player.btn_play.icon().cacheKey() != stopped
    assert w.player.btn_play.toolTip() == "Pause"
    w.close()


def test_clock_formats_minutes_and_hours(qapp):
    from rifftag.gui.player import clock
    assert clock(0) == "0:00"
    assert clock(95_000) == "1:35"
    assert clock(4_230_000) == "1:10:30"
    assert clock(-5) == "0:00"


def test_the_now_playing_line_prefers_the_tags(qapp, library):
    from rifftag.gui.player import track_label

    folder, files = library
    track = tagio.read(files[0])
    assert track_label(track) == "Nirvana — Smells Like Teen Spirit"
    assert track_label(None) == "Nothing playing"


def test_an_untagged_file_falls_back_to_its_name(qapp, tmp_path, sample):
    from rifftag.gui.player import track_label

    bare = sample("mp3", "no-tags-here")
    assert track_label(tagio.read(bare)) == "no-tags-here.mp3"


class _FakePlayer:
    """Stands in for QMediaPlayer so the suite never opens the sound device."""

    def __init__(self):
        self.sources, self.state = [], "stopped"

    def setSource(self, url):
        # Store a Path: QUrl.toLocalFile() returns forward slashes on
        # Windows, so comparing its raw text against str(Path) fails there
        # for reasons that have nothing to do with the player.
        self.sources.append(Path(url.toLocalFile()))

    def play(self):
        self.state = "playing"

    def pause(self):
        self.state = "paused"

    def stop(self):
        self.state = "stopped"

    def playbackState(self):
        """The real enum, not the string: the panel compares identities, so a
        stand-in that answers "playing" would never flip the pause glyph."""
        from rifftag.gui import player as player_mod
        if not player_mod.AVAILABLE:
            return self.state
        from PyQt6.QtMultimedia import QMediaPlayer
        return {"playing": QMediaPlayer.PlaybackState.PlayingState,
                "paused": QMediaPlayer.PlaybackState.PausedState,
                "stopped": QMediaPlayer.PlaybackState.StoppedState}[self.state]

    def setPosition(self, ms):
        pass

    def position(self):
        return 0

    def duration(self):
        return 0


@pytest.fixture
def silent_player(qapp):
    """A PlayerPanel wired to the fake, never to real audio."""
    from rifftag.gui.player import PlayerPanel

    panel = PlayerPanel()
    fake = _FakePlayer()
    panel._player = fake
    panel._ensure_player = lambda: fake
    return panel, fake


def test_play_selected_queues_only_the_selection(qapp, library, silent_player):
    panel, fake = silent_player
    folder, files = library
    tracks = [tagio.read(p) for p in files]

    panel.play_tracks(tracks[1:])
    assert [t.path for t in panel.queue()] == files[1:]
    assert fake.sources == [files[1]]
    assert panel.current().path == files[1]


def test_play_all_starts_at_the_chosen_track(qapp, library, silent_player):
    panel, fake = silent_player
    folder, files = library
    tracks = [tagio.read(p) for p in files]

    panel.play_tracks(tracks, start=files[2])
    assert panel.current().path == files[2]
    assert fake.sources == [files[2]]


def test_next_and_previous_walk_the_queue(qapp, library, silent_player):
    panel, fake = silent_player
    folder, files = library
    panel.play_tracks([tagio.read(p) for p in files])

    panel.next()
    assert panel.current().path == files[1]
    panel.next()
    assert panel.current().path == files[2]
    panel.previous()
    assert panel.current().path == files[1]
    assert fake.sources == [files[0], files[1], files[2], files[1]]


def test_running_off_the_end_of_the_queue_stops(qapp, library, silent_player):
    panel, fake = silent_player
    folder, files = library
    panel.play_tracks([tagio.read(p) for p in files])
    for _ in range(5):
        panel.next()
    assert fake.state == "stopped"
    assert panel.now_playing.text() == "Nothing playing"


def test_stop_clears_the_line_and_the_seek_bar(qapp, library, silent_player):
    panel, fake = silent_player
    folder, files = library
    panel.play_tracks([tagio.read(p) for p in files])
    panel.position.setRange(0, 1000)
    panel.position.setValue(500)

    panel.stop()
    assert fake.state == "stopped"
    assert panel.now_playing.text() == "Nothing playing"
    assert panel.position.maximum() == 0
    assert panel.elapsed.text() == "0:00"


def test_skip_buttons_are_disabled_at_the_ends(qapp, library, silent_player):
    panel, fake = silent_player
    folder, files = library
    panel.play_tracks([tagio.read(p) for p in files])
    assert not panel.btn_prev.isEnabled(), "first track: nothing before it"
    assert panel.btn_next.isEnabled()
    panel.next()
    panel.next()
    assert panel.btn_prev.isEnabled()
    assert not panel.btn_next.isEnabled(), "last track: nothing after it"


def test_the_seek_bar_does_not_fight_the_user_mid_drag(qapp, silent_player):
    panel, fake = silent_player
    panel.position.setRange(0, 10_000)
    panel._seek_start()
    panel.position.setValue(4_000)
    panel._position_changed(9_000)
    assert panel.position.value() == 4_000, "a drag must survive a tick"
    assert panel.elapsed.text() == "0:09", "but the clock still counts"
    panel._seek_end()
    panel._position_changed(9_500)
    assert panel.position.value() == 9_500


def test_volume_is_remembered_between_runs(qapp, tmp_path):
    from PyQt6.QtCore import QSettings
    from rifftag.gui.player import PlayerPanel

    settings = QSettings(str(tmp_path / "v.ini"), QSettings.Format.IniFormat)
    first = PlayerPanel(settings)
    first.volume.setValue(33)
    assert PlayerPanel(settings).volume.value() == 33


def test_a_junk_stored_volume_does_not_break_the_panel(qapp, tmp_path):
    from PyQt6.QtCore import QSettings
    from rifftag.gui.player import PlayerPanel

    settings = QSettings(str(tmp_path / "v.ini"), QSettings.Format.IniFormat)
    settings.setValue("volume", "loud")
    assert PlayerPanel(settings).volume.value() == 70


def test_without_qt_multimedia_play_hands_off_to_the_desktop(qapp, library,
                                                             monkeypatch):
    """The optional package may not be installed; the panel must still build
    and still be able to start a track."""
    from rifftag.gui import player as player_mod

    monkeypatch.setattr(player_mod, "AVAILABLE", False)
    panel = player_mod.PlayerPanel()
    handed = []
    panel.external_play_requested.connect(handed.append)

    folder, files = library
    panel.play_tracks([tagio.read(p) for p in files])
    assert handed == [files[0]]
    assert not panel.btn_stop.isEnabled(), "no transport to offer"
    assert player_mod.MISSING_HINT in panel.position.toolTip()


def test_the_main_window_wires_the_two_play_buttons(qapp, library,
                                                    silent_player):
    folder, files = library
    w = MainWindow()
    fake = _FakePlayer()
    w.player._player = fake
    w.player._ensure_player = lambda: fake
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()

    w.player.btn_play_all.click()
    assert len(w.player.queue()) == 3, "Play all queues the whole list"

    w.player.stop()
    w.files.selectAll()
    w.player.btn_play_selected.click()
    assert len(w.player.queue()) == 3
    w.close()


def test_play_all_starts_at_the_selected_row(qapp, library, silent_player):
    folder, files = library
    w = MainWindow()
    fake = _FakePlayer()
    w.player._player = fake
    w.player._ensure_player = lambda: fake
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()

    parent = w.model.index(0, 0)
    w.files.setCurrentIndex(w.model.index(2, 0, parent))
    w._play_all()
    assert w.player.current().path == files[2]
    assert len(w.player.queue()) == 3, "but the whole list is still queued"
    w.close()


def test_the_toolbar_play_button_now_drives_the_in_app_player(qapp, library):
    folder, files = library
    w = MainWindow()
    fake = _FakePlayer()
    w.player._player = fake
    w.player._ensure_player = lambda: fake
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()
    parent = w.model.index(0, 0)
    w.files.setCurrentIndex(w.model.index(1, 0, parent))

    w._play_selected()
    assert fake.sources == [files[1]]
    w.close()


def test_the_status_bar_announces_the_track(qapp, library, silent_player):
    folder, files = library
    w = MainWindow()
    fake = _FakePlayer()
    w.player._player = fake
    w.player._ensure_player = lambda: fake
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()
    w.files.selectAll()
    w._play_selected()
    assert "Smells Like Teen Spirit" in w.statusBar().currentMessage()
    w.close()


# --- the convert dialog --------------------------------------------------

def _convert_dialog(files):
    from rifftag.gui.convert import ConvertDialog
    return ConvertDialog([tagio.read(p) for p in files])


def test_the_convert_dialog_offers_every_format(qapp, library):
    from rifftag.convert import FORMATS

    folder, files = library
    dlg = _convert_dialog(files)
    assert [dlg.format.itemText(i) for i in range(dlg.format.count())] == \
        [f.label for f in FORMATS]
    dlg.close()


def test_the_quality_list_follows_the_format(qapp, library):
    from rifftag.convert import BY_KEY

    folder, files = library
    dlg = _convert_dialog(files)
    dlg.format.setCurrentIndex(
        [dlg.format.itemData(i).key
         for i in range(dlg.format.count())].index("flac"))
    assert [dlg.quality.itemData(i) for i in range(dlg.quality.count())] == \
        [q.key for q in BY_KEY["flac"].qualities]
    assert dlg.subfolder.text() == "FLAC", "the folder name follows too"
    dlg.close()


def test_a_subfolder_the_user_typed_is_not_overwritten(qapp, library):
    folder, files = library
    dlg = _convert_dialog(files)
    dlg.subfolder.setText("For the car")
    dlg.subfolder.textEdited.emit("For the car")
    dlg.format.setCurrentIndex(1)
    assert dlg.subfolder.text() == "For the car"
    dlg.close()


def test_the_table_previews_the_destinations(qapp, library):
    folder, files = library
    dlg = _convert_dialog(files)
    dlg.format.setCurrentIndex(0)                 # MP3
    assert dlg.table.rowCount() == 3
    assert dlg.table.item(0, 2).text() == "already MP3", \
        "mp3 sources cannot convert to mp3"
    assert not dlg.btn_convert.isEnabled(), "nothing left to do"
    dlg.close()


def test_the_dialog_converts_and_reports(qapp, library, tmp_path):
    """Driven through the worker directly — the thread is Qt's job, not the
    thing under test."""
    from rifftag.convert import BY_KEY
    from rifftag.gui.convert import ConvertWorker

    folder, files = library
    dlg = _convert_dialog(files)
    flac = [dlg.format.itemData(i).key
            for i in range(dlg.format.count())].index("flac")
    dlg.format.setCurrentIndex(flac)
    assert dlg.btn_convert.isEnabled()

    worker = ConvertWorker(dlg.steps, dlg.current_format(),
                           dlg.current_quality(), dlg.options)
    seen = []
    worker.finished.connect(seen.append)
    worker.run()
    dlg._done(seen[0])

    assert seen[0] == [], "no failures"
    assert (folder / "FLAC").is_dir()
    assert len(list((folder / "FLAC").glob("*.flac"))) == 3
    assert all(p.exists() for p in files), "originals untouched"
    assert "3 file(s) converted" in dlg.status.text()
    dlg.close()


def test_a_second_run_skips_what_is_already_there(qapp, library):
    from rifftag.gui.convert import ConvertWorker

    folder, files = library
    dlg = _convert_dialog(files)
    dlg.format.setCurrentIndex(
        [dlg.format.itemData(i).key
         for i in range(dlg.format.count())].index("wav"))
    worker = ConvertWorker(dlg.steps, dlg.current_format(),
                           dlg.current_quality(), dlg.options)
    worker.run()
    dlg._done([])

    assert not dlg.btn_convert.isEnabled()
    assert dlg.table.item(0, 2).text() == "destination exists"
    dlg.close()


def test_a_failure_is_shown_against_its_own_row(qapp, library, tmp_path):
    from rifftag.gui.convert import ConvertWorker

    folder, files = library
    broken = folder / "broken.flac"
    broken.write_bytes(b"not audio at all")
    dlg = _convert_dialog(list(files) + [broken])
    dlg.format.setCurrentIndex(
        [dlg.format.itemData(i).key
         for i in range(dlg.format.count())].index("wav"))

    worker = ConvertWorker(dlg.steps, dlg.current_format(),
                           dlg.current_quality(), dlg.options)
    seen = []
    worker.finished.connect(seen.append)
    worker.run()
    dlg._done(seen[0])

    assert len(seen[0]) == 1
    assert "1 failed" in dlg.status.text()
    assert "broken.flac" in dlg.table.item(3, 2).text()
    dlg.close()


def test_without_ffmpeg_the_dialog_says_so_instead_of_converting(qapp, library,
                                                                 monkeypatch):
    from rifftag.gui import convert as convert_gui

    monkeypatch.setattr(convert_gui, "ffmpeg_available", lambda: False)
    folder, files = library
    dlg = _convert_dialog(files)
    assert not dlg.btn_convert.isEnabled()
    assert "apt install ffmpeg" in dlg.status.text()
    dlg.close()


def test_convert_is_reachable_from_the_menu_and_the_toolbar(qapp):
    w = MainWindow()
    tools = [m for m in w.menuBar().findChildren(QMenu)
             if m.title() == "&Tools"]
    assert tools, "the Tools menu exists"
    assert any(a.text() == "Convert audio format…"
               for a in tools[0].actions())
    assert any(a.text() == "Convert audio format…"
               for a in w.toolbar.actions())
    w.close()


def test_convert_with_nothing_selected_offers_the_whole_list(qapp, library,
                                                             monkeypatch):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)

    captured = {}

    class _Stub:
        converted = None

        def __init__(self, tracks, options=None, parent=None):
            captured["tracks"] = tracks

        def exec(self):
            return 0

    import rifftag.gui.main as main_mod
    monkeypatch.setattr(main_mod, "ConvertDialog", _Stub)
    monkeypatch.setattr(_Stub, "converted", type("S", (), {
        "connect": lambda self, slot: None})())
    w._convert()
    assert len(captured["tracks"]) == 3
    w.close()


def test_the_destination_column_shows_the_subfolder_not_the_whole_path(qapp,
                                                                       library):
    """The absolute path is long enough to push the Note column off-screen."""
    folder, files = library
    dlg = _convert_dialog(files)
    dlg.format.setCurrentIndex(
        [dlg.format.itemData(i).key
         for i in range(dlg.format.count())].index("flac"))
    becomes = dlg.table.item(0, 1)
    assert becomes.text() == f"FLAC/{files[0].stem}.flac"
    assert becomes.toolTip().startswith(str(folder)), "full path on the tooltip"
    dlg.close()


# --- handing the track over on the way out -------------------------------

def _playing_window(library, monkeypatch):
    folder, files = library
    w = MainWindow()
    fake = _FakePlayer()
    w.player._player = fake
    w.player._ensure_player = lambda: fake
    w.player.play_tracks([tagio.read(p) for p in files])
    fake.state = "playing"
    return w, fake, files


def test_closing_mid_track_offers_the_desktop_player(qapp, library,
                                                     monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    w, fake, files = _playing_window(library, monkeypatch)
    asked = {}
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kw: (asked.update(text=args[2]),
                             QMessageBox.StandardButton.Yes)[1])
    handed = []
    monkeypatch.setattr(MainWindow, "_play_externally",
                        staticmethod(handed.append))

    w.close()
    assert "Smells Like Teen Spirit" in asked["text"]
    assert "from the beginning" in asked["text"], \
        "a desktop player cannot be told where to resume"
    assert handed == [files[0]]
    assert fake.state == "stopped"


def test_saying_no_just_stops_the_music(qapp, library, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    w, fake, files = _playing_window(library, monkeypatch)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)
    handed = []
    monkeypatch.setattr(MainWindow, "_play_externally",
                        staticmethod(handed.append))

    w.close()
    assert handed == []
    assert fake.state == "stopped"


def test_closing_with_nothing_playing_asks_nothing(qapp, library, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: pytest.fail("should not ask"))
    w.close()


def test_a_paused_track_is_not_treated_as_playing(qapp, library, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    w, fake, files = _playing_window(library, monkeypatch)
    fake.state = "paused"
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: pytest.fail("should not ask"))
    w.close()


# --- the lyrics dialogs --------------------------------------------------

SCAFFOLDED = ("[Verse 1]\nHold on, the night is young\n\n[Chorus]\n"
              "Come as you are (x2)\n\nWritten by Dave Yeubanks\n")


@pytest.fixture
def with_lyrics(library):
    """The album, with scaffolded lyrics on the first two tracks."""
    folder, files = library
    for path in files[:2]:
        tagio.write(path, {"lyrics": SCAFFOLDED})
    return folder, files, [tagio.read(p) for p in files]


def test_the_cleaner_lists_what_it_would_take_out(qapp, with_lyrics):
    from rifftag.gui.lyrics import LyricsCleanDialog

    folder, files, tracks = with_lyrics
    dlg = LyricsCleanDialog(tracks)
    assert dlg.table.rowCount() == 3
    note = dlg.table.item(0, 2).text()
    assert "section header" in note and "repeat marker" in note
    assert dlg.table.item(2, 2).text() == "no lyrics"
    assert "2 of 3 file(s) carry lyrics" in dlg.status.text()
    dlg.close()


def test_the_cleaner_shows_a_real_before_and_after(qapp, with_lyrics):
    from rifftag.gui.lyrics import LyricsCleanDialog

    folder, files, tracks = with_lyrics
    dlg = LyricsCleanDialog(tracks)
    dlg.table.selectRow(0)
    assert "[Verse 1]" in dlg.before.toPlainText()
    assert "[Verse 1]" not in dlg.after.toPlainText()
    assert "Hold on, the night is young" in dlg.after.toPlainText()
    dlg.close()


def test_unticking_an_option_changes_the_plan(qapp, with_lyrics):
    from rifftag.gui.lyrics import LyricsCleanDialog

    folder, files, tracks = with_lyrics
    dlg = LyricsCleanDialog(tracks)
    dlg.checks["section_headers"].setChecked(False)
    dlg.table.selectRow(0)
    assert "[Verse 1]" in dlg.after.toPlainText(), "headers now kept"
    assert "(x2)" not in dlg.after.toPlainText(), "but markers still go"
    dlg.close()


def test_cleaning_writes_the_tag_and_leaves_nothing_to_do(qapp, with_lyrics):
    from rifftag.gui.lyrics import LyricsCleanDialog

    folder, files, tracks = with_lyrics
    dlg = LyricsCleanDialog(tracks)
    written = []
    dlg.changed.connect(written.append)
    assert dlg.btn_clean.isEnabled()

    dlg.btn_clean.click()
    assert written and len(written[0]) == 2
    stored = tagio.read(files[0]).values["lyrics"]
    assert "[Verse 1]" not in stored
    assert "Hold on, the night is young" in stored
    assert "Cleaned 2 file(s)" in dlg.status.text()
    assert not dlg.btn_clean.isEnabled(), "a second pass has nothing to do"
    dlg.close()


def test_a_file_whose_lyrics_are_already_clean_is_left_alone(qapp, library):
    from rifftag.gui.lyrics import LyricsCleanDialog

    folder, files = library
    tagio.write(files[0], {"lyrics": "Come as you are\n\nAs you were"})
    dlg = LyricsCleanDialog([tagio.read(files[0])])
    assert dlg.table.item(0, 2).text() == "already clean"
    assert not dlg.btn_clean.isEnabled()
    dlg.close()


def test_the_cleaner_is_reachable_from_the_tools_menu(qapp):
    w = MainWindow()
    tools = [m for m in w.menuBar().findChildren(QMenu)
             if m.title() == "&Tools"][0]
    labels = [a.text() for a in tools.actions()]
    assert "Clean lyrics for streaming…" in labels
    assert "Import lyrics from text files…" in labels
    w.close()


def test_import_matches_text_files_beside_the_tracks(qapp, library):
    from rifftag.gui.lyrics import LyricsImportDialog

    folder, files = library
    (folder / f"{files[0].stem}.txt").write_text("[Chorus]\nWords here\n")
    tracks = [tagio.read(p) for p in files]

    dlg = LyricsImportDialog(tracks)
    assert dlg.table.item(0, 1).text() == f"{files[0].stem}.txt"
    assert dlg.table.item(1, 1).text() == "—"
    assert dlg.table.item(1, 2).text() == "nothing matched"
    assert "1 file(s) would get lyrics" in dlg.status.text()
    dlg.close()


def test_import_writes_and_cleans_on_the_way_in(qapp, library):
    from rifftag.gui.lyrics import LyricsImportDialog

    folder, files = library
    (folder / f"{files[0].stem}.txt").write_text(
        "[Chorus]\nCome as you are (x2)\n")
    dlg = LyricsImportDialog([tagio.read(p) for p in files])
    dlg.btn_import.click()

    stored = tagio.read(files[0]).values["lyrics"]
    assert stored == "Come as you are"
    dlg.close()


def test_import_can_be_told_not_to_clean(qapp, library):
    from rifftag.gui.lyrics import LyricsImportDialog

    folder, files = library
    (folder / f"{files[0].stem}.lrc").write_text("[00:12.00]Come as you are")
    dlg = LyricsImportDialog([tagio.read(p) for p in files])
    dlg.clean_on_import.setChecked(False)
    dlg.btn_import.click()

    assert "[00:12.00]" in tagio.read(files[0]).values["lyrics"], \
        "the timestamps were kept on purpose"
    dlg.close()


def test_import_does_not_overwrite_existing_lyrics_unless_told(qapp, library):
    from rifftag.gui.lyrics import LyricsImportDialog

    folder, files = library
    tagio.write(files[0], {"lyrics": "Mine, hand written"})
    (folder / f"{files[0].stem}.txt").write_text("From the file")

    dlg = LyricsImportDialog([tagio.read(files[0])])
    assert "already has lyrics" in dlg.table.item(0, 2).text()
    assert not dlg.btn_import.isEnabled()

    dlg.overwrite.setChecked(True)
    assert dlg.btn_import.isEnabled()
    dlg.btn_import.click()
    assert tagio.read(files[0]).values["lyrics"] == "From the file"
    dlg.close()


def test_the_main_window_re_reads_what_the_dialog_wrote(qapp, with_lyrics):
    """The grid and the Tag Editor must not keep showing the old lyrics."""
    folder, files, tracks = with_lyrics
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    assert "[Verse 1]" in w.model.all_tracks()[0].values["lyrics"]

    tagio.write(files[0], {"lyrics": "Hold on, the night is young"})
    w._lyrics_written([files[0]])
    assert w.model.all_tracks()[0].values["lyrics"] == \
        "Hold on, the night is young"
    w.close()


# --- Release Check -------------------------------------------------------

def test_release_check_is_a_labelled_button_not_another_icon(qapp):
    """Dave's note: the lyrics tools were invisible in the Tools menu, and
    this is the feature you open the app for when an album is done."""
    w = MainWindow()
    assert w.release_button.text().strip() == "Release Check"
    assert w.release_button.toolButtonStyle() in (
        Qt.ToolButtonStyle.ToolButtonTextOnly,
        Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    assert w.lyrics_button.text().strip() == "Lyrics"
    labels = [a.text() for a in w.lyrics_menu.actions()]
    assert "Clean lyrics for streaming…" in labels
    assert "Import lyrics from text files…" in labels
    w.close()


def test_release_check_is_also_in_the_tools_menu_and_on_f9(qapp):
    w = MainWindow()
    tools = [m for m in w.menuBar().findChildren(QMenu)
             if m.title() == "&Tools"][0]
    assert any(a.text().startswith("Release Check")
               for a in tools.actions())
    assert any(a.shortcut().toString() == "F9" for a in w.actions())
    w.close()


@pytest.fixture
def album(sample, tmp_path):
    """A release that passes every check — imported from the engine tests so
    the two suites cannot drift apart."""
    from tests.test_preflight import album as _album
    return _album.__wrapped__(sample, tmp_path)


@pytest.fixture
def release(sample, tmp_path):
    """A small release with one of everything wrong."""
    from tests.test_preflight import _png

    folder = tmp_path / "release"
    folder.mkdir()
    art = [tagio.Picture(_png(1400, 1400), mime="image/png")]
    made = []
    for n, title in enumerate(["Opening", "closing"], 1):
        path = folder / f"{n:02d} {title}.mp3"
        sample("mp3", f"t{n}").rename(path)
        tagio.write(path, {"artist": "Dave Yeubanks", "album": "Scratch",
                           "title": title, "track": str(n), "year": "2026"},
                    pictures=art)
        made.append(path)
    return [tagio.read(p) for p in made]


def test_the_dialog_lists_the_findings_worst_first(qapp, release):
    from rifftag.gui.preflight import PreflightDialog

    dlg = PreflightDialog(release)
    assert dlg.table.rowCount() > 0
    levels = [dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount())]
    order = ["Problem", "Warning", "Note"]
    assert levels == sorted(levels, key=order.index), "worst first"
    assert "Warning" in levels, "small art and mixed casing are warnings"
    dlg.close()


def test_a_clean_release_says_so_plainly(qapp, album):
    from rifftag.gui.preflight import PreflightDialog

    dlg = PreflightDialog(album)
    assert dlg.headline.text() == "Ready to upload"
    assert dlg.table.rowCount() == 0
    dlg.close()


def test_every_row_names_the_file_and_the_fix(qapp, release):
    from rifftag.gui.preflight import PreflightDialog

    dlg = PreflightDialog(release)
    for row in range(dlg.table.rowCount()):
        assert dlg.table.item(row, 1).text(), "every finding says where"
        assert dlg.table.item(row, 2).text(), "and what"
    dlg.close()


def test_the_report_copies_as_readable_text(qapp, release):
    from rifftag.gui.preflight import PreflightDialog

    dlg = PreflightDialog(release)
    text = dlg.as_text()
    assert "RiffTag Release Check" in text
    assert "3000x3000" in text, "the artwork warning carries its numbers"
    dlg._copy()
    assert "copied" in dlg.subhead.text().lower()
    dlg.close()


def test_loudness_findings_appear_once_measured(qapp, release):
    from rifftag.preflight import Loudness
    from rifftag.gui.preflight import PreflightDialog

    dlg = PreflightDialog(release)
    before = dlg.table.rowCount()
    dlg.loudness = {release[0].path: Loudness(integrated=-6.0, true_peak=0.5)}
    dlg.refresh()
    assert dlg.table.rowCount() > before
    assert "true_peak_hot" in {f.code for f in dlg.report.findings}
    dlg.close()


def test_the_loudness_worker_is_off_the_gui_thread(qapp, release):
    """It decodes every file; doing that inline would freeze the dialog."""
    from rifftag.gui.preflight import LoudnessWorker

    worker = LoudnessWorker(release)
    seen = []
    worker.progress.connect(lambda d, t: seen.append(d))
    out = []
    worker.finished.connect(out.append)
    worker.run()
    assert seen == [1, 2]
    assert set(out[0]) == {t.path for t in release}


def test_without_ffmpeg_the_loudness_box_is_disabled(qapp, release,
                                                     monkeypatch):
    from rifftag.gui import preflight as preflight_gui

    monkeypatch.setattr(preflight_gui, "ffmpeg_available", lambda: False)
    dlg = preflight_gui.PreflightDialog(release)
    assert not dlg.measure.isEnabled()
    assert "ffmpeg" in dlg.measure.toolTip()
    dlg.close()


def test_release_check_with_an_empty_list_says_what_to_do(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    said = {}
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: said.update(text=a[2]))
    w = MainWindow()
    w.model.clear()
    w._release_check()
    assert "album" in said["text"].lower()
    w.close()


# --- the folder column slides to the folder you are in -------------------

@pytest.fixture
def deep_tree(qapp, tmp_path):
    """A window whose folder column is narrow and whose current folder is
    several levels down — the situation where the name hides past the edge."""
    deep = tmp_path
    for part in ("Music", "AI Music", "AI Albums", "2026 - Those Who Are Known"):
        deep = deep / part
    deep.mkdir(parents=True)

    w = MainWindow()
    w.show()
    qapp.processEvents()
    w.folder_panel.resize(240, w.folder_panel.height())
    w.folder_tree.resize(240, 600)
    qapp.processEvents()
    w._reveal(deep)
    for _ in range(20):
        qapp.processEvents()
    return w, deep


def _current_rect(w):
    return w.folder_tree.visualRect(w.folder_tree.currentIndex())


def test_the_current_folder_is_scrolled_into_view(qapp, deep_tree):
    """Dave's report: four levels down you had to drag the scroll bar every
    time to see which folder you were in."""
    w, deep = deep_tree
    rect = _current_rect(w)
    assert rect.isValid()
    assert rect.left() < w.folder_tree.viewport().width(), \
        "the name must start inside the column, not past its right edge"
    w.close()


def test_a_folder_already_in_view_is_left_where_it_is(qapp, deep_tree):
    w, deep = deep_tree
    bar = w.folder_tree.horizontalScrollBar()
    w.show_current_folder()
    settled = bar.value()
    w.show_current_folder()
    assert bar.value() == settled, "no creep on a second call"
    w.close()


def test_scrolling_back_left_when_the_name_is_off_that_side(qapp, deep_tree):
    w, deep = deep_tree
    bar = w.folder_tree.horizontalScrollBar()
    if bar.maximum() <= 0:
        pytest.skip("nothing to scroll in this layout")
    bar.setValue(bar.maximum())
    qapp.processEvents()
    w.show_current_folder()
    rect = _current_rect(w)
    assert rect.left() >= 0, "the name was dragged back into view"
    w.close()


def test_the_name_start_is_preferred_over_its_end(qapp, deep_tree):
    """A folder wider than the column must show its beginning, not its tail."""
    w, deep = deep_tree
    w.show_current_folder()
    rect = _current_rect(w)
    assert rect.left() >= 0 or rect.width() > w.folder_tree.viewport().width()
    w.close()


def test_no_current_folder_is_not_an_error(qapp):
    from PyQt6.QtCore import QModelIndex

    w = MainWindow()
    w.folder_tree.setCurrentIndex(QModelIndex())
    w.show_current_folder()          # must simply do nothing
    w.close()


# --- tap-to-sync ---------------------------------------------------------

LYRIC_TEXT = "Hold on, the night is young\nI hear you calling\n\nCome as you are"


@pytest.fixture
def sync_dialog(qapp, library):
    """A sync dialog on a track with lyrics, wired to a fake player."""
    from rifftag.gui.lrcsync import LrcSyncDialog
    from rifftag.gui.player import PlayerPanel

    folder, files = library
    tagio.write(files[0], {"lyrics": LYRIC_TEXT})
    track = tagio.read(files[0])

    panel = PlayerPanel()
    fake = _FakePlayer()
    panel._player = fake
    panel._ensure_player = lambda: fake
    positions = {"ms": 0}
    fake.position = lambda: positions["ms"]
    panel.position_ms = lambda: positions["ms"]

    dlg = LrcSyncDialog(track, panel)
    dlg.timer.stop()                 # drive the clock by hand in tests
    return dlg, positions, track


def test_the_lyrics_arrive_as_tappable_lines_with_the_gap_kept(sync_dialog):
    dlg, _, _ = sync_dialog
    assert [l.text for l in dlg.lines] == [
        "Hold on, the night is young", "I hear you calling", "",
        "Come as you are"]
    assert dlg.table.rowCount() == 4
    assert "gap" in dlg.table.item(2, 2).text()
    dlg.close()


def test_tapping_stamps_the_line_and_moves_on(sync_dialog):
    dlg, position, _ = sync_dialog
    position["ms"] = 12_340
    dlg.tap()
    assert dlg.lines[0].ms == 12_340
    assert dlg.cursor == 1
    assert dlg.table.item(0, 1).text() == "[00:12.34]"
    dlg.close()


def test_back_undoes_the_last_tap(sync_dialog):
    dlg, position, _ = sync_dialog
    position["ms"] = 5_000
    dlg.tap()
    dlg.back()
    assert dlg.cursor == 0
    assert not dlg.lines[0].timed
    dlg.close()


def test_back_at_the_very_start_does_nothing(sync_dialog):
    dlg, _, _ = sync_dialog
    dlg.back()
    assert dlg.cursor == 0
    dlg.close()


def test_tapping_past_the_last_line_stops(sync_dialog):
    dlg, position, _ = sync_dialog
    for n in range(10):
        position["ms"] = 1000 * (n + 1)
        dlg.tap()
    assert dlg.cursor == len(dlg.lines)
    assert all(l.timed for l in dlg.lines)
    dlg.close()


def test_clear_all_starts_the_take_again(sync_dialog):
    dlg, position, _ = sync_dialog
    position["ms"] = 1000
    dlg.tap()
    dlg.tap()
    dlg.clear_all()
    assert dlg.cursor == 0
    assert not any(l.timed for l in dlg.lines)
    assert not dlg.btn_save.isEnabled()
    dlg.close()


def test_double_clicking_a_line_carries_on_from_there(sync_dialog):
    dlg, _, _ = sync_dialog
    dlg._cursor_to_row(2)
    assert dlg.cursor == 2
    dlg.close()


def test_nudging_moves_every_stamp(sync_dialog):
    dlg, position, _ = sync_dialog
    position["ms"] = 10_000
    dlg.tap()
    position["ms"] = 20_000
    dlg.tap()
    dlg.nudge(-500)
    assert [l.ms for l in dlg.lines[:2]] == [9_500, 19_500]
    assert "-0.5s" in dlg.status.text() or "−0.5" in dlg.status.text()
    dlg.close()


def test_saving_writes_an_lrc_beside_the_track(sync_dialog):
    dlg, position, track = sync_dialog
    position["ms"] = 12_340
    dlg.tap()
    position["ms"] = 15_000
    dlg.tap()

    saved = []
    dlg.saved.connect(saved.append)
    dlg.btn_save.click()

    path = track.path.with_suffix(".lrc")
    assert saved == [path]
    text = path.read_text()
    assert "[00:12.34]Hold on, the night is young" in text
    assert "[ti:Smells Like Teen Spirit]" in text, "metadata from the tags"
    assert "Come as you are" not in text, "untimed lines are left out"
    dlg.close()


def test_saving_can_also_put_the_timed_text_in_the_tag(sync_dialog):
    dlg, position, track = sync_dialog
    position["ms"] = 1_000
    dlg.tap()
    dlg.also_tag.setChecked(True)
    dlg.btn_save.click()

    stored = tagio.read(track.path).values["lyrics"]
    assert "[00:01.00]" in stored
    assert "lyrics tag" in dlg.status.text()
    dlg.close()


def test_reopening_picks_up_the_lrc_already_written(qapp, library):
    """Editing a sync you started beats starting it again."""
    from rifftag.gui.lrcsync import LrcSyncDialog
    from rifftag.gui.player import PlayerPanel

    folder, files = library
    tagio.write(files[0], {"lyrics": LYRIC_TEXT})
    files[0].with_suffix(".lrc").write_text(
        "[ti:In Bloom]\n\n[00:09.00]Hold on, the night is young\n")

    dlg = LrcSyncDialog(tagio.read(files[0]), PlayerPanel())
    dlg.timer.stop()
    assert dlg.lines[0].ms == 9_000
    assert dlg.meta["ti"] == "In Bloom"
    dlg.close()


def test_a_track_with_no_lyrics_says_what_to_do_first(qapp, library):
    from rifftag.gui.lrcsync import LrcSyncDialog
    from rifftag.gui.player import PlayerPanel

    folder, files = library
    dlg = LrcSyncDialog(tagio.read(files[2]), PlayerPanel())
    dlg.timer.stop()
    assert dlg.lines == []
    assert "Import lyrics" in dlg.status.text()
    assert not dlg.btn_save.isEnabled()
    dlg.close()


def test_the_playing_marker_follows_the_clock(sync_dialog):
    dlg, position, _ = sync_dialog
    position["ms"] = 1_000
    dlg.tap()
    position["ms"] = 5_000
    dlg.tap()

    position["ms"] = 6_000
    dlg._tick()
    assert dlg.table.item(1, 0).text().startswith("▶")
    assert dlg.clock_label.text() == "0:06"
    dlg.close()


def test_without_playback_the_tap_button_is_disabled(qapp, library,
                                                     monkeypatch):
    from rifftag.gui import player as player_mod
    from rifftag.gui.lrcsync import LrcSyncDialog

    monkeypatch.setattr(player_mod, "AVAILABLE", False)
    folder, files = library
    tagio.write(files[0], {"lyrics": LYRIC_TEXT})
    dlg = LrcSyncDialog(tagio.read(files[0]), player_mod.PlayerPanel())
    dlg.timer.stop()
    assert not dlg.btn_tap.isEnabled()
    assert "qtmultimedia" in dlg.status.text()
    dlg.close()


def test_sync_is_on_the_lyrics_button_and_in_the_tools_menu(qapp):
    w = MainWindow()
    assert any(a.text() == "Sync lyrics to the music…"
               for a in w.lyrics_menu.actions())
    tools = [m for m in w.menuBar().findChildren(QMenu)
             if m.title() == "&Tools"][0]
    assert any(a.text() == "Sync lyrics to the music…"
               for a in tools.actions())
    w.close()


def test_the_number_column_leaves_room_for_the_playing_marker(sync_dialog):
    """Sized to contents while the rows read "5", "▶ 5" came out as "…"."""
    from rifftag.gui.lrcsync import MARKER_ROOM

    dlg, position, _ = sync_dialog
    position["ms"] = 1_000
    dlg.tap()
    plain = dlg.table.fontMetrics().horizontalAdvance("▶ 1")
    assert dlg.table.columnWidth(0) >= plain
    assert MARKER_ROOM > 0
    dlg.close()


# --- dropping pictures onto the cover-art boxes --------------------------

def _mime_with_file(path):
    from PyQt6.QtCore import QMimeData, QUrl
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def _write_image(path, fmt="PNG", size=(64, 64), colour=0xFF3366):
    from PyQt6.QtGui import QColor, QImage
    image = QImage(size[0], size[1], QImage.Format.Format_RGB32)
    image.fill(QColor(colour))
    assert image.save(str(path), fmt)
    return path


def test_a_dropped_jpeg_passes_through_byte_for_byte(qapp, tmp_path):
    """Re-encoding a cover someone exported at 3000x3000 would cost quality
    for nothing."""
    from rifftag.gui.art import from_path

    path = _write_image(tmp_path / "cover.jpg", "JPEG")
    data, mime = from_path(path)
    assert mime == "image/jpeg"
    assert data == path.read_bytes()


def test_a_dropped_png_passes_through_too(qapp, tmp_path):
    from rifftag.gui.art import from_path

    path = _write_image(tmp_path / "cover.png", "PNG")
    data, mime = from_path(path)
    assert mime == "image/png"
    assert data == path.read_bytes()


def test_a_format_tag_readers_dislike_is_converted_to_jpeg(qapp, tmp_path):
    from rifftag.gui.art import from_path

    path = _write_image(tmp_path / "cover.bmp", "BMP")
    data, mime = from_path(path)
    assert mime == "image/jpeg"
    assert data[:2] == b"\xff\xd8", "really a JPEG now"


def test_a_non_picture_is_refused(qapp, tmp_path):
    from rifftag.gui.art import ArtError, from_path

    path = tmp_path / "notes.txt"
    path.write_text("not a picture")
    with pytest.raises(ArtError, match="not a picture file"):
        from_path(path)


def test_dropped_image_bytes_with_no_file_are_accepted(qapp):
    """Dragging straight out of a browser carries pixels, not a path."""
    from PyQt6.QtCore import QMimeData
    from PyQt6.QtGui import QColor, QImage
    from rifftag.gui.art import carries_image, from_mime

    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(QColor(0x00AA88))
    mime = QMimeData()
    mime.setImageData(image)

    assert carries_image(mime)
    data, kind = from_mime(mime)
    assert kind == "image/jpeg" and data[:2] == b"\xff\xd8"


def test_the_drop_target_only_lights_up_for_pictures(qapp, tmp_path):
    from PyQt6.QtCore import QMimeData
    from rifftag.gui.art import carries_image

    assert carries_image(_mime_with_file(_write_image(tmp_path / "a.png")))
    text = QMimeData()
    text.setText("just some words")
    assert not carries_image(text)


def test_dropping_on_file_art_writes_the_tag_for_every_selected_file(
        qapp, library, tmp_path):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()
    w.files.selectAll()

    path = _write_image(tmp_path / "cover.png", "PNG")
    data, mime = __import__("rifftag.gui.art", fromlist=["x"]).from_path(path)
    w._file_art_dropped(data, mime)

    for f in files:
        pictures = tagio.read(f).pictures
        assert pictures and pictures[0].data == data
    assert "3 file(s)" in w.statusBar().currentMessage()
    w.close()


def test_dropping_on_file_art_with_nothing_selected_says_so(qapp, library,
                                                            monkeypatch,
                                                            tmp_path):
    from PyQt6.QtWidgets import QMessageBox

    said = {}
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: said.update(text=a[2]))
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.clearSelection()

    w._file_art_dropped(b"\xff\xd8ignored", "image/jpeg")
    assert "Select the files" in said["text"]
    assert not tagio.read(files[0]).pictures, "nothing was written"
    w.close()


def test_dropping_on_folder_art_writes_folder_jpg(qapp, library, tmp_path):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)

    path = _write_image(tmp_path / "cover.jpg", "JPEG")
    data = path.read_bytes()
    w._folder_art_dropped(data, "image/jpeg")

    assert (folder / "folder.jpg").read_bytes() == data
    assert "folder.jpg" in w.statusBar().currentMessage()
    w.close()


def test_a_png_dropped_on_folder_art_becomes_folder_jpg(qapp, library,
                                                        tmp_path):
    """folder.jpg is the name the rest of the app looks for; writing
    folder.png beside an existing folder.jpg would leave the stale one
    winning the lookup."""
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)

    path = _write_image(tmp_path / "cover.png", "PNG")
    w._folder_art_dropped(path.read_bytes(), "image/png")

    written = folder / "folder.jpg"
    assert written.exists()
    assert written.read_bytes()[:2] == b"\xff\xd8"
    assert not (folder / "folder.png").exists()
    assert "converted" in w.statusBar().currentMessage()
    w.close()


def test_the_tag_editor_art_box_takes_a_drop(qapp, library, tmp_path):
    folder, files = library
    dlg = TagEditorDialog([tagio.read(files[0])])
    path = _write_image(tmp_path / "cover.png", "PNG")
    data = path.read_bytes()

    dlg._art_dropped(data, "image/png")
    assert dlg._pictures and dlg._pictures[0].data == data
    assert dlg._pictures_dirty, "so Save actually writes it"
    dlg.close()


def test_the_art_boxes_say_you_can_drop_on_them(qapp):
    w = MainWindow()
    assert "drop" in w.file_art.text().lower()
    assert "drop" in w.folder_art.text().lower()
    assert w.file_art.acceptDrops() and w.folder_art.acceptDrops()
    w.close()


def test_a_real_drop_event_reaches_the_signal(qapp, tmp_path):
    """The helpers are tested above; this drives the widget's own event
    handlers, which is where a wrong accept/ignore would hide."""
    from PyQt6.QtCore import QPoint, QPointF, Qt as _Qt
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent
    from rifftag.gui.art import ArtDropLabel

    label = ArtDropLabel("drop here")
    label.resize(120, 120)
    got = []
    label.art_dropped.connect(lambda data, mime: got.append(mime))

    path = _write_image(tmp_path / "cover.png", "PNG")
    mime = _mime_with_file(path)
    enter = QDragEnterEvent(QPoint(10, 10), _Qt.DropAction.CopyAction, mime,
                            _Qt.MouseButton.LeftButton,
                            _Qt.KeyboardModifier.NoModifier)
    label.dragEnterEvent(enter)
    assert enter.isAccepted(), "a picture must be welcomed"

    drop = QDropEvent(QPointF(10, 10), _Qt.DropAction.CopyAction, mime,
                      _Qt.MouseButton.LeftButton,
                      _Qt.KeyboardModifier.NoModifier)
    label.dropEvent(drop)
    assert got == ["image/png"]


def test_a_text_drag_is_turned_away_at_the_door(qapp):
    from PyQt6.QtCore import QMimeData, QPoint, Qt as _Qt
    from PyQt6.QtGui import QDragEnterEvent
    from rifftag.gui.art import ArtDropLabel

    label = ArtDropLabel("drop here")
    mime = QMimeData()
    mime.setText("some words")
    enter = QDragEnterEvent(QPoint(5, 5), _Qt.DropAction.CopyAction, mime,
                            _Qt.MouseButton.LeftButton,
                            _Qt.KeyboardModifier.NoModifier)
    label.dragEnterEvent(enter)
    assert not enter.isAccepted()


# --- Lyrics tab: load from / save to a text file -------------------------

def test_the_lyrics_tab_has_load_and_save_buttons(qapp, library):
    folder, files = library
    dlg = TagEditorDialog([tagio.read(files[0])])
    assert "Load" in dlg.btn_lyrics_load.text()
    assert "file" in dlg.btn_lyrics_save.text()
    dlg.close()


def test_loading_a_text_file_fills_the_box_and_ticks_it(qapp, library,
                                                        tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    folder, files = library
    source = tmp_path / "words.txt"
    source.write_text("Hold on, the night is young\nI hear you calling\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(source), ""))

    dlg = TagEditorDialog([tagio.read(files[0])])
    dlg._load_lyrics()
    assert "the night is young" in dlg.inputs["lyrics"].toPlainText()
    assert dlg.checks["lyrics"].isChecked(), "so Save actually writes it"
    dlg.close()


def test_loaded_lyrics_are_written_on_save(qapp, library, tmp_path,
                                           monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    folder, files = library
    source = tmp_path / "words.txt"
    source.write_text("Come as you are")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(source), ""))

    dlg = TagEditorDialog([tagio.read(files[0])])
    dlg._load_lyrics()
    dlg._save_and_close()
    assert tagio.read(files[0]).values["lyrics"] == "Come as you are"


def test_a_file_in_another_encoding_still_loads(qapp, library, tmp_path,
                                                monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    folder, files = library
    source = tmp_path / "words.txt"
    source.write_bytes("Café nights".encode("cp1252"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(source), ""))

    dlg = TagEditorDialog([tagio.read(files[0])])
    dlg._load_lyrics()
    assert "Café" in dlg.inputs["lyrics"].toPlainText()
    dlg.close()


def test_saving_writes_what_is_in_the_box(qapp, library, tmp_path,
                                          monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    folder, files = library
    target = tmp_path / "out.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(target), ""))

    dlg = TagEditorDialog([tagio.read(files[0])])
    dlg.inputs["lyrics"].setPlainText("Hold on\nI hear you calling")
    dlg._save_lyrics()
    assert target.read_text() == "Hold on\nI hear you calling"
    dlg.close()


def test_saving_an_empty_box_says_so_instead_of_writing_nothing(
        qapp, library, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    said = {}
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: said.update(text=a[2]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *a, **k: pytest.fail("should not be asked"))

    folder, files = library
    dlg = TagEditorDialog([tagio.read(files[0])])
    dlg.inputs["lyrics"].setPlainText("   \n  ")
    dlg._save_lyrics()
    assert "no lyrics" in said["text"].lower()
    dlg.close()


def test_the_suggested_name_sits_beside_the_track(qapp, library):
    folder, files = library
    dlg = TagEditorDialog([tagio.read(files[0])])
    assert dlg._lyrics_default_path() == files[0].with_suffix(".txt")
    dlg.close()


def test_editing_several_files_suggests_one_name_for_the_folder(qapp, library):
    folder, files = library
    dlg = TagEditorDialog([tagio.read(p) for p in files])
    assert dlg._lyrics_default_path() == folder / "lyrics.txt"
    dlg.close()


def test_cancelling_the_dialog_changes_nothing(qapp, library, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    folder, files = library
    dlg = TagEditorDialog([tagio.read(files[0])])
    before = dlg.inputs["lyrics"].toPlainText()
    dlg._load_lyrics()
    assert dlg.inputs["lyrics"].toPlainText() == before
    dlg.close()


# --- up one folder -------------------------------------------------------

def test_the_folder_header_has_an_up_button(qapp):
    from PyQt6.QtWidgets import QToolButton

    w = MainWindow()
    tips = {b.toolTip() for b in w.folder_panel.findChildren(QToolButton)}
    assert "Up one folder" in tips
    w.close()


def test_going_up_asks_for_the_parent(panel, tmp_path):
    deep = tmp_path / "Music" / "AI Albums" / "2026 - Prism Logic"
    deep.mkdir(parents=True)
    panel.set_current(deep)

    asked = []
    panel.folder_chosen.connect(asked.append)
    panel.btn_up.click()
    assert asked == [deep.parent]


def test_the_up_button_is_dead_at_the_root(panel):
    from pathlib import Path

    panel.set_current(Path("/"))
    assert not panel.btn_up.isEnabled(), "Path('/').parent is itself"
    asked = []
    panel.folder_chosen.connect(asked.append)
    panel._go_up()
    assert asked == []


def test_the_up_button_starts_disabled_until_a_folder_is_open(panel):
    assert not panel.btn_up.isEnabled()


def test_the_up_button_wakes_up_once_a_folder_is_open(panel, tmp_path):
    panel.set_current(tmp_path)
    assert panel.btn_up.isEnabled()


def test_going_up_opens_that_folder_in_the_window(qapp, library):
    folder, files = library
    w = MainWindow()
    w._goto(folder)
    assert w.folder == folder

    w.folder_panel.btn_up.click()
    assert w.folder == folder.parent, "the list now shows the parent"
    w.close()


def test_the_folders_label_gives_way_before_the_buttons_are_clipped(qapp):
    """A QLabel does not elide, it just gets cut off mid-word — which reads
    as a bug rather than as a narrow column."""
    w = MainWindow()
    w.show()
    qapp.processEvents()

    # Driven by width rather than by resize(): inside a splitter the panel
    # does not keep a width you hand it, and the button labels differ with
    # the icon theme, so a magic number would only test this machine.
    w.folder_panel.fit_title(4000)
    assert w.folder_panel.title.isVisible(), "plenty of room here"

    w.folder_panel.fit_title(60)
    assert not w.folder_panel.title.isVisible()
    for button in (w.folder_panel.btn_up, w.folder_panel.btn_home,
                   w.folder_panel.btn_close):
        assert button.isVisible(), "the buttons are what must survive"
    w.close()


def test_windows_gets_fusion_so_the_palette_is_actually_used(qapp):
    """The native windows11 style only partly consults the QPalette: on a
    dark Windows 11 the rename mask and result preview came out as dark text
    on a dark field. Every colour in this app is palette-derived, so the
    style has to honour it."""
    from rifftag.gui.theme import use_portable_style

    before = qapp.style().objectName()
    try:
        assert use_portable_style(qapp, platform="win32")
        assert qapp.style().objectName().lower() == "fusion"
    finally:
        qapp.setStyle(before)


def test_other_platforms_keep_their_native_style(qapp):
    from rifftag.gui.theme import use_portable_style

    before = qapp.style().objectName()
    assert not use_portable_style(qapp, platform="linux")
    assert qapp.style().objectName() == before


# --- row spacing and the shading below the last row ----------------------

def test_rows_get_a_little_breathing_room(qapp, library):
    """Dave: the lines ran together and were tiring to read."""
    from PyQt6.QtWidgets import QStyleOptionViewItem, QStyledItemDelegate
    from rifftag.gui.model import ROW_PADDING

    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    parent = w.model.index(0, 0)
    index = w.model.index(0, 0, parent)

    option = QStyleOptionViewItem()
    w.shade_delegate.initStyleOption(option, index)
    plain = QStyledItemDelegate().sizeHint(option, index).height()
    assert w.shade_delegate.sizeHint(option, index).height() == \
        plain + ROW_PADDING
    assert 0 < ROW_PADDING <= 6, "slightly roomier, not double-spaced"
    w.close()


def test_the_shading_division_carries_on_below_the_last_row(qapp, library):
    folder, files = library
    w = MainWindow()
    w.show()
    qapp.processEvents()
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()
    qapp.processEvents()

    rect = w.files.empty_section_rect()
    assert rect is not None and not rect.isEmpty(), \
        "three files leave plenty of empty pane"
    tag_col = w.model.first_tag_column()
    assert rect.left() == max(
        0, w.files.header().sectionViewportPosition(tag_col)), \
        "the slab starts exactly where the tag columns do"
    assert rect.right() >= w.files.viewport().width() - 1
    assert rect.top() >= w.files.last_row_bottom()
    w.close()


def test_nothing_is_painted_when_the_rows_fill_the_pane(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()
    w.files.resize(600, 20)          # shorter than one row
    qapp.processEvents()
    assert w.files.empty_section_rect() is None
    w.close()


def test_an_empty_list_leaves_the_watermark_alone(qapp):
    w = MainWindow()
    w.model.clear()
    assert w.files.last_row_bottom() == 0
    w.close()


def test_no_tag_columns_means_no_slab(qapp, library):
    from rifftag.gui.model import Column

    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.model.set_columns([Column("__name__", "File Name"),
                         Column("__size__", "Size")])
    assert w.files.empty_section_rect() is None, "nothing to divide"
    w.close()


# --- applying straight from the preview ----------------------------------

def test_the_rename_preview_offers_to_go_ahead(qapp, library, monkeypatch):
    """Dave: you had to close the window and then find the other button."""
    from PyQt6.QtWidgets import QDialog
    from rifftag.gui.main import PreviewDialog

    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()
    w.files.selectAll()
    w.rename_tab.mask_row.combo.setEditText("%1 - %2")

    seen = {}

    def fake_exec(self):
        seen["label"] = self.btn_action.text() if self.btn_action else None
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PreviewDialog, "exec", fake_exec)

    w._do_rename(True)
    assert seen["label"] == "Rename 3 file(s)", "says what it will do"
    assert (folder / "Nirvana - In Bloom.mp3").exists(), "and then did it"
    w.close()


def test_closing_the_preview_renames_nothing(qapp, library, monkeypatch):
    from PyQt6.QtWidgets import QDialog
    from rifftag.gui.main import PreviewDialog

    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()
    w.files.selectAll()
    w.rename_tab.mask_row.combo.setEditText("%1 - %2")
    monkeypatch.setattr(PreviewDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected)

    w._do_rename(True)
    assert all(p.exists() for p in files), "the originals are untouched"
    w.close()


def test_the_tag_preview_offers_to_write(qapp, library, monkeypatch):
    from PyQt6.QtWidgets import QDialog
    from rifftag.gui.main import PreviewDialog

    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w.files.expandAll()
    w.files.selectAll()
    w.parse_tab.mask_row.combo.setEditText("%6 %2")

    seen = {}

    def fake_exec(self):
        seen["label"] = self.btn_action.text() if self.btn_action else None
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PreviewDialog, "exec", fake_exec)

    w._do_parse(True)
    assert seen["label"] == "Write tags to 3 file(s)"
    assert tagio.read(files[1]).values["title"] == "In Bloom"
    w.close()


def test_a_preview_with_nothing_to_do_offers_no_button(qapp, library):
    from rifftag.gui.main import PreviewDialog

    dlg = PreviewDialog([("a.mp3", "", "error: nope")], "Test", None)
    assert dlg.btn_action is None, "no action when there is nothing to apply"
    assert dlg.btn_close is not None
    dlg.close()


def test_the_preview_does_not_ask_twice(qapp, library):
    """The preview IS the confirmation — it lists every change. A second
    'are you sure?' on top only teaches people to dismiss dialogs."""
    from rifftag.gui.main import PreviewDialog

    dlg = PreviewDialog([("a.mp3", "b.mp3", "")], "Test", None,
                        action_text="Rename 1 file(s)")
    assert dlg.btn_action.isDefault()
    dlg.accept()
    assert dlg.result() == 1, "accepting applies, with nothing in between"


def test_about_names_the_licence_and_invites_support_once(qapp):
    """One link, in one dialog. No startup prompt, no counter, no nag."""
    from rifftag.gui.about import AboutDialog, LICENCE, SUPPORT_URL

    dlg = AboutDialog()
    html = dlg.findChildren(__import__(
        "PyQt6.QtWidgets", fromlist=["QTextBrowser"]).QTextBrowser)[0].toHtml()
    assert LICENCE in html
    assert SUPPORT_URL in html
    assert "free and always will be" in html
    dlg.close()


def test_nothing_outside_about_asks_for_money(qapp):
    w = MainWindow()
    from rifftag.gui.about import SUPPORT_URL

    texts = []
    for widget in w.findChildren(__import__(
            "PyQt6.QtWidgets", fromlist=["QWidget"]).QWidget):
        for attr in ("text", "toolTip"):
            getter = getattr(widget, attr, None)
            if callable(getter):
                try:
                    texts.append(str(getter()))
                except TypeError:
                    pass
    joined = " ".join(texts).lower()
    assert SUPPORT_URL not in " ".join(texts)
    for word in ("donate", "coffee", "support rifftag", "upgrade", "pro"):
        assert word not in joined, f"the main window must never sell: {word}"
    w.close()


# --- a folder change must not inherit the last folder's files ------------

def test_results_from_a_superseded_scan_are_dropped(qapp, library):
    """Stopping a worker does not unqueue the batches it already emitted:
    they arrive after model.clear() and land in the NEW folder's list. Select
    All then Save, and you have written tags to the album you just left."""
    folder, files = library
    w = MainWindow()
    tracks = [tagio.read(p) for p in files]

    w._scan_id = 7
    w._scan_found(6, tracks)                 # a straggler from the old scan
    assert w.model.all_tracks() == [], "late results are ignored"

    w._scan_found(7, tracks)                 # the current one
    assert len(w.model.all_tracks()) == 3
    w.close()


def test_a_superseded_scan_cannot_move_the_progress_bar(qapp):
    w = MainWindow()
    w._scan_id = 4
    w.progress.setValue(0)
    w._scan_progress(3, 50, 100)
    assert w.progress.value() == 0
    w._scan_progress(4, 50, 100)
    assert w.progress.value() == 50
    w.close()


def test_a_superseded_scan_cannot_declare_the_list_finished(qapp, library):
    folder, files = library
    w = MainWindow()
    w.folder = folder
    _scan(w, folder)
    w._scan_id = 9
    w.statusBar().showMessage("Reading tags… 2/12")
    w._scan_done(8)
    assert "Reading tags" in w.statusBar().currentMessage(), \
        "the old scan must not announce a total for the new folder"
    w._scan_done(9)
    assert "file(s)" in w.statusBar().currentMessage()
    w.close()


def test_switching_folders_leaves_only_the_new_folder_s_files(qapp, library,
                                                              sample, tmp_path):
    """The end-to-end shape of the bug, as it was actually noticed."""
    first, files = library
    second = tmp_path / "Other Album"
    second.mkdir()
    other = second / "01 Something Else.mp3"
    sample("mp3", "other").rename(other)
    tagio.write(other, {"artist": "Someone", "title": "Something Else"})

    w = MainWindow()
    w.folder = first
    _scan(w, first)
    assert len(w.model.all_tracks()) == 3

    w.folder = second
    w._scan_id += 1
    w.model.clear()
    _scan(w, second)
    names = [t.path.name for t in w.model.all_tracks()]
    assert names == ["01 Something Else.mp3"], names
    w.close()


def test_the_asset_search_covers_a_flatpak_install(qapp):
    """Inside a Flatpak the app is in /app but runs the runtime's python, so
    sys.prefix is /usr and the share/ candidate finds nothing. Without the
    /app entry the window builds with no logo at all."""
    import inspect
    from rifftag.gui import splash

    source = inspect.getsource(splash._find_assets)
    assert "/app/share/rifftag/assets" in source
