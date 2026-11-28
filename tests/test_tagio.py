"""Tag read/write round-trips against real encodes of every supported family."""

import pytest

from rifftag import matroska, tagio
from rifftag.tagio import Picture, WriteOptions

#: Matroska goes through MKVToolNix rather than mutagen; without it installed
#: those cases skip instead of failing.
_NEEDS_MKV = pytest.mark.skipif(not matroska.available(),
                                reason="MKVToolNix is not installed")
MKV = [pytest.param(e, marks=_NEEDS_MKV) for e in ("mka", "mkv", "webm")]

ALL = ["mp3", "flac", "ogg", "opus", "m4a", "wma", "wv", "wav", "aiff", *MKV]
#: formats whose tag standard carries the long tail of extra frames
RICH = ["mp3", "flac", "ogg", "m4a", "wv", *MKV]

CORE = {
    "artist": "Nirvana",
    "title": "Smells Like Teen Spirit",
    "album": "Nevermind",
    "year": "1991",
    "genre": "Grunge",
    "comment": "first single",
}


@pytest.mark.parametrize("ext", ALL)
def test_core_fields_round_trip(sample, ext):
    p = sample(ext)
    tagio.write(p, CORE)
    got = tagio.read(p)
    assert got.error is None
    for key, want in CORE.items():
        assert got.values.get(key) == want, f"{ext}: {key}"


@pytest.mark.parametrize("ext", ALL)
def test_track_and_disc_carry_their_totals(sample, ext):
    p = sample(ext)
    tagio.write(p, {"track": "3", "track_total": "12",
                    "disc": "1", "disc_total": "2"})
    v = tagio.read(p).values
    assert v.get("track") == "3"
    assert v.get("disc") == "1"
    if ext not in ("wma",):     # ASF has no standard total-tracks field
        assert v.get("track_total") == "12"
        assert v.get("disc_total") == "2"


@pytest.mark.parametrize("ext", RICH)
def test_extended_fields_round_trip(sample, ext):
    p = sample(ext)
    extra = {
        "album_artist": "Nirvana",
        "composer": "Kurt Cobain",
        "conductor": "Butch Vig",
        "label": "DGC",
        "initial_key": "F",
        "isrc": "USGF19100001",
        "bpm": "117",
        "mood": "Angsty",
        "subtitle": "LP version",
        "grouping": "90s",
    }
    tagio.write(p, extra)
    v = tagio.read(p).values
    for key, want in extra.items():
        assert str(v.get(key)) == want, f"{ext}: {key}"


@pytest.mark.parametrize("ext", ALL)
def test_unwritten_fields_are_left_alone(sample, ext):
    """The whole premise of the multi-file editor: absent key means untouched."""
    p = sample(ext)
    tagio.write(p, {"artist": "Nirvana", "title": "Lithium"})
    tagio.write(p, {"album": "Nevermind"})          # second pass, title absent
    v = tagio.read(p).values
    assert v.get("title") == "Lithium"
    assert v.get("artist") == "Nirvana"
    assert v.get("album") == "Nevermind"


@pytest.mark.parametrize("ext", ALL)
def test_empty_string_clears_a_field(sample, ext):
    p = sample(ext)
    tagio.write(p, {"artist": "Nirvana", "comment": "scratch"})
    tagio.write(p, {"comment": ""})
    v = tagio.read(p).values
    assert not v.get("comment")
    assert v.get("artist") == "Nirvana"


@pytest.mark.parametrize("ext", ["mp3", "flac", "ogg", "m4a", "wma", "wv", *MKV])
def test_cover_art_round_trips(sample, ext, jpeg_bytes):
    p = sample(ext)
    tagio.write(p, {"title": "x"}, pictures=[Picture(jpeg_bytes, "image/jpeg")])
    got = tagio.read(p)
    assert got.pictures, f"{ext}: no cover came back"
    assert got.pictures[0].data == jpeg_bytes
    assert got.pictures[0].dimensions() == (8, 8)


