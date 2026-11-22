"""Lyrics cleanup for streaming delivery.

Writing tools hand you lyrics full of scaffolding — ``[Verse 1]``, ``(x2)``,
``Chorus:``, a copyright line at the bottom — and distributors want the words
and nothing else. This module strips the scaffolding and leaves the song.

Two rules shape every decision here:

**Never delete a line that might be lyrics.** A bracketed line is scaffolding
by construction, so those go unconditionally. A *bare* line like ``Chorus:``
is only dropped when the word is one of the known section names, because
"Hold on:" is a lyric and losing it would be unforgivable. Anything the
cleaner is unsure of is left alone.

**Everything is reversible until it is written.** :func:`clean` is pure and
:func:`describe` says what it would do, so the dialog can show a real
before/after per file before anything touches a tag.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

#: Section names recognised as scaffolding when a line is nothing but the
#: name (optionally numbered, optionally followed by a colon). Deliberately a
#: closed list: anything not named here is treated as a lyric.
SECTION_WORDS = (
    "intro", "verse", "pre-chorus", "prechorus", "pre chorus", "chorus",
    "post-chorus", "hook", "refrain", "bridge", "middle 8", "middle eight",
    "breakdown", "drop", "interlude", "instrumental", "solo", "outro",
    "coda", "tag", "vamp", "spoken", "ad-lib", "adlib", "ad libs",
    "backing vocals", "harmony", "end", "ending", "repeat",
)

#: Lines that are credits rather than words of the song.
CREDIT_PREFIXES = (
    "written by", "writer:", "writers:", "words by", "lyrics by",
    "music by", "composed by", "composer:", "produced by", "producer:",
    "performed by", "arranged by", "mixed by", "mastered by",
    "copyright", "all rights reserved", "℗", "©", "(c)", "(p)",
)

_SECTION_ALTERNATION = "|".join(
    re.escape(word) for word in sorted(SECTION_WORDS, key=len, reverse=True))

#: A whole line that is only a bracketed or parenthesised annotation.
_BRACKETED_LINE = re.compile(r"^\s*[\[(\{]\s*[^\[\](){}]*\s*[\])\}]\s*$")
#: A bare section name, e.g. "Chorus:", "VERSE 2", "Pre-Chorus 3 :"
_BARE_SECTION = re.compile(
    rf"^\s*({_SECTION_ALTERNATION})\s*\d*\s*:?\s*$", re.IGNORECASE)
#: Repeat markers anywhere on a line: (x2) [2x] (repeat 3x) (xN)
_REPEAT = re.compile(
    r"[\[(\{]\s*(?:repeat\s*)?(?:x\s*\d+|\d+\s*x)\s*[\])\}]|"
    r"[\[(\{]\s*repeat\s*[\])\}]", re.IGNORECASE)
#: An LRC timestamp at the start of a line: [00:12.34] or [1:02]
_LRC_STAMP = re.compile(r"^\s*(?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*)+")
#: Any LRC metadata line: [ar:…] [ti:…] [by:…] [offset:…]
_LRC_META = re.compile(r"^\s*\[[a-z]{2,8}:[^\]]*\]\s*$", re.IGNORECASE)
_HTML_BREAK = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>\n]{1,40}>")

#: Curly punctuation -> its plain equivalent. ID3v2.3 text frames fall back to
#: UTF-16 the moment a character leaves latin-1, and a stray smart quote is
#: the usual cause of a lyric sheet arriving at a distributor as mojibake.
_PLAIN = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "―": "-", "−": "-",
    "…": "...", " ": " ", " ": " ", " ": " ",
    "​": "", "‌": "", "‍": "", "﻿": "",
}


@dataclass(frozen=True)
class CleanOptions:
    """Every step is optional; all default on except the last."""

    section_headers: bool = True
    repeat_markers: bool = True
    credits: bool = True
    timestamps: bool = True
    html: bool = True
    plain_punctuation: bool = True
    collapse_blanks: bool = True
    trim_space: bool = True
    #: Off by default: some writers deliberately keep a stanza in capitals.
    fix_shouting: bool = False


@dataclass
class Change:
    """One thing the cleaner did, for the preview column."""

    kind: str
    count: int

    def __str__(self) -> str:
        label = {
            "section_headers": "section header",
            "repeat_markers": "repeat marker",
            "credits": "credit line",
            "timestamps": "timestamp",
            "html": "HTML bit",
            "plain_punctuation": "smart character",
            "collapse_blanks": "blank line",
            "trim_space": "trailing space",
            "fix_shouting": "shouted line",
        }.get(self.kind, self.kind)
        return f"{self.count} {label}{'s' if self.count != 1 else ''}"


def clean(text: str, options: CleanOptions | None = None) -> str:
    """Return the cleaned lyrics. Pure: nothing is written anywhere."""
    return _run(text, options or CleanOptions())[0]


def describe(text: str, options: CleanOptions | None = None) -> list[Change]:
    """What :func:`clean` would change, for a preview that can be read."""
    return _run(text, options or CleanOptions())[1]


def _run(text: str, opt: CleanOptions) -> tuple[str, list[Change]]:
    if not text:
        return "", []
    counts: dict[str, int] = {}

    def bump(kind: str, n: int = 1) -> None:
        if n:
            counts[kind] = counts.get(kind, 0) + n

    body = text.replace("\r\n", "\n").replace("\r", "\n")

    if opt.html:
        broken = _HTML_BREAK.sub("\n", body)
        bump("html", len(_HTML_BREAK.findall(body)))
        stripped = _HTML_TAG.sub("", broken)
        bump("html", len(_HTML_TAG.findall(broken)))
        unescaped = html.unescape(stripped)
        if unescaped != stripped:
            bump("html")
        body = unescaped

    if opt.plain_punctuation:
        swapped = "".join(_PLAIN.get(ch, ch) for ch in body)
        bump("plain_punctuation",
             sum(1 for ch in body if ch in _PLAIN))
        body = swapped

    out: list[str] = []
    for line in body.split("\n"):
        if opt.timestamps:
            if _LRC_META.match(line):
                bump("timestamps")
                continue
            without = _LRC_STAMP.sub("", line)
            if without != line:
                bump("timestamps")
                line = without

        if opt.repeat_markers:
            without = _REPEAT.sub("", line)
            if without != line:
                bump("repeat_markers", len(_REPEAT.findall(line)))
                line = without

        if opt.section_headers and line.strip():
            if _BRACKETED_LINE.match(line) or _BARE_SECTION.match(line):
                bump("section_headers")
                continue

        if opt.credits and _is_credit(line):
            bump("credits")
            continue

        if opt.fix_shouting and _is_shouting(line):
            bump("fix_shouting")
            line = _unshout(line)

        if opt.trim_space:
            trimmed = line.rstrip().replace("\t", " ")
            if trimmed != line:
                bump("trim_space")
            line = trimmed

        out.append(line)

    if opt.collapse_blanks:
        collapsed, removed = _collapse(out)
        bump("collapse_blanks", removed)
        out = collapsed

    changes = [Change(kind, n) for kind, n in counts.items() if n]
    return "\n".join(out), changes


def _is_credit(line: str) -> bool:
    stripped = line.strip().lower()
    if not stripped:
        return False
    return any(stripped.startswith(prefix) for prefix in CREDIT_PREFIXES)


def _is_shouting(line: str) -> bool:
    letters = [ch for ch in line if ch.isalpha()]
    if len(letters) < 4:
        return False
    return all(ch.isupper() for ch in letters)


def _unshout(line: str) -> str:
    """Title-ish case that keeps I and common contractions readable."""
    out = line.lower()
    out = re.sub(r"(^|[.!?]\s+|\n)([a-z])",
                 lambda m: m.group(1) + m.group(2).upper(), out)
    out = re.sub(r"\bi\b", "I", out)
    out = re.sub(r"\bi'(m|ll|ve|d)\b", lambda m: "I'" + m.group(1), out)
    return out


def _collapse(lines: list[str]) -> tuple[list[str], int]:
    """At most one blank line between stanzas, none at either end."""
    out: list[str] = []
    removed = 0
    for line in lines:
        if not line.strip():
            if not out or not out[-1].strip():
                removed += 1
                continue
            out.append("")
        else:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
        removed += 1
    return out, removed


def looks_like_lrc(text: str) -> bool:
    """Whether the text carries LRC timestamps worth keeping elsewhere."""
    return any(_LRC_STAMP.match(line) for line in (text or "").split("\n"))


# --- importing lyrics that live beside the music ------------------------

#: Extensions searched when importing lyrics from files
TEXT_EXTENSIONS = (".txt", ".lrc", ".text")


def _key(name: str) -> str:
    """A loose comparison key, so "01 - In Bloom" matches "In Bloom"."""
    folded = unicodedata.normalize("NFKD", name).casefold()
    return re.sub(r"[^a-z0-9]+", "", folded)


def find_lyrics_file(track, folder: Path | None = None) -> Path | None:
    """A .txt/.lrc beside the track whose name matches it or its title.

    Matching is deliberately loose — track numbers, punctuation and case all
    differ between a file you typed and a file the tagger named — but it
    never guesses between two candidates: an ambiguous match returns None.
    """
    path = Path(track.path)
    folder = Path(folder) if folder else path.parent
    if not folder.is_dir():
        return None
    title = str((getattr(track, "values", {}) or {}).get("title", "") or "")
    wanted = {_key(path.stem)}
    if title:
        wanted.add(_key(title))
    wanted.discard("")

    exact, loose = [], []
    for candidate in sorted(folder.iterdir()):
        if candidate.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        key = _key(candidate.stem)
        if key in wanted:
            exact.append(candidate)
        elif key and any(w and (key.endswith(w) or w.endswith(key))
                         for w in wanted):
            loose.append(candidate)
    if len(exact) == 1:
        return exact[0]
    if not exact and len(loose) == 1:
        return loose[0]
    return None


def read_text(path: Path) -> str:
    """Read a lyrics file, tolerating whatever encoding it was saved in."""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")
