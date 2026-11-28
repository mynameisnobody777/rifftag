"""The video window (rifftag.gui.videowindow) and the player's use of it.

No real playback here: QMediaPlayer needs a sound device and GStreamer, and
the suite must run anywhere. The window's own behaviour and the panel's
show/hide decisions are tested directly.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")
videowindow = pytest.importorskip("rifftag.gui.videowindow")
if not videowindow.AVAILABLE:
    pytest.skip("Qt Multimedia widgets not installed", allow_module_level=True)

from PyQt6.QtCore import Qt                                     # noqa: E402
from PyQt6.QtTest import QTest                                  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget               # noqa: E402

from rifftag import tagio                                       # noqa: E402
from rifftag.gui.videowindow import VideoWindow                 # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_keys_drive_the_player(qapp):
    win = VideoWindow()
    got = []
    win.toggle_play.connect(lambda: got.append("play"))
    win.seek_by.connect(lambda ms: got.append(ms))
    for key in (Qt.Key.Key_Space, Qt.Key.Key_Right, Qt.Key.Key_Left,
                Qt.Key.Key_Up, Qt.Key.Key_Down):
        QTest.keyClick(win, key)
    assert got == ["play", 10_000, -10_000, 60_000, -60_000]


def test_subtitle_picker(qapp):
    win = VideoWindow()
    assert not win.subs.isEnabled()
    win.set_subtitles(["English", ""], active=-1)
    assert [win.subs.itemText(i) for i in range(win.subs.count())] == [
        "Subtitles: off", "English", "Track 2"]
    chosen = []
    win.subtitle_chosen.connect(chosen.append)
    win.subs.activated.emit(1)
    win.subs.activated.emit(0)
    assert chosen == [0, -1]


def test_time_label_and_seek(qapp):
    win = VideoWindow()
    win.set_duration(3_723_000)
    win.set_position(61_000)
    assert win.time.text() == "1:01 / 1:02:03"
    target = []
    win.seek_to.connect(target.append)
    win.position.setValue(5_000)
    win._seek_end()
    assert target == [5_000]


def test_closing_the_window_says_so(qapp):
    win = VideoWindow()
    win.show()
    fired = []
    win.closed.connect(lambda: fired.append(True))
    win.close()
    assert fired == [True]


def test_panel_shows_the_window_for_video_and_hides_it_on_stop(
        qapp, sample, tmp_path):
    from rifftag.gui.player import PlayerPanel
    host = QWidget()
    panel = PlayerPanel(parent=host)
    panel.video_window = VideoWindow(host)
    track = tagio.read(sample("mp3"))
    panel._queue, panel._index = [track], 0
    panel._video_changed(True)
    assert panel.video_window.isVisible()
    assert track.path.name in panel.video_window.windowTitle()
    panel._video_changed(False)
    assert not panel.video_window.isVisible()
    panel._video_changed(True)
    panel.stop()
    assert not panel.video_window.isVisible()
