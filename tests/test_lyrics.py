"""Lyrics cleanup.

The thing these tests mostly guard is the *conservative* half of the rule:
scaffolding goes, but a line that might be a lyric always stays.
"""

import pytest

from rifftag import lyrics
from rifftag.lyrics import CleanOptions, clean, describe, find_lyrics_file


SONG = """[Verse 1]
Hold on, the night is young
I hear you calling

[Chorus]
Come as you are (x2)
As you were

Chorus:
Come as you are

Written by Dave Yeubanks
© 2026 Ecobyte Collective
"""


def test_the_scaffolding_goes_and_the_song_stays():
    out = clean(SONG)
    assert "[Verse 1]" not in out
    assert "[Chorus]" not in out
    assert "Chorus:" not in out
    assert "(x2)" not in out
    assert "Written by" not in out
    assert "©" not in out
    assert "Hold on, the night is young" in out
    assert "Come as you are" in out


def test_a_lyric_that_merely_ends_in_a_colon_is_not_a_section_header():
    """The reason bare words are matched against a closed list."""
    text = "Hold on:\nSomething's coming\nMy love:"
    assert clean(text) == text


def test_an_unknown_bracketed_line_still_goes():
    """Bracketed-only lines are scaffolding by construction."""
    assert clean("[Whispered backwards]\nWords") == "Words"
    assert clean("(spoken, distant)\nWords") == "Words"


def test_brackets_inside_a_line_are_left_alone():
    line = "I can't (I can't) let you go"
    assert clean(line) == line


def test_repeat_markers_go_without_taking_the_line():
    assert clean("Come as you are (x2)") == "Come as you are"
    assert clean("Come as you are [2x]") == "Come as you are"
    assert clean("Come as you are (repeat 3x)") == "Come as you are"
    assert clean("Come as you are (repeat)") == "Come as you are"


def test_lrc_timestamps_are_stripped_but_the_words_kept():
    text = "[ar:Nirvana]\n[00:12.34]Come as you are\n[1:02]As you were"
    assert clean(text) == "Come as you are\nAs you were"


def test_looks_like_lrc_spots_a_synced_file():
    assert lyrics.looks_like_lrc("[00:12.34]Come as you are")
    assert not lyrics.looks_like_lrc("Come as you are")


def test_html_from_a_web_paste_is_unwrapped():
    out = clean("Line one<br>Line two<br/>Caf&eacute; &amp; bar")
    assert out == "Line one\nLine two\nCafé & bar"


def test_smart_punctuation_is_flattened():
    """ID3v2.3 drops to UTF-16 the moment a character leaves latin-1, and a
    stray smart quote is the usual cause of mojibake at the distributor."""
    out = clean("“Don’t” — she said…")
    assert out == '"Don\'t" - she said...'


def test_smart_punctuation_can_be_kept():
    text = "Don’t"
    assert clean(text, CleanOptions(plain_punctuation=False)) == text


def test_blank_runs_collapse_to_one_and_the_ends_are_trimmed():
    out = clean("\n\n\nOne\n\n\n\nTwo\n\n\n")
    assert out == "One\n\nTwo"


def test_trailing_space_and_tabs_go():
    assert clean("One   \nTwo\tthree  ") == "One\nTwo three"


def test_shouting_is_left_alone_unless_asked():
    text = "I WILL NOT GO QUIETLY"
    assert clean(text) == text
    assert clean(text, CleanOptions(fix_shouting=True)) == \
        "I will not go quietly"


def test_a_short_capitalised_word_is_not_shouting():
    assert clean("OK", CleanOptions(fix_shouting=True)) == "OK"


def test_unshouting_keeps_the_personal_pronoun():
    out = clean("I'M READY AND I KNOW IT", CleanOptions(fix_shouting=True))
    assert out.startswith("I'm ready")
    assert " I know it" in out


def test_every_option_can_be_turned_off():
    untouched = clean(SONG, CleanOptions(
        section_headers=False, repeat_markers=False, credits=False,
        timestamps=False, html=False, plain_punctuation=False,
        collapse_blanks=False, trim_space=False))
    assert untouched == SONG.replace("\r\n", "\n")


def test_cleaning_twice_changes_nothing_the_second_time():
    once = clean(SONG)
    assert clean(once) == once


def test_empty_lyrics_are_handled():
    assert clean("") == ""
    assert describe("") == []


def test_describe_counts_what_would_change():
    kinds = {c.kind: c.count for c in describe(SONG)}
    assert kinds["section_headers"] == 3
    assert kinds["repeat_markers"] == 1
    assert kinds["credits"] == 2
    assert str(describe("Come as you are (x2)")[0]) == "1 repeat marker"


def test_describe_is_plural_aware():
    text = "[Verse]\nOne\n[Chorus]\nTwo"
    assert "2 section headers" in [str(c) for c in describe(text)]


def test_nothing_to_do_describes_nothing():
    assert describe("Come as you are\n\nAs you were") == []


# --- importing lyrics from files beside the music -----------------------

class _Track:
    def __init__(self, path, title=""):
        self.path = path
        self.values = {"title": title} if title else {}


def test_a_matching_text_file_is_found(tmp_path):
    song = tmp_path / "02 In Bloom.mp3"
    song.touch()
    (tmp_path / "02 In Bloom.txt").write_text("words")
    assert find_lyrics_file(_Track(song)).name == "02 In Bloom.txt"


def test_the_title_is_matched_when_the_file_name_differs(tmp_path):
    song = tmp_path / "02 - In Bloom.mp3"
    song.touch()
    (tmp_path / "In Bloom.txt").write_text("words")
    assert find_lyrics_file(_Track(song, "In Bloom")).name == "In Bloom.txt"


def test_punctuation_and_case_do_not_stop_a_match(tmp_path):
    song = tmp_path / "Don't Look Back.mp3"
    song.touch()
    (tmp_path / "dont look back.TXT").write_text("words")
    assert find_lyrics_file(_Track(song)) is not None


def test_an_lrc_file_counts_too(tmp_path):
    song = tmp_path / "In Bloom.flac"
    song.touch()
    (tmp_path / "In Bloom.lrc").write_text("[00:01.00]words")
    assert find_lyrics_file(_Track(song)).suffix == ".lrc"


def test_an_ambiguous_match_is_refused_rather_than_guessed(tmp_path):
    song = tmp_path / "02 In Bloom.mp3"
    song.touch()
    (tmp_path / "In Bloom.txt").write_text("one")
    (tmp_path / "02 In Bloom (alt).txt").write_text("two")
    # "In Bloom" and "02 In Bloom (alt)" both loosely match: refuse.
    assert find_lyrics_file(_Track(song, "In Bloom")) is not None  # exact wins
    (tmp_path / "02 In Bloom.txt").write_text("three")
    (tmp_path / "02inbloom.txt").write_text("four")
    assert find_lyrics_file(_Track(song)) is None, "two exact-ish matches"


def test_no_text_file_means_no_match(tmp_path):
    song = tmp_path / "In Bloom.mp3"
    song.touch()
    assert find_lyrics_file(_Track(song)) is None


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
def test_a_lyrics_file_is_read_whatever_it_was_saved_as(tmp_path, encoding):
    path = tmp_path / "words.txt"
    path.write_bytes("Café — night".encode(encoding))
    assert "Café" in lyrics.read_text(path)


def test_an_undecodable_file_is_not_a_crash(tmp_path):
    path = tmp_path / "words.txt"
    path.write_bytes(b"\xff\xfe\x00broken")
    assert isinstance(lyrics.read_text(path), str)
