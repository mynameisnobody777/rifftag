"""Mask engine tests.

The cases marked HELP are worked examples lifted straight out of Tag&Rename's
own help file, so they pin our behaviour to the original rather than to
whatever we happened to implement.
"""

import pytest

from rifftag.casing import Case, CaseRules
from rifftag.masks import (
    MaskError,
    RenderOptions,
    describe,
    parse,
    render,
    sanitize_component,
    tokenize,
)
from rifftag.fields import RENAME_TOKENS, RENAME_VARS

TAGS = {
    "artist": "Nirvana",
    "title": "Smells Like Teen Spirit",
    "album": "Nevermind",
    "year": "1991",
    "genre": "Grunge",
    "track": "1",
    "disc": "01",
    "album_artist": "Nirvana",
    "composer": "Kurt Cobain",
    "bpm": "117",
}


# --- tokenizer ----------------------------------------------------------

def test_longest_token_wins():
    """%aa must not be read as %a-plus-stray-a, and %snr not as %s."""
    nodes = tokenize("%aa - %snr", RENAME_TOKENS, RENAME_VARS)
    assert [getattr(n, "token", None) for n in nodes if hasattr(n, "token")] == ["%aa", "%snr"]


def test_bare_percent_is_literal():
    assert render("%1 100%% live", {**TAGS}) == "Nirvana 100%% live"


# --- rendering ----------------------------------------------------------

def test_help_example_artist_album_title():
    """HELP: '%1 - %3 - %2' produces 'Artist - Album - Title'."""
    assert render("%1 - %3 - %2", TAGS) == "Nirvana - Nevermind - Smells Like Teen Spirit"


def test_help_example_folder_structure():
    """HELP: mask '%1 - %2\\%8 %2' -> 'Artist - Album/01 Title'.

    The help's own table is loose about which variable is which here; what is
    being pinned is that a backslash creates a folder level and that the file
    number is zero padded to two digits.
    """
    out = render("%1 - %3\\%8 %2", {**TAGS, "file_number": 1})
    assert out == "Nirvana - Nevermind\\01 Smells Like Teen Spirit"


def test_track_is_zero_padded():
    assert render("%6 %2", TAGS) == "01 Smells Like Teen Spirit"


def test_track_written_as_x_of_y_keeps_only_the_number():
    """A "3/12" track must not put a slash in the file name."""
    assert render("%6", {"track": "3/12"}) == "03"


def test_missing_field_refuses_rather_than_leaving_a_hole():
    with pytest.raises(MaskError, match="Composer"):
        render("%cp - %2", {"title": "x"})


def test_mask_without_variables_is_rejected():
    with pytest.raises(MaskError):
        render("just text", TAGS)


def test_underscores_option():
    opts = RenderOptions(underscores=True)
    assert render("%1 - %2", TAGS, options=opts) == \
        "Nirvana_-_Smells_Like_Teen_Spirit"


def test_case_option_applies_per_component():
    opts = RenderOptions(case=CaseRules(mode=Case.UPPER))
    assert render("%1\\%2", TAGS, options=opts) == \
        "NIRVANA\\SMELLS LIKE TEEN SPIRIT"


def test_strip_disc_leading_zero():
    opts = RenderOptions(strip_disc_leading_zero=True)
    assert render("CD%#", TAGS, options=opts) == "CD1"
    assert render("CD%#", TAGS) == "CD01"


def test_separator_slash_is_accepted_and_normalised():
    assert render("%1/%2", TAGS) == render("%1\\%2", TAGS)


def test_random_variable_is_four_digits():
    out = render("%rnd", TAGS)
    assert len(out) == 4 and out.isdigit()


# --- sanitising ---------------------------------------------------------

def test_illegal_characters_are_replaced_for_ntfs_and_samba():
    assert sanitize_component('AC/DC: Back?') == "AC_DC_ Back_"


def test_trailing_dot_stripped_because_windows_drops_it():
    assert sanitize_component("Vol. 1.") == "Vol. 1"


def test_colon_in_a_title_does_not_leak_into_the_name():
    out = render("%2", {"title": "Rock: The Sequel"})
    assert ":" not in out


def test_slash_in_a_tag_value_cannot_create_a_directory():
    out = render("%1", {"artist": "AC/DC"})
    assert out == "AC_DC"


def test_empty_component_is_rejected():
    with pytest.raises(MaskError):
        render("%2", {"title": "..."})


# --- parsing ------------------------------------------------------------

def test_help_example_filename_to_tag():
    """HELP: files like '01 Artist - Title.mp3' with mask '%6 %1 - %2'."""
    got = parse("%6 %1 - %2", "01 The Ramones - Blitzkrieg Bop")
    assert got == {"track": "01", "artist": "The Ramones", "title": "Blitzkrieg Bop"}


def test_help_example_folder_supplies_artist_and_album():
    """HELP: folder named 'Artist - Album', mask '%1 - %3\\%2'."""
    got = parse("%1 - %3\\%2", "/music/Nirvana - Nevermind/Smells Like Teen Spirit")
    assert got == {"artist": "Nirvana", "album": "Nevermind",
                   "title": "Smells Like Teen Spirit"}


def test_leading_variables_are_non_greedy_so_the_last_one_absorbs_the_rest():
    got = parse("%1 - %2", "AC - DC - Thunder")
    assert got == {"artist": "AC", "title": "DC - Thunder"}


def test_skip_variable_discards_without_capturing():
    got = parse("%d - %1 - %2", "ignore me - Nirvana - Lithium")
    assert got == {"artist": "Nirvana", "title": "Lithium"}


def test_numeric_field_captures_digits_only():
    """Without digit-only capture, '%6 %1 - %2' mis-splits a numeric artist."""
    got = parse("%6 %1 - %2", "07 2Pac - Changes")
    assert got["track"] == "07"
    assert got["artist"] == "2Pac"


def test_non_matching_mask_raises():
    with pytest.raises(MaskError):
        parse("%6 %1 - %2", "no track number here")


def test_empty_capture_is_dropped_not_written_as_blank():
    got = parse("%1 - %2", " - Lithium")
    assert "artist" not in got
    assert got["title"] == "Lithium"


def test_parse_applies_case_rules():
    got = parse("%1 - %2", "nirvana - lithium",
                case=CaseRules(mode=Case.TITLE))
    assert got == {"artist": "Nirvana", "title": "Lithium"}


def test_parse_rejects_derived_variables():
    """%0 is bitrate — read-only, so it is a literal here, not a capture."""
    with pytest.raises(MaskError):
        parse("%0", "320")


# --- preview ------------------------------------------------------------

def test_describe_matches_the_screenshot_preview():
    """The help screenshot shows mask '%6 %2' previewing as '<Track #> <Title>'."""
    assert describe("%6 %2") == "<Track #> <Title>"