@pytest.mark.parametrize("ext", ["mp3", "flac", "ogg", "m4a", *MKV])
def test_pictures_none_leaves_art_alone_and_empty_list_removes_it(sample, ext, jpeg_bytes):
    p = sample(ext)
    tagio.write(p, {"title": "x"}, pictures=[Picture(jpeg_bytes)])
    tagio.write(p, {"album": "y"})                   # pictures=None
    assert tagio.read(p).pictures
    tagio.write(p, {}, pictures=[])
    assert not tagio.read(p).pictures


@pytest.mark.parametrize("ext", ALL)
def test_unicode_survives(sample, ext):
    p = sample(ext)
    tagio.write(p, {"artist": "Бьёрк", "title": "Jóga — 日本語"})
    v = tagio.read(p).values
    assert v.get("artist") == "Бьёрк"
    assert v.get("title") == "Jóga — 日本語"


@pytest.mark.parametrize("ext", ["mp3", "flac", "ogg", "m4a", "wv", *MKV])
def test_compilation_flag(sample, ext):
    p = sample(ext)
    tagio.write(p, {"compilation": True})
    assert tagio.read(p).values.get("compilation") is True
    tagio.write(p, {"compilation": False})
    assert not tagio.read(p).values.get("compilation")


@pytest.mark.parametrize("ext", ["mp3", "flac", "ogg", "m4a", *MKV])
def test_lyrics_round_trip(sample, ext):
    p = sample(ext)
    text = "With the lights out\nit's less dangerous"
    tagio.write(p, {"lyrics": text})
    assert tagio.read(p).values.get("lyrics") == text


def test_mp3_defaults_to_id3v23_for_compatibility(sample):
    """Tag&Rename always writes 2.3; a 2.4-only TDRC must become TYER."""
    import mutagen.id3
    p = sample("mp3")
    tagio.write(p, {"artist": "Nirvana", "year": "1991"})
    # translate=False, or mutagen silently upgrades the tag back to 2.4 on
    # load and TYER would never be visible.
    raw = mutagen.id3.ID3(p, translate=False)
    assert raw.version[:2] == (2, 3)
    assert str(raw.getall("TYER")[0]) == "1991"
    assert tagio.read(p).values.get("year") == "1991"


def test_mp3_can_be_written_as_id3v24(sample):
    import mutagen.id3
    p = sample("mp3")
    tagio.write(p, {"artist": "Nirvana", "year": "1991"},
                options=WriteOptions(id3_version=4))
    raw = mutagen.id3.ID3(p)
    assert raw.version[:2] == (2, 4)
    assert tagio.read(p).values.get("year") == "1991"


def test_mp3_rating_is_windows_media_player_compatible(sample):
    import mutagen.id3
    p = sample("mp3")
    tagio.write(p, {"rating": 4})
    popm = mutagen.id3.ID3(p).getall("POPM")
    assert popm[0].email == "Windows Media Player 9 Series"
    assert popm[0].rating == 196          # WMP's value for four stars
    assert tagio.read(p).values.get("rating") == 4


def test_grouping_reads_both_spellings_and_writes_the_configured_one(sample):
    """iTunes moved TIT1 -> GRP1; Tag&Rename 3.9.13 changed its default too."""
    import mutagen.id3
    p = sample("mp3")
    tagio.write(p, {"grouping": "90s"})
    assert mutagen.id3.ID3(p).getall("GRP1")
    assert not mutagen.id3.ID3(p).getall("TIT1")
    assert tagio.read(p).values.get("grouping") == "90s"

    tagio.write(p, {"grouping": "00s"},
                options=WriteOptions(id3_grouping_frame="TIT1"))
    raw = mutagen.id3.ID3(p)
    assert raw.getall("TIT1") and not raw.getall("GRP1")
    assert tagio.read(p).values.get("grouping") == "00s"


