"""The Release Check dialog.

Reads the release, says what a distributor would send back, and names the fix
for each thing. Loudness is optional because it decodes every file — one
click, on a worker thread, with the rest of the report already on screen.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPalette
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                             QDialog, QDialogButtonBox, QHBoxLayout,
                             QHeaderView, QLabel, QProgressBar, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..preflight import (ERROR, NOTE, WARNING, Report, check, ffmpeg_available,
                         measure_all)
from .model import _blend, apply_zebra

#: level -> (column text, hue). The hue is blended into the theme's own Base,
#: never used raw — the same rule as the file list's row tints.
LEVELS = {
    ERROR: ("Problem", QColor(225, 70, 80)),
    WARNING: ("Warning", QColor(235, 165, 30)),
    NOTE: ("Note", QColor(70, 150, 225)),
}
ORDER = (ERROR, WARNING, NOTE)
TINT = 0.22


class LoudnessWorker(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)        # {path: Loudness}

    def __init__(self, tracks):
        super().__init__()
        self.tracks = tracks
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        out = measure_all(self.tracks,
                          progress=lambda d, t: self.progress.emit(d, t),
                          should_stop=lambda: self._stop)
        self.finished.emit(out)


class PreflightDialog(QDialog):
    """What would bounce, and what to do about it."""

    def __init__(self, tracks, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Release Check")
        self.resize(1000, 620)
        self.tracks = list(tracks)
        self.loudness: dict = {}
        self.report = Report()
        self._thread: QThread | None = None
        self._worker: LoudnessWorker | None = None

        lay = QVBoxLayout(self)

        self.headline = QLabel()
        font = self.headline.font()
        font.setPointSize(max(11, font.pointSize() + 3))
        font.setBold(True)
        self.headline.setFont(font)
        lay.addWidget(self.headline)

        self.subhead = QLabel()
        self.subhead.setWordWrap(True)
        lay.addWidget(self.subhead)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["", "File", "What", "What to do"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        apply_zebra(self.table)
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.measure = QCheckBox("Also measure loudness (decodes every file)")
        self.measure.setToolTip(
            "Integrated LUFS and true peak, so you can see what will clip "
            "once a store transcodes it.")
        self.measure.setEnabled(ffmpeg_available())
        if not ffmpeg_available():
            self.measure.setToolTip("ffmpeg is not installed")
        self.measure.toggled.connect(self._measure_toggled)
        row.addWidget(self.measure)
        self.progress = QProgressBar()
        self.progress.hide()
        row.addWidget(self.progress, 1)
        lay.addLayout(row)

        buttons = QDialogButtonBox()
        self.btn_recheck = QPushButton("Check again")
        self.btn_recheck.clicked.connect(self.refresh)
        buttons.addButton(self.btn_recheck,
                          QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_copy = QPushButton("Copy report")
        self.btn_copy.setToolTip("Put the whole report on the clipboard")
        self.btn_copy.clicked.connect(self._copy)
        buttons.addButton(self.btn_copy,
                          QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_close = QPushButton("Close")
        self.btn_close.setDefault(True)
        self.btn_close.clicked.connect(self.reject)
        buttons.addButton(self.btn_close,
                          QDialogButtonBox.ButtonRole.RejectRole)
        lay.addWidget(buttons)

        self.refresh()

    # --- the report ---

    def refresh(self) -> None:
        self.report = check(self.tracks, loudness=self.loudness)
        findings = sorted(self.report.findings,
                          key=lambda f: (ORDER.index(f.level), f.where))

        base = self.palette().color(QPalette.ColorRole.Base)
        self.table.setRowCount(len(findings))
        for row, finding in enumerate(findings):
            label, hue = LEVELS[finding.level]
            tint = QBrush(_blend(base, hue, TINT))
            cells = (label, finding.where, finding.message, finding.fix)
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 0:
                    item.setBackground(tint)
                self.table.setItem(row, col, item)
        for col in (0, 1):
            self.table.resizeColumnToContents(col)
        self.table.setColumnWidth(2, 420)

        errors, warnings = self.report.of(ERROR), self.report.of(WARNING)
        if not findings:
            self.headline.setText("Ready to upload")
            self.subhead.setText(
                f"{len(self.tracks)} file(s) checked. Nothing to fix.")
        elif errors:
            self.headline.setText(self.report.summary)
            self.subhead.setText(
                "Problems will be rejected or are plainly wrong. Warnings "
                "will probably hurt you. Notes are your call.")
        else:
            self.headline.setText(self.report.summary)
            self.subhead.setText(
                "Nothing that would be rejected outright"
                + (" — the warnings are worth a look." if warnings else "."))

    def as_text(self) -> str:
        lines = [f"RiffTag Release Check — {self.report.summary}", ""]
        for level in ORDER:
            group = self.report.of(level)
            if not group:
                continue
            lines.append(f"{LEVELS[level][0].upper()}S"
                         if level != NOTE else "NOTES")
            for finding in group:
                lines.append(f"  [{finding.where}] {finding.message}")
                if finding.fix:
                    lines.append(f"      -> {finding.fix}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _copy(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.as_text())
        self.subhead.setText("Report copied to the clipboard.")

    # --- loudness ---

    def _measure_toggled(self, on: bool) -> None:
        if not on or self._thread is not None or not self.tracks:
            return
        self._set_busy(True)
        self.progress.setMaximum(len(self.tracks))
        self.progress.setValue(0)
        self.progress.show()

        self._thread = QThread(self)
        self._worker = LoudnessWorker(self.tracks)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._progress)
        self._worker.finished.connect(self._measured)
        self._thread.start()

    def _progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.subhead.setText(f"Measuring loudness… {done}/{total}")

    def _measured(self, loudness) -> None:
        self.loudness = loudness
        self._stop_thread()
        self.progress.hide()
        self._set_busy(False)
        self.refresh()

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.measure, self.btn_recheck, self.btn_copy):
            widget.setEnabled(not busy)
        if not busy:
            self.measure.setEnabled(ffmpeg_available())

    def _stop_thread(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = self._worker = None

    def closeEvent(self, event) -> None:
        self._stop_thread()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_thread()
        super().reject()
