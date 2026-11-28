"""The logo-and-transport block that fills the top-left corner.

Tag&Rename gives its whole top strip to the tag block; here the tabs stay
where they are and this square takes the corner beside them, which is what
lets the folder tree start level with the file list instead of running the
full height of the window.

Playback is real and in-process — ``QMediaPlayer`` from Qt Multimedia, so
stop, skip, seek and volume actually mean something. Handing the file to the
desktop's player (what the toolbar's Play button used to do, and still does
when Qt Multimedia is missing) can start a track but can never control it.

Qt Multimedia is an optional import on purpose: ``python3-pyqt6.qtmultimedia``
is a separate distro package, so the panel degrades to the old hand-off rather
than refusing to build. ``AVAILABLE`` says which mode it is in.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap, QPolygonF
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QSlider, QToolButton, QVBoxLayout, QWidget)

from .splash import player_logo

try:                                        # pragma: no cover - import guard
    from PyQt6.QtCore import QUrl
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    AVAILABLE = True
except ImportError:                         # pragma: no cover - import guard
    AVAILABLE = False

#: what to tell the user when the optional package is not installed
MISSING_HINT = ("In-app playback needs Qt Multimedia:\n"
                "sudo apt install python3-pyqt6.qtmultimedia\n"
                "Until then Play hands the track to your desktop player.")

#: The artwork is drawn across the block's full width, up to this. Dave's
#: Logo-05 is composed with the mark in the upper part of a square and clear
#: space beneath it for the transport, so "fill the width" is the whole rule:
#: the asset is cropped to the art, and the controls start under it.
LOGO_MAX_WIDTH = 300


def clock(ms: int) -> str:
    """m:ss, or h:mm:ss past the hour — matching AudioInfo.play_time."""
    total = max(0, int(ms)) // 1000
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def track_label(track) -> str:
    """Artist — Title, falling back to the file name for an untagged file."""
    if track is None:
        return "Nothing playing"
    artist = str(track.values.get("artist", "") or "").strip()
    title = str(track.values.get("title", "") or "").strip()
    if artist and title:
        return f"{artist} — {title}"
    return title or track.path.name


#: Scaled lockups, keyed by (width, device ratio). The source art is 1254px
#: wide; rescaling it on every resize event — or for every window the test
#: suite builds — is pure waste.
_LOGO_CACHE: dict = {}


def _lockup(width: int, ratio: float) -> QPixmap:
    key = (int(width), round(ratio, 2))
    cached = _LOGO_CACHE.get(key)
    if cached is None:
        cached = player_logo(width, ratio)
        _LOGO_CACHE[key] = cached
    return cached


#: Transport glyphs, keyed by (kind, size, colour, ratio).
_GLYPH_CACHE: dict = {}


def transport_icon(kind: str, size: int, colour: QColor,
                   ratio: float = 1.0) -> QIcon:
    """Draw a transport symbol in the palette's own text colour.

    Desktop icon themes are the wrong source here: Dave's reads almost white,
    which disappeared against the light theme's window. Drawing the five
    shapes ourselves means they are always exactly as legible as the text
    beside them, in any theme, on any icon set.
    """
    key = (kind, int(size), colour.name(), round(ratio, 2))
    cached = _GLYPH_CACHE.get(key)
    if cached is not None:
        return cached

    px = int(round(size * max(1.0, ratio)))
    pixmap = QPixmap(px, px)
    pixmap.setDevicePixelRatio(max(1.0, ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(colour)
    _draw_glyph(painter, kind, px)
    painter.end()

    icon = QIcon(pixmap)
    _GLYPH_CACHE[key] = icon
    return icon


def _draw_glyph(painter: QPainter, kind: str, px: int) -> None:
    """Shapes in a 0..1 box, scaled to the pixmap."""
    def pt(x, y):
        return QPointF(x * px, y * px)

    def rect(x, y, w, h):
        return QRectF(x * px, y * px, w * px, h * px)

    if kind == "play":
        painter.drawPolygon(QPolygonF([pt(0.26, 0.16), pt(0.26, 0.84),
                                       pt(0.84, 0.5)]))
    elif kind == "pause":
        painter.drawRect(rect(0.28, 0.18, 0.15, 0.64))
        painter.drawRect(rect(0.57, 0.18, 0.15, 0.64))
    elif kind == "stop":
        painter.drawRect(rect(0.24, 0.24, 0.52, 0.52))
    elif kind == "previous":
        painter.drawRect(rect(0.18, 0.2, 0.11, 0.6))
        painter.drawPolygon(QPolygonF([pt(0.84, 0.2), pt(0.84, 0.8),
                                       pt(0.34, 0.5)]))
    elif kind == "next":
        painter.drawPolygon(QPolygonF([pt(0.16, 0.2), pt(0.16, 0.8),
                                       pt(0.66, 0.5)]))
        painter.drawRect(rect(0.71, 0.2, 0.11, 0.6))
    elif kind in ("volume", "mute"):
        painter.drawRect(rect(0.12, 0.38, 0.16, 0.24))
        painter.drawPolygon(QPolygonF([pt(0.28, 0.38), pt(0.52, 0.16),
                                       pt(0.52, 0.84), pt(0.28, 0.62)]))
        if kind == "volume":
            # Two arcs, drawn as pen strokes rather than filled shapes.
            from PyQt6.QtGui import QPen
            pen = QPen(painter.brush().color())
            pen.setWidthF(max(1.0, px * 0.07))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for radius in (0.16, 0.28):
                box = QRectF((0.52 - radius) * px, (0.5 - radius) * px,
                             radius * 2 * px, radius * 2 * px)
                painter.drawArc(box, -55 * 16, 110 * 16)
        else:
            from PyQt6.QtGui import QPen
            pen = QPen(painter.brush().color())
            pen.setWidthF(max(1.0, px * 0.09))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(pt(0.62, 0.36), pt(0.86, 0.64))
            painter.drawLine(pt(0.86, 0.36), pt(0.62, 0.64))


class PlayerPanel(QWidget):
    """Logo, now-playing line, seek bar, transport and volume."""

    play_all_requested = pyqtSignal()
    play_selected_requested = pyqtSignal()
    #: emitted instead of playing when Qt Multimedia is not installed
    external_play_requested = pyqtSignal(object)      # Path
    now_playing_changed = pyqtSignal(object)          # Track or None

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._queue: list = []
        self._index = -1
        self._player = None
        self._audio = None
        #: VideoWindow, built with the player; None without Qt Multimedia
        self.video_window = None
        self._seeking = False
        #: button -> which glyph it is currently showing, so a theme change
        #: can redraw every one of them in the new ink colour
        self._glyph_kinds: dict = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # The artwork is painted as the block's backdrop rather than put in a
        # label, because Dave composed it with clear space under the mark for
        # exactly these controls. This widget reserves the art's height in the
        # layout; paintEvent() draws into it.
        self._logo = QPixmap()
        self.logo_area = QWidget()
        self.logo_area.setFixedHeight(1)
        lay.addWidget(self.logo_area)

        self.now_playing = QLabel("Nothing playing")
        self.now_playing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.now_playing.setWordWrap(True)
        self.now_playing.setMinimumHeight(32)
        lay.addWidget(self.now_playing)

        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, 0)
        self.position.sliderPressed.connect(self._seek_start)
        self.position.sliderReleased.connect(self._seek_end)
        lay.addWidget(self.position)

        times = QHBoxLayout()
        self.elapsed = QLabel("0:00")
        self.total = QLabel("0:00")
        times.addWidget(self.elapsed)
        times.addStretch(1)
        times.addWidget(self.total)
        lay.addLayout(times)

        lay.addLayout(self._build_transport())
        lay.addLayout(self._build_volume())

        self.btn_play_all = QPushButton("Play all")
        self.btn_play_all.setToolTip(
            "Play every file in the list, starting at the selected one")
        self.btn_play_all.clicked.connect(self.play_all_requested.emit)
        self.btn_play_selected = QPushButton("Play selected")
        self.btn_play_selected.setToolTip("Play only the selected files")
        self.btn_play_selected.clicked.connect(
            self.play_selected_requested.emit)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self.btn_play_all)
        row.addWidget(self.btn_play_selected)
        lay.addLayout(row)
        lay.addStretch(1)

        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Preferred)
        self.refresh_logo()
        if not AVAILABLE:
            self._disable_transport()
        self._update_buttons()

    # --- construction helpers ---

    def _build_transport(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(2)
        row.addStretch(1)
        self.btn_prev = self._tool("Previous track", self.previous, "previous")
        self.btn_play = self._tool("Play / pause", self.toggle_play, "play")
        self.btn_stop = self._tool("Stop", self.stop, "stop")
        self.btn_next = self._tool("Next track", self.next, "next")
        for button in (self.btn_prev, self.btn_play, self.btn_stop,
                       self.btn_next):
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _build_volume(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.btn_mute = self._tool("Mute", self.toggle_mute, "volume")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(self._stored_volume())
        self.volume.setToolTip("Volume")
        self.volume.valueChanged.connect(self._volume_changed)
        row.addWidget(self.btn_mute)
        row.addWidget(self.volume, 1)
        return row

    #: logical size of a transport glyph
    GLYPH = 20

    def _tool(self, tip: str, slot, kind: str) -> QToolButton:
        button = QToolButton()
        button.setToolTip(tip if AVAILABLE else f"{tip}\n\n{MISSING_HINT}")
        button.setAutoRaise(True)
        button.setIconSize(QSize(self.GLYPH, self.GLYPH))
        button.setIcon(self._glyph(kind))
        button.clicked.connect(slot)
        self._glyph_kinds[button] = kind
        return button

    def _glyph(self, kind: str) -> QIcon:
        return transport_icon(kind, self.GLYPH, self._ink(),
                              max(1.0, self.devicePixelRatioF()))

    def _ink(self) -> QColor:
        """The colour the controls are drawn in: whatever the text is."""
        return self.palette().color(QPalette.ColorRole.WindowText)

    def _disable_transport(self) -> None:
        for button in (self.btn_prev, self.btn_play, self.btn_stop,
                       self.btn_next, self.btn_mute):
            button.setEnabled(False)
        for slider in (self.position, self.volume):
            slider.setEnabled(False)
            slider.setToolTip(MISSING_HINT)
        self.now_playing.setToolTip(MISSING_HINT)

    def refresh_theme(self) -> None:
        """Re-derive everything computed from the palette. One asset serves
        both themes — it is a cut-out with no backdrop — so only the glyphs
        actually change colour."""
        for button, kind in self._glyph_kinds.items():
            button.setIcon(self._glyph(kind))
        self._update_buttons()
        self.update()

    def refresh_logo(self) -> None:
        """Rescale the artwork for the block's current width."""
        width = min(max(self.width(), 1), LOGO_MAX_WIDTH)
        self._logo = _lockup(width, max(1.0, self.devicePixelRatioF()))
        height = (1 if self._logo.isNull()
                  else int(self._logo.height() / self._logo.devicePixelRatio()))
        self.logo_area.setFixedHeight(height)

    def logo_pixmap(self) -> QPixmap:
        return self._logo

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refresh_logo()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._logo.isNull():
            return
        width = int(self._logo.width() / self._logo.devicePixelRatio())
        painter = QPainter(self)
        painter.drawPixmap((self.width() - width) // 2, 0, self._logo)
        painter.end()

    # --- the player itself ---

    def _ensure_player(self):
        """Built on first use, not in __init__.

        Constructing a QAudioOutput opens the sound device; doing that for
        every MainWindow the test suite builds is both slow and noisy, and a
        session that never presses Play should never touch the audio stack.
        """
        if self._player is not None or not AVAILABLE:
            return self._player
        self._audio = QAudioOutput()
        self._audio.setVolume(self.volume.value() / 100)
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio)
        self._player.positionChanged.connect(self._position_changed)
        self._player.durationChanged.connect(self._duration_changed)
        self._player.playbackStateChanged.connect(
            lambda *_: self._update_buttons())
        self._player.mediaStatusChanged.connect(self._status_changed)
        self._attach_video_window()
        return self._player

    def _attach_video_window(self) -> None:
        """Give the player somewhere to draw pictures (see videowindow.py).

        Without a video output QMediaPlayer silently drops the picture and
        plays only the sound, which is all this panel did before 0.6.
        """
        from . import videowindow
        if not videowindow.AVAILABLE:
            return
        win = videowindow.VideoWindow(self.window(), self.settings)
        self.video_window = win
        player = self._player
        player.setVideoOutput(win.video)
        player.hasVideoChanged.connect(self._video_changed)
        player.tracksChanged.connect(self._tracks_changed)
        # GStreamer (Qt 6.4) can announce tracksChanged before the subtitle
        # tracks are known, leaving the picker empty; ask again once loaded.
        player.mediaStatusChanged.connect(
            lambda status: self._tracks_changed()
            if status in (QMediaPlayer.MediaStatus.LoadedMedia,
                          QMediaPlayer.MediaStatus.BufferedMedia) else None)
        player.positionChanged.connect(win.set_position)
        player.durationChanged.connect(win.set_duration)
        win.closed.connect(self.stop)
        win.toggle_play.connect(self.toggle_play)
        win.seek_to.connect(player.setPosition)
        win.seek_by.connect(lambda ms: player.setPosition(
            max(0, min(player.duration(), player.position() + ms))))
        win.subtitle_chosen.connect(player.setActiveSubtitleTrack)

    def _video_changed(self, has_video: bool) -> None:
        win = self.video_window
        if win is None:
            return
        if has_video:
            track = self.current()
            win.set_title(track.path.name if track is not None else "Video")
            win.show_video()
            self._tracks_changed()
        elif win.isVisible():
            win.remember()
            win.hide()

    def _tracks_changed(self) -> None:
        """List the file's subtitle tracks in the video window's picker."""
        win, player = self.video_window, self._player
        if win is None or player is None:
            return
        from PyQt6.QtCore import QLocale
        from PyQt6.QtMultimedia import QMediaMetaData
        # Only stringValue/value here: QMediaMetaData.keys() on a subtitle
        # track segfaults Qt 6.4's GStreamer backend (found 2026-11-28).
        names = []
        for meta in player.subtitleTracks():
            title = meta.stringValue(QMediaMetaData.Key.Title)
            lang = meta.value(QMediaMetaData.Key.Language)
            lang_name = (QLocale.languageToString(lang)
                         if isinstance(lang, QLocale.Language)
                         and lang != QLocale.Language.AnyLanguage else "")
            names.append(title or lang_name)
        win.set_subtitles(names, player.activeSubtitleTrack())

    # --- queue ---

    def play_tracks(self, tracks, start=None) -> None:
        """Queue these tracks and start at ``start`` (a Path), or the first."""
        tracks = [t for t in tracks if t is not None]
        if not tracks:
            return
        self._queue = tracks
        self._index = 0
        if start is not None:
            for i, track in enumerate(tracks):
                if track.path == Path(start):
                    self._index = i
                    break
        self._start_current()

    def _start_current(self) -> None:
        track = self.current()
        if track is None:
            return
        self.now_playing.setText(track_label(track))
        self.now_playing.setToolTip(str(track.path))
        self.now_playing_changed.emit(track)
        if not AVAILABLE:
            # No transport to offer: hand it to whatever plays music here.
            self.external_play_requested.emit(track.path)
            return
        player = self._ensure_player()
        player.setSource(QUrl.fromLocalFile(str(track.path)))
        player.play()
        self._update_buttons()

    def current(self):
        if 0 <= self._index < len(self._queue):
            return self._queue[self._index]
        return None

    def is_playing(self) -> bool:
        if not AVAILABLE or self._player is None:
            return False
        return (self._player.playbackState()
                is QMediaPlayer.PlaybackState.PlayingState)

    def position_ms(self) -> int:
        return 0 if self._player is None else int(self._player.position())

    def queue(self) -> list:
        return list(self._queue)

    # --- transport ---

    def toggle_play(self) -> None:
        if not AVAILABLE:
            return
        if not self._queue:
            self.play_all_requested.emit()
            return
        player = self._ensure_player()
        if player.playbackState() is QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        elif player.playbackState() is QMediaPlayer.PlaybackState.PausedState:
            player.play()
        else:
            self._start_current()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()
        if self.video_window is not None and self.video_window.isVisible():
            self.video_window.remember()
            self.video_window.hide()
        self.now_playing.setText("Nothing playing")
        self.now_playing.setToolTip("")
        self.position.setRange(0, 0)
        self.elapsed.setText("0:00")
        self.total.setText("0:00")
        self._index = -1 if not self._queue else self._index
        self.now_playing_changed.emit(None)
        self._update_buttons()

    def next(self) -> None:
        self._step(+1)

    def previous(self) -> None:
        self._step(-1)

    def _step(self, delta: int) -> None:
        if not self._queue:
            return
        target = self._index + delta
        if not 0 <= target < len(self._queue):
            self.stop()
            return
        self._index = target
        self._start_current()

    def toggle_mute(self) -> None:
        if self._audio is None:
            self._ensure_player()
        if self._audio is None:
            return
        self._audio.setMuted(not self._audio.isMuted())
        muted = self._audio.isMuted()
        self.btn_mute.setToolTip("Unmute" if muted else "Mute")
        kind = "mute" if muted else "volume"
        self._glyph_kinds[self.btn_mute] = kind
        self.btn_mute.setIcon(self._glyph(kind))

    # --- slots ---

    def _volume_changed(self, value: int) -> None:
        if self._audio is not None:
            self._audio.setVolume(value / 100)
        if self.settings is not None:
            self.settings.setValue("volume", value)

    def _stored_volume(self) -> int:
        if self.settings is None:
            return 70
        try:
            return max(0, min(100, int(self.settings.value("volume", 70))))
        except (TypeError, ValueError):
            return 70

    def _seek_start(self) -> None:
        self._seeking = True

    def _seek_end(self) -> None:
        self._seeking = False
        if self._player is not None:
            self._player.setPosition(self.position.value())

    def _position_changed(self, ms: int) -> None:
        if not self._seeking:
            self.position.setValue(ms)
        self.elapsed.setText(clock(ms))

    def _duration_changed(self, ms: int) -> None:
        self.position.setRange(0, ms)
        self.total.setText(clock(ms))

    def _status_changed(self, status) -> None:
        if status is QMediaPlayer.MediaStatus.EndOfMedia:
            self.next()

    def _update_buttons(self) -> None:
        if not AVAILABLE:
            return
        playing = (self._player is not None
                   and self._player.playbackState()
                   is QMediaPlayer.PlaybackState.PlayingState)
        kind = "pause" if playing else "play"
        self._glyph_kinds[self.btn_play] = kind
        self.btn_play.setIcon(self._glyph(kind))
        self.btn_play.setToolTip("Pause" if playing else "Play / pause")
        has_queue = bool(self._queue)
        self.btn_stop.setEnabled(has_queue)
        self.btn_prev.setEnabled(has_queue and self._index > 0)
        self.btn_next.setEnabled(has_queue
                                 and self._index < len(self._queue) - 1)
