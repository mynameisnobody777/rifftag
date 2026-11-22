"""Lyrics dialogs: clean them up, or import them from files.

Both follow the house pattern — show the whole plan first, write only when
the user says so. The cleaner adds a real before/after pane because lyrics
are the one field where a bad automated edit is both easy to make and hard
to notice afterwards.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                             QDialogButtonBox, QGridLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                             QPushButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from ..lyrics import (CleanOptions, clean, describe, find_lyrics_file,
                      read_text)
from ..tagio import TagError, WriteOptions, write
from .model import apply_zebra

#: option field -> label, in the order they are shown
OPTION_LABELS = {
    "section_headers": "Section headers — [Verse 1], (Chorus), Chorus:",
    "repeat_markers": "Repeat markers — (x2), [2x], (repeat)",
    "credits": "Credit and copyright lines",
    "timestamps": "LRC timestamps and metadata",
    "html": "HTML left over from a web paste",
    "plain_punctuation": "Smart quotes and dashes → plain",
    "collapse_blanks": "Extra blank lines",
    "trim_space": "Trailing spaces and tabs",
    "fix_shouting": "ALL-CAPS LINES → Sentence case",
}


class _TrackTable(QTableWidget):
    """Three columns, read-only, full-row selection."""

    def __init__(self, headers):
        super().__init__(0, len(headers))
        self.setHorizontalHeaderLabels(headers)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        apply_zebra(self)


class LyricsCleanDialog(QDialog):
    """Strip the scaffolding out of the lyrics on a selection."""

    changed = pyqtSignal(object)         # list[Path] actually written

    def __init__(self, tracks, options: WriteOptions | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clean lyrics for streaming")
        self.resize(1000, 620)
        self.tracks = list(tracks)
        self.write_options = options or WriteOptions()
        self.results: list[tuple[object, str, list]] = []

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Scaffolding comes out, the words stay. Nothing is written until "
            "you press Clean."))

        self.checks: dict[str, QCheckBox] = {}
        box = QGroupBox("Take out")
        grid = QGridLayout(box)
        defaults = CleanOptions()
        for i, field in enumerate(dataclass_fields(CleanOptions)):
            check = QCheckBox(OPTION_LABELS[field.name])
            check.setChecked(bool(getattr(defaults, field.name)))
            check.toggled.connect(self.refresh)
            grid.addWidget(check, i % 5, i // 5)
            self.checks[field.name] = check
        lay.addWidget(box)

        self.table = _TrackTable(["File", "Lyrics", "What would change"])
        self.table.itemSelectionChanged.connect(self._show_preview)

        self.before = QPlainTextEdit()
        self.after = QPlainTextEdit()
        for pane, title in ((self.before, "Before"), (self.after, "After")):
            pane.setReadOnly(True)
            pane.setPlaceholderText(title)
            pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        previews = QWidget()
        row = QHBoxLayout(previews)
        row.setContentsMargins(0, 0, 0, 0)
        for pane, title in ((self.before, "Before"), (self.after, "After")):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            column.addWidget(pane)
            row.addLayout(column)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(previews)
        split.setSizes([260, 300])
        lay.addWidget(split, 1)

        self.status = QLabel("")
        lay.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.btn_clean = QPushButton("Clean && Save")
        self.btn_clean.setDefault(True)
        self.btn_clean.clicked.connect(self._apply)
        buttons.addButton(self.btn_clean,
                          QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        buttons.addButton(self.btn_close,
                          QDialogButtonBox.ButtonRole.RejectRole)
        lay.addWidget(buttons)

        self.refresh()

    # --- the plan ---

    def options(self) -> CleanOptions:
        return CleanOptions(**{name: check.isChecked()
                               for name, check in self.checks.items()})

    def refresh(self) -> None:
        opts = self.options()
        self.results = []
        for track in self.tracks:
            text = str((track.values or {}).get("lyrics", "") or "")
            self.results.append((track, clean(text, opts) if text else "",
                                 describe(text, opts) if text else []))

        self.table.setRowCount(len(self.results))
        for row, (track, cleaned, changes) in enumerate(self.results):
            text = str((track.values or {}).get("lyrics", "") or "")
            note = (", ".join(str(c) for c in changes) if changes
                    else ("already clean" if text else "no lyrics"))
            cells = (track.path.name,
                     f"{len(text)} chars" if text else "—",
                     note)
            for col, value in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)

        todo = len(self.to_write())
        with_lyrics = sum(
            1 for t, _, _ in self.results if (t.values or {}).get("lyrics"))
        self.status.setText(
            f"{with_lyrics} of {len(self.results)} file(s) carry lyrics — "
            f"{todo} would change")
        self.btn_clean.setEnabled(bool(todo))
        if self.table.rowCount() and not self.table.selectedItems():
            self.table.selectRow(0)
        self._show_preview()

    def to_write(self) -> list[tuple[object, str]]:
        """Only files whose lyrics would actually come out different."""
        out = []
        for track, cleaned, changes in self.results:
            text = str((track.values or {}).get("lyrics", "") or "")
            if text and changes and cleaned != text:
                out.append((track, cleaned))
        return out

    def _show_preview(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.before.setPlainText("")
            self.after.setPlainText("")
            return
        track, cleaned, _ = self.results[rows[0].row()]
        text = str((track.values or {}).get("lyrics", "") or "")
        self.before.setPlainText(text)
        self.after.setPlainText(cleaned)

    # --- writing ---

    def _apply(self) -> None:
        written, failures = [], []
        for track, cleaned in self.to_write():
            try:
                write(track.path, {"lyrics": cleaned},
                      options=self.write_options)
            except TagError as exc:
                failures.append(f"{track.path.name}: {exc}")
                continue
            track.values["lyrics"] = cleaned
            written.append(track.path)

        self.refresh()
        self.status.setText(
            f"Cleaned {len(written)} file(s)."
            + (f" {len(failures)} failed: {failures[0]}" if failures else ""))
        if written:
            self.changed.emit(written)


class LyricsImportDialog(QDialog):
    """Pull lyrics in from .txt/.lrc files sitting beside the music."""

    changed = pyqtSignal(object)         # list[Path] actually written

    def __init__(self, tracks, options: WriteOptions | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import lyrics from text files")
        self.resize(860, 520)
        self.tracks = list(tracks)
        self.write_options = options or WriteOptions()
        self.matches: list[tuple[object, object]] = []

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Looks for a .txt or .lrc beside each track, matched on the file "
            "name or the title. An ambiguous match is left alone rather than "
            "guessed."))

        self.clean_on_import = QCheckBox(
            "Clean the lyrics as they come in (recommended for .lrc)")
        self.clean_on_import.setChecked(True)
        lay.addWidget(self.clean_on_import)

        self.overwrite = QCheckBox("Replace lyrics that are already there")
        self.overwrite.setChecked(False)
        self.overwrite.toggled.connect(self.refresh)
        lay.addWidget(self.overwrite)

        self.table = _TrackTable(["File", "Lyrics file", "Note"])
        lay.addWidget(self.table, 1)

        self.status = QLabel("")
        lay.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.btn_import = QPushButton("Import")
        self.btn_import.setDefault(True)
        self.btn_import.clicked.connect(self._apply)
        buttons.addButton(self.btn_import,
                          QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        buttons.addButton(self.btn_close,
                          QDialogButtonBox.ButtonRole.RejectRole)
        lay.addWidget(buttons)

        self.refresh()

    def refresh(self) -> None:
        self.matches = [(track, find_lyrics_file(track))
                        for track in self.tracks]
        self.table.setRowCount(len(self.matches))
        for row, (track, found) in enumerate(self.matches):
            note = ""
            if found is None:
                note = "nothing matched"
            elif (track.values or {}).get("lyrics") and not self.overwrite.isChecked():
                note = "already has lyrics — tick Replace to overwrite"
            cells = (track.path.name, found.name if found else "—", note)
            for col, value in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)

        todo = len(self.to_import())
        self.status.setText(f"{todo} file(s) would get lyrics")
        self.btn_import.setEnabled(bool(todo))

    def to_import(self) -> list[tuple[object, object]]:
        out = []
        for track, found in self.matches:
            if found is None:
                continue
            if (track.values or {}).get("lyrics") and not self.overwrite.isChecked():
                continue
            out.append((track, found))
        return out

    def _apply(self) -> None:
        written, failures = [], []
        for track, source in self.to_import():
            try:
                text = read_text(source)
            except OSError as exc:
                failures.append(f"{source.name}: {exc}")
                continue
            if self.clean_on_import.isChecked():
                text = clean(text)
            try:
                write(track.path, {"lyrics": text},
                      options=self.write_options)
            except TagError as exc:
                failures.append(f"{track.path.name}: {exc}")
                continue
            track.values["lyrics"] = text
            written.append(track.path)

        self.refresh()
        self.status.setText(
            f"Imported into {len(written)} file(s)."
            + (f" {len(failures)} failed: {failures[0]}" if failures else ""))
        if written:
            self.changed.emit(written)
