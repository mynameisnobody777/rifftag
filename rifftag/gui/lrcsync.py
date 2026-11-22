"""Tap-to-sync: time the lyrics against the track as it plays.

Press Space on the beat of each line. The dialog reads the position straight
off the player the main window is already using, so what you hear and what
gets stamped are the same clock — a second player would drift against the
first within a verse.

Everything stays in memory until Save, and Save writes an ordinary ``.lrc``
beside the track (optionally also dropping the timed text into the lyrics
tag, which some players read).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                             QDialogButtonBox, QHBoxLayout, QHeaderView,
                             QLabel, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout)

from .. import lrc
from ..lrc import Line, build, format_stamp, metadata_for, parse, split_for_sync
from ..tagio import TagError, WriteOptions, write
from .model import _blend, apply_zebra
from .player import clock, track_label

#: how often the elapsed clock and the playing marker refresh
TICK_MS = 100
#: one nudge of the offset buttons
NUDGE_MS = 500
#: extra pixels in the line-number column for the ▶ marker
MARKER_ROOM = 24


class LrcSyncDialog(QDialog):
    """Space on the beat, line by line."""

    saved = pyqtSignal(object)           # Path of the .lrc written

    def __init__(self, track, player, options: WriteOptions | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sync lyrics to the music")
        self.resize(760, 640)
        self.track = track
        self.player = player
        self.write_options = options or WriteOptions()
        self.cursor = 0

        source = str((track.values or {}).get("lyrics", "") or "")
        existing = lrc.default_path(track)
        if existing.exists():
            # Editing a sync you already started beats starting it again.
            self.lines, self.meta = parse(
                existing.read_text(encoding="utf-8", errors="replace"))
        elif source:
            self.lines, self.meta = parse(source)
            if not any(line.timed for line in self.lines):
                self.lines = split_for_sync(source)
        else:
            self.lines, self.meta = [], {}
        if not self.meta:
            self.meta = metadata_for(track)

        lay = QVBoxLayout(self)
        self.heading = QLabel(track_label(track))
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        lay.addWidget(self.heading)

        self.clock_label = QLabel("0:00")
        lay.addWidget(self.clock_label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Time", "Line"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        # No focus: Space must always reach the Tap shortcut, never get eaten
        # by the table's own selection handling.
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        apply_zebra(self.table)
        self.table.cellDoubleClicked.connect(self._cursor_to_row)
        lay.addWidget(self.table, 1)

        lay.addLayout(self._build_tap_row())
        lay.addLayout(self._build_transport_row())

        self.also_tag = QCheckBox(
            "Also store the timed text in the lyrics tag")
        self.also_tag.setToolTip(
            "Some players read synced lyrics straight out of the tag. The "
            ".lrc file beside the track is what most of them use.")
        lay.addWidget(self.also_tag)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.btn_save = QPushButton("Save .lrc")
        self.btn_save.clicked.connect(self._save)
        buttons.addButton(self.btn_save,
                          QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        buttons.addButton(self.btn_close,
                          QDialogButtonBox.ButtonRole.RejectRole)
        lay.addWidget(buttons)

        for key, slot in ((Qt.Key.Key_Space, self.tap),
                          (Qt.Key.Key_Return, self.tap),
                          (Qt.Key.Key_Enter, self.tap),
                          (Qt.Key.Key_Backspace, self.back)):
            QShortcut(QKeySequence(key), self, slot)

        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self.refresh()
        self._start_track()

    # --- construction ---

    def _build_tap_row(self):
        row = QHBoxLayout()
        self.btn_tap = QPushButton("Tap  (Space)")
        self.btn_tap.setToolTip(
            "Stamp the highlighted line with the position you are hearing, "
            "then move to the next one")
        self.btn_tap.setMinimumHeight(40)
        self.btn_tap.clicked.connect(self.tap)
        self.btn_tap.setDefault(True)
        row.addWidget(self.btn_tap, 2)

        self.btn_back = QPushButton("Back  (Backspace)")
        self.btn_back.setToolTip("Undo the last tap and try that line again")
        self.btn_back.clicked.connect(self.back)
        row.addWidget(self.btn_back, 1)

        self.btn_clear = QPushButton("Clear all")
        self.btn_clear.clicked.connect(self.clear_all)
        row.addWidget(self.btn_clear, 1)
        return row

    def _build_transport_row(self):
        row = QHBoxLayout()
        self.btn_play = QPushButton("Play / Pause")
        self.btn_play.clicked.connect(self._toggle_play)
        row.addWidget(self.btn_play)

        self.btn_restart = QPushButton("Back to the start")
        self.btn_restart.clicked.connect(self._restart)
        row.addWidget(self.btn_restart)

        row.addStretch(1)
        row.addWidget(QLabel("Shift every stamp:"))
        self.btn_earlier = QPushButton(f"−{NUDGE_MS / 1000:.1f}s")
        self.btn_earlier.setToolTip("Everything is landing late")
        self.btn_earlier.clicked.connect(lambda: self.nudge(-NUDGE_MS))
        self.btn_later = QPushButton(f"+{NUDGE_MS / 1000:.1f}s")
        self.btn_later.setToolTip("Everything is landing early")
        self.btn_later.clicked.connect(lambda: self.nudge(NUDGE_MS))
        row.addWidget(self.btn_earlier)
        row.addWidget(self.btn_later)
        return row

    # --- playback ---

    def _start_track(self) -> None:
        if not self._playable():
            self.status.setText(
                "In-app playback is not available, so there is no clock to "
                "tap against. Install python3-pyqt6.qtmultimedia.")
            for button in (self.btn_tap, self.btn_back, self.btn_play,
                           self.btn_restart):
                button.setEnabled(False)
            return
        if self.player.current() is not self.track:
            self.player.play_tracks([self.track])

    def _playable(self) -> bool:
        from . import player as player_mod
        return bool(player_mod.AVAILABLE and self.player is not None)

    def position(self) -> int:
        return self.player.position_ms() if self.player is not None else 0

    def _toggle_play(self) -> None:
        if self.player is not None:
            self.player.toggle_play()

    def _restart(self) -> None:
        if self.player is not None and self.player._player is not None:
            self.player._player.setPosition(0)

    def _tick(self) -> None:
        self.clock_label.setText(clock(self.position()))
        self._mark_playing()

    # --- tapping ---

    def tap(self) -> None:
        """Stamp the line under the cursor and step to the next one."""
        if not self.lines or self.cursor >= len(self.lines):
            return
        if not self._playable():
            return
        self.lines[self.cursor] = Line(self.lines[self.cursor].text,
                                       self.position())
        self.cursor = min(self.cursor + 1, len(self.lines))
        self.refresh()

    def back(self) -> None:
        """Step back and un-stamp, for the line you were half a beat late on."""
        if self.cursor == 0:
            return
        self.cursor -= 1
        self.lines[self.cursor] = Line(self.lines[self.cursor].text, None)
        self.refresh()

    def clear_all(self) -> None:
        self.lines = [Line(line.text, None) for line in self.lines]
        self.cursor = 0
        self.refresh()

    def nudge(self, delta_ms: int) -> None:
        self.lines = lrc.shift(self.lines, delta_ms)
        self.refresh()
        self.status.setText(
            f"Every stamp moved {delta_ms / 1000:+.1f}s.")

    def _cursor_to_row(self, row: int, _column: int = 0) -> None:
        """Double-click a line to carry on from there."""
        if 0 <= row < len(self.lines):
            self.cursor = row
            self.refresh()

    # --- display ---

    def refresh(self) -> None:
        self.table.setRowCount(len(self.lines))
        base = self.palette().color(QPalette.ColorRole.Base)
        waiting = QBrush(_blend(base, QColor(70, 150, 225), 0.26))

        for row, line in enumerate(self.lines):
            cells = (str(row + 1),
                     format_stamp(line.ms) if line.timed else "",
                     line.text or "⏸ (gap — clears the screen)")
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if row == self.cursor:
                    item.setBackground(waiting)
                self.table.setItem(row, col, item)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
        # Room for the ▶ the playing marker prepends. Sizing to contents
        # while the rows read "5" left "▶ 5" elided to an ellipsis.
        self.table.setColumnWidth(0, self.table.columnWidth(0) + MARKER_ROOM)
        if 0 <= self.cursor < len(self.lines):
            self.table.scrollToItem(
                self.table.item(self.cursor, 0),
                QAbstractItemView.ScrollHint.PositionAtCenter)

        timed = sum(1 for line in self.lines if line.timed)
        self.btn_save.setEnabled(bool(timed))
        if not self.lines:
            self.status.setText(
                "This track has no lyrics yet — Lyrics → Import lyrics from "
                "text files, or paste them in the Tag Editor first.")
        else:
            self.status.setText(f"{timed} of {len(self.lines)} line(s) timed.")

    def _mark_playing(self) -> None:
        """Dim marker on the line the player is currently inside."""
        now = self.position()
        playing = None
        for row, line in enumerate(self.lines):
            if line.timed and line.ms <= now:
                playing = row
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            item.setText(f"▶ {row + 1}" if row == playing else str(row + 1))

    # --- saving ---

    def lrc_text(self) -> str:
        return build(self.lines, self.meta)

    def _save(self) -> None:
        path = lrc.default_path(self.track)
        text = self.lrc_text()
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self.status.setText(f"Could not write {path.name}: {exc}")
            return
        message = f"Saved {path.name} beside the track."
        if self.also_tag.isChecked():
            try:
                write(self.track.path, {"lyrics": text},
                      options=self.write_options)
                self.track.values["lyrics"] = text
                message += " Timed text also written to the lyrics tag."
            except TagError as exc:
                message += f" Tag write failed: {exc}"
        self.status.setText(message)
        self.saved.emit(path)

    def reject(self) -> None:
        self.timer.stop()
        super().reject()

    def closeEvent(self, event) -> None:
        self.timer.stop()
        super().closeEvent(event)
