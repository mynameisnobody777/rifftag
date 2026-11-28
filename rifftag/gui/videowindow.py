"""The picture for video files, in a window of its own.

The player block in the corner is ~300 px wide — fine for a lockup and a seek
bar, no good for a film. So when the current file has video, this window
opens (where it was last left), and it hides again for audio. The corner
panel stays the remote; this window carries its own small bar too, so a film
can be watched full screen without reaching back to the main window.

Keys: Space play/pause, F or double-click full screen, Esc leave full screen,
←/→ 10 s, ↑/↓ 1 min. Closing the window stops playback.

Subtitles: embedded tracks (see Tools → Embed subtitles into MKV) are listed
in the Subtitles box; Qt Multimedia renders the chosen one on the picture.
Added 2026-11-28 at Dave's request ("the player is gonna have to double for
video too").
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPalette
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QSlider,
                             QToolButton, QVBoxLayout, QWidget)

try:                                        # pragma: no cover - import guard
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    AVAILABLE = True
except ImportError:                         # pragma: no cover - import guard
    AVAILABLE = False


def _clock(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _Video(QVideoWidget if AVAILABLE else QWidget):   # type: ignore[misc]
    """The picture; double-click toggles full screen."""

    double_clicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()


class VideoWindow(QWidget):
    """Top-level window showing the player's video output."""

    #: the user closed the window: stop playback
    closed = pyqtSignal()
    toggle_play = pyqtSignal()
    seek_by = pyqtSignal(int)                 # milliseconds, +/-
    seek_to = pyqtSignal(int)                 # absolute milliseconds
    subtitle_chosen = pyqtSignal(int)         # track index, -1 for none

    def __init__(self, parent=None, settings=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.settings = settings
        self.setWindowTitle("RiffTag — video")
        self.resize(960, 560)
        self._seeking = False
        self._shown_once = False

        self.video = _Video()
        pal = self.video.palette()
        pal.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.black)
        self.video.setPalette(pal)
        self.video.setAutoFillBackground(True)
        self.video.double_clicked.connect(self.toggle_fullscreen)

        self.btn_play = QToolButton()
        self.btn_play.setText("⏯")
        self.btn_play.setToolTip("Play / pause   (Space)")
        self.btn_play.clicked.connect(self.toggle_play.emit)
        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.sliderPressed.connect(self._seek_start)
        self.position.sliderReleased.connect(self._seek_end)
        self.time = QLabel("0:00 / 0:00")
        self.subs = QComboBox()
        self.subs.setToolTip("Subtitles")
        self.subs.addItem("Subtitles: off", -1)
        self.subs.setEnabled(False)
        self.subs.activated.connect(
            lambda i: self.subtitle_chosen.emit(self.subs.itemData(i)))
        self.btn_full = QToolButton()
        self.btn_full.setText("⛶")
        self.btn_full.setToolTip("Full screen   (F, or double-click)")
        self.btn_full.clicked.connect(self.toggle_fullscreen)

        self.bar = QWidget()
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(6, 4, 6, 4)
        row.addWidget(self.btn_play)
        row.addWidget(self.position, 1)
        row.addWidget(self.time)
        row.addWidget(self.subs)
        row.addWidget(self.btn_full)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.video, 1)
        lay.addWidget(self.bar)

        # In full screen the bar hides, and comes back while the mouse moves.
        self._hide_bar = QTimer(self)
        self._hide_bar.setSingleShot(True)
        self._hide_bar.setInterval(2500)
        self._hide_bar.timeout.connect(
            lambda: self.bar.setVisible(not self.isFullScreen()))
        self.setMouseTracking(True)
        self.video.setMouseTracking(True)
        # The picture widget swallows mouse moves, so watch it from here.
        self.video.installEventFilter(self)

    # --- what the player tells us ---

    def set_title(self, text: str) -> None:
        self.setWindowTitle(f"{text} — RiffTag")

    def set_duration(self, ms: int) -> None:
        self.position.setRange(0, max(0, ms))
        self._update_time(self.position.value())

    def set_position(self, ms: int) -> None:
        if not self._seeking:
            self.position.setValue(ms)
        self._update_time(ms)

    def set_subtitles(self, names: list[str], active: int = -1) -> None:
        self.subs.clear()
        self.subs.addItem("Subtitles: off", -1)
        for i, name in enumerate(names):
            self.subs.addItem(name or f"Track {i + 1}", i)
        self.subs.setEnabled(bool(names))
        self.subs.setCurrentIndex(active + 1 if 0 <= active < len(names) else 0)

    def _update_time(self, ms: int) -> None:
        self.time.setText(f"{_clock(ms)} / {_clock(self.position.maximum())}")

    def _seek_start(self) -> None:
        self._seeking = True

    def _seek_end(self) -> None:
        self._seeking = False
        self.seek_to.emit(self.position.value())

    # --- showing / hiding ---

    def show_video(self) -> None:
        if not self._shown_once and self.settings is not None:
            geometry = self.settings.value("video_window_geometry")
            if isinstance(geometry, QByteArray) and not geometry.isEmpty():
                self.restoreGeometry(geometry)
            self._shown_once = True
        if not self.isVisible():
            self.show()
        self.raise_()

    def remember(self) -> None:
        if self.settings is not None and not self.isFullScreen():
            self.settings.setValue("video_window_geometry", self.saveGeometry())

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.bar.setVisible(True)
        else:
            self.remember()
            self.showFullScreen()
            self._hide_bar.start()

    # --- input ---

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.toggle_play.emit()
        elif key == Qt.Key.Key_F:
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.seek_by.emit(10_000 if key == Qt.Key.Key_Right else -10_000)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.seek_by.emit(60_000 if key == Qt.Key.Key_Up else -60_000)
        elif event.matches(QKeySequence.StandardKey.Close):
            self.close()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:
        from PyQt6.QtCore import QEvent
        if (obj is self.video and event.type() == QEvent.Type.MouseMove
                and self.isFullScreen()):
            self.bar.setVisible(True)
            self._hide_bar.start()
        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:
        if self.isFullScreen():
            self.showNormal()
        self.remember()
        super().closeEvent(event)
        self.closed.emit()
