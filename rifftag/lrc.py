"""LRC — timestamped lyrics.

The format is a line per lyric, each prefixed with ``[mm:ss.cc]``, plus a few
optional metadata tags at the top. Players are forgiving about most of it and
unforgiving about two things, so both are pinned here:

**Centiseconds, two digits.** Three-digit milliseconds appear in the wild and
some players read them, but ``[01:23.456]`` is rejected by enough of them
that it is not worth the extra precision.

**Minutes are not capped at 59.** A 70-minute live set is ``[70:12.00]``, not
``[1:10:12.00]``; there is no hours field in LRC.

The module is pure text in, pure text out — the dialog does the timing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: [mm:ss.cc] or [mm:ss] or [mm:ss.ccc], captured leniently on the way IN
_STAMP = re.compile(r"\[(\d{1,4}):([0-5]?\d)(?:[.:](\d{1,3}))?\]")
#: [ti:…] and friends. Anything whose "minutes" are not digits is metadata.
_META = re.compile(r"^\s*\[([a-zA-Z]{2,10}):([^\]]*)\]\s*$")

#: Metadata keys written out, in order, when the value is known.
META_ORDER = ("ti", "ar", "al", "by", "length", "offset")


@dataclass
class Line:
    """One lyric line, timed or not yet."""

    text: str
    ms: int | None = None

    @property
    def timed(self) -> bool:
        return self.ms is not None


def format_stamp(ms: int) -> str:
    """Milliseconds -> ``[mm:ss.cc]``. Minutes run past 59 on purpose."""
    total = max(0, int(ms))
    minutes, rest = divmod(total, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"[{minutes:02d}:{seconds:02d}.{millis // 10:02d}]"


def parse_stamp(text: str) -> int | None:
    """``[mm:ss.cc]`` -> milliseconds, or None if it is not a stamp."""
    match = _STAMP.match(text.strip())
    if not match:
        return None
    minutes, seconds, fraction = match.groups()
    ms = int(minutes) * 60_000 + int(seconds) * 1000
    if fraction:
        # Two digits are centiseconds, three are milliseconds.
        ms += int(fraction) * 10 if len(fraction) <= 2 else int(fraction)
    return ms


def split_for_sync(text: str) -> list[Line]:
    """Plain lyrics -> the lines to be tapped, blank separators included.

    Blank lines are kept: a timed blank is how an LRC player clears the
    screen between verses, which is exactly what the gap is for.
    """
    if not text:
        return []
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [Line(line.rstrip()) for line in body.split("\n")]
    while lines and not lines[-1].text:
        lines.pop()
    return lines


def parse(text: str) -> tuple[list[Line], dict]:
    """Read an existing .lrc back into lines and metadata.

    A line carrying several stamps (the usual way to repeat a chorus) comes
    back as one entry per stamp, so re-tapping it works like any other line.
    """
    lines: list[Line] = []
    meta: dict = {}
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        meta_match = _META.match(raw)
        if meta_match and parse_stamp(raw) is None:
            meta[meta_match.group(1).lower()] = meta_match.group(2).strip()
            continue
        stamps = []
        rest = raw
        while True:
            match = _STAMP.match(rest.lstrip())
            if not match:
                break
            rest = rest.lstrip()[match.end():]
            stamps.append(parse_stamp(match.group(0)))
        if stamps:
            for ms in stamps:
                lines.append(Line(rest.strip(), ms))
        elif raw.strip() or lines:
            lines.append(Line(raw.rstrip()))
    while lines and not lines[-1].text and not lines[-1].timed:
        lines.pop()
    return lines, meta


def build(lines, meta: dict | None = None) -> str:
    """Lines -> .lrc text. Untimed lines are left out, timed ones sorted.

    Sorting matters: a line re-tapped out of order would otherwise make a
    player jump backwards and then sit on the wrong line for a whole verse.
    """
    out: list[str] = []
    for key in META_ORDER:
        value = (meta or {}).get(key)
        if value not in (None, ""):
            out.append(f"[{key}:{value}]")
    if out:
        out.append("")
    timed = sorted((l for l in lines if l.timed), key=lambda l: l.ms)
    for line in timed:
        out.append(f"{format_stamp(line.ms)}{line.text}")
    return "\n".join(out) + ("\n" if out else "")


def shift(lines, delta_ms: int) -> list[Line]:
    """Nudge every stamp, never below zero. Untimed lines pass through."""
    out = []
    for line in lines:
        if line.timed:
            out.append(Line(line.text, max(0, line.ms + delta_ms)))
        else:
            out.append(Line(line.text, None))
    return out


def metadata_for(track, length_ms: int = 0) -> dict:
    """The [ti:]/[ar:]/[al:] block, from the tags we already hold."""
    values = getattr(track, "values", {}) or {}

    def value(key):
        return str(values.get(key, "") or "").strip()

    meta = {"ti": value("title"), "ar": value("artist"), "al": value("album")}
    if length_ms:
        total = int(length_ms) // 1000
        meta["length"] = f"{total // 60:02d}:{total % 60:02d}"
    return {k: v for k, v in meta.items() if v}


def default_path(track) -> Path:
    """Where the .lrc goes: beside the track, same stem."""
    return Path(track.path).with_suffix(".lrc")
