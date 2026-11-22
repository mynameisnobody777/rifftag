"""The globe button — Load Album Titles from the Web.

Two pages in one dialog, mirroring the original's two windows:

1. **Search** — artist / album / year, a source to search, and a table of
   matching releases.
2. **Album information** — the fetched track list lined up against the files
   you selected, with a tick per field so you choose what gets written.

Every network call runs on a worker thread. A lookup that blocks the GUI for
ten seconds feels broken, and MusicBrainz is throttled to one request a second
by design.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                             QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QMessageBox, QPushButton, QStackedWidget,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from ..tagio import Picture, TagError, write
from .model import apply_zebra
from ..webimport import Album, Discogs, MusicBrainz, WebImportError, align

#: tag fields the user can choose to write, in the order they are shown
WRITABLE = (
    ("title", "Title", True),
    ("artist", "Artist", True),
    ("album", "Album", True),
    ("track", "Track #", True),
    ("year", "Year", True),
    ("genre", "Genre", False),
    ("label", "Label", False),
    ("catalog", "Catalog #", False),
    ("album_artist", "Album Artist", False),
)


#: Every worker thread, held from the moment it starts until it finishes.
#:
#: Two separate hazards need this. A blocking ``urlopen`` cannot be cancelled,
#: so closing the dialog must not wait on it — and destroying a QThread that
#: is still running aborts the whole process. Keeping a strong reference here,
#: rather than only on the dialog, means a dialog that is closed *or simply
#: dropped* cannot take a live thread down with it. The dialog separately
#: disconnects the job's signals so a late answer is ignored.
_LIVE: set = set()


def _park(thread, job) -> None:
    _LIVE.add((thread, job))
    thread.finished.connect(lambda: _LIVE.discard((thread, job)))


class _Job(QObject):
    """Runs one provider call off the GUI thread."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self) -> None:
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except WebImportError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                        # never kill the thread
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class WebImportDialog(QDialog):
    def __init__(self, tracks, parent=None, *, discogs_token: str = "",
                 write_options=None):
        super().__init__(parent)
        self.tracks = list(tracks)
        self.write_options = write_options
        self.providers = [MusicBrainz(), Discogs(discogs_token)]
        self.results: list[Album] = []
        self.album: Album | None = None
        self.cover: bytes | None = None
        self.wrote = 0

        self._thread: QThread | None = None
        self._job: _Job | None = None

        total = sum(t.info.length for t in self.tracks)
        h, rem = divmod(int(total), 3600)
        m, s = divmod(rem, 60)
        self.setWindowTitle(
            f"Load Album Titles from Web  [selected files: {len(self.tracks)}, "
            f"selected files time: {h}:{m:02d}:{s:02d}]")
        self.resize(1040, 620)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_search_page())
        self.pages.addWidget(self._build_album_page())
        root = QVBoxLayout(self)
        root.addWidget(self.pages)
        self.status = QLabel("")
        root.addWidget(self.status)

        self._prefill()

    # --- page 1: search ---

    def _build_search_page(self) -> QWidget:
        page = QWidget()
        self.artist = QComboBox()
        self.album_box = QComboBox()
        self.year = QComboBox()
        for box in (self.artist, self.album_box, self.year):
            box.setEditable(True)
            box.setMinimumWidth(260)
        self.year.setMinimumWidth(110)

        self.btn_search = QPushButton("&Search!")
        self.btn_search.clicked.connect(self._search)
        self.masters_only = QCheckBox("Search only master releases on Discogs")

        self.source = QComboBox()
        for provider in self.providers:
            label = provider.name
            if getattr(provider, "needs_token", False) and not getattr(
                    provider, "available", True):
                label += "  (needs a token)"
            self.source.addItem(label)

        def field(widget) -> QWidget:
            """Wrap a field so the grid column can stretch past its width.

            A bare widget with a maximum width caps its whole grid column, and
            the leftover space lands in the label column instead — which is
            what pushed the labels miles away from their boxes.
            """
            holder = QWidget()
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(widget)
            lay.addStretch(1)
            return holder

        form = QGridLayout()
        for row, (label, widget) in enumerate((
                ("Artist:", self.artist), ("Album:", self.album_box),
                ("Year:", self.year), ("Search in:", self.source))):
            form.addWidget(QLabel(label), row, 0)
            form.addWidget(field(widget), row, 1)
        form.addWidget(self.masters_only, 4, 1)
        form.addWidget(self.btn_search, 0, 2)
        form.setColumnStretch(1, 1)
        form.setColumnMinimumWidth(2, 150)

        top = QHBoxLayout()
        top.addLayout(form)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Artist", "Album", "Year", "Label", "Cat #", "Tracks",
             "Format", "Link"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        apply_zebra(self.table)
        header = self.table.horizontalHeader()
        # Interactive, not Stretch: Stretch locks a column so it cannot be
        # dragged. This way every divider drags, and double-clicking one fits
        # it to its contents, as in a spreadsheet.
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._load_album)
        self.table.cellDoubleClicked.connect(self._maybe_open_link)

        self.btn_load = QPushButton("&Load Album!")
        self.btn_load.clicked.connect(self._load_album)
        self.btn_load.setEnabled(False)
        self.table.itemSelectionChanged.connect(
            lambda: self.btn_load.setEnabled(bool(self.table.selectedItems())))
        cancel = QPushButton("&Cancel")
        cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.btn_load)
        buttons.addWidget(cancel)

        lay = QVBoxLayout(page)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addLayout(buttons)
        return page

    # --- page 2: album information ---

    def _build_album_page(self) -> QWidget:
        page = QWidget()
        self.header = QLabel("")
        self.pairs = QTableWidget(0, 4)
        self.pairs.setHorizontalHeaderLabels(
            ["File", "New title", "New artist", "Time"])
        self.pairs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        apply_zebra(self.pairs)
        pairs_header = self.pairs.horizontalHeader()
        pairs_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        pairs_header.setStretchLastSection(True)
        self.pairs.verticalHeader().setVisible(False)

        self.field_checks: dict[str, QCheckBox] = {}
        fields = QGroupBox("Save to tags:")
        grid = QGridLayout(fields)
        for i, (key, label, default) in enumerate(WRITABLE):
            box = QCheckBox(label)
            box.setChecked(default)
            self.field_checks[key] = box
            grid.addWidget(box, i % 5, i // 5)

        self.cover_check = QCheckBox("Cover art")
        self.cover_label = QLabel("no cover found")
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setMinimumSize(150, 150)
        art = QGroupBox("Cover art")
        art_lay = QVBoxLayout(art)
        art_lay.addWidget(self.cover_label)
        art_lay.addWidget(self.cover_check)

        up = QPushButton("Move title ↑")
        down = QPushButton("Move title ↓")
        up.clicked.connect(lambda: self._shift(-1))
        down.clicked.connect(lambda: self._shift(+1))

        back = QPushButton("← &Back to results")
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        self.btn_write = QPushButton("&Write Tags")
        self.btn_write.clicked.connect(self._write_tags)
        cancel = QPushButton("&Cancel")
        cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(back)
        buttons.addWidget(up)
        buttons.addWidget(down)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_write)
        buttons.addWidget(cancel)

        side = QHBoxLayout()
        side.addWidget(fields, 2)
        side.addWidget(art, 1)

        lay = QVBoxLayout(page)
        lay.addWidget(self.header)
        lay.addWidget(self.pairs, 1)
        lay.addLayout(side)
        lay.addLayout(buttons)
        return page

    # --- behaviour ---

    def _prefill(self) -> None:
        """Seed the boxes from the tags the files already carry."""
        for key, box in (("artist", self.artist), ("album", self.album_box),
                         ("year", self.year)):
            seen = []
            for track in self.tracks:
                value = str(track.values.get(key, "") or "")
                if value and value not in seen:
                    seen.append(value)
            box.addItems(seen)
            if seen:
                box.setEditText(seen[0])
        if not self.artist.currentText() and self.tracks:
            # Nothing tagged yet — the folder name is the usual next best clue.
            self.album_box.setEditText(self.tracks[0].path.parent.name)

    def _busy(self, on: bool, message: str = "") -> None:
        self.btn_search.setEnabled(not on)
        self.btn_load.setEnabled(not on and bool(self.table.selectedItems()))
        self.status.setText(message)

    def _run(self, fn, on_done, *args, **kwargs) -> None:
        self._stop_thread()
        # No parent: a QThread parented to the dialog would be destroyed with
        # it while still running, which aborts the process.
        self._thread = QThread()
        self._job = _Job(fn, *args, **kwargs)
        self._job.moveToThread(self._thread)
        _park(self._thread, self._job)
        self._thread.started.connect(self._job.run)
        self._job.done.connect(on_done)
        self._job.failed.connect(self._failed)
        self._job.done.connect(self._thread.quit)
        self._job.failed.connect(self._thread.quit)
        self._thread.start()

    def _stop_thread(self) -> None:
        """Abandon any lookup in flight without blocking on it."""
        if self._job is not None:
            # Drop the answer rather than deliver it to a dead dialog.
            for signal in (self._job.done, self._job.failed):
                try:
                    signal.disconnect()
                except TypeError:
                    pass
        if self._thread is not None:
            self._thread.quit()      # _LIVE keeps it alive until it finishes
        self._thread = self._job = None

    def _failed(self, message: str) -> None:
        self._busy(False)
        QMessageBox.warning(self, "Web import", message)

    def _provider(self):
        return self.providers[self.source.currentIndex()]

    def _search(self) -> None:
        provider = self._provider()
        kwargs = {"artist": self.artist.currentText().strip(),
                  "album": self.album_box.currentText().strip(),
                  "year": self.year.currentText().strip()}
        if isinstance(provider, Discogs):
            kwargs["masters_only"] = self.masters_only.isChecked()
        self._busy(True, f"Searching {provider.name}…")
        self._run(provider.search, self._show_results, **kwargs)

    def _show_results(self, albums: list[Album]) -> None:
        self.results = albums
        self.table.setRowCount(len(albums))
        for row, album in enumerate(albums):
            cells = [album.artist, album.album, album.year, album.label,
                     album.catalog,
                     str(album.track_count or ""), album.format, album.url]
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self._fit_columns()
        self._busy(False, f"{len(albums)} release(s) found."
                   if albums else "Nothing found. Try fewer words.")

    #: widest a column is allowed to auto-size to, so one very long album
    #: title cannot push every other column off the right edge
    MAX_AUTO_WIDTH = 420

    def _fit_columns(self) -> None:
        self.table.resizeColumnsToContents()
        for col in range(self.table.columnCount()):
            if self.table.columnWidth(col) > self.MAX_AUTO_WIDTH:
                self.table.setColumnWidth(col, self.MAX_AUTO_WIDTH)

    def _maybe_open_link(self, row: int, col: int) -> None:
        if col == 7 and 0 <= row < len(self.results):
            QDesktopServices.openUrl(QUrl(self.results[row].url))

    def _selected_album(self) -> Album | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        return self.results[rows.pop()] if len(rows) == 1 else None

    def _load_album(self, *_) -> None:
        album = self._selected_album()
        if album is None:
            return
        self._busy(True, f"Loading {album.album}…")
        self._run(self._provider().fetch, self._show_album, album)

    def _show_album(self, album: Album) -> None:
        self.album = album
        self._busy(False)
        self.header.setText(
            f"{album.artist} — {album.album}   ({album.year})   "
            f"{len(album.tracks)} track(s), {album.total_time}   "
            f"[{album.provider}]")
        self._fill_pairs()
        self.pages.setCurrentIndex(1)
        self.cover = None
        self.cover_check.setChecked(False)
        self.cover_label.setText("looking for cover art…")
        self._run(self._provider().cover, self._show_cover, album)

    def _fill_pairs(self) -> None:
        matched = align(len(self.tracks), self.album.tracks if self.album else [])
        self.pairs.setRowCount(len(self.tracks))
        for row, (file_track, found) in enumerate(zip(self.tracks, matched)):
            cells = [file_track.path.name,
                     found.title if found else "— no track —",
                     (found.artist or (self.album.artist if self.album else ""))
                     if found else "",
                     found.time if found else ""]
            for col, text in enumerate(cells):
                self.pairs.setItem(row, col, QTableWidgetItem(text))
        self.pairs.resizeColumnsToContents()

    def _shift(self, delta: int) -> None:
        """Nudge the fetched titles against the files to fix an offset."""
        if not self.album or not self.album.tracks:
            return
        tracks = self.album.tracks
        if delta > 0:
            self.album.tracks = tracks[-1:] + tracks[:-1]
        else:
            self.album.tracks = tracks[1:] + tracks[:1]
        self._fill_pairs()

    def _show_cover(self, data: bytes | None) -> None:
        self.cover = data
        if not data:
            self.cover_label.setText("no cover found")
            self.cover_check.setEnabled(False)
            return
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        if pixmap.isNull():
            self.cover_label.setText("cover art unreadable")
            self.cover_check.setEnabled(False)
            return
        self.cover_label.setPixmap(pixmap.scaled(
            170, 170, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self.cover_check.setEnabled(True)
        self.cover_check.setChecked(True)

    def planned_writes(self) -> list[tuple[object, dict]]:
        """What Write Tags would do. Separated out so it can be tested."""
        if not self.album:
            return []
        wanted = {k for k, box in self.field_checks.items() if box.isChecked()}
        matched = align(len(self.tracks), self.album.tracks)
        plans = []
        for file_track, found in zip(self.tracks, matched):
            if found is None:
                continue
            values = self.album.values_for(
                found, use_album_artist="album_artist" in wanted,
                total=len(self.tracks))
            kept = {k: v for k, v in values.items()
                    if k in wanted or k == "track_total"}
            if "track" not in wanted:
                kept.pop("track_total", None)
            if kept:
                plans.append((file_track, kept))
        return plans

    def _write_tags(self) -> None:
        plans = self.planned_writes()
        if not plans:
            QMessageBox.information(self, "Web import", "Nothing to write.")
            return
        pictures = ([Picture(self.cover,
                             "image/png" if self.cover[:4] == b"\x89PNG"
                             else "image/jpeg")]
                    if self.cover and self.cover_check.isChecked() else None)
        failures = []
        for file_track, values in plans:
            try:
                write(file_track.path, values, pictures=pictures,
                      options=self.write_options)
                self.wrote += 1
            except TagError as exc:
                failures.append(str(exc))
        if failures:
            QMessageBox.warning(self, "Web import",
                                "Some files failed:\n" + "\n".join(failures[:12]))
        self.accept()

    def done(self, result: int) -> None:
        """Covers close, Cancel and Write Tags alike."""
        self._stop_thread()
        super().done(result)

    def closeEvent(self, event) -> None:
        self._stop_thread()
        super().closeEvent(event)
