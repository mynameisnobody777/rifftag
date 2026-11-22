"""Release pre-flight checks.

The fixtures build real tagged files so the checks run against what a
distributor would actually receive, and the loudness test runs a real ffmpeg
analysis pass.
"""

import pytest

from rifftag import preflight, tagio
from rifftag.preflight import ERROR, NOTE, WARNING, check


def _png(width: int, height: int) -> bytes:
    """A PNG header the dimension probe can read — no encoder needed."""
    import struct
    import zlib

    def chunk(kind, data):
        body = kind + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body)))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00" * 16))
            + chunk(b"IEND", b""))


@pytest.fixture
def album(sample, tmp_path):
    """Three tracks that pass every check, as a starting point."""
    art = [tagio.Picture(_png(3000, 3000), mime="image/png")]
    folder = tmp_path / "release"
    folder.mkdir()
    made = []
    for n, title in enumerate(["Opening", "Middle Eight", "The Long Way"], 1):
        path = folder / f"{n:02d} {title}.wav"
        sample("wav", f"t{n}").rename(path)
        tagio.write(path, {"artist": "Dave Yeubanks", "album_artist":
                           "Dave Yeubanks", "album": "Scratch Tapes",
                           "title": title, "track": str(n),
                           "track_total": "3", "year": "2026",
                           "genre": "Indie", "isrc": f"QZABC260000{n}"},
                    pictures=art)
        made.append(path)
    return [tagio.read(p) for p in made]


def codes(report, level=None):
    return {f.code for f in report.findings
            if level is None or f.level == level}


def test_a_clean_release_passes(album):
    report = check(album)
    assert codes(report, ERROR) == set()
    assert codes(report, WARNING) == set()
    assert report.summary == "No problems found."


def test_an_empty_selection_is_not_a_crash():
    assert check([]).findings == []


def test_a_missing_title_is_an_error(album, tmp_path):
    tagio.write(album[0].path, {"title": ""})
    report = check([tagio.read(album[0].path)] + album[1:])
    assert "title_missing" in codes(report, ERROR)


def test_artwork_is_required(album):
    tagio.write(album[1].path, {}, pictures=[])
    report = check([album[0], tagio.read(album[1].path), album[2]])
    assert "artwork_missing" in codes(report, ERROR)


def test_small_artwork_is_a_warning_with_its_real_size(album):
    tagio.write(album[0].path, {},
                pictures=[tagio.Picture(_png(1400, 1400), mime="image/png")])
    report = check([tagio.read(album[0].path)] + album[1:])
    finding = [f for f in report.findings if f.code == "artwork_small"][0]
    assert finding.level is WARNING or finding.level == WARNING
    assert "1400x1400" in finding.message
    assert "3000x3000" in finding.message


def test_non_square_artwork_is_flagged(album):
    tagio.write(album[0].path, {},
                pictures=[tagio.Picture(_png(3000, 2000), mime="image/png")])
    report = check([tagio.read(album[0].path)] + album[1:])
    assert "artwork_not_square" in codes(report, WARNING)


def test_mixed_artwork_across_the_release_is_flagged(album):
    tagio.write(album[2].path, {},
                pictures=[tagio.Picture(_png(3000, 3000) + b"different",
                                        mime="image/png")])
    report = check(album[:2] + [tagio.read(album[2].path)])
    assert "artwork_inconsistent" in codes(report, WARNING)


def test_an_album_title_that_disagrees_with_itself_is_an_error(album):
    tagio.write(album[2].path, {"album": "Scratch Tapes (Deluxe)"})
    report = check(album[:2] + [tagio.read(album[2].path)])
    assert "album_inconsistent" in codes(report, ERROR)


def test_an_album_artist_that_disagrees_is_a_warning(album):
    tagio.write(album[1].path, {"album_artist": "D. Yeubanks"})
    report = check([album[0], tagio.read(album[1].path), album[2]])
    assert "album_artist_inconsistent" in codes(report, WARNING)


