"""Track-number formatting and auto-fill."""

import pytest

from rifftag import tracknum
from rifftag.tracknum import BY_KEY, autofill, reformat


class FakeTrack:
    def __init__(self, **values):
        self.values = values


def _tracks(*numbers, total=None):
    out = []
    for n in numbers:
        v = {"track": n} if n is not None else {}
        if total:
            v["track_total"] = total
        out.append(FakeTrack(**v))
    return out


# --- the five menu formats ----------------------------------------------

@pytest.mark.parametrize("key,expected", [
    # Read literally: 0x is "a zero, then the number", not "pad to two".
    ("x", ["1", "2", "10"]),
    ("0x", ["01", "02", "010"]),
    ("00x", ["001", "002", "0010"]),
    ("xx", ["01", "02", "10"]),
])
def test_number_formats(key, expected):
    plans = reformat(_tracks("1", "02", "10"), BY_KEY[key])
    assert [p["track"] for p in plans] == expected


def test_xx_slash_xx_includes_the_total():
    plans = reformat(_tracks("1", "2", "3", total="12"), BY_KEY["xx/xx"])
    assert [p["track"] for p in plans] == ["01/12", "02/12", "03/12"]


def test_every_format_is_reachable_from_the_menu_list():
    assert [f.key for f in tracknum.FORMATS] == ["x", "0x", "00x", "xx/xx", "xx"]


def test_xx_slash_xx_clears_the_separate_total_field():
    """The total now lives inside the track field; two copies could disagree."""
    plans = reformat(_tracks("1", total="12"), BY_KEY["xx/xx"])
    assert plans[0]["track_total"] == ""


def test_xx_slash_xx_falls_back_to_the_selection_size():
    plans = reformat(_tracks("1", "2", "3"), BY_KEY["xx/xx"])
    assert plans[0]["track"] == "01/03"


def test_xx_and_0x_differ_once_numbers_reach_two_digits():
    """The reason xx is read literally: otherwise it duplicates 0x."""
    tracks = _tracks("10")
    assert reformat(tracks, BY_KEY["xx"])[0]["track"] == "10"
    assert reformat(tracks, BY_KEY["0x"])[0]["track"] == "010"


def test_formats_read_a_number_out_of_an_x_of_y_value():
    plans = reformat(_tracks("7/12"), BY_KEY["0x"])
    assert plans[0]["track"] == "07"


def test_a_file_with_no_track_number_is_left_alone():
    """Reformatting restyles what is there; it must not invent numbers."""
    plans = reformat(_tracks("1", None, "3"), BY_KEY["0x"])
    assert plans[1] == {}
    assert [p.get("track") for p in plans] == ["01", None, "03"]


def test_a_non_numeric_track_value_is_left_alone():
    plans = reformat(_tracks("A-side"), BY_KEY["0x"])
    assert plans[0] == {}


def test_every_menu_entry_has_a_distinct_example():
    examples = [f.example() for f in tracknum.FORMATS]
    assert len(set(examples)) == len(examples), examples


# --- auto fill -----------------------------------------------------------

def test_autofill_numbers_consecutively_from_one():
    plans = autofill(3)
    assert [p["track"] for p in plans] == ["01", "02", "03"]
    assert plans[0]["track_total"] == "3"


def test_autofill_can_start_anywhere():
    plans = autofill(3, start=7)
    assert [p["track"] for p in plans] == ["07", "08", "09"]


def test_autofill_start_affects_the_total_too():
    """Starting at 7 for 3 files means 7..9, so the total is 9, not 3."""
    plans = autofill(3, start=7)
    assert plans[0]["track_total"] == "9"


def test_autofill_crosses_into_three_digits():
    plans = autofill(3, start=99)
    assert [p["track"] for p in plans] == ["99", "100", "101"]


def test_autofill_with_the_xx_of_xx_option():
    plans = autofill(3, use_total=True)
    assert [p["track"] for p in plans] == ["01/03", "02/03", "03/03"]
    assert plans[0]["track_total"] == ""


def test_autofill_can_start_from_zero():
    assert autofill(2, start=0)[0]["track"] == "00"
