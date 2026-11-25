"""Format conversion.

Every test here runs a real ffmpeg encode and then reads the result back with
mutagen — the same rule as the rest of the suite. Asserting against a
synthesised file would only prove the module agrees with itself.
"""

import shutil
import subprocess

import pytest

from rifftag import tagio
from rifftag.convert import (BY_KEY, FORMATS, ConvertError, Step, convert,
                             ffmpeg_available, plan, run, to_convert)
from rifftag.tagio import WriteOptions

MP3 = BY_KEY["mp3"]
FLAC = BY_KEY["flac"]
WAV = BY_KEY["wav"]
M4A = BY_KEY["m4a"]


@pytest.fixture
def tagged(sample):
    """A file of the given type, carrying a full set of tags and cover art."""
    def make(ext, jpeg=None, name="track"):
        path = sample(ext, name)
        tagio.write(path, {"artist": "Nirvana", "album": "Nevermind",
                           "title": "In Bloom", "year": "1991",
                           "genre": "Grunge", "track": "2"},
                    pictures=[tagio.Picture(jpeg)] if jpeg else None)
        return tagio.read(path)
    return make


def test_the_plan_writes_into_a_subfolder_of_the_source(tagged):
    track = tagged("flac")
    step = plan([track], MP3)[0]
    assert step.dst == track.path.parent / "MP3" / (track.path.stem + ".mp3")
    assert not step.skipped


def test_the_subfolder_name_can_be_overridden(tagged):
    track = tagged("flac")
    step = plan([track], MP3, "For the car")[0]
    assert step.dst.parent.name == "For the car"


def test_a_blank_subfolder_falls_back_to_the_format_name(tagged):
    track = tagged("flac")
    assert plan([track], MP3, "   ")[0].dst.parent.name == "MP3"


def test_a_file_already_in_the_target_format_is_skipped(tagged):
    track = tagged("mp3")
    step = plan([track], MP3)[0]
    assert step.skipped and step.note == "already MP3"


def test_an_existing_destination_is_skipped_not_overwritten(tagged):
    track = tagged("flac")
    step = plan([track], MP3)[0]
    step.dst.parent.mkdir(parents=True)
    step.dst.write_bytes(b"precious")

    again = plan([track], MP3)[0]
    assert again.note == "destination exists"
    assert to_convert([again]) == []
    assert step.dst.read_bytes() == b"precious"


def test_two_sources_that_would_collide_are_caught(tagged, tmp_path):
    one = tagged("flac", name="same")
    two = tagged("wav", name="same")
    steps = plan([one, two], MP3)
    assert not steps[0].skipped
    assert steps[1].note == "two files would land on the same name"


def test_flac_to_mp3_produces_a_real_mp3_and_keeps_the_originals(tagged):
    track = tagged("flac")
    step = plan([track], MP3)[0]
    convert(step, MP3, MP3.quality("v0"))

    assert step.dst.exists(), "the mp3 was written"
    assert track.path.exists(), "the flac is untouched"
    out = tagio.read(step.dst)
    assert out.error is None
    assert out.info.bitrate > 0
    assert out.family == "id3"


@pytest.mark.parametrize("src_ext,fmt_key,quality_key", [
    ("wav", "mp3", "320"),
    ("flac", "mp3", "v2"),
    ("wav", "flac", "default"),
    ("mp3", "wav", "16"),
    ("flac", "m4a", "192"),
])
def test_every_offered_conversion_round_trips_its_tags(tagged, src_ext,
                                                       fmt_key, quality_key):
    fmt = BY_KEY[fmt_key]
    track = tagged(src_ext)
    step = plan([track], fmt)[0]
    convert(step, fmt, fmt.quality(quality_key))

    out = tagio.read(step.dst)
    assert out.error is None
    assert out.values.get("artist") == "Nirvana"
    assert out.values.get("album") == "Nevermind"
    assert out.values.get("title") == "In Bloom"
    assert str(out.values.get("year", "")).startswith("1991")
    assert str(out.values.get("track", "")).startswith("2")


def test_cover_art_survives_the_conversion(tagged, jpeg_bytes):
    track = tagged("flac", jpeg=jpeg_bytes)
    assert track.pictures, "the fixture really carries art"
    step = plan([track], MP3)[0]
    convert(step, MP3, MP3.default_quality)

    out = tagio.read(step.dst)
    assert out.pictures and out.pictures[0].data == jpeg_bytes


def test_the_tags_are_written_by_rifftag_not_copied_by_ffmpeg(tagged):
    """ffmpeg's own metadata copy would bypass the ID3 version choice, so the
    encode must start clean and tagio must put the tags on afterwards."""
    track = tagged("flac")
    step = plan([track], MP3)[0]
    convert(step, MP3, MP3.default_quality,
            options=WriteOptions(id3_version=4))
    assert "2.4" in tagio.read(step.dst).tag_version

    step2 = plan([track], MP3, "MP3-23")[0]
    convert(step2, MP3, MP3.default_quality,
            options=WriteOptions(id3_version=3))
    assert "2.3" in tagio.read(step2.dst).tag_version


