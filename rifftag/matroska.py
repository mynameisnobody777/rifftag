"""Matroska tags (``.mkv`` ``.mka`` ``.webm``), through MKVToolNix.

mutagen cannot read or write Matroska, so this module drives the MKVToolNix
command-line tools instead:

* ``mkvmerge -J``  — container info, tracks, attachments (JSON)
* ``mkvextract tags`` — the tag tree as XML
* ``mkvextract attachments`` — the cover-art bytes
* ``mkvpropedit`` — every write. It edits in place without remuxing, so a
  4 GB film is retagged in well under a second and the streams are never
  touched.

:mod:`rifftag.tagio` dispatches the ``"matroska"`` family here; nothing else
imports this module.

What is supported, and why it stops there
-----------------------------------------
**File-level tags and cover art only.** A Matroska tag is attached to a
*target*: the whole file, one track, a chapter, an edition or an attachment.
RiffTag edits the tags with no track/chapter/edition/attachment UID in their
``<Targets>`` (the "global" tags) and leaves every targeted tag exactly as it
was — ``mkvpropedit --tags global:`` replaces only the global ones.

Two conventions for file-level tags exist in the wild, and both are read:

* **Flat** (what ffmpeg, HandBrake and most muxers write): one tag with an
  empty ``<Targets/>`` holding ``TITLE``, ``ARTIST``, ``ALBUM``,
  ``ALBUM_ARTIST``, ``PART_NUMBER=3/12``…
* **Nested** (the Matroska spec's own music example): a level-50 tag for the
  album (``TITLE`` = album, ``ARTIST`` = album artist, ``TOTAL_PARTS``) and a
  level-30 tag for the song. Detected by the presence of a global tag with
  ``TargetTypeValue`` below 50; writes then go back to the level they came
  from, so a spec-style file stays spec-style.

ffmpeg puts the title in the segment *info* title, not in a ``TITLE`` tag,
and that is what most players display for a video. So ``title`` is read from
the ``TITLE`` tag first and the segment title second, and written to both.

Cover art is an *attachment*, not a tag. Per the spec's naming convention the
front cover is ``cover.jpg`` / ``cover.png``; every ``image/*`` attachment is
read as a picture (fonts and other attachments are never touched), and a
write replaces the image attachments as a set.

Locating the tools
------------------
Linux: on ``PATH`` (``sudo apt install mkvtoolnix``). Windows: ``PATH``, then a
``mkvtoolnix`` folder beside the executable (a bundled copy), then the default
``C:\\Program Files\\MKVToolNix`` install. Missing tools never crash a folder
load: the track gets an ``error`` saying what to install.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .fields import BY_KEY

TOOLS = ("mkvmerge", "mkvextract", "mkvpropedit")
INSTALL_HINT = ("MKVToolNix is needed for Matroska files — "
                + ("install it from mkvtoolnix.download"
                   if sys.platform == "win32"
                   else "sudo apt install mkvtoolnix"))
_TIMEOUT = 120


class MatroskaError(RuntimeError):
    """A Matroska file could not be read or written."""


# --- finding the tools --------------------------------------------------

def _candidates(name: str) -> list[Path]:
    exe = name + (".exe" if sys.platform == "win32" else "")
    dirs = [Path(sys.executable).resolve().parent / "mkvtoolnix",
            Path(__file__).resolve().parent.parent / "mkvtoolnix"]
    if sys.platform == "win32":
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(env):
                dirs.append(Path(os.environ[env]) / "MKVToolNix")
    return [d / exe for d in dirs]


def tool(name: str) -> str | None:
    """Full path to one MKVToolNix program, or ``None``."""
    found = shutil.which(name)
    if found:
        return found
    for cand in _candidates(name):
        if cand.is_file():
            return str(cand)
    return None


def available() -> bool:
    return all(tool(t) for t in TOOLS)


def _run(name: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    exe = tool(name)
    if exe is None:
        raise MatroskaError(INSTALL_HINT)
    # English messages on every machine, but it MUST be a UTF-8 locale:
    # under plain LC_ALL=C, mkvextract cuts its output off at the first
    # non-ASCII character ("Jóga" came back as "J") and non-ASCII file
    # names stop round-tripping. Windows ignores these variables.
    env = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")
    kw: dict[str, Any] = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000        # CREATE_NO_WINDOW
    try:
        proc = subprocess.run([exe, *args], capture_output=True, env=env,
                              timeout=_TIMEOUT, **kw)
    except subprocess.TimeoutExpired as exc:
        raise MatroskaError(f"{name} timed out") from exc
    except OSError as exc:
        raise MatroskaError(f"could not run {name}: {exc}") from exc
    # MKVToolNix exit codes: 0 ok, 1 warnings (the work was done), 2 error.
    if proc.returncode >= 2:
        msg = _last_error(proc) or f"{name} failed ({proc.returncode})"
        raise MatroskaError(msg)
    return proc


def _last_error(proc: subprocess.CompletedProcess[bytes]) -> str:
    text = (proc.stdout + b"\n" + proc.stderr).decode("utf-8", "replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    errs = [ln for ln in lines if ln.lower().startswith("error")]
    return (errs or lines or [""])[-1]


# --- field mapping ------------------------------------------------------
# Names are the Matroska spec's official tag names where one exists
# (matroska.org/technical/tagging.html), otherwise what ffmpeg writes, so a
# value set here shows up in ffprobe, mpv, VLC and Kodi.
#
# key -> (name we write, *names we also accept on read, in priority order)

_NAMES: dict[str, tuple[str, ...]] = {
    "title": ("TITLE",),
    "artist": ("ARTIST", "LEAD_PERFORMER"),
    "album": ("ALBUM",),
    "album_artist": ("ALBUM_ARTIST", "ALBUMARTIST", "ALBUM ARTIST"),
    "year": ("DATE_RELEASED", "DATE", "DATE_RECORDED", "YEAR"),
    "genre": ("GENRE",),
    "comment": ("COMMENT", "DESCRIPTION"),
    "composer": ("COMPOSER",),
    "conductor": ("CONDUCTOR",),
    "lyricist": ("LYRICIST", "WRITTEN_BY"),
    "copyright": ("COPYRIGHT",),
    "encoded_by": ("ENCODED_BY",),
    "label": ("LABEL", "PUBLISHER"),
    "remixed_by": ("REMIXED_BY",),
    "initial_key": ("INITIAL_KEY",),
    "subtitle": ("SUBTITLE",),
    "isrc": ("ISRC",),
    "catalog": ("CATALOG_NUMBER",),
    "mood": ("MOOD",),
    "bpm": ("BPM",),
    "grouping": ("GROUPING",),
    "lyrics": ("LYRICS",),
    "url": ("URL",),
    "rating": ("RATING",),
    "compilation": ("COMPILATION",),
    "orig_artist": ("ORIGINAL_ARTIST",),
    "show_title": ("SHOW", "SERIES"),
    "season_number": ("SEASON", "SEASON_NUMBER"),
    "episode_number": ("EPISODE", "EPISODE_NUMBER"),
    "episode_id": ("EPISODE_ID",),
    # counters: PART_NUMBER is the spec's; ffmpeg writes "3/12" into it.
    # Disc has no spec name; DISC is what ffmpeg produces from an ID3 TPOS.
    "track": ("PART_NUMBER", "TRACKNUMBER", "TRACK"),
    "track_total": ("TOTAL_PARTS", "TRACKTOTAL"),
    "disc": ("DISC", "DISCNUMBER", "DISC_NUMBER"),
    "disc_total": ("DISCTOTAL", "TOTAL_DISCS"),
}

#: In the nested convention these keys live on the level-50 (album) tag,
#: under the spec's generic names.
_ALBUM_LEVEL = {"album": "TITLE", "album_artist": "ARTIST",
                "track_total": "TOTAL_PARTS"}

SUPPORTED_KEYS = frozenset(_NAMES)


# --- the tag tree -------------------------------------------------------

def _is_global(tag: ET.Element) -> bool:
    targets = tag.find("Targets")
    if targets is None:
        return True
    return not any(child.tag.endswith("UID") for child in targets)


def _level(tag: ET.Element) -> int:
    t = tag.find("Targets")
    raw = t.findtext("TargetTypeValue") if t is not None else None
    try:
        return int(raw) if raw else 50
    except ValueError:
        return 50


def _simples(tag: ET.Element) -> dict[str, str]:
    """Top-level SimpleTags of one Tag, upper-cased name -> first value."""
    out: dict[str, str] = {}
    for s in tag.findall("Simple"):
        name = (s.findtext("Name") or "").strip().upper()
        val = s.findtext("String")
        if name and val is not None and name not in out:
            out[name] = val
    return out


def _parse_tags(xml: bytes) -> ET.Element:
    if not xml.strip():
        return ET.Element("Tags")
    return ET.fromstring(xml.lstrip(b"\xef\xbb\xbf"))


def _layout(root: ET.Element) -> tuple[ET.Element | None, ET.Element | None]:
    """(song tag, album tag) for the file-level tags.

    Flat files have one primary tag and no album tag. Nested files have a
    level-30 song tag and a level-50 album tag.
    """
    globals_ = [t for t in root.findall("Tag") if _is_global(t)]
    songs = [t for t in globals_ if _level(t) < 50]
    if songs:
        albums = [t for t in globals_ if _level(t) >= 50]
        return songs[0], (albums[0] if albums else None)
    return (globals_[0] if globals_ else None), None


def _first(simple: dict[str, str], names: tuple[str, ...]) -> str | None:
    for n in names:
        if simple.get(n.upper()):
            return simple[n.upper()]
    return None


def values_from_tags(root: ET.Element) -> dict[str, Any]:
    song, album = _layout(root)
    s = _simples(song) if song is not None else {}
    a = _simples(album) if album is not None else {}
    # Any other file-level tags contribute too, at lowest priority.
    rest: dict[str, str] = {}
    for t in root.findall("Tag"):
        if _is_global(t) and t is not song and t is not album:
            for k, v in _simples(t).items():
                rest.setdefault(k, v)

    out: dict[str, Any] = {}
    for key, names in _NAMES.items():
        got = _first(s, names)
        if got is None and album is not None and key in _ALBUM_LEVEL:
            got = _first(a, (_ALBUM_LEVEL[key],))
        if got is None and album is not None and key not in ("title", "artist"):
            got = _first(a, names)
        if got is None:
            got = _first(rest, names)
        if got is not None and got.strip():
            out[key] = got.strip()

    for key in ("track", "disc"):
        raw = out.get(key)
        if raw and "/" in raw:
            num, _, total = raw.partition("/")
            if num.strip():
                out[key] = num.strip()
            else:
                del out[key]
            if total.strip():
                out.setdefault(BY_KEY[key].total_of, total.strip())
    if "compilation" in out:
        out["compilation"] = str(out["compilation"]).strip().lower() not in (
            "", "0", "no", "false")
    if "rating" in out:
        # The spec's RATING is 0-5 and may be fractional; RiffTag has stars.
        try:
            out["rating"] = max(0, min(5, round(float(out["rating"]))))
        except ValueError:
            del out["rating"]
    return out


def _set_simple(tag: ET.Element, name: str, value: str) -> None:
    """Make ``name`` hold exactly ``value`` in this tag ("" removes it).

    An unchanged value is left as it is, nested children and all — a
    rewrite would drop e.g. a SORT_WITH under TITLE.
    """
    matches = [s for s in tag.findall("Simple")
               if (s.findtext("Name") or "").strip().upper() == name.upper()]
    if value and len(matches) == 1 and matches[0].findtext("String") == value:
        return
    for s in matches:
        tag.remove(s)
    if value:
        s = ET.SubElement(tag, "Simple")
        ET.SubElement(s, "Name").text = name
        ET.SubElement(s, "String").text = value


def _drop_names(tag: ET.Element, names: tuple[str, ...]) -> None:
    for n in names:
        _set_simple(tag, n, "")


def apply_values(root: ET.Element, values: dict[str, Any]) -> None:
    """Write ``values`` into the file-level tags of ``root``, in place.

    Same contract as :func:`rifftag.tagio.write`: absent keys untouched,
    ``""`` clears. Keys Matroska has no home for are ignored.
    """
    song, album = _layout(root)
    if song is None:
        song = ET.SubElement(root, "Tag")
        ET.SubElement(song, "Targets")
    others = [t for t in root.findall("Tag")
              if _is_global(t) and t is not song]

    def text(key: str) -> str:
        v = values.get(key)
        if v is None or v is False:
            return ""
        if v is True:
            return "1"
        return str(v).strip()

    # Counters go back the way they were found: "3/12" in one PART_NUMBER
    # stays that way (ffmpeg style); separate fields stay separate.
    pairs = {"track": "PART_NUMBER", "disc": "DISC"}
    handled: set[str] = set()
    for key, name in pairs.items():
        total_key = BY_KEY[key].total_of
        if key not in values and total_key not in values:
            continue
        handled |= {key, total_key}
        cur = values_from_tags(root)
        num = text(key) if key in values else str(cur.get(key, ""))
        total = (text(total_key) if total_key in values
                 else str(cur.get(total_key, "")))
        combined = "/" in (_first(_simples(song), _NAMES[key]) or "")
        if album is not None and key == "track":
            combined = False
        if key == "disc":
            combined = True        # there is no spec'd disc-total name
        for t in [song, *others]:
            _drop_names(t, _NAMES[key][1:])
            _drop_names(t, _NAMES[total_key])
            if t is not song:
                _drop_names(t, (name,))
        if album is not None and key == "track":
            _set_simple(song, name, num)
            _set_simple(album, "TOTAL_PARTS", total)
        elif combined:
            _set_simple(song, name, f"{num}/{total}" if total else num)
        else:
            _set_simple(song, name, num)
            _set_simple(song, _NAMES[total_key][0], total)

    for key, names in _NAMES.items():
        if key not in values or key in handled:
            continue
        val = text(key)
        if key == "rating" and val in ("0", ""):
            val = ""
        if album is not None and key in _ALBUM_LEVEL:
            target, name = album, _ALBUM_LEVEL[key]
            for t in [song, *[o for o in others if o is not album]]:
                _drop_names(t, names)
        else:
            target, name = song, names[0]
            _drop_names(song, names[1:])
            for t in others:
                if t is album and key in ("title", "artist"):
                    continue   # album TITLE/ARTIST mean album/album artist
                _drop_names(t, names)
        _set_simple(target, name, val)

    # A file-level tag left with nothing in it is just noise.
    for t in list(root.findall("Tag")):
        if _is_global(t) and not t.findall("Simple"):
            root.remove(t)


def tags_xml(root: ET.Element, *, global_only: bool = True) -> bytes:
    out = ET.Element("Tags")
    for t in root.findall("Tag"):
        if not global_only or _is_global(t):
            out.append(t)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        out, encoding="utf-8")


# --- reading ------------------------------------------------------------

def identify(path: Path) -> dict[str, Any]:
    proc = _run("mkvmerge", "-J", str(path))
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError as exc:
        raise MatroskaError("mkvmerge returned unreadable output") from exc
    cont = data.get("container") or {}
    if not cont.get("recognized") or cont.get("type") not in ("Matroska", "WebM"):
        errs = data.get("errors") or ["not a Matroska file"]
        raise MatroskaError(str(errs[0]))
    return data


def read_tags(path: Path) -> ET.Element:
    # To a file, not stdout: the file is always UTF-8, stdout is at the
    # mercy of the console code page on Windows.
    with tempfile.TemporaryDirectory(prefix="rifftag-mkv-") as tmp:
        out = Path(tmp) / "tags.xml"
        _run("mkvextract", str(path), "tags", str(out))
        raw = out.read_bytes() if out.is_file() else b""
    try:
        return _parse_tags(raw)
    except ET.ParseError as exc:
        raise MatroskaError(f"unreadable tag XML: {exc}") from exc


def _cover_kind(name: str) -> int:
    stem = Path(name).stem.lower()
    if stem in ("cover", "small_cover", "cover_land", "small_cover_land",
                "front", "folder"):
        return 3
    if stem.startswith("back"):
        return 4
    return 0


def image_attachments(info: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in info.get("attachments", [])
            if str(a.get("content_type", "")).lower().startswith("image/")]


def read_pictures(path: Path, info: dict[str, Any]) -> list[tuple[bytes, str, int, str]]:
    """(data, mime, kind, description) for every image attachment.

    Front covers first, so the one the file list shows is the right one.
    """
    atts = image_attachments(info)
    if not atts:
        return []
    with tempfile.TemporaryDirectory(prefix="rifftag-mkv-") as tmp:
        specs = [f"{a['id']}:{Path(tmp) / str(a['id'])}" for a in atts]
        _run("mkvextract", str(path), "attachments", *specs)
        out = []
        for a in atts:
            f = Path(tmp) / str(a["id"])
            if f.is_file():
                out.append((f.read_bytes(), str(a["content_type"]).lower(),
                            _cover_kind(str(a.get("file_name", ""))),
                            str(a.get("description", "") or "")))
    out.sort(key=lambda p: p[2] != 3)
    return out


def audio_info(path: Path, info: dict[str, Any]) -> dict[str, Any]:
    props = (info.get("container") or {}).get("properties") or {}
    length = float(props.get("duration", 0) or 0) / 1e9
    tracks = info.get("tracks") or []
    audio = next((t for t in tracks if t.get("type") == "audio"), None)
    has_video = any(t.get("type") == "video" for t in tracks)
    ap = (audio or {}).get("properties") or {}
    bitrate = 0
    bps = ap.get("tag_bps")
    if bps and str(bps).isdigit():
        bitrate = int(bps) // 1000
    elif length and not has_video:
        bitrate = int(path.stat().st_size * 8 / length / 1000)
    depth = int(ap.get("audio_bits_per_sample", 0) or 0)
    codec = str((audio or {}).get("codec", "") or "")
    if codec.lower() in ("vorbis", "opus", "aac") or "float" in codec.lower():
        depth = 0     # mkvmerge reports a decoder detail, not a source depth
    return {
        "length": length,
        "bitrate": bitrate,
        "sample_rate": int(ap.get("audio_sampling_frequency", 0) or 0),
        "channels": int(ap.get("audio_channels", 0) or 0),
        "bit_depth": depth,
        "codec": codec,
        "segment_title": str(props.get("title", "") or ""),
    }


# --- writing ------------------------------------------------------------

_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
        "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp"}


def _attachment_names(pictures: list[tuple[bytes, str, int, str]]) -> list[str]:
    names, used = [], set()
    for _data, mime, kind, _desc in pictures:
        ext = _EXT.get(mime, ".jpg")
        base = {3: "cover", 4: "back"}.get(kind, "picture")
        if base == "cover" and "cover" in {n.rsplit(".", 1)[0] for n in used}:
            base = "picture"     # only one file may claim to be THE cover
        name, n = base + ext, 2
        while name in used:
            name, n = f"{base}_{n}{ext}", n + 1
        used.add(name)
        names.append(name)
    return names


def write(path: Path, values: dict[str, Any],
          pictures: list[tuple[bytes, str, int, str]] | None) -> None:
    """One ``mkvpropedit`` call: tags, segment title and cover art together.

    One call matters: it means a failure leaves the file as it was rather
    than half-written.
    """
    info = identify(path)
    args: list[str] = [str(path)]
    with tempfile.TemporaryDirectory(prefix="rifftag-mkv-") as tmp:
        tmpdir = Path(tmp)
        if any(k in SUPPORTED_KEYS for k in values):
            root = read_tags(path)
            apply_values(root, values)
            glob = [t for t in root.findall("Tag") if _is_global(t)]
            if glob:
                xml = tmpdir / "tags.xml"
                xml.write_bytes(tags_xml(root))
                args += ["--tags", f"global:{xml}"]
            else:
                args += ["--tags", "global:"]
        if "title" in values:
            title = str(values["title"] or "").strip()
            args += ["--edit", "info"]
            args += ["--set", f"title={title}"] if title else ["--delete", "title"]
        if pictures is not None:
            for a in image_attachments(info):
                uid = (a.get("properties") or {}).get("uid")
                args += ["--delete-attachment",
                         f"={uid}" if uid is not None else str(a["id"])]
            for i, (pic, name) in enumerate(zip(pictures, _attachment_names(pictures))):
                data, mime, _kind, desc = pic
                f = tmpdir / f"att{i}{_EXT.get(mime, '.jpg')}"
                f.write_bytes(data)
                args += ["--attachment-name", name,
                         "--attachment-mime-type", mime]
                if desc:
                    args += ["--attachment-description", desc]
                args += ["--add-attachment", str(f)]
        if len(args) > 1:
            _run("mkvpropedit", *args)


def remove_all(path: Path) -> None:
    """Strip the file-level tags, the segment title and the cover art.

    Track-level tags (language, stream encoder, duration) are technical
    metadata, not tags in the sense this button means, and are kept.
    """
    info = identify(path)
    args = [str(path), "--tags", "global:", "--edit", "info", "--delete", "title"]
    for a in image_attachments(info):
        uid = (a.get("properties") or {}).get("uid")
        args += ["--delete-attachment",
                 f"={uid}" if uid is not None else str(a["id"])]
    _run("mkvpropedit", *args)
