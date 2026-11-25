"""Command-line targets: what "Open with -> RiffTag" hands us.

Headless on purpose — parse_targets never touches Qt, so these run without a
display and without building a window.
"""

from pathlib import Path

from rifftag.cli import Target, parse_targets


def _songs(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_bytes(b"")
    return [tmp_path / name for name in names]


def test_no_arguments_is_no_target():
    assert not parse_targets(["rifftag"])
    assert parse_targets(["rifftag"]) == Target()
    assert not parse_targets(None)
    assert not parse_targets([])


def test_a_file_opens_its_folder_and_selects_it(tmp_path):
    song, = _songs(tmp_path, "track.mp3")
    target = parse_targets(["rifftag", str(song)])
    assert target.folder == tmp_path
    assert target.files == (song,)
    assert target


def test_a_folder_opens_itself_with_nothing_selected(tmp_path):
    target = parse_targets(["rifftag", str(tmp_path)])
    assert target.folder == tmp_path
    assert target.files == ()


def test_several_files_in_one_folder_are_all_selected(tmp_path):
    a, b = _songs(tmp_path, "a.mp3", "b.mp3")
    target = parse_targets(["rifftag", str(a), str(b)])
    assert target.folder == tmp_path
    assert target.files == (a, b)


def test_files_outside_the_first_folder_are_ignored(tmp_path):
    """Selecting across folders is not something the list can show."""
    other = tmp_path / "other"
    other.mkdir()
    a, = _songs(tmp_path, "a.mp3")
    b, = _songs(other, "b.mp3")
    target = parse_targets(["rifftag", str(a), str(b)])
    assert target.folder == tmp_path
    assert target.files == (a,)


def test_duplicates_are_collapsed(tmp_path):
    song, = _songs(tmp_path, "track.mp3")
    target = parse_targets(["rifftag", str(song), str(song)])
    assert target.files == (song,)


def test_qt_options_are_not_mistaken_for_files(tmp_path):
    """Qt takes -style/-platform out of argv; neither is a path."""
    song, = _songs(tmp_path, "track.mp3")
    target = parse_targets(["rifftag", "-style", "fusion",
                            "-platform", "offscreen", str(song)])
    assert target.folder == tmp_path
    assert target.files == (song,)


def test_a_path_that_does_not_exist_is_ignored(tmp_path):
    assert not parse_targets(["rifftag", str(tmp_path / "gone.mp3")])


def test_file_uris_are_accepted_and_decoded(tmp_path):
    song, = _songs(tmp_path, "a song.mp3")
    target = parse_targets(["rifftag", song.as_uri()])
    assert target.files == (song,)


def test_remote_uris_are_not_ours_to_open(tmp_path):
    assert not parse_targets(["rifftag", "file://elsewhere/music/a.mp3"])


def test_argv0_is_never_a_target(tmp_path):
    """python3 -m rifftag makes argv[0] a real, existing path."""
    song, = _songs(tmp_path, "track.mp3")
    assert parse_targets([str(song)]) == Target()
