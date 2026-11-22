"""The Convert audio format dialog.

Shows the full before/after table before anything is encoded — the same
contract as the rename preview — then runs ffmpeg on a worker thread so a
thirty-track album does not freeze the window.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                             QDialogButtonBox, QFormLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QProgressBar,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)

from ..convert import FORMATS, Format, ffmpeg_available, plan, run, to_convert
from ..tagio import WriteOptions
from .model import apply_zebra


class ConvertWorker(QObject):
    """Runs the batch off the GUI thread."""

    progress = pyqtSignal(int, int)
    finished = pyqtSignal(object)        # list[(Step, error)]

    def __init__(self, steps, fmt, quality, options):
        super().__init__()
        self.steps, self.fmt, self.quality = steps, fmt, quality
        self.options = options
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        failures = run(self.steps, self.fmt, self.quality,
                       options=self.options,
                       progress=lambda d, t: self.progress.emit(d, t),
                       should_stop=lambda: self._stop)
        self.finished.emit(failures)


class ConvertDialog(QDialog):
    """Pick a format, check the table, convert."""

    converted = pyqtSignal()             # something was written to disk

    def __init__(self, tracks, options: WriteOptions | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Convert audio format")
        self.resize(820, 560)
        self.tracks = list(tracks)
        self.options = options or WriteOptions()
        self.steps: list = []
        self._thread: QThread | None = None
        self._worker: ConvertWorker | None = None
        self._subfolder_touched = False
        self._wrote_anything = False

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            f"{len(self.tracks)} file(s) selected. Originals are never "
            "touched — the converted files are written into a subfolder."))

        form = QFormLayout()
        self.format = QComboBox()
        for fmt in FORMATS:
            self.format.addItem(fmt.label, fmt)
        self.format.currentIndexChanged.connect(self._format_changed)
        self.quality = QComboBox()
        self.subfolder = QLineEdit()
        self.subfolder.setToolTip(
            "Created inside each source folder. Album/MP3/track.mp3")
        self.subfolder.textEdited.connect(self._subfolder_edited)
        form.addRow("Convert to:", self.format)
        form.addRow("Quality:", self.quality)
        form.addRow("Into subfolder:", self.subfolder)
        lay.addLayout(form)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File", "Will become", "Note"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        apply_zebra(self.table)
        lay.addWidget(self.table, 1)

        self.status = QLabel("")
        self.progress = QProgressBar()
        self.progress.hide()
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(self.progress)
        lay.addLayout(row)

        self.buttons = QDialogButtonBox()
        self.btn_convert = QPushButton("Convert")
        self.btn_convert.setDefault(True)
        self.btn_convert.clicked.connect(self._start)
        self.buttons.addButton(self.btn_convert,
                               QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        self.buttons.addButton(self.btn_close,
                               QDialogButtonBox.ButtonRole.RejectRole)
        lay.addWidget(self.buttons)

        self._format_changed()
        if not ffmpeg_available():
            self.btn_convert.setEnabled(False)
            self.status.setText(
                "ffmpeg is not installed — sudo apt install ffmpeg")

    # --- the preview table ---

    def current_format(self) -> Format:
        return self.format.currentData()

    def current_quality(self):
        fmt = self.current_format()
        return fmt.quality(self.quality.currentData() or "")

    def _format_changed(self, *_) -> None:
        fmt = self.current_format()
        self.quality.clear()
        for quality in fmt.qualities:
            self.quality.addItem(quality.label, quality.key)
        if not self._subfolder_touched:
            self.subfolder.setText(fmt.subfolder)
        self.refresh()

    def _subfolder_edited(self, *_) -> None:
        self._subfolder_touched = True
        self.refresh()

    def refresh(self, status: bool = True) -> None:
        """Re-plan and redraw the table.

        ``status=False`` leaves the status line and the Note column alone,
        which is what a finished run needs: the plan has to be recomputed so
        the button and the "destination exists" notes are right, but the
        summary and the per-file errors must survive it.
        """
        self.steps = plan(self.tracks, self.current_format(),
                          self.subfolder.text())
        self.table.setRowCount(len(self.steps))
        for row, step in enumerate(self.steps):
            # The destination is always inside the source folder, so the
            # subfolder plus the new name is the whole story — the absolute
            # path only pushed the Note column off the right-hand edge.
            becomes = ("" if step.skipped
                       else f"{step.dst.parent.name}/{step.dst.name}")
            for col, text in enumerate((step.src.name, becomes, step.note)):
                item = QTableWidgetItem(text)
                if col == 1 and text:
                    item.setToolTip(str(step.dst))
                self.table.setItem(row, col, item)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
        todo = len(to_convert(self.steps))
        skipped = len(self.steps) - todo
        if status:
            self.status.setText(
                f"{todo} to convert"
                + (f", {skipped} skipped" if skipped else ""))
        self.btn_convert.setEnabled(bool(todo) and ffmpeg_available())

    # --- running ---

    def _start(self) -> None:
        work = to_convert(self.steps)
        if not work:
            return
        self._set_busy(True)
        self.progress.setMaximum(len(work))
        self.progress.setValue(0)
        self.progress.show()

        self._thread = QThread(self)
        self._worker = ConvertWorker(self.steps, self.current_format(),
                                     self.current_quality(), self.options)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._progress)
        self._worker.finished.connect(self._done)
        self._thread.start()

    def _progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.status.setText(f"Converting… {done}/{total}")

    def _done(self, failures) -> None:
        self._stop_thread()
        self.progress.hide()
        self._set_busy(False)
        self._wrote_anything = True
        self.converted.emit()
        total = len(to_convert(self.steps))
        attempted = {id(step) for step, _ in failures}
        rows = {id(step): row for row, step in enumerate(self.steps)}

        # Re-plan first: the destinations now exist, so a second Convert must
        # skip them. status=False keeps the summary this method is about to
        # write — an earlier version called plain refresh() here and the
        # result report vanished the instant it appeared.
        self.refresh(status=False)
        if not failures:
            self.status.setText(f"Done — {total} file(s) converted.")
            return
        self.status.setText(
            f"Done — {total - len(failures)} converted, "
            f"{len(failures)} failed.")
        for step, error in failures:
            row = rows.get(id(step))
            if row is not None and id(step) in attempted:
                self.table.setItem(row, 2, QTableWidgetItem(error))

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.format, self.quality, self.subfolder,
                       self.btn_convert):
            widget.setEnabled(not busy)
        self.btn_close.setText("Stop" if busy else "Close")

    def _stop_thread(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = self._worker = None

    def reject(self) -> None:
        """Close, or stop a run in progress — the button does both."""
        if self._thread is not None:
            self._worker.stop()
            self.status.setText("Stopping after the current file…")
            return
        super().reject()

    def closeEvent(self, event) -> None:
        self._stop_thread()
        super().closeEvent(event)