def test_a_duplicate_track_number_is_an_error(album):
    tagio.write(album[2].path, {"track": "2"})
    report = check(album[:2] + [tagio.read(album[2].path)])
    assert "track_number_duplicate" in codes(report, ERROR)


def test_a_gap_in_the_numbering_is_a_warning(album):
    tagio.write(album[2].path, {"track": "7"})
    report = check(album[:2] + [tagio.read(album[2].path)])
    assert "track_number_gap" in codes(report, WARNING)


def test_a_missing_track_number_is_an_error(album):
    tagio.write(album[0].path, {"track": ""})
    report = check([tagio.read(album[0].path)] + album[1:])
    assert "track_number_missing" in codes(report, ERROR)


def test_mixed_title_casing_is_flagged_with_the_shape_of_it(album):
    tagio.write(album[0].path, {"title": "opening"})
    report = check([tagio.read(album[0].path)] + album[1:])
    finding = [f for f in report.findings
               if f.code == "title_casing_inconsistent"][0]
    assert "Title Case" in finding.message and "lower" in finding.message


def test_one_capitalisation_style_is_not_flagged(album):
    assert "title_casing_inconsistent" not in codes(check(album))


def test_feat_in_the_title_is_a_question_not_a_verdict(album):
    """Distributors disagree about this, so it must never be an error."""
    tagio.write(album[0].path, {"title": "Opening (feat. Tammy)"})
    report = check([tagio.read(album[0].path)] + album[1:])
    finding = [f for f in report.findings if f.code == "feat_in_title"][0]
    assert finding.level == NOTE
    assert "distributor" in finding.fix.lower()


def test_feat_in_both_fields_is_left_alone(album):
    tagio.write(album[0].path, {"title": "Opening (feat. Tammy)",
                                "artist": "Dave Yeubanks feat. Tammy"})
    report = check([tagio.read(album[0].path)] + album[1:])
    assert "feat_in_title" not in codes(report)


def test_a_lossy_master_is_a_note_with_a_way_out(album, sample, tmp_path):
    mp3 = tmp_path / "release" / "04 Bonus.mp3"
    sample("mp3", "bonus").rename(mp3)
    tagio.write(mp3, {"artist": "Dave Yeubanks", "album": "Scratch Tapes",
                      "title": "Bonus", "track": "4"})
    report = check(album + [tagio.read(mp3)])
    finding = [f for f in report.findings if f.code == "lossy_master"][0]
    assert finding.level == NOTE
    assert "Convert audio format" in finding.fix


def test_lyrics_scaffolding_points_at_the_cleaner(album):
    tagio.write(album[0].path, {"lyrics": "[Verse 1]\nHold on"})
    report = check([tagio.read(album[0].path)] + album[1:])
    finding = [f for f in report.findings if f.code == "lyrics_scaffolding"][0]
    assert "Clean lyrics" in finding.fix


def test_clean_lyrics_are_not_flagged(album):
    tagio.write(album[0].path, {"lyrics": "Hold on, the night is young"})
    report = check([tagio.read(album[0].path)] + album[1:])
    assert "lyrics_scaffolding" not in codes(report)


def test_a_missing_isrc_is_only_a_note(album):
    tagio.write(album[0].path, {"isrc": ""})
    report = check([tagio.read(album[0].path)] + album[1:])
    assert "isrc_missing" in codes(report, NOTE)
    assert "isrc_missing" not in codes(report, ERROR)


def test_the_summary_counts_each_level(album):
    tagio.write(album[0].path, {"title": "", "isrc": ""})
    report = check([tagio.read(album[0].path)] + album[1:])
    assert "problem" in report.summary
    assert report.of(ERROR)


def test_every_finding_says_where_it_is(album):
    tagio.write(album[0].path, {"title": ""})
    report = check([tagio.read(album[0].path)] + album[1:])
    for finding in report.findings:
        assert finding.where


