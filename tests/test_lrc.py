"""The LRC format: stamps in, stamps out."""

import pytest

from rifftag import lrc
from rifftag.lrc import Line, build, format_stamp, parse, parse_stamp, shift


def test_a_stamp_is_centiseconds_two_digits():
    """Three-digit milliseconds are rejected by enough players to matter."""
    assert format_stamp(0) == "[00:00.00]"
    assert format_stamp(83_456) == "[01:23.45]"
    assert format_stamp(599) == "[00:00.59]"


def test_minutes_are_not_capped_at_an_hour():
    """There is no hours field in LRC: 70 minutes is [70:12.00]."""
    assert format_stamp(70 * 60_000 + 12_000) == "[70:12.00]"


def test_a_negative_position_cannot_produce_a_negative_stamp():
    assert format_stamp(-500) == "[00:00.00]"


@pytest.mark.parametrize("text,ms", [
    ("[01:23.45]", 83_450),
    ("[01:23]", 83_000),
    ("[00:00.09]", 90),
    ("[01:23.456]", 83_456),
])
def test_stamps_are_read_back_leniently(text, ms):
    assert parse_stamp(text) == ms


def test_a_metadata_tag_is_not_a_stamp():
    assert parse_stamp("[ar:Nirvana]") is None
    assert parse_stamp("not a stamp") is None


def test_a_stamp_round_trips():
    for ms in (0, 999, 60_000, 83_450, 3_600_000):
        assert parse_stamp(format_stamp(ms)) == ms - ms % 10


def test_plain_lyrics_split_into_tappable_lines_keeping_the_gaps():
    lines = lrc.split_for_sync("One\nTwo\n\nThree\n\n\n")
    assert [l.text for l in lines] == ["One", "Two", "", "Three"]
    assert all(not l.timed for l in lines)


def test_empty_lyrics_split_into_nothing():
    assert lrc.split_for_sync("") == []


def test_building_leaves_untimed_lines_out():
    lines = [Line("One", 1000), Line("Two"), Line("Three", 3000)]
    assert build(lines) == "[00:01.00]One\n[00:03.00]Three\n"


def test_building_sorts_by_time():
    """A line re-tapped out of order would make a player jump backwards."""
    lines = [Line("Later", 9000), Line("Earlier", 1000)]
    assert build(lines).splitlines() == ["[00:01.00]Earlier",
                                         "[00:09.00]Later"]


def test_metadata_comes_first_and_only_when_known():
    out = build([Line("One", 0)], {"ti": "In Bloom", "ar": "Nirvana",
                                   "al": "", "by": None})
    assert out.splitlines()[:2] == ["[ti:In Bloom]", "[ar:Nirvana]"]
    assert "[al:" not in out


def test_nothing_timed_builds_an_empty_file():
    assert build([Line("One"), Line("Two")]) == ""


def test_a_timed_blank_line_is_kept_as_a_screen_clear():
    assert build([Line("", 5000)]) == "[00:05.00]\n"


def test_an_existing_lrc_is_read_back(tmp_path):
    text = ("[ti:In Bloom]\n[ar:Nirvana]\n\n"
            "[00:12.30]Sell the kids for food\n"
            "[00:15.00]Weather changes moods\n")
    lines, meta = parse(text)
    assert meta == {"ti": "In Bloom", "ar": "Nirvana"}
    assert [(l.text, l.ms) for l in lines] == [
        ("Sell the kids for food", 12_300),
        ("Weather changes moods", 15_000)]


def test_a_repeated_line_with_several_stamps_becomes_several_lines():
    lines, _ = parse("[00:10.00][01:10.00]Come as you are")
    assert [(l.text, l.ms) for l in lines] == [
        ("Come as you are", 10_000), ("Come as you are", 70_000)]


def test_plain_text_inside_an_lrc_survives_as_untimed():
    lines, _ = parse("[00:01.00]One\nTwo never got a stamp")
    assert lines[1].text == "Two never got a stamp"
    assert not lines[1].timed


def test_an_lrc_round_trips_through_parse_and_build():
    text = "[ti:In Bloom]\n\n[00:12.30]One\n[00:15.00]Two\n"
    lines, meta = parse(text)
    assert build(lines, meta) == text


def test_shifting_moves_every_stamp():
    lines = shift([Line("One", 1000), Line("Two", 2000)], 500)
    assert [l.ms for l in lines] == [1500, 2500]


def test_shifting_cannot_go_below_zero():
    assert shift([Line("One", 200)], -5000)[0].ms == 0


def test_shifting_leaves_untimed_lines_untimed():
    assert not shift([Line("One")], 500)[0].timed


class _Track:
    def __init__(self, path, **values):
        self.path = path
        self.values = values


def test_metadata_is_taken_from_the_tags(tmp_path):
    track = _Track(tmp_path / "x.mp3", title="In Bloom", artist="Nirvana",
                   album="Nevermind")
    meta = lrc.metadata_for(track, length_ms=255_000)
    assert meta == {"ti": "In Bloom", "ar": "Nirvana", "al": "Nevermind",
                    "length": "04:15"}


def test_metadata_skips_what_the_file_does_not_carry(tmp_path):
    assert lrc.metadata_for(_Track(tmp_path / "x.mp3")) == {}


def test_the_lrc_goes_beside_the_track(tmp_path):
    track = _Track(tmp_path / "02 In Bloom.flac")
    assert lrc.default_path(track) == tmp_path / "02 In Bloom.lrc"