def test_album_artist_spelling_is_configurable_and_both_are_read(sample):
    p = sample("flac")
    tagio.write(p, {"album_artist": "Various Artists"},
                options=WriteOptions(album_artist_key="ALBUM ARTIST"))
    import mutagen.flac
    assert "ALBUM ARTIST" in {k.upper() for k in mutagen.flac.FLAC(p).tags.keys()}
    assert tagio.read(p).values.get("album_artist") == "Various Artists"


def test_label_spelling_is_configurable(sample):
    p = sample("flac")
    tagio.write(p, {"label": "DGC"},
                options=WriteOptions(label_key="ORGANIZATION"))
    import mutagen.flac
    keys = {k.upper() for k in mutagen.flac.FLAC(p).tags.keys()}
    assert "ORGANIZATION" in keys and "LABEL" not in keys
    assert tagio.read(p).values.get("label") == "DGC"


def test_slash_in_vorbis_tracknumber_is_split(sample):
    p = sample("flac")
    import mutagen.flac
    f = mutagen.flac.FLAC(p)
    f["TRACKNUMBER"] = ["3/12"]
    f.save()
    v = tagio.read(p).values
    assert v["track"] == "3" and v["track_total"] == "12"


def test_v24_only_frames_survive_a_v23_save(sample):
    """Mood and the sort fields must not vanish just because we write 2.3."""
    p = sample("mp3")
    vals = {"mood": "Angsty", "release_time": "1991-09-24",
            "sort_artist": "Nirvana", "sort_album": "Nevermind"}
    tagio.write(p, vals)
    import mutagen.id3
    assert mutagen.id3.ID3(p, translate=False).version[:2] == (2, 3)
    got = tagio.read(p).values
    for key, want in vals.items():
        assert got.get(key) == want, key


def test_itunes_tv_frames_round_trip_in_mp3(sample):
    """Unregistered TVSH/TVEN would be dropped on save; we register them."""
    p = sample("mp3")
    tv = {"show_title": "Planet Earth", "episode_id": "S1E3",
          "season_number": "1", "episode_number": "3"}
    tagio.write(p, tv)
    v = tagio.read(p).values
    for key, want in tv.items():
        assert str(v.get(key)) == want


def test_audio_info_is_populated(sample):
    got = tagio.read(sample("mp3"))
    assert 0.8 < got.info.length < 1.5
    assert got.info.sample_rate == 44100
    assert got.info.channels == 2
    assert got.info.bitrate > 0
    assert got.info.play_time == "0:01"


def test_read_of_a_non_audio_file_reports_an_error_rather_than_raising(tmp_path):
    junk = tmp_path / "notes.mp3"
    junk.write_bytes(b"this is not audio")
    got = tagio.read(junk)
    assert got.error is not None
    assert got.values == {}


def test_write_to_an_unsupported_extension_raises(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    with pytest.raises(tagio.TagError):
        tagio.write(f, {"artist": "x"})


@pytest.mark.parametrize("ext", ALL)
def test_remove_tags_clears_everything(sample, ext):
    p = sample(ext)
    tagio.write(p, CORE)
    tagio.remove_tags(p)
    assert not tagio.read(p).values


def test_rename_values_includes_derived_variables(sample):
    p = sample("mp3")
    tagio.write(p, {"artist": "Nirvana", "title": "Lithium"})
    v = tagio.read(p).rename_values(file_number=7)
    assert v["artist"] == "Nirvana"
    assert v["filename"] == "track"
    assert v["file_number"] == 7
    assert v["bitrate"] > 0
    assert v["play_time"] == "0:01"


def test_rename_values_carry_episode_forms(sample):
    p = sample("mp3")
    tagio.write(p, {"season_number": "1", "episode_number": "3"})
    v = tagio.read(p).rename_values()
    assert (v["sxe"], v["s00e00"]) == ("1x03", "S01E03")
