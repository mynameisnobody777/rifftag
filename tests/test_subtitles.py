"""Embedding sidecar subtitles into Matroska (rifftag.subtitles)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rifftag import matroska, subtitles, tagio
from rifftag.subtitles import parse_sidecar

NEEDS_MKV = pytest.mark.skipif(not matroska.available(),
                               reason="MKVToolNix is not installed")
SRT = "1\n00:00:00,200 --> 00:00:00,800\nHello there\n\n"


@pytest.mark.parametrize("name, lang, label, forced, sdh", [
    ("Heat (1995).srt", "und", "", False, False),
    ("Heat (1995).eng.srt", "eng", "English", False, False),
    ("Heat (1995).en.srt", "eng", "English", False, False),
    ("Heat (1995).es.forced.srt", "spa", "Spanish (Forced)", True, False),
    ("Heat (1995).en.sdh.srt", "eng", "English (SDH)", False, True),
    ("Heat (1995).en.hi.srt", "eng", "English (SDH)", False, True),
    ("Heat (1995).hi.srt", "hin", "Hindi", False, False),
    ("Heat (1995).French.srt", "fre", "French", False, False),
    ("Heat (1995).2.eng.srt", "eng", "English", False, False),
])
def test_parse_sidecar(name, lang, label, forced, sdh):
    s = parse_sidecar(Path("Heat (1995).mkv"), Path(name))
    assert (s.language, s.name, s.forced, s.sdh) == (lang, label, forced, sdh)


def test_find_sidecars_matches_only_this_film(tmp_path):
    video = tmp_path / "Heat (1995).mkv"
    video.touch()
    for n in ("Heat (1995).srt", "heat (1995).ENG.SRT", "Heat (1995).en.ass",
              "Heat (1995) - Director's Cut.srt", "Heat 2 (2000).srt",
              "Heat (1995).txt"):
        (tmp_path / n).write_text(SRT)
    got = sorted(s.path.name for s in subtitles.find_sidecars(video))
    assert got == ["Heat (1995).en.ass", "Heat (1995).srt",
                   "heat (1995).ENG.SRT"]


def test_mp4_is_left_alone(tmp_path):
    p = tmp_path / "Heat (1995).mp4"
    p.touch()
    (tmp_path / "Heat (1995).srt").write_text(SRT)
    assert "MP4" in subtitles.plan(p).error


def _streams(path) -> str:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:V?", "-map",
         "0:a", "-c", "copy", "-f", "streamhash", "-"],
        capture_output=True, check=True).stdout.decode()


@pytest.fixture
def film(sample, tmp_path, jpeg_bytes):
    video = sample("mkv", "film").rename(tmp_path / "Heat (1995).mkv")
    tagio.write(video, {"title": "Heat", "year": "1995"},
                pictures=[tagio.Picture(jpeg_bytes)])
    (tmp_path / "Heat (1995).eng.srt").write_text(SRT, encoding="utf-8")
    (tmp_path / "Heat (1995).es.forced.srt").write_bytes(
        "1\n00:00:00,200 --> 00:00:00,800\nSeñor café\n\n".encode("cp1252"))
    return video


@NEEDS_MKV
def test_embed_adds_tracks_and_keeps_everything_else(film, tmp_path):
    before = _streams(film)
    plan = subtitles.plan(film)
    assert [s.name for s in plan.add] == ["English", "Spanish (Forced)"]
    seen = []
    assert subtitles.embed(plan, progress=seen.append) == 2
    assert seen and seen[-1] == 100

    info = matroska.identify(film)
    subs = [t["properties"] for t in info["tracks"] if t["type"] == "subtitles"]
    assert [(p["language"], p.get("track_name"), bool(p.get("forced_track")))
            for p in subs] == [("eng", "English", False),
                               ("spa", "Spanish (Forced)", True)]
    assert _streams(film) == before                    # not re-encoded
    got = tagio.read(film)
    assert got.values["title"] == "Heat" and got.pictures   # tags and art kept
    # The cp1252 file was converted, not mangled.
    out = tmp_path / "out.srt"
    sub_id = [t["id"] for t in info["tracks"] if t["type"] == "subtitles"][1]
    subprocess.run([matroska.tool("mkvextract"), str(film), "tracks",
                    f"{sub_id}:{out}"], check=True, capture_output=True)
    assert "Señor café" in out.read_text(encoding="utf-8-sig")
    # Sidecars kept by default, nothing left behind, nothing new next time.
    assert (tmp_path / "Heat (1995).eng.srt").exists()
    assert not [p for p in tmp_path.iterdir() if "rifftag-embedding" in p.name]
    again = subtitles.plan(film)
    assert not again.add and len(again.already) == 2


@NEEDS_MKV
def test_delete_sidecars_only_after_success(film, tmp_path):
    subtitles.embed(subtitles.plan(film), delete_sidecars=True)
    assert not list(tmp_path.glob("*.srt"))


@NEEDS_MKV
def test_a_failed_check_leaves_the_original_alone(film, tmp_path, monkeypatch):
    original = film.read_bytes()
    plan = subtitles.plan(film)
    real = matroska.identify

    def lying(path):
        info = real(path)
        if "rifftag-embedding" in Path(path).name:
            info["tracks"] = [t for t in info["tracks"] if t["type"] != "audio"]
        return info
    monkeypatch.setattr(matroska, "identify", lying)
    with pytest.raises(subtitles.EmbedError, match="audio"):
        subtitles.embed(plan, delete_sidecars=True)
    assert film.read_bytes() == original
    assert (tmp_path / "Heat (1995).eng.srt").exists()
    assert not [p for p in tmp_path.iterdir() if "rifftag-embedding" in p.name]


@NEEDS_MKV
def test_no_room_for_the_copy_is_refused_up_front(film, monkeypatch):
    monkeypatch.setattr(subtitles.shutil, "disk_usage",
                        lambda _: shutil._ntuple_diskusage(1, 1, 1000))
    with pytest.raises(subtitles.EmbedError, match="free space"):
        subtitles.embed(subtitles.plan(film))


@NEEDS_MKV
def test_the_dialog_runs_the_batch(film, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from rifftag.gui.subembed import SubtitleEmbedDialog
    dlg = SubtitleEmbedDialog([tagio.read(film)])
    assert dlg.table.item(0, 1).text() == "English, Spanish (Forced)"
    assert dlg.btn_go.isEnabled()
    dlg._start()
    import time
    end = time.monotonic() + 30
    while dlg.running and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert not dlg.running and dlg.embedded == 1
    assert dlg.table.item(0, 3).text() == "Added 2 track(s)"
