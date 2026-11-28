"""Matroska specifics: the things the generic round-trips in test_tagio miss.

Every file here is a real ffmpeg encode, and every check that matters to
another program is made with that program's own reader (mkvextract, ffprobe)
rather than with ours.
"""

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from rifftag import matroska, tagio
from rifftag.tagio import Picture

pytestmark = pytest.mark.skipif(not matroska.available(),
                                reason="MKVToolNix is not installed")


def _mkv_tags(path) -> ET.Element:
    out = subprocess.run(["mkvextract", str(path), "tags", "-"],
                         capture_output=True, check=True,
                         env={"LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"}).stdout
    return matroska._parse_tags(out)


def _ffprobe_tags(path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-of", "json",
                          "-show_entries", "format_tags", str(path)],
                         capture_output=True, check=True).stdout
    return {k.lower(): v for k, v in
            json.loads(out)["format"].get("tags", {}).items()}


def _set_tags_xml(path, xml: str, tmp_path) -> None:
    f = tmp_path / "in.xml"
    f.write_text(xml, encoding="utf-8")
    subprocess.run(["mkvpropedit", str(path), "--tags", f"all:{f}"],
                   check=True, capture_output=True)


def _audio_md5(path) -> str:
    """Hash of the audio and video packets only — not attachments or tags."""
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                           "-map", "0:V?", "-map", "0:a",  # V: not cover art
                           "-c", "copy", "-f", "streamhash", "-"],
                          capture_output=True, check=True).stdout.decode()


# --- interop with what other tools wrote and read -----------------------

def test_reads_what_ffmpeg_wrote(tmp_path):
    """ffmpeg is how most MKVs get their tags: title in the segment info,
    track as "3/12" in PART_NUMBER."""
    p = tmp_path / "ff.mka"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=stereo", "-t", "1",
                    "-c:a", "libvorbis",
                    "-metadata", "title=Lithium", "-metadata", "artist=Nirvana",
                    "-metadata", "album=Nevermind",
                    "-metadata", "album_artist=Nirvana",
                    "-metadata", "track=3/12", "-metadata", "date=1991",
                    str(p)], check=True, capture_output=True)
    v = tagio.read(p).values
    assert v["title"] == "Lithium"
    assert v["artist"] == "Nirvana"
    assert v["album"] == "Nevermind"
    assert v["album_artist"] == "Nirvana"
    assert v["track"] == "3" and v["track_total"] == "12"
    assert v["year"] == "1991"


def test_ffprobe_sees_what_we_wrote(sample):
    p = sample("mkv")
    tagio.write(p, {"title": "Lithium", "artist": "Nirvana",
                    "album": "Nevermind", "genre": "Grunge"})
    tags = _ffprobe_tags(p)
    assert tags["title"] == "Lithium"
    assert tags["artist"] == "Nirvana"
    assert tags["album"] == "Nevermind"
    assert tags["genre"] == "Grunge"


def test_title_also_goes_to_the_segment_title_players_show(sample):
    p = sample("mkv")
    tagio.write(p, {"title": "Lithium"})
    info = matroska.identify(p)
    assert info["container"]["properties"]["title"] == "Lithium"
    tagio.write(p, {"title": ""})
    assert "title" not in matroska.identify(p)["container"]["properties"]
    assert "title" not in tagio.read(p).values


def test_a_combined_track_number_stays_combined(tmp_path):
    p = tmp_path / "ff.mka"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "anullsrc", "-t", "1", "-c:a", "libvorbis",
                    "-metadata", "track=3/12", str(p)],
                   check=True, capture_output=True)
    tagio.write(p, {"track": "4"})
    names = {s.findtext("Name"): s.findtext("String")
             for s in _mkv_tags(p).iter("Simple")}
    assert names["PART_NUMBER"] == "4/12"
    assert "TOTAL_PARTS" not in names


# --- the spec's nested album/song layout --------------------------------

NESTED = """<?xml version="1.0" encoding="UTF-8"?>
<Tags>
  <Tag>
    <Targets><TargetTypeValue>50</TargetTypeValue></Targets>
    <Simple><Name>TITLE</Name><String>Nevermind</String></Simple>
    <Simple><Name>ARTIST</Name><String>Nirvana</String></Simple>
    <Simple><Name>TOTAL_PARTS</Name><String>12</String></Simple>
    <Simple><Name>DATE_RELEASED</Name><String>1991</String></Simple>
  </Tag>
  <Tag>
    <Targets><TargetTypeValue>30</TargetTypeValue></Targets>
    <Simple><Name>TITLE</Name><String>Lithium</String></Simple>
    <Simple><Name>ARTIST</Name><String>Kurt</String></Simple>
    <Simple><Name>PART_NUMBER</Name><String>3</String></Simple>
  </Tag>
</Tags>
"""


def test_nested_layout_is_read_by_level(sample, tmp_path):
    p = sample("mka")
    _set_tags_xml(p, NESTED, tmp_path)
    v = tagio.read(p).values
    assert v["title"] == "Lithium"
    assert v["artist"] == "Kurt"
    assert v["album"] == "Nevermind"
    assert v["album_artist"] == "Nirvana"
    assert v["track"] == "3" and v["track_total"] == "12"
    assert v["year"] == "1991"


