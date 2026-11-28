"""Get movie / TV info from TMDB — the video counterpart of the globe button.

Two pages, like the album lookup:

1. **Search** — Movie or TV show, a title and an optional year, prefilled
   from the first file's name (``Title (Year)``, ``Show - S01E02``...).
2. **Details** — what will be written, with a tick per field and the poster.

The two kinds behave differently because the files do:

* **Movies** are one film per file, so the selection is walked one file at
  a time: write (or skip) and the search page comes back prefilled for the
  next file, already searched.
* **TV episodes** share one show, so the show is looked up once and every
  selected file is matched to an episode by the season/episode numbers in
  its name (S01E02, 1x02). Those numbers are editable in the table for files
  whose names do not carry them.

Network calls run on worker threads via the same parked-thread machinery as
the album lookup (see ``webimport._park`` for why threads are held globally).
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QThread, Qt, QUrl
from PyQt6.QtGui import (QColor, QDesktopServices, QIcon, QPainter,
                         QPixmap, QPolygonF)
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox,
                             QDialog, QGridLayout, QGroupBox, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QRadioButton, QStackedWidget,
                             QTableWidget, QTableWidgetItem, QTextBrowser,
                             QVBoxLayout, QWidget)

from ..tagio import Picture, TagError, write
from ..tmdb import (KEY_HINT, TMDB, Episode, Video, episode_values, guess,
                    movie_values)
from ..webimport import WebImportError
from .model import apply_zebra
from .webimport import _Job, _park

#: (key, label, ticked by default) — movie page
MOVIE_FIELDS = (
    ("title", "Title", True),
    ("year", "Year", True),
    ("genre", "Genre", True),
    ("comment", "Description (Comment)", True),
)
#: (key, label, ticked by default) — TV page. Season and episode numbers are
#: one tick: writing one without the other is never what anyone wants.
TV_FIELDS = (
    ("show_title", "Show", True),
    ("numbers", "Season / episode #", True),
    ("title", "Episode title", True),
    ("year", "Year (air date)", True),
    ("genre", "Genre", True),
    ("comment", "Description (Comment)", True),
)
_NUMBER_KEYS = ("season_number", "episode_number", "episode_id")


def camera_icon(colour: QColor) -> QIcon:
    """A movie camera — two film reels on a body, lens to the right.

    Drawn rather than taken from the icon theme: Windows has no theme icons
    at all, and the freedesktop names (camera-video, video-x-generic) vary
    from a webcam to a film strip between Linux themes. Dave asked for a
    movie camera. Drawn in the palette's text colour so it sits with the
    other monochrome buttons in light and dark alike.
    """
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(size / 24.0, size / 24.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawEllipse(QRectF(2.5, 2.0, 7.5, 7.5))           # rear reel
        p.drawEllipse(QRectF(9.5, 2.0, 7.5, 7.5))           # front reel
        p.drawRoundedRect(QRectF(2.0, 10.0, 14.5, 9.0), 1.8, 1.8)   # body
        p.drawPolygon(QPolygonF([QPointF(16.0, 13.0), QPointF(22.0, 10.0),
                                 QPointF(22.0, 19.0), QPointF(16.0, 16.0)]))
        # Hub holes, so the reels read as reels and not as two dots.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.drawEllipse(QRectF(5.25, 4.75, 2.0, 2.0))
        p.drawEllipse(QRectF(12.25, 4.75, 2.0, 2.0))
        p.end()
        icon.addPixmap(pm)
    return icon


def _write_all(plans, pictures, options) -> tuple[int, list[str]]:
    """Runs on the worker thread. Returns (files written, failures)."""
    wrote, failures = 0, []
    for track, values in plans:
        try:
            write(track.path, values, pictures=pictures, options=options)
            wrote += 1
        except TagError as exc:
            failures.append(f"{track.path.name}: {exc}")
    return wrote, failures


#: Shown while writing. The first edit of a "fast start" MP4 (index ahead of
#: the video, no spare room — how most downloads and Handbrake output come)
#: has to rewrite the whole file: ~1 minute for 2 GB on a USB disk, measured
#: 2026-11-28. mutagen then leaves ~0.1% spare room, so later edits of that
#: file are instant. Matroska is always edited in place.
WRITING_NOTE = ("Writing… An MP4 is rewritten in full the first time it is "
                "tagged (about a minute for a 2 GB film); later edits are "
                "instant, and MKV files always are.")


class VideoLookupDialog(QDialog):
    def __init__(self, tracks, parent=None, *, tmdb_key: str = "",
                 write_options=None, client: TMDB | None = None):
        super().__init__(parent)
        self.tracks = list(tracks)
        self.write_options = write_options
        self.client = client or TMDB(tmdb_key)
        self.results: list[Video] = []
        self.video: Video | None = None
        self.poster: bytes | None = None
        self.wrote = 0
        self.index = 0                      # movie mode: current file
        #: TV mode: per-file [season, episode] (None when unknown)
        self.numbers: list[list[int | None]] = []

        self._thread: QThread | None = None
        self._job: _Job | None = None
        #: True from Write Tags until its result is handled. A flag, not a
        #: look at the worker thread: the thread is still winding down when
        #: the result arrives, so "is it running?" said yes and the dialog
        #: refused to close after a successful write (2026-11-28).
        self.writing = False

        self.setWindowTitle(f"Get Movie / TV Info from TMDB  "
                            f"[selected files: {len(self.tracks)}]")
        self.resize(980, 640)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_search_page())
        self.pages.addWidget(self._build_movie_page())
        self.pages.addWidget(self._build_tv_page())
        root = QVBoxLayout(self)
        root.addWidget(self.pages)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        first = guess(self.tracks[0].path) if self.tracks else None
        if first is not None and first.kind == "tv":
            self.radio_tv.setChecked(True)
        else:
            self.radio_movie.setChecked(True)
        self._prefill()
        if not self.client.available:
            self.status.setText(KEY_HINT)

    # --- page 0: search ---

    def _build_search_page(self) -> QWidget:
        page = QWidget()
        self.radio_movie = QRadioButton("&Movie")
        self.radio_tv = QRadioButton("&TV show")
        kinds = QButtonGroup(page)
        kinds.addButton(self.radio_movie)
        kinds.addButton(self.radio_tv)
        self.radio_movie.toggled.connect(lambda _: self._prefill())

        self.title_edit = QLineEdit()
        self.title_edit.setMinimumWidth(360)
        self.year_edit = QLineEdit()
        self.year_edit.setMaximumWidth(90)
        self.year_edit.setPlaceholderText("optional")
        self.title_edit.returnPressed.connect(self._search)
        self.year_edit.returnPressed.connect(self._search)
        self.btn_search = QPushButton("&Search!")
        self.btn_search.clicked.connect(self._search)
        self.file_label = QLabel("")

        form = QGridLayout()
        kind_row = QHBoxLayout()
        kind_row.addWidget(self.radio_movie)
        kind_row.addWidget(self.radio_tv)
        kind_row.addStretch(1)
        form.addWidget(QLabel("Look up:"), 0, 0)
        form.addLayout(kind_row, 0, 1)
        form.addWidget(QLabel("Title:"), 1, 0)
        form.addWidget(self.title_edit, 1, 1)
        form.addWidget(QLabel("Year:"), 2, 0)
        form.addWidget(self.year_edit, 2, 1, Qt.AlignmentFlag.AlignLeft)
        form.addWidget(self.btn_search, 1, 2)
        form.addWidget(self.file_label, 3, 1)
        form.setColumnStretch(1, 1)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Title", "Year", "Overview", "Link"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        apply_zebra(self.table)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self._double_clicked)

        self.btn_load = QPushButton("&Load!")
        self.btn_load.setEnabled(False)
        self.btn_load.clicked.connect(self._load)
        self.table.itemSelectionChanged.connect(
            lambda: self.btn_load.setEnabled(bool(self.table.selectedItems())))
        self.btn_skip = QPushButton("S&kip this file")
        self.btn_skip.clicked.connect(self._next_movie)
        cancel = QPushButton("&Close")
        cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_skip)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_load)
        buttons.addWidget(cancel)

        lay = QVBoxLayout(page)
        lay.addLayout(form)
        lay.addWidget(self.table, 1)
        lay.addLayout(buttons)
        return page

    # --- page 1: movie details ---

    def _poster_box(self):
        label = QLabel("no poster")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(170, 250)
        check = QCheckBox("Poster as cover art")
        box = QGroupBox("Poster")
        lay = QVBoxLayout(box)
        lay.addWidget(label, 1)
        lay.addWidget(check)
        return box, label, check

    def _checks(self, spec) -> tuple[QGroupBox, dict[str, QCheckBox]]:
        group = QGroupBox("Save to tags:")
        grid = QGridLayout(group)
        checks = {}
        for i, (key, label, default) in enumerate(spec):
            box = QCheckBox(label)
            box.setChecked(default)
            checks[key] = box
            grid.addWidget(box, i % 3, i // 3)
        return group, checks

    def _build_movie_page(self) -> QWidget:
        page = QWidget()
        self.movie_header = QLabel("")
        self.movie_header.setWordWrap(True)
        self.movie_text = QTextBrowser()
        self.movie_text.setOpenExternalLinks(True)
        fields, self.movie_checks = self._checks(MOVIE_FIELDS)
        art, self.movie_poster_label, self.movie_poster_check = self._poster_box()

        back = QPushButton("← &Back to results")
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        self.btn_write_movie = QPushButton("&Write Tags")
        self.btn_write_movie.clicked.connect(self._write_movie)
        cancel = QPushButton("&Close")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(back)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_write_movie)
        buttons.addWidget(cancel)

        left = QVBoxLayout()
        left.addWidget(self.movie_text, 1)
        left.addWidget(fields)
        body = QHBoxLayout()
        body.addLayout(left, 3)
        body.addWidget(art, 1)

        lay = QVBoxLayout(page)
        lay.addWidget(self.movie_header)
        lay.addLayout(body, 1)
        lay.addLayout(buttons)
        return page

    # --- page 2: TV episodes ---

    def _build_tv_page(self) -> QWidget:
        page = QWidget()
        self.tv_header = QLabel("")
        self.tv_header.setWordWrap(True)
        self.pairs = QTableWidget(0, 4)
        self.pairs.setHorizontalHeaderLabels(
            ["File", "Season", "Episode", "Episode title"])
        apply_zebra(self.pairs)
        header = self.pairs.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.pairs.verticalHeader().setVisible(False)
        self.pairs.itemChanged.connect(self._number_edited)
        hint = QLabel("Season and episode come from the file names; "
                      "double-click a number to correct it.")
        fields, self.tv_checks = self._checks(TV_FIELDS)
        art, self.tv_poster_label, self.tv_poster_check = self._poster_box()

        back = QPushButton("← &Back to results")
        back.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        self.btn_write_tv = QPushButton("&Write Tags")
        self.btn_write_tv.clicked.connect(self._write_tv)
        cancel = QPushButton("&Close")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(back)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_write_tv)
        buttons.addWidget(cancel)

        left = QVBoxLayout()
        left.addWidget(self.pairs, 1)
        left.addWidget(hint)
        left.addWidget(fields)
        body = QHBoxLayout()
        body.addLayout(left, 3)
        body.addWidget(art, 1)

        lay = QVBoxLayout(page)
        lay.addWidget(self.tv_header)
        lay.addLayout(body, 1)
        lay.addLayout(buttons)
        return page

    # --- helpers ---

    @property
    def kind(self) -> str:
        return "tv" if self.radio_tv.isChecked() else "movie"

    def _prefill(self) -> None:
        """Title and year from the current file: its tags, else its name."""
        if not self.tracks:
            return
        if self.kind == "movie":
            track = self.tracks[min(self.index, len(self.tracks) - 1)]
            g = guess(track.path)
            # The file name first: Plex-style "Title (Year)" names are the
            # norm, while embedded titles in downloaded films are often junk
            # (a release group, a scene name, "Hello"). Tags only fill gaps.
            title = g.title or str(track.values.get("title") or "")
            year = g.year or str(track.values.get("year") or "")[:4]
            self.file_label.setText(
                f"File {self.index + 1} of {len(self.tracks)}: {track.path.name}")
            self.btn_skip.setVisible(len(self.tracks) > 1)
        else:
            track = self.tracks[0]
            g = guess(track.path)
            title = g.title or str(track.values.get("show_title") or "")
            year = g.year
            self.file_label.setText(
                f"{len(self.tracks)} episode file(s) — one show for all of them")
            self.btn_skip.setVisible(False)
        self.title_edit.setText(title)
        self.year_edit.setText(year)

    def _busy(self, on: bool, message: str = "") -> None:
        for button in (self.btn_search, self.btn_write_movie, self.btn_write_tv):
            button.setEnabled(not on)
        self.btn_load.setEnabled(not on and bool(self.table.selectedItems()))
        self.status.setText(message)

    def _run(self, fn, on_done, *args) -> None:
        self._stop_thread()
        self._thread = QThread()
        self._job = _Job(fn, *args)
        self._job.moveToThread(self._thread)
        _park(self._thread, self._job)
        self._thread.started.connect(self._job.run)
        self._job.done.connect(on_done)
        self._job.failed.connect(self._failed)
        self._job.done.connect(self._thread.quit)
        self._job.failed.connect(self._thread.quit)
        self._thread.start()

    def _stop_thread(self) -> None:
        if self._job is not None:
            for signal in (self._job.done, self._job.failed):
                try:
                    signal.disconnect()
                except TypeError:
                    pass
        if self._thread is not None:
            self._thread.quit()
        self._thread = self._job = None

    def _failed(self, message: str) -> None:
        self.writing = False
        self._busy(False)
        QMessageBox.warning(self, "TMDB", message)

    # --- search ---

    def _search(self) -> None:
        if not self.client.available:
            QMessageBox.information(self, "TMDB", KEY_HINT)
            return
        title, year = self.title_edit.text(), self.year_edit.text()
        self._busy(True, f"Searching TMDB for “{title.strip()}”…")
        self._run(self.client.search, self._show_results, self.kind, title, year)

    def _show_results(self, videos: list[Video]) -> None:
        self.results = videos
        self.table.setRowCount(len(videos))
        for row, v in enumerate(videos):
            overview = v.overview if len(v.overview) < 140 else v.overview[:137] + "…"
            for col, text in enumerate((v.title, v.year, overview, v.url)):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        for col, cap in ((0, 320), (2, 420)):
            if self.table.columnWidth(col) > cap:
                self.table.setColumnWidth(col, cap)
        if videos:
            self.table.selectRow(0)
        self._busy(False, f"{len(videos)} match(es)." if videos
                   else "Nothing found. Try fewer words, or no year.")

    def _double_clicked(self, row: int, col: int) -> None:
        if col == 3 and 0 <= row < len(self.results):
            QDesktopServices.openUrl(QUrl(self.results[row].url))
        else:
            self._load()

    def _selected(self) -> Video | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        return self.results[rows.pop()] if len(rows) == 1 else None

    def _load(self, *_) -> None:
        video = self._selected()
        if video is None:
            return
        self._busy(True, f"Loading {video.title}…")
        if video.kind == "tv":
            self.numbers = [[g.season, g.episode] for g in
                            (guess(t.path) for t in self.tracks)]
            wanted = [s for s, _ in self.numbers if s is not None]
            self._run(self.client.seasons, self._show_tv, video, wanted)
        else:
            self._run(self.client.details, self._show_movie, video)

    def _fetch_poster(self, path: str, label: QLabel, check: QCheckBox) -> None:
        self.poster = None
        check.setChecked(False)
        check.setEnabled(False)
        label.setPixmap(QPixmap())
        label.setText("looking for a poster…" if path else "no poster")
        if path:
            self._run(self.client.poster,
                      lambda data: self._show_poster(data, label, check), path)

    def _show_poster(self, data: bytes | None, label: QLabel,
                     check: QCheckBox) -> None:
        self._busy(False, self.status.text())
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            label.setText("no poster")
            return
        self.poster = data
        label.setPixmap(pixmap.scaled(
            170, 250, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        check.setEnabled(True)
        check.setChecked(True)

    # --- movie ---

    def _show_movie(self, video: Video) -> None:
        self.video = video
        self._busy(False, "")
        track = self.tracks[self.index]
        self.movie_header.setText(
            f"<b>{video.title}</b> ({video.year or 'year unknown'})  →  "
            f"{track.path.name}   [{self.index + 1} of {len(self.tracks)}]")
        details = [f"<p><b>Genre:</b> {', '.join(video.genres) or '—'}<br>"
                   f"<b>Director:</b> {video.director or '—'}<br>"
                   f"<b>TMDB:</b> <a href='{video.url}'>{video.url}</a></p>",
                   f"<p>{video.overview or '<i>No description on TMDB.</i>'}</p>"]
        self.movie_text.setHtml("".join(details))
        self.pages.setCurrentIndex(1)
        self._fetch_poster(video.poster_path, self.movie_poster_label,
                           self.movie_poster_check)

    def planned_movie(self) -> dict[str, str]:
        if not self.video:
            return {}
        wanted = {k for k, box in self.movie_checks.items() if box.isChecked()}
        return {k: v for k, v in movie_values(self.video).items() if k in wanted}

    def _pictures(self, check: QCheckBox):
        if self.poster and check.isChecked():
            mime = "image/png" if self.poster[:4] == b"\x89PNG" else "image/jpeg"
            return [Picture(self.poster, mime)]
        return None

    def _write_movie(self) -> None:
        values = self.planned_movie()
        pictures = self._pictures(self.movie_poster_check)
        if not values and not pictures:
            QMessageBox.information(self, "TMDB", "Nothing ticked to write.")
            return
        track = self.tracks[self.index]
        self._busy(True, WRITING_NOTE)
        self.writing = True
        self._run(_write_all, self._movie_written, [(track, values)],
                  pictures, self.write_options)

    def _movie_written(self, result) -> None:
        wrote, failures = result
        self.writing = False
        self.wrote += wrote
        self._busy(False)
        if failures:
            QMessageBox.warning(self, "TMDB", "\n".join(failures))
            return
        self._next_movie()

    def _next_movie(self) -> None:
        """On to the next file, searched already; close after the last."""
        self.index += 1
        if self.index >= len(self.tracks):
            self.accept()
            return
        self.video = None
        self.results = []
        self.table.setRowCount(0)
        self.pages.setCurrentIndex(0)
        self._prefill()
        if self.client.available and self.title_edit.text().strip():
            self._search()

    # --- TV ---

    def _show_tv(self, video: Video) -> None:
        self.video = video
        found = sum(1 for s, e in self.numbers
                    if s is not None and e is not None and video.episode(s, e))
        self._busy(False, f"Matched {found} of {len(self.tracks)} file(s) to an "
                   "episode." + ("" if found == len(self.tracks) else
                                 " Fix the numbers for the others, or untick them "
                                 "by clearing their numbers."))
        self.tv_header.setText(
            f"<b>{video.title}</b> ({video.year or '?'})   "
            f"{', '.join(video.genres)}   <a href='{video.url}'>TMDB</a>")
        self.tv_header.setOpenExternalLinks(True)
        self._fill_pairs()
        self.pages.setCurrentIndex(2)
        seasons = {s for s, _ in self.numbers if s is not None}
        poster = video.poster_for(seasons.pop() if len(seasons) == 1 else None)
        self._fetch_poster(poster, self.tv_poster_label, self.tv_poster_check)

    def _fill_pairs(self) -> None:
        self.pairs.blockSignals(True)
        self.pairs.setRowCount(len(self.tracks))
        for row, (track, (s, e)) in enumerate(zip(self.tracks, self.numbers)):
            ep = self._episode_for(row)
            cells = (track.path.name, "" if s is None else str(s),
                     "" if e is None else str(e),
                     ep.title if ep else ("— no match —" if self.video else ""))
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col not in (1, 2):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.pairs.setItem(row, col, item)
        self.pairs.resizeColumnsToContents()
        self.pairs.blockSignals(False)

    def _episode_for(self, row: int) -> Episode | None:
        s, e = self.numbers[row]
        if self.video is None or s is None or e is None:
            return None
        return self.video.episode(s, e)

    def _number_edited(self, item: QTableWidgetItem) -> None:
        col = item.column()
        if col not in (1, 2):
            return
        text = item.text().strip()
        value = int(text) if text.isdigit() else None
        self.numbers[item.row()][col - 1] = value
        season = self.numbers[item.row()][0]
        if (self.video is not None and season is not None
                and season not in self.video.seasons):
            # A season nobody had asked for yet: fetch it, then re-match.
            self._busy(True, f"Loading season {season}…")
            self._run(self.client.season, lambda v: (self._busy(False),
                                                     self._fill_pairs()),
                      self.video, season)
            return
        self._fill_pairs()

    def planned_tv(self) -> list[tuple[object, dict[str, str]]]:
        """(track, values) per file that matched an episode."""
        if not self.video:
            return []
        wanted = {k for k, box in self.tv_checks.items() if box.isChecked()}
        if "numbers" in wanted:
            wanted.update(_NUMBER_KEYS)
        plans = []
        for row, track in enumerate(self.tracks):
            ep = self._episode_for(row)
            if ep is None:
                continue
            values = {k: v for k, v in episode_values(self.video, ep).items()
                      if k in wanted}
            if values:
                plans.append((track, values))
        return plans

    def _write_tv(self) -> None:
        plans = self.planned_tv()
        pictures = self._pictures(self.tv_poster_check)
        if not plans:
            QMessageBox.information(self, "TMDB",
                                    "No file matched an episode — nothing to write.")
            return
        self._busy(True, WRITING_NOTE)
        self.writing = True
        self._run(_write_all, self._tv_written, plans, pictures,
                  self.write_options)

    def _tv_written(self, result) -> None:
        wrote, failures = result
        self.writing = False
        self.wrote += wrote
        self._busy(False)
        if failures:
            QMessageBox.warning(self, "TMDB",
                                "Some files failed:\n" + "\n".join(failures[:12]))
        self.accept()

    def done(self, result: int) -> None:
        if self.writing:
            return              # never abandon a write halfway through a file
        self._stop_thread()
        super().done(result)

    def closeEvent(self, event) -> None:
        if self.writing:
            event.ignore()
            return
        self._stop_thread()
        super().closeEvent(event)
