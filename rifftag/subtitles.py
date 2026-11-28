"""Embed sidecar subtitles (.srt / .ass / .ssa) into Matroska files.

Dave keeps ``Movie (Year).eng.srt`` beside his films and wanted them inside
the MKV, as FileBot does (2026-11-28). This is the one RiffTag operation that
**rewrites a whole video file**: adding a track is a remux, not a tag edit,
so ``mkvpropedit`` cannot do it. ``mkvmerge`` copies every existing track,
tag, chapter and attachment (cover art included) bit for bit and adds the new
subtitle tracks — no re-encoding — into a temporary file *beside* the
original, which is checked and then swapped in with an atomic rename.

Safety, in order:

* free-space check first (the temp copy needs the file's size again);
* the temp file is verified with ``mkvmerge -J`` — same video/audio tracks,
  exactly the expected number of new subtitle tracks — before the swap;
* the original is only replaced by ``os.replace`` (atomic on one filesystem);
  any failure deletes the temp file and leaves the original untouched;
* sidecars are kept unless the caller asks for them to be deleted, and they
  are deleted only after a verified swap.

MP4 is deliberately not handled: Plex, Jellyfin and players read sidecar
``.srt`` next to an MP4 fine, and remuxing MP4 through ffmpeg risks the
iTunes tags.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import matroska

SUB_EXTS = (".srt", ".ass", ".ssa")

#: ISO 639-1 / 639-2 code (and common spelled-out names) -> (639-2, name).
#: Not exhaustive; anything unknown becomes "und" with the token kept in the
#: track name, so nothing invalid ever reaches mkvmerge.
_LANGS: dict[str, tuple[str, str]] = {}
for _codes, _name in (
        (("en", "eng", "english"), "English"),
        (("es", "spa", "spanish", "espanol"), "Spanish"),
        (("fr", "fre", "fra", "french"), "French"),
        (("de", "ger", "deu", "german"), "German"),
        (("it", "ita", "italian"), "Italian"),
        (("pt", "por", "portuguese"), "Portuguese"),
        (("nl", "dut", "nld", "dutch"), "Dutch"),
        (("sv", "swe", "swedish"), "Swedish"),
        (("no", "nor", "nb", "nob", "norwegian"), "Norwegian"),
        (("da", "dan", "danish"), "Danish"),
        (("fi", "fin", "finnish"), "Finnish"),
        (("pl", "pol", "polish"), "Polish"),
        (("ru", "rus", "russian"), "Russian"),
        (("uk", "ukr", "ukrainian"), "Ukrainian"),
        (("cs", "cze", "ces", "czech"), "Czech"),
        (("el", "gre", "ell", "greek"), "Greek"),
        (("tr", "tur", "turkish"), "Turkish"),
        (("ar", "ara", "arabic"), "Arabic"),
        (("he", "heb", "hebrew"), "Hebrew"),
        (("hi", "hin", "hindi"), "Hindi"),
        (("ja", "jpn", "japanese"), "Japanese"),
        (("ko", "kor", "korean"), "Korean"),
        (("zh", "chi", "zho", "chinese"), "Chinese"),
        (("vi", "vie", "vietnamese"), "Vietnamese"),
        (("th", "tha", "thai"), "Thai"),
        (("id", "ind", "indonesian"), "Indonesian"),
        (("hu", "hun", "hungarian"), "Hungarian"),
        (("ro", "rum", "ron", "romanian"), "Romanian"),
        (("hr", "hrv", "croatian"), "Croatian"),
        (("sr", "srp", "serbian"), "Serbian"),
        (("bg", "bul", "bulgarian"), "Bulgarian")):
    for _c in _codes:
        _LANGS[_c] = (_codes[1], _name)

_FORCED = {"forced", "foreign"}
_SDH = {"sdh", "cc", "hi", "hoh"}


@dataclass
class Sidecar:
    path: Path
    language: str = "und"           # ISO 639-2
    name: str = ""                  # track name shown by players
    forced: bool = False
    sdh: bool = False

    @property
    def label(self) -> str:
        return self.name or ("language not given" if self.language == "und"
                             else self.language)


def parse_sidecar(video: Path, sub: Path) -> Sidecar:
    """Language and flags from the part of the name after the video's stem.

    ``Movie.eng.srt`` → English; ``Movie.en.forced.srt`` → English, forced;
    ``Movie.en.sdh.srt`` → English SDH. ``hi`` means Hindi when it is the only
    token and hearing-impaired when a language came before it — both
    conventions are in the wild.
    """
    extra = sub.name[len(video.stem):-len(sub.suffix)].strip(". _-")
    tokens = [t for t in re.split(r"[._\s-]+", extra.lower()) if t]
    side = Sidecar(sub)
    leftovers = []
    for tok in tokens:
        if tok in _FORCED:
            side.forced = True
        elif tok in _SDH and (tok != "hi" or side.language != "und"):
            side.sdh = True
        elif tok in _LANGS and side.language == "und":
            side.language = _LANGS[tok][0]
        elif not tok.isdigit():
            leftovers.append(tok)
    name = next((n for c, n in _LANGS.values() if c == side.language), "")
    flags = [w for w, on in (("Forced", side.forced), ("SDH", side.sdh)) if on]
    if leftovers and not name:
        name = " ".join(leftovers)
    side.name = name + (f" ({', '.join(flags)})" if flags else "")
    return side


def find_sidecars(video: Path) -> list[Sidecar]:
    """Subtitle files beside ``video`` whose names start with its stem."""
    video = Path(video)
    stem = video.stem.casefold()
    found = []
    try:
        entries = sorted(video.parent.iterdir())
    except OSError:
        return []
    for p in entries:
        if not p.is_file() or p.suffix.lower() not in SUB_EXTS:
            continue
        name = p.name.casefold()
        # Exactly the stem, or the stem then a dot: "Heat (1995).eng.srt".
        # Not a space or dash, or "Heat (1995) - Director's Cut.srt" would be
        # taken for the theatrical cut's subtitles.
        if name.startswith(stem) and (len(name) == len(stem) + len(p.suffix)
                                      or name[len(stem)] == "."):
            found.append(parse_sidecar(video, p))
    return found


@dataclass
class Plan:
    video: Path
    add: list[Sidecar] = field(default_factory=list)
    already: list[Sidecar] = field(default_factory=list)   # embedded before
    error: str = ""


def _existing_subs(info: dict) -> set[tuple[str, bool, bool]]:
    out = set()
    for t in info.get("tracks") or []:
        if t.get("type") != "subtitles":
            continue
        props = t.get("properties") or {}
        lang = props.get("language") or "und"
        lang = _LANGS.get(lang, (lang, ""))[0]
        out.add((lang, bool(props.get("forced_track")),
                 bool(props.get("flag_hearing_impaired"))))
    return out


def plan(video: Path) -> Plan:
    """What embedding would add, skipping languages already inside."""
    video = Path(video)
    result = Plan(video)
    if video.suffix.lower() not in (".mkv", ".mka", ".webm"):
        result.error = "not a Matroska file (MP4 keeps its .srt beside it)"
        return result
    sidecars = find_sidecars(video)
    if not sidecars:
        return result
    try:
        have = _existing_subs(matroska.identify(video))
    except matroska.MatroskaError as exc:
        result.error = str(exc)
        return result
    for s in sidecars:
        key = (s.language, s.forced, s.sdh)
        (result.already if key in have else result.add).append(s)
        have.add(key)                 # two sidecars for one language: once
    return result


def _charset(path: Path) -> str | None:
    """None for UTF-8; otherwise the 8-bit code page most .srt files use."""
    data = path.read_bytes()
    try:
        data.decode("utf-8")
        return None
    except UnicodeDecodeError:
        return "windows-1252"


class EmbedError(RuntimeError):
    pass


def _counts(info: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in info.get("tracks") or []:
        counts[t.get("type", "?")] = counts.get(t.get("type", "?"), 0) + 1
    return counts


def embed(p: Plan, *, delete_sidecars: bool = False,
          progress: Callable[[int], None] | None = None) -> int:
    """Remux ``p.video`` with ``p.add`` as new subtitle tracks.

    Returns how many tracks were added. Raises EmbedError with a message for
    the user; the original file is untouched whenever it raises.
    """
    if not p.add:
        return 0
    video = p.video
    exe = matroska.tool("mkvmerge")
    if exe is None:
        raise EmbedError(matroska.INSTALL_HINT)
    size = video.stat().st_size
    free = shutil.disk_usage(video.parent).free
    if free < size + 64 * 1024 * 1024:
        raise EmbedError(
            f"not enough free space on that drive: needs {size / 1e9:.1f} GB "
            f"for the temporary copy, {free / 1e9:.1f} GB free")

    before = _counts(matroska.identify(video))
    tmp = video.with_name(f".{video.stem}.rifftag-embedding{video.suffix}")
    cmd = [exe, "--gui-mode", "-o", str(tmp), str(video)]
    for s in p.add:
        cmd += ["--language", f"0:{s.language}"]
        if s.name:
            cmd += ["--track-name", f"0:{s.name}"]
        if s.forced:
            cmd += ["--forced-display-flag", "0:yes"]
        if s.sdh:
            cmd += ["--hearing-impaired-flag", "0:yes"]
        cmd += ["--default-track-flag", "0:no"]
        if s.path.suffix.lower() == ".srt":
            cs = _charset(s.path)
            if cs:
                cmd += ["--sub-charset", f"0:{cs}"]
        cmd.append(str(s.path))

    env = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, **kw)
        lines = []
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            lines.append(line)
            m = re.match(r"#GUI#progress\s+(\d+)%", line)
            if m and progress:
                progress(int(m.group(1)))
        code = proc.wait()
        if code >= 2:
            err = next((ln.split(":", 1)[-1].strip() for ln in reversed(lines)
                        if "error" in ln.lower()), f"mkvmerge failed ({code})")
            raise EmbedError(err)
        after_info = matroska.identify(tmp)
        after = _counts(after_info)
        for kind in ("video", "audio"):
            if after.get(kind, 0) != before.get(kind, 0):
                raise EmbedError(f"verification failed: {kind} tracks "
                                 f"{before.get(kind, 0)} → {after.get(kind, 0)}")
        if after.get("subtitles", 0) != before.get("subtitles", 0) + len(p.add):
            raise EmbedError("verification failed: subtitle tracks did not "
                             "come out as expected")
        os.replace(tmp, video)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if delete_sidecars:
        for s in p.add + p.already:
            try:
                s.path.unlink()
            except OSError:
                pass
    return len(p.add)