def test_nested_layout_is_written_back_to_the_right_level(sample, tmp_path):
    p = sample("mka")
    _set_tags_xml(p, NESTED, tmp_path)
    tagio.write(p, {"album": "In Utero", "title": "Dumb", "track_total": "13"})
    by_level = {}
    for tag in _mkv_tags(p).findall("Tag"):
        # 50 is the default, and mkvextract leaves a default value out
        lvl = tag.find("Targets").findtext("TargetTypeValue") or "50"
        by_level[lvl] = {s.findtext("Name"): s.findtext("String")
                         for s in tag.findall("Simple")}
    assert by_level["50"]["TITLE"] == "In Utero"
    assert by_level["50"]["TOTAL_PARTS"] == "13"
    assert by_level["30"]["TITLE"] == "Dumb"
    v = tagio.read(p).values
    assert (v["album"], v["title"], v["track"]) == ("In Utero", "Dumb", "3")


# --- what must never be touched -----------------------------------------

def test_track_level_tags_are_kept(sample):
    p = sample("mka")
    before = [t for t in _mkv_tags(p).findall("Tag")
              if not matroska._is_global(t)]
    assert before, "ffmpeg should have written a per-track ENCODER tag"
    tagio.write(p, {"artist": "Nirvana"})
    tagio.remove_tags(p)
    after = [t for t in _mkv_tags(p).findall("Tag")
             if not matroska._is_global(t)]
    assert len(after) == len(before)


def test_unknown_file_level_tags_survive_an_edit(sample, tmp_path):
    p = sample("mka")
    _set_tags_xml(p, """<Tags><Tag><Targets/>
        <Simple><Name>ARTIST</Name><String>A</String></Simple>
        <Simple><Name>PRODUCER</Name><String>Butch Vig</String></Simple>
        </Tag></Tags>""", tmp_path)
    tagio.write(p, {"artist": "B"})
    names = {s.findtext("Name"): s.findtext("String")
             for s in _mkv_tags(p).iter("Simple")}
    assert names["PRODUCER"] == "Butch Vig"
    assert names["ARTIST"] == "B"


def test_non_image_attachments_are_left_alone(sample, tmp_path, jpeg_bytes):
    """Subtitled films carry their fonts as attachments."""
    p = sample("mkv")
    font = tmp_path / "font.ttf"
    font.write_bytes(b"\x00\x01\x00\x00not-really-a-font")
    subprocess.run(["mkvpropedit", str(p), "--attachment-mime-type",
                    "font/ttf", "--add-attachment", str(font)],
                   check=True, capture_output=True)
    tagio.write(p, {}, pictures=[Picture(jpeg_bytes)])
    tagio.write(p, {}, pictures=[])
    tagio.remove_tags(p)
    atts = matroska.identify(p)["attachments"]
    assert [a["file_name"] for a in atts] == ["font.ttf"]


def test_the_streams_are_not_touched(sample, jpeg_bytes):
    p = sample("mkv")
    before = _audio_md5(p)
    tagio.write(p, {"title": "x", "artist": "y"},
                pictures=[Picture(jpeg_bytes)])
    assert _audio_md5(p) == before


# --- cover art ----------------------------------------------------------

def test_cover_uses_the_spec_attachment_name(sample, jpeg_bytes):
    p = sample("mka")
    tagio.write(p, {}, pictures=[Picture(jpeg_bytes, "image/jpeg", 3),
                                 Picture(b"\x89PNG\r\n\x1a\n" + bytes(16),
                                         "image/png", 4, "the back")])
    atts = matroska.identify(p)["attachments"]
    assert [(a["file_name"], a["content_type"]) for a in atts] == [
        ("cover.jpg", "image/jpeg"), ("back.png", "image/png")]
    pics = tagio.read(p).pictures
    assert [(x.kind, x.mime, x.desc) for x in pics] == [
        (3, "image/jpeg", ""), (4, "image/png", "the back")]


def test_replacing_the_cover_does_not_stack_copies(sample, jpeg_bytes):
    p = sample("mka")
    for _ in range(3):
        tagio.write(p, {}, pictures=[Picture(jpeg_bytes)])
    assert len(matroska.identify(p)["attachments"]) == 1


# --- the rest -----------------------------------------------------------

def test_audio_properties_are_read(sample):
    t = tagio.read(sample("mka"))
    assert t.error is None
    assert t.family == "matroska" and t.tag_version == "Matroska"
    assert t.info.sample_rate == 44100 and t.info.channels == 2
    assert t.info.play_time == "0:01"
    assert tagio.read(sample("webm")).tag_version == "WebM"


def test_non_ascii_file_names_work(sample):
    p = sample("mka", name="Björk — Jóga")
    tagio.write(p, {"artist": "Björk"})
    assert tagio.read(p).values["artist"] == "Björk"


def test_missing_mkvtoolnix_is_an_error_not_a_crash(sample, monkeypatch):
    p = sample("mka")
    monkeypatch.setattr(matroska, "tool", lambda name: None)
    t = tagio.read(p)
    assert t.error and "MKVToolNix" in t.error
    with pytest.raises(tagio.TagError, match="MKVToolNix"):
        tagio.write(p, {"artist": "x"})


def test_a_file_that_is_not_matroska_is_an_error(tmp_path):
    p = tmp_path / "fake.mkv"
    p.write_bytes(b"not a matroska file at all" * 10)
    assert tagio.read(p).error
    with pytest.raises(tagio.TagError):
        tagio.write(p, {"artist": "x"})


def test_tools_are_found_beside_a_bundled_exe(tmp_path, monkeypatch):
    """The Windows build ships MKVToolNix in a folder next to RiffTag.exe."""
    bundled = tmp_path / "mkvtoolnix"
    bundled.mkdir()
    exe = bundled / "mkvpropedit"
    exe.write_text("")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(matroska.sys, "executable", str(tmp_path / "RiffTag"))
    monkeypatch.setattr(matroska.sys, "platform", "linux")
    assert matroska.tool("mkvpropedit") == str(exe)
