"""Track-number formatting and auto-fill.

The two tools from Tag&Rename's toolbar that Dave reaches for most: the
**track #** dropdown that restyles existing numbers across a selection, and the
**auto fill** that renumbers a selection from any starting point.

Kept out of the GUI so the rules are testable on their own.

The five entries are read **literally**, which is the only interpretation
under which all five differ:

======  ===================================  ==============
entry   meaning                              1 and 10 give
======  ===================================  ==============
x       the number, nothing added            1, 10
0x      a literal zero, then the number      01, 010
00x     two literal zeros, then the number   001, 0010
xx/xx   two digits, with the total           01/12, 10/12
xx      two digits                           01, 10
======  ===================================  ==============

The first attempt read ``xx`` as "pad to the width of the largest number in
the selection", and a test caught that this makes ``xx`` and ``0x`` identical
for any album of 10 or more tracks — the original would not ship two menu
entries that do the same thing. Hence the literal reading. :data:`FORMATS` is
the single place to change if it turns out to mean something else; every menu
entry shows a live example so a wrong guess is visible rather than silent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrackFormat:
    key: str
    label: str
    #: literal zeros written in front of the number, however long it is
    prefix: str = ""
    #: zero-pad to this many digits instead; mutually exclusive with prefix
    width: int | None = None
    with_total: bool = False

    def example(self) -> str:
        """What this does to tracks 1 and 10. Shown beside the menu entry."""
        return ", ".join(render(n, 12, self) for n in (1, 10))


FORMATS: tuple[TrackFormat, ...] = (
    TrackFormat("x", "Format Track # to x"),
    TrackFormat("0x", "Format Track # to 0x", prefix="0"),
    TrackFormat("00x", "Format Track # to 00x", prefix="00"),
    TrackFormat("xx/xx", "Format Track # to xx/xx", width=2, with_total=True),
    TrackFormat("xx", "Format Track # to xx", width=2),
)

BY_KEY = {f.key: f for f in FORMATS}


def render(number: int, total: int | None, fmt: TrackFormat) -> str:
    """One formatted track value."""
    def one(n: int) -> str:
        text = str(int(n))
        return fmt.prefix + (text.zfill(fmt.width) if fmt.width else text)

    text = one(number)
    if fmt.with_total and total:
        return f"{text}/{one(total)}"
    return text


def _as_int(value) -> int | None:
    """Pull a number out of "7", "07", "7/12" or " 7 ". None if there isn't one."""
    if value is None:
        return None
    text = str(value).split("/", 1)[0].strip()
    return int(text) if text.isdigit() else None


def reformat(tracks, fmt: TrackFormat) -> list[dict]:
    """Restyle the track numbers a selection already has.

    Returns one write-dict per track, aligned with ``tracks``; an empty dict
    means that file has no usable track number and should be left alone.
    Nothing is invented — this only changes how an existing number is written.
    """
    numbers = [_as_int(t.values.get("track")) for t in tracks]
    out: list[dict] = []
    for track, number in zip(tracks, numbers):
        if number is None:
            out.append({})
            continue
        total = _as_int(track.values.get("track_total"))
        if fmt.with_total and not total:
            total = len(tracks)
        values = {"track": render(number, total, fmt)}
        # xx/xx puts the total inside the track field, so a separate
        # track_total would be a second, possibly disagreeing copy.
        values["track_total"] = "" if fmt.with_total else (
            str(total) if total else "")
        out.append(values)
    return out


def autofill(count: int, *, start: int = 1, use_total: bool = False,
             width: int = 2) -> list[dict]:
    """Number ``count`` files consecutively from ``start``.

    ``use_total`` writes the original's "xx/xx" form. The total counts the
    files being numbered, which is what makes it right for a selected album
    and wrong for a half-selected one — same as the original.
    """
    total = start + count - 1
    out = []
    for i in range(count):
        n = start + i
        if use_total:
            out.append({"track": f"{str(n).zfill(width)}/{str(total).zfill(width)}",
                        "track_total": ""})
        else:
            out.append({"track": str(n).zfill(width),
                        "track_total": str(total)})
    return out
