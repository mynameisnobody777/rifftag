"""Tools → Embed subtitles into MKV.

Lists every selected Matroska file with the sidecar subtitles found beside it
(``Movie (Year).eng.srt`` and friends), what is already inside, and then
remuxes them in one by one on a worker thread with a progress bar. The engine
and its safety rules are in :mod:`rifftag.subtitles`; this is only the window.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                             QDialog, QHBoxLayout, QHeaderView, QLabel,
                             QMessageBox, QProgressBar, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import matroska, subtitles
from .model import apply_zebra


class _Worker(QObject):
    file_started = pyqtSignal(int)
    file_progress = pyqtSignal(int, int)          # row, percent
    file_done = pyqtSignal(int, str, bool)        # row, message, ok
    finished = pyqtSignal()

    def __init__(self, jobs, delete_sidecars: bool):
        super().__init__()
        self.jobs = jobs                          # [(row, Plan)]
        self.delete_sidecars = delete_sidecars
        self.stop = False

    def run(self) -> None:
        for row, plan in self.jobs:
            if self.stop:
                break
            self.file_started.emit(row)
            try:
                n = subtitles.embed(
                    plan, delete_sidecars=self.delete_sidecars,
                    progress=lambda pct, r=row: self.file_progress.emit(r, pct))
                self.file_done.emit(row, f"Added {n} track(s)", True)
            except (subtitles.EmbedError, matroska.MatroskaError,
                    OSError) as exc:
                self.file_done.emit(row, f"Failed: {exc}", False)
            except Exception as exc:                  # never kill the thread
                self.file_done.emit(row, f"Failed: {type(exc).__name__}: {exc}",
                                    False)
        self.finished.emit()


class SubtitleEmbedDialog(QDialog):
    def __init__(self, tracks, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Embed Subtitles into MKV")
        self.resize(940, 520)
        self.embedded = 0
        self.running = False
        self._thread: QThread | None = None
        self._worker: _Worker | None = None

        mkv = [t for t in tracks
               if t.path.suffix.lower() in (".mkv", ".mka", ".webm")]
        skipped = len(tracks) - len(mkv)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.plans = [subtitles.plan(t.path) for t in mkv]
        finally:
            QApplication.restoreOverrideCursor()

        intro = QLabel(
            "Subtitle files named after a film — <code>Movie (Year).srt</code>, "
            "<code>Movie (Year).eng.srt</code>, <code>….en.forced.srt</code> — "
            "are added to it as subtitle tracks. The MKV is rebuilt into a "
            "temporary copy beside it (no re-encoding; about a minute for "
            "2 GB on a USB disk), checked, and only then swapped in.")
        intro.setWordWrap(True)

        self.table = QTableWidget(len(self.plans), 4)
        self.table.setHorizontalHeaderLabels(
            ["File", "Will add", "Already inside", "Status"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        apply_zebra(self.table)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for row, plan in enumerate(self.plans):
            status = (plan.error or ("Ready" if plan.add else
                                     ("Nothing new" if plan.already
                                      else "No subtitle files found")))
            for col, text in enumerate((
                    plan.video.name,
                    ", ".join(s.label for s in plan.add),
                    ", ".join(s.label for s in plan.already), status)):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        if self.table.columnWidth(0) > 380:
            self.table.setColumnWidth(0, 380)

        self.delete_check = QCheckBox(
            "Delete the subtitle files once they are safely inside")
        self.delete_check.setToolTip(
            "Off by default. With both, Plex and Jellyfin list every language "
            "twice; the files are only deleted after the new MKV has been "
            "checked and swapped in.")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        work = sum(1 for p in self.plans if p.add)
        self.btn_go = QPushButton(f"&Embed into {work} file(s)")
        self.btn_go.setEnabled(work > 0)
        self.btn_go.clicked.connect(self._start)
        self.btn_stop = QPushButton("&Stop after this file")
        self.btn_stop.setVisible(False)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_close = QPushButton("&Close")
        self.btn_close.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self.delete_check)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_go)
        buttons.addWidget(self.btn_stop)
        buttons.addWidget(self.btn_close)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addLayout(buttons)

        if not self.plans:
            self.status.setText("None of the selected files is an MKV. "
                                "(MP4s keep their .srt beside them — Plex and "
                                "players read it there.)")
        elif skipped:
            self.status.setText(f"{skipped} non-MKV file(s) left out.")
        if not matroska.available():
            self.status.setText(matroska.INSTALL_HINT)
            self.btn_go.setEnabled(False)

    def _set_status(self, row: int, text: str) -> None:
        self.table.item(row, 3).setText(text)

    def _start(self) -> None:
        jobs = [(row, p) for row, p in enumerate(self.plans) if p.add]
        if self.delete_check.isChecked():
            answer = QMessageBox.question(
                self, "Embed subtitles",
                "Delete the subtitle files after they are embedded?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.running = True
        self.btn_go.setEnabled(False)
        self.delete_check.setEnabled(False)
        self.btn_close.setEnabled(False)
        self.btn_stop.setVisible(True)
        self.progress.setVisible(True)
        self._total, self._done = len(jobs), 0

        self._thread = QThread()
        self._worker = _Worker(jobs, self.delete_check.isChecked())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.file_started.connect(self._on_started)
        self._worker.file_progress.connect(self._on_progress)
        self._worker.file_done.connect(self._on_done)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.stop = True
            self.btn_stop.setEnabled(False)
            self.status.setText("Stopping after the current file…")

    def _on_started(self, row: int) -> None:
        self._set_status(row, "Embedding…")
        self.progress.setValue(0)
        self.status.setText(f"File {self._done + 1} of {self._total}: "
                            f"{self.plans[row].video.name}")

    def _on_progress(self, row: int, pct: int) -> None:
        self.progress.setValue(pct)
        self._set_status(row, f"Embedding… {pct}%")

    def _on_done(self, row: int, message: str, ok: bool) -> None:
        self._done += 1
        if ok:
            self.embedded += 1
        self._set_status(row, message)

    def _on_finished(self) -> None:
        self.running = False
        self.btn_close.setEnabled(True)
        self.btn_stop.setVisible(False)
        self.progress.setVisible(False)
        self.status.setText(f"Done: subtitles embedded into {self.embedded} "
                            f"of {self._total} file(s).")
        if self._thread is not None:
            self._thread.wait(2000)

    def done(self, result: int) -> None:
        if self.running:
            return                  # never abandon a remux halfway
        super().done(result)

    def closeEvent(self, event) -> None:
        if self.running:
            event.ignore()
            return
        super().closeEvent(event)