def test_copy_tags_off_leaves_the_output_bare(tagged):
    track = tagged("flac")
    step = plan([track], MP3)[0]
    convert(step, MP3, MP3.default_quality, copy_tags=False)
    assert not tagio.read(step.dst).has_tag


def test_a_failed_encode_leaves_no_half_written_file(tmp_path, tagged):
    """A stub left behind would be read as "destination exists" next time and
    the file would be skipped for good."""
    broken = tmp_path / "not-audio.flac"
    broken.write_bytes(b"this is not a flac file")
    track = tagio.read(broken)
    step = plan([track], MP3)[0]

    with pytest.raises(ConvertError) as caught:
        convert(step, MP3, MP3.default_quality)
    assert "not-audio.flac" in str(caught.value)
    assert not step.dst.exists()


def test_a_missing_source_is_reported_not_crashed_on(tagged):
    track = tagged("flac")
    step = plan([track], MP3)[0]
    track.path.unlink()
    with pytest.raises(ConvertError, match="source file is gone"):
        convert(step, MP3, MP3.default_quality)


def test_a_missing_ffmpeg_says_how_to_install_it(tagged, monkeypatch):
    from rifftag import convert as convert_mod
    monkeypatch.setattr(convert_mod.shutil, "which", lambda name: None)
    track = tagged("flac")
    step = plan([track], MP3)[0]
    with pytest.raises(ConvertError, match="apt install ffmpeg"):
        convert_mod.convert(step, MP3, MP3.default_quality)


def test_run_converts_the_batch_and_reports_progress(tagged):
    tracks = [tagged("flac", name=f"t{n}") for n in range(3)]
    steps = plan(tracks, MP3)
    seen = []
    failures = run(steps, MP3, MP3.quality("v2"),
                   progress=lambda done, total: seen.append((done, total)))
    assert failures == []
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert all(s.dst.exists() for s in steps)


def test_one_bad_file_does_not_abandon_the_rest(tagged, tmp_path):
    good = [tagged("flac", name=f"ok{n}") for n in range(2)]
    broken = tmp_path / "broken.flac"
    broken.write_bytes(b"nope")
    steps = plan([good[0], tagio.read(broken), good[1]], MP3)

    failures = run(steps, MP3, MP3.quality("v2"))
    assert len(failures) == 1
    assert failures[0][0].src.name == "broken.flac"
    assert all(plan([t], MP3)[0].dst.exists() for t in good)


def test_a_stop_request_is_honoured_between_files(tagged):
    tracks = [tagged("flac", name=f"t{n}") for n in range(4)]
    steps = plan(tracks, MP3)
    done = []

    def progress(i, total):
        done.append(i)

    run(steps, MP3, MP3.quality("v2"), progress=progress,
        should_stop=lambda: len(done) >= 2)
    assert len(done) == 2, "stopped after the second file"
    assert not steps[3].dst.exists()


def test_every_format_offers_at_least_one_quality():
    for fmt in FORMATS:
        assert fmt.qualities
        assert fmt.default_quality is fmt.qualities[0]
        assert fmt.extension.startswith(".")


def test_an_unknown_quality_key_falls_back_to_the_default():
    assert MP3.quality("nonsense") is MP3.default_quality


def test_every_encoder_we_offer_is_actually_installed():
    """The quality tables name encoders by ffmpeg's own ids; a typo would
    only surface as a failed conversion at the worst moment."""
    if not ffmpeg_available():
        pytest.skip("ffmpeg is needed")
    listed = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                            capture_output=True, text=True).stdout
    for fmt in FORMATS:
        for quality in fmt.qualities:
            codec = quality.args[quality.args.index("-c:a") + 1]
            assert f" {codec} " in listed, f"{fmt.label}/{quality.key}: {codec}"


# --- what the MP3 quality labels promise --------------------------------


def _mp3_qualities():
    from rifftag.convert import FORMATS
    return next(f for f in FORMATS if f.key == "mp3").qualities


def test_vbr_is_offered_first_and_says_average():
    """245 next to 320 reads as "worse" unless the label explains itself.

    V0 varies the bitrate per frame and 245 is only the average; 320 CBR
    spends 320 on the near-silence too. Dave read the old label exactly the
    way the numbers invite, which is what these words are for.
    """
    qualities = _mp3_qualities()
    assert qualities[0].key == "v0", "VBR comes first"
    assert "average" in qualities[0].label
    assert "average" in qualities[1].label


def test_the_cbr_entries_say_why_you_would_pick_one():
    """A fixed rate is a compliance choice, not a quality one."""
    for quality in _mp3_qualities():
        if quality.key.isdigit():
            assert "fixed rate" in quality.label, quality.label
    by_key = {q.key: q for q in _mp3_qualities()}
    assert "distributor" in by_key["320"].label


def test_the_quality_keys_are_unchanged_by_relabelling():
    """Keys are what a saved preference stores; labels are prose."""
    assert [q.key for q in _mp3_qualities()] == ["v0", "v2", "320", "256", "192"]
