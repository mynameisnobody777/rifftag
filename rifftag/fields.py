"""Canonical tag field model.

One vocabulary for every container. The rest of the codebase speaks only these
``Field`` keys; :mod:`rifftag.tagio` is the only module that knows what an
ID3 frame id or an MP4 atom looks like.

Field set and the rename-variable table come from Tag&Rename 3.9.15's own help
file (kept privately at ``apps/_reference/tagrename/``, outside the repo) so that masks Dave already has
memorised keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from enum import Enum


class Kind(Enum):
    TEXT = "text"
    INT = "int"
    BOOL = "bool"
    RATING = "rating"
    PICTURE = "picture"
    LONGTEXT = "longtext"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: Kind = Kind.TEXT
    #: rename / filename-to-tag variable, e.g. ``%1``. ``None`` = not maskable.
    var: str | None = None
    #: a "x of y" counter field, e.g. track 3 of 12 -> companion holds 12
    total_of: str | None = None
    group: str = "additional"


# --- the canonical list -------------------------------------------------
# group names map to the Tag Editor tabs in the original: general / lyrics /
# additional / more / sorting.  ``var`` values are verbatim from the help.

FIELDS: tuple[Field, ...] = (
    Field("title", "Title", var="%2", group="general"),
    Field("artist", "Artist", var="%1", group="general"),
    Field("album", "Album", var="%3", group="general"),
    Field("year", "Year", var="%4", group="general"),
    Field("genre", "Genre", var="%5", group="general"),
    Field("comment", "Comment", Kind.LONGTEXT, var="%9", group="general"),
    Field("track", "Track #", Kind.INT, var="%6", total_of="track_total", group="general"),
    Field("track_total", "Track total", Kind.INT, group="general"),
    Field("disc", "Disc #", Kind.INT, var="%#", total_of="disc_total", group="general"),
    Field("disc_total", "Disc total", Kind.INT, group="general"),
    Field("rating", "Rating", Kind.RATING, group="general"),
    Field("mood", "Mood", var="%md", group="general"),
    Field("bpm", "BPM", Kind.INT, var="%b", group="general"),
    Field("compilation", "Part of a compilation", Kind.BOOL, group="general"),
    Field("picture", "Cover art", Kind.PICTURE, group="general"),

    Field("lyrics", "Lyrics", Kind.LONGTEXT, group="lyrics"),

    Field("composer", "Composer", var="%cp", group="general"),
    Field("orig_artist", "Orig. Artist", group="general"),
    Field("copyright", "Copyright", group="general"),
    Field("url", "URL", group="general"),
    Field("encoded_by", "Encoded by", group="general"),
    Field("album_artist", "Album Artist", var="%aa", group="additional"),
    Field("conductor", "Conductor", var="%co", group="additional"),
    Field("lyricist", "Lyricist", group="additional"),
    Field("orig_lyricist", "Orig. Lyricist", group="additional"),
    Field("label", "Label", var="%lb", group="additional"),
    Field("remixed_by", "Remixed by", var="%rb", group="additional"),
    Field("initial_key", "Initial Key", var="%key", group="additional"),
    Field("grouping", "Grouping", group="additional"),
    Field("subtitle", "Subtitle", group="additional"),
    Field("release_time", "Release Time", var="%rt", group="additional"),
    Field("isrc", "ISRC #", group="additional"),
    Field("catalog", "Catalog #", group="additional"),

    Field("radio_station", "Radio station name", group="more"),
    Field("url_file", "Audio file info page", group="more"),
    Field("url_artist", "Artist web page", group="more"),
    Field("url_source", "Audio source web page", group="more"),
    Field("url_radio", "Radio station web page", group="more"),
    Field("url_buy", "Buy CD web page", group="more"),
    Field("podcast", "Podcast", Kind.BOOL, group="more"),
    Field("podcast_category", "Podcast Category", group="more"),
    Field("podcast_desc", "Podcast Description", Kind.LONGTEXT, group="more"),
    Field("podcast_id", "Podcast ID", group="more"),
    Field("podcast_url", "Podcast URL", group="more"),
    Field("media_kind", "Video Kind", group="more"),
    Field("show_title", "Show Title", var="%swt", group="more"),
    Field("episode_id", "Episode ID", var="%eid", group="more"),
    Field("season_number", "Season Number", Kind.INT, var="%snr", group="more"),
    Field("episode_number", "Episode Number", Kind.INT, var="%enr", group="more"),

    Field("sort_title", "Title (sort)", group="sorting"),
    Field("sort_artist", "Artist (sort)", group="sorting"),
    Field("sort_album_artist", "Album Artist (sort)", group="sorting"),
    Field("sort_album", "Album (sort)", group="sorting"),
    Field("sort_composer", "Composer (sort)", group="sorting"),
    Field("sort_show", "Show (sort)", group="sorting"),
)

BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}
GROUPS = ("general", "lyrics", "additional", "more", "sorting")


def group(name: str) -> tuple[Field, ...]:
    return tuple(f for f in FIELDS if f.group == name)


# --- rename variables ---------------------------------------------------
# Some variables are not tag fields at all but derived from the file itself.
# They are read-only: they can appear in a rename mask, never in a parse mask.

DERIVED_VARS: dict[str, str] = {
    "%7": "filename",       # current file name, no extension
    "%8": "file_number",    # position in the current selection, 1-based
    "%0": "bitrate",
    "%t": "play_time",
    "%khz": "sample_rate",
    "%bit": "bit_depth",
    "%enc": "encoder",
    "%rnd": "random",
    # Video naming (2026-11-28). Plex and Jellyfin both read
    # "Show - 1x01 - Title" and "Show - S01E01 - Title"; the plain %snr/%enr
    # are zero-padded as counters, which gives "01x01".
    "%sxe": "sxe",          # 1x01
    "%see": "s00e00",       # S01E01
}

#: ``%d`` means "something is here, throw it away" when parsing a file name.
PARSE_SKIP_VAR = "%d"

#: var -> field key, for every field that carries one.
VAR_TO_KEY: dict[str, str] = {f.var: f.key for f in FIELDS if f.var}

#: Everything legal on the right of a ``%`` in a *rename* mask.
RENAME_VARS: dict[str, str] = {**VAR_TO_KEY, **DERIVED_VARS}

#: Everything legal in a *filename-to-tag* mask. Derived values cannot be
#: written back into a tag, so only real fields plus the skip token.
PARSE_VARS: dict[str, str] = {**VAR_TO_KEY, PARSE_SKIP_VAR: "__skip__"}


def _longest_first(vars_: dict[str, str]) -> list[str]:
    """Token match order.

    ``%aa`` and ``%a``-prefixed tokens overlap, so the scanner must try the
    longest spelling first or ``%aa`` reads as ``%a`` + a stray ``a``.
    """
    return sorted(vars_, key=len, reverse=True)


RENAME_TOKENS = _longest_first(RENAME_VARS)
PARSE_TOKENS = _longest_first(PARSE_VARS)
