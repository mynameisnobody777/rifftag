"""The mask engine — both directions.

* :func:`render`  tags  -> relative path   (the "Rename Files" tab)
* :func:`parse`   path  -> tags            (the "Get Tags from File Name" tab)

Mask syntax is Tag&Rename 3.9.15's, unchanged, so existing muscle memory and
saved masks carry over:

    %1 Artist  %2 Title  %3 Album  %4 Year  %5 Genre  %6 Track #  %7 File name
    %8 File number  %9 Comment  %0 Bitrate  %t Play time  %b BPM  %cp Composer
    %aa Album Artist  %co Conductor  %# Disc #  %rt Release Time  %md Mood
    %lb Label  %rb Remixed by  %key Initial Key  %swt Show Title  %eid Episode
    ID  %snr Season number  %enr Episode number  %rnd Random number
    %d (parse only) skip this part of the name

``\\`` in a mask is a folder boundary — ``%1 - %3\\%6 %2`` both renames the file
and files it under an ``Artist - Album`` directory. ``/`` is accepted as a
synonym since this is Linux, but ``\\`` stays canonical so masks round-trip.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from .casing import CaseRules
from .fields import (
    BY_KEY,
    PARSE_SKIP_VAR,
    PARSE_TOKENS,
    PARSE_VARS,
    RENAME_TOKENS,
    RENAME_VARS,
    Kind,
)

SEP = "\\"
#: Characters Windows forbids in a name. Linux only objects to "/" and NUL, but
#: these files live on NTFS and Samba shares, so sanitising by default keeps a
#: rename from producing a name the share cannot store.
WIN_ILLEGAL = r'<>:"/\|?*'


class MaskError(ValueError):
    """Raised for a mask that cannot be rendered or parsed as written."""


@dataclass(frozen=True)
class Lit:
    text: str


@dataclass(frozen=True)
class Var:
    token: str
    name: str   # field key, or a DERIVED_VARS name, or "__skip__"


Node = Lit | Var


def tokenize(mask: str, tokens: list[str], table: dict[str, str]) -> list[Node]:
    """Split a mask into literals and variables.

    ``tokens`` must be ordered longest-first or ``%aa`` would be read as ``%a``.
    A ``%`` that starts no known token is treated as a literal percent sign.
    """
    out: list[Node] = []
    buf: list[str] = []
    i = 0
    while i < len(mask):
        ch = mask[i]
        if ch == "%":
            for tok in tokens:
                if mask.startswith(tok, i):
                    if buf:
                        out.append(Lit("".join(buf)))
                        buf = []
                    out.append(Var(tok, table[tok]))
                    i += len(tok)
                    break
            else:
                buf.append(ch)
                i += 1
            continue
        if ch == "/":
            ch = SEP          # accept the Linux separator, store the canonical one
        buf.append(ch)
        i += 1
    if buf:
        out.append(Lit("".join(buf)))
    return out


# --- rendering ----------------------------------------------------------

def _pad(value: object, width: int) -> str:
    """Zero-pad a counter.

    A value stored as "3/12" is a track-of-total; only the number belongs in a
    file name, and the slash would be sanitised into a stray underscore if it
    survived this far.
    """
    s = str(value).split("/", 1)[0].strip()
    return s.zfill(width) if s.isdigit() else s


def sanitize_component(name: str, *, windows_safe: bool = True,
                       replacement: str = "_") -> str:
    """Make one path component storable.

    Always strips "/" and NUL. With ``windows_safe`` also strips the characters
    NTFS/SMB reject and the trailing dot or space Windows silently drops.
    """
    bad = WIN_ILLEGAL if windows_safe else "/"
    name = "".join(replacement if c in bad or ord(c) < 32 else c for c in name)
    if windows_safe:
        name = name.rstrip(". ")
    return name.strip()


@dataclass
class RenderOptions:
    case: CaseRules | None = None
    underscores: bool = False      # "Replace spaces with underscores"
    counter_width: int = 2
    windows_safe: bool = True
    #: drop a leading zero from the disc number, as Tag&Rename's Advanced option
    strip_disc_leading_zero: bool = False


def render(mask: str, values: dict[str, object], *,
           options: RenderOptions | None = None) -> str:
    """Render a mask to a relative path (no extension).

    Raises :class:`MaskError` if the mask references a field this file has no
    value for — matching Tag&Rename, which refuses rather than writing a name
    with a hole in it.
    """
    opts = options or RenderOptions()
    nodes = tokenize(mask, RENAME_TOKENS, RENAME_VARS)
    if not any(isinstance(n, Var) for n in nodes):
        raise MaskError("mask contains no variables")

    parts: list[str] = []
    for node in nodes:
        if isinstance(node, Lit):
            parts.append(node.text)
            continue
        if node.name == "random":
            parts.append(str(random.randint(0, 9999)).zfill(4))
            continue
        raw = values.get(node.name)
        if raw is None or str(raw) == "":
            raise MaskError(
                f"{node.token} has no value "
                f"({BY_KEY[node.name].label if node.name in BY_KEY else node.name})")
        text = str(raw)
        fld = BY_KEY.get(node.name)
        if fld is not None and fld.kind is Kind.INT:
            text = _pad(text, opts.counter_width)
            if node.name == "disc" and opts.strip_disc_leading_zero:
                text = text.lstrip("0") or "0"
        elif node.name == "file_number":
            text = _pad(text, opts.counter_width)
        parts.append(text)

    path = "".join(parts)
    components = [c for c in path.split(SEP)]
    if opts.case:
        components = [opts.case.apply(c) for c in components]
    if opts.underscores:
        components = [c.replace(" ", "_") for c in components]
    components = [sanitize_component(c, windows_safe=opts.windows_safe)
                  for c in components]
    if any(not c for c in components):
        raise MaskError("mask produced an empty path component")
    return SEP.join(components)


# --- parsing ------------------------------------------------------------

_INT_PAT = r"[0-9]+(?:\s*/\s*[0-9]+)?"


def build_regex(mask: str) -> tuple[re.Pattern[str], list[str]]:
    """Compile a filename-to-tag mask into an anchored regex.

    Variables become non-greedy captures so that ``%1 - %2`` on
    ``"AC - DC - Thunder"`` yields ``artist="AC"`` and ``title="DC - Thunder"``,
    the same split Tag&Rename produces. Numeric fields capture digits only,
    which is what makes ``%6 %1 - %2`` reliable on ``"01 Artist - Title"``.
    The final variable is greedy so it absorbs the rest of the name.
    """
    nodes = tokenize(mask, PARSE_TOKENS, PARSE_VARS)
    var_idx = [i for i, n in enumerate(nodes) if isinstance(n, Var)]
    if not var_idx:
        raise MaskError("mask contains no variables")

    keys: list[str] = []
    out: list[str] = []
    for i, node in enumerate(nodes):
        if isinstance(node, Lit):
            out.append(re.escape(node.text).replace(re.escape(SEP), re.escape(SEP)))
            continue
        last = i == var_idx[-1]
        if node.name == "__skip__":
            out.append(r"(?:.*)" if last else r"(?:.*?)")
            continue
        fld = BY_KEY[node.name]
        if fld.kind is Kind.INT:
            out.append(f"({_INT_PAT})")
        else:
            out.append(r"(.*)" if last else r"(.*?)")
        keys.append(node.name)

    return re.compile("^" + "".join(out) + "$", re.DOTALL), keys


def parse(mask: str, path: str, *,
          case: CaseRules | None = None) -> dict[str, str]:
    """Pull tag values out of a path using a mask.

    ``path`` should be the file's path without its extension. If the mask spans
    folders (contains ``\\``) only that many trailing components are matched,
    so a full absolute path can be passed in as-is.
    """
    mask_depth = tokenize(mask, PARSE_TOKENS, PARSE_VARS)
    depth = 1 + sum(n.text.count(SEP) for n in mask_depth if isinstance(n, Lit))

    norm = path.replace("/", SEP)
    components = [c for c in norm.split(SEP) if c]
    subject = SEP.join(components[-depth:]) if depth <= len(components) else SEP.join(components)

    rx, keys = build_regex(mask)
    m = rx.match(subject)
    if not m:
        raise MaskError(f"mask does not match {subject!r}")

    values: dict[str, str] = {}
    for key, raw in zip(keys, m.groups()):
        text = raw.strip()
        if not text:
            continue
        fld = BY_KEY[key]
        if fld.total_of and "/" in text:
            # "03/12" in a file name carries both the number and the total.
            num, _, total = text.partition("/")
            values[key] = num.strip()
            if total.strip():
                values[fld.total_of] = total.strip()
            continue
        if case and fld.kind is not Kind.INT:
            text = case.apply(text)
        values[key] = text
    return values


def describe(mask: str) -> str:
    """Human-readable preview, e.g. ``%6 %2`` -> ``<Track #> <Title>``.

    Drives the "Result preview" box before any file is selected.
    """
    out: list[str] = []
    for node in tokenize(mask, RENAME_TOKENS, RENAME_VARS):
        if isinstance(node, Lit):
            out.append(node.text)
        elif node.name in BY_KEY:
            out.append(f"<{BY_KEY[node.name].label}>")
        else:
            out.append(f"<{node.name.replace('_', ' ').title()}>")
    return "".join(out)
