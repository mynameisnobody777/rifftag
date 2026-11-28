"""The file types RiffTag claims, and the files that have to agree about them.

These exist because of one bug: right-click a .wav, Open with, and RiffTag
was not in the list. The desktop entry claimed ``audio/x-wav``;
shared-mime-info calls that file ``audio/vnd.wave`` and does not resolve the
alias when building the menu. Three hand-maintained lists had drifted from
each other and from the app's own format support, so the lists are generated
from one module now and these tests fail if any of them drifts again.
"""

import re
from pathlib import Path

from rifftag import tagio
from rifftag.desktopfile import (MIME_BY_EXTENSION, VIDEO_EXTENSIONS,
                                 desktop_entry_text, extensions, mime_line,
                                 mime_types)

ROOT = Path(__file__).resolve().parents[1]


def test_wav_claims_the_type_the_desktop_actually_uses():
    """The whole bug, in one assertion."""
    assert "audio/vnd.wave" in mime_types()
    assert "audio/vnd.wave" in MIME_BY_EXTENSION[".wav"]


def test_the_alias_is_kept_as_well_as_the_canonical_name():
    """Which spelling is canonical differs by distribution; claim both."""
    assert "audio/x-wav" in mime_types()


def test_every_format_the_app_reads_is_claimed_or_deliberately_not():
    """No format may be silently missing from the associations.

    tagio.EXT_FAMILY is the app's real answer to "can you open this". Each
    entry is either in MIME_BY_EXTENSION or named in VIDEO_EXTENSIONS, which
    is the explicit decision not to put a tag editor in the Open-with menu of
    every video on the machine.
    """
    missing = [e for e in tagio.AUDIO_EXTENSIONS
               if e not in MIME_BY_EXTENSION and e not in VIDEO_EXTENSIONS]
    assert not missing, f"no MIME types decided for {missing}"


def test_video_containers_are_not_claimed():
    for ext in VIDEO_EXTENSIONS:
        assert ext not in extensions()


def test_the_mime_line_is_a_valid_desktop_entry_value():
    line = mime_line()
    assert line.startswith("MimeType=")
    assert line.endswith(";"), "the spec wants a trailing semicolon"
    values = [v for v in line[len("MimeType="):].split(";") if v]
    assert len(values) == len(set(values)), "duplicates in the MimeType list"
    for value in values:
        assert re.fullmatch(r"[\w.+-]+/[\w.+-]+", value), value


def test_the_entry_carries_the_file_argument():
    """Without %F the launcher never hands the clicked file over."""
    assert "Exec=rifftag %F\n" in desktop_entry_text("rifftag %F")


def test_the_flatpak_entry_keeps_its_own_icon_name():
    """Flathub requires the icon to be named after the application id."""
    assert "Icon=com.nobody777.RiffTag\n" in desktop_entry_text(
        "rifftag %F", "com.nobody777.RiffTag")


# --- the shipped files -------------------------------------------------

def _entry(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1)
                for line in path.read_text().splitlines()
                if "=" in line and not line.startswith("["))


def test_the_flatpak_desktop_file_is_in_step():
    entry = _entry(ROOT / "packaging/flatpak/com.nobody777.RiffTag.desktop")
    assert entry["MimeType"] == mime_line()[len("MimeType="):]
    assert entry["Exec"].endswith("%F")
    assert entry["Icon"] == "com.nobody777.RiffTag"


def test_the_development_desktop_file_is_in_step():
    entry = _entry(ROOT / "rifftag.desktop")
    assert entry["MimeType"] == mime_line()[len("MimeType="):]
    assert entry["Exec"].endswith("%F")


def test_the_deb_generates_its_entry_rather_than_repeating_it():
    """A hand-written copy in the build script is how this drifted before."""
    script = (ROOT / "packaging/build-deb.sh").read_text()
    assert "desktop_entry_text" in script
    assert "MimeType=audio" not in script


def test_the_windows_extension_list_matches():
    script = (ROOT / "packaging/register-windows.ps1").read_text()
    block = script[script.index("$extensions = @("):]
    block = block[:block.index(")")]
    listed = tuple(sorted(re.findall(r'"([^"]+)"', block)))
    assert listed == extensions()


def test_matroska_video_is_claimed():
    """Dave right-clicks .mkv files to tag them (2026-11-27)."""
    assert ".mkv" in extensions() and ".webm" in extensions()
    assert "video/x-matroska" in mime_types()
    assert "video/webm" in mime_types()