# --- loudness ------------------------------------------------------------

def test_loudness_is_measured_from_the_real_file(album):
    if not preflight.ffmpeg_available():
        pytest.skip("ffmpeg is needed")
    measured = preflight.measure(album[0].path)
    assert measured.error == ""
    # The fixtures are silence, so the numbers are extreme but must parse.
    assert measured.integrated is None or isinstance(measured.integrated, float)
    assert measured.true_peak is None or isinstance(measured.true_peak, float)


def test_a_hot_true_peak_is_a_warning(album):
    loud = {album[0].path: preflight.Loudness(integrated=-14.0, true_peak=0.8)}
    report = check(album, loudness=loud)
    finding = [f for f in report.findings if f.code == "true_peak_hot"][0]
    assert "+0.8" in finding.message
    assert finding.path == album[0].path


def test_a_safe_true_peak_is_not_flagged(album):
    loud = {album[0].path: preflight.Loudness(integrated=-14.0, true_peak=-1.5)}
    assert "true_peak_hot" not in codes(check(album, loudness=loud))


def test_being_far_off_the_streaming_target_is_a_note(album):
    loud = {album[0].path: preflight.Loudness(integrated=-7.5, true_peak=-2.0)}
    finding = [f for f in check(album, loudness=loud).findings
               if f.code == "loudness_off_target"][0]
    assert finding.level == NOTE
    assert "louder" in finding.message


def test_being_on_target_is_not_mentioned(album):
    loud = {album[0].path: preflight.Loudness(integrated=-13.2, true_peak=-2.0)}
    assert "loudness_off_target" not in codes(check(album, loudness=loud))


def test_a_failed_measurement_is_reported_not_silent(album):
    loud = {album[0].path: preflight.Loudness(error="ffmpeg timed out")}
    assert "loudness_unmeasured" in codes(check(album, loudness=loud), NOTE)


def test_measure_all_reports_progress_and_can_be_stopped(album):
    if not preflight.ffmpeg_available():
        pytest.skip("ffmpeg is needed")
    seen = []
    out = preflight.measure_all(album, progress=lambda d, t: seen.append(d),
                                should_stop=lambda: len(seen) >= 2)
    assert seen == [1, 2]
    assert len(out) == 2, "stopped before the third"


def test_measuring_a_file_that_is_not_audio_fails_cleanly(tmp_path):
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"not audio")
    measured = preflight.measure(broken)
    assert measured.error
    assert measured.integrated is None


def test_a_problem_every_track_shares_is_reported_once(album):
    """A 17-track album with one undersized cover produced 17 identical
    warnings, which buried everything that actually differed."""
    small = [tagio.Picture(_png(1400, 1400), mime="image/png")]
    for track in album:
        tagio.write(track.path, {}, pictures=small)
    report = check([tagio.read(t.path) for t in album])

    rows = [f for f in report.findings if f.code == "artwork_small"]
    assert len(rows) == 1
    assert rows[0].path is None
    assert "every track" in rows[0].message


def test_findings_that_differ_stay_per_file(album):
    tagio.write(album[0].path, {},
                pictures=[tagio.Picture(_png(1400, 1400), mime="image/png")])
    tagio.write(album[1].path, {},
                pictures=[tagio.Picture(_png(600, 600), mime="image/png")])
    report = check([tagio.read(album[0].path), tagio.read(album[1].path),
                    album[2]])
    rows = [f for f in report.findings if f.code == "artwork_small"]
    assert len(rows) == 2, "two different sizes: the file name is the point"
    assert all(f.path is not None for f in rows)


def test_a_single_track_is_never_collapsed(album):
    tagio.write(album[0].path, {},
                pictures=[tagio.Picture(_png(1400, 1400), mime="image/png")])
    report = check([tagio.read(album[0].path)])
    rows = [f for f in report.findings if f.code == "artwork_small"]
    assert rows[0].path is not None
