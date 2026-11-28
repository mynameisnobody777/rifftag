"""Reading and writing tags, for every container Tag&Rename supports.

This is the only module that knows what an ID3 frame id, an MP4 atom or a
Vorbis comment key looks like. Everything above it speaks :mod:`rifftag.fields`
keys and nothing else.

Five families cover the whole format list from the original's overview page,
and a sixth was added for Matroska:

========  =======================================  ====================
family    extensions                               tag standard
========  =======================================  ====================
id3       mp3 aiff aif wav dsf dsd                 ID3v2.3 / ID3v2.4
mp4       m4a m4b m4r m4p m4v mp4                  iTunes atoms
vorbis    ogg oga opus spx flac                    Vorbis comments
ape       mpc mp+ ape ofr ofs wv tta               APEv2
asf       wma asf wmv                              Windows Media
matroska  mkv mka webm                             Matroska tags
========  =======================================  ====================

mutagen has no Matroska support, so the ``matroska`` family is handed to
:mod:`rifftag.matroska`, which drives the MKVToolNix command-line tools. That
module is the one exception to "only tagio knows the wire format"; it is
called from here and nowhere else.

Interop notes that cost real debugging time, so they are settings rather than
assumptions — all three are options Tag&Rename itself exposes:

* **Grouping.** iTunes moved from ``TIT1`` to ``GRP1``; Tag&Rename 3.9.12 added
  ``GRP1`` and 3.9.13 changed which one it writes by default. We read both,
  preferring ``GRP1``, and write whichever :class:`WriteOptions` says.
* **Album Artist in APE/Vorbis.** Some tools spell it ``ALBUMARTIST`` and some
  ``ALBUM ARTIST``. We read either and write the one you pick.
* **Label in APE/Vorbis.** Historically ``ORGANIZATION``; ``LABEL`` is now
  common. Same treatment.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field as _dc
from pathlib import Path
from typing import Any

import mutagen
from mutagen import id3 as _id3
from mutagen.apev2 import APEv2File, APEBinaryValue
from mutagen.asf import ASF, ASFByteArrayAttribute
from mutagen.flac import FLAC, Picture as FLACPicture
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm, AtomDataType

from . import matroska as _mkv
from .fields import BY_KEY, FIELDS, Kind


class TagError(RuntimeError):
    """A tag could not be read or written."""


# --- iTunes TV frames ---------------------------------------------------
# mutagen ships no class for these, so an unregistered TVSH written by
# Tag&Rename would come back as an opaque blob. Registering them makes those
# existing tags round-trip instead of being silently dropped on save.

def _register_tv_frames() -> None:
    import mutagen.id3._frames as _f
    for name, doc in (("TVSH", "Show title"), ("TVEN", "Episode id"),
                      ("TVSE", "Season number"), ("TVES", "Episode number")):
        if hasattr(_id3, name):
            continue
        cls = type(name, (_id3.TextFrame,), {"__doc__": doc})
        setattr(_id3, name, cls)
        _f.Frames[name] = cls


_register_tv_frames()


EXT_FAMILY: dict[str, str] = {
    **{e: "id3" for e in (".mp3", ".aiff", ".aif", ".aifc", ".wav", ".dsf", ".dsd", ".mpg")},
    **{e: "mp4" for e in (".m4a", ".m4b", ".m4r", ".m4p", ".m4v", ".mp4")},
    **{e: "vorbis" for e in (".ogg", ".oga", ".opus", ".spx", ".flac")},
    **{e: "ape" for e in (".mpc", ".mp+", ".ape", ".ofr", ".ofs", ".wv", ".tta")},
    **{e: "asf" for e in (".wma", ".asf", ".wmv")},
    **{e: "matroska" for e in (".mkv", ".mka", ".webm")},
}

AUDIO_EXTENSIONS = tuple(sorted(EXT_FAMILY))


def family_for(path: os.PathLike[str] | str) -> str | None:
    return EXT_FAMILY.get(Path(path).suffix.lower())


# --- value objects ------------------------------------------------------

@dataclass
class Picture:
    data: bytes
    mime: str = "image/jpeg"
    kind: int = 3               # ID3 picture type 3 = Cover (front)
    desc: str = ""

    @property
    def size(self) -> int:
        return len(self.data)

    def dimensions(self) -> tuple[int, int] | None:
        """Width/height without pulling in an image library.

        Only JPEG and PNG are probed; anything else returns ``None`` rather
        than guessing. Used for the Cover Dimensions file-list column.
        """
        d = self.data
        if d[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(d[16:20], "big"), int.from_bytes(d[20:24], "big")
        if d[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(d):
                if d[i] != 0xFF:
                    i += 1
                    continue
                marker = d[i + 1]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    return (int.from_bytes(d[i + 7:i + 9], "big"),
                            int.from_bytes(d[i + 5:i + 7], "big"))
                i += 2 + int.from_bytes(d[i + 2:i + 4], "big")
        return None


@dataclass
class AudioInfo:
    length: float = 0.0          # seconds
    bitrate: int = 0             # kbps
    sample_rate: int = 0         # Hz
    channels: int = 0
    bit_depth: int = 0
    codec: str = ""

    @property
    def play_time(self) -> str:
        m, s = divmod(int(round(self.length)), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class Track:
    path: Path
    family: str
    values: dict[str, Any] = _dc(default_factory=dict)
    pictures: list[Picture] = _dc(default_factory=list)
    info: AudioInfo = _dc(default_factory=AudioInfo)
    tag_version: str = ""
    error: str | None = None

    @property
    def has_tag(self) -> bool:
        """Whether the file carries any tag at all.

        Checked by content rather than by container: an empty APEv2 or Vorbis
        block still "exists" as far as the parser is concerned, and a file
        with nothing in it should read as untagged.
        """
        return bool(self.values) or bool(self.pictures)

    def rename_values(self, *, file_number: int = 1) -> dict[str, Any]:
        """Everything a rename mask can reference, tags plus derived."""
        v: dict[str, Any] = dict(self.values)
        v.update(
            filename=self.path.stem,
            file_number=file_number,
            bitrate=self.info.bitrate or "",
            play_time=self.info.play_time,
            sample_rate=self.info.sample_rate or "",
            bit_depth=self.info.bit_depth or "",
            encoder=self.values.get("encoded_by", ""),
        )
        season = str(self.values.get("season_number") or "").split("/")[0].strip()
        episode = str(self.values.get("episode_number") or "").split("/")[0].strip()
        if season.isdigit() and episode.isdigit():
            v["sxe"] = f"{int(season)}x{int(episode):02d}"
            v["s00e00"] = f"S{int(season):02d}E{int(episode):02d}"
        return {k: val for k, val in v.items() if val not in (None, "")}


@dataclass
class WriteOptions:
    """Per-library conventions. Defaults match Tag&Rename's own defaults."""

    id3_version: int = 3              # "always writes ID3v2.3 for compatibility"
    id3_unicode: bool = True          # "Always save ID3v2 tags in Unicode"
    id3_grouping_frame: str = "GRP1"  # or "TIT1"
    keep_id3v1: bool = True
    album_artist_key: str = "ALBUMARTIST"   # or "ALBUM ARTIST"
    label_key: str = "LABEL"                # or "ORGANIZATION"
    ogg_new_cover_standard: bool = True     # METADATA_BLOCK_PICTURE
    #: WMP-compatible POPM owner, so star ratings agree with Windows Media Player
    popm_email: str = "Windows Media Player 9 Series"


# --- ID3 ----------------------------------------------------------------

_ID3_TEXT = {
    "title": "TIT2", "artist": "TPE1", "album": "TALB", "genre": "TCON",
    "composer": "TCOM", "album_artist": "TPE2", "conductor": "TPE3",
    "lyricist": "TEXT", "orig_artist": "TOPE", "orig_lyricist": "TOLY",
    "copyright": "TCOP", "encoded_by": "TENC", "label": "TPUB",
    "remixed_by": "TPE4", "initial_key": "TKEY", "subtitle": "TIT3",
    "isrc": "TSRC", "mood": "TMOO", "radio_station": "TRSN",
    "release_time": "TDRL", "year": "TDRC",
    "bpm": "TBPM",
    "sort_title": "TSOT", "sort_artist": "TSOP", "sort_album_artist": "TSO2",
    "sort_album": "TSOA", "sort_composer": "TSOC",
    "podcast_category": "TCAT", "podcast_id": "TGID", "podcast_desc": "TDES",
    "show_title": "TVSH", "episode_id": "TVEN",
    "season_number": "TVSE", "episode_number": "TVES",
}
_ID3_URL = {
    "url_file": "WOAF", "url_artist": "WOAR", "url_source": "WOAS",
    "url_radio": "WORS", "url_buy": "WCOM",
}
_ID3_TXXX = {"catalog": "CATALOGNUMBER", "media_kind": "MEDIA KIND"}
_ID3_PAIRED = {"track": "TRCK", "disc": "TPOS"}

_POPM_STARS = ((0, 0), (1, 1), (2, 64), (3, 128), (4, 196), (5, 255))


def _popm_to_stars(rating: int) -> int:
    best = 0
    for stars, value in _POPM_STARS:
        if rating >= value:
            best = stars
    return best


def _stars_to_popm(stars: int) -> int:
    return dict((s, v) for s, v in _POPM_STARS).get(int(stars), 0)


def _read_id3(tags: _id3.ID3 | None, out: dict[str, Any],
              pics: list[Picture], opts: WriteOptions) -> str:
    if tags is None:
        return ""
    for key, fid in _ID3_TEXT.items():
        fr = tags.getall(fid)
        if fr and str(fr[0]):
            out[key] = str(fr[0])
    # Grouping lives in two places; iTunes' GRP1 wins when both exist.
    for fid in ("GRP1", "TIT1"):
        fr = tags.getall(fid)
        if fr and str(fr[0]):
            out["grouping"] = str(fr[0])
            break
    for key, fid in _ID3_PAIRED.items():
        fr = tags.getall(fid)
        if fr and str(fr[0]):
            num, _, total = str(fr[0]).partition("/")
            if num.strip():
                out[key] = num.strip()
            if total.strip():
                out[BY_KEY[key].total_of] = total.strip()
    for key, fid in _ID3_URL.items():
        fr = tags.getall(fid)
        if fr and getattr(fr[0], "url", ""):
            out[key] = fr[0].url
    for fr in tags.getall("WXXX"):
        if fr.url:
            out["url"] = fr.url
            break
    for fr in tags.getall("TXXX"):
        for key, desc in _ID3_TXXX.items():
            if fr.desc.upper() == desc:
                out[key] = str(fr)
    comm = [f for f in tags.getall("COMM") if f.desc in ("", "comment")]
    if comm and str(comm[0]):
        out["comment"] = str(comm[0])
    uslt = tags.getall("USLT")
    if uslt and str(uslt[0]):
        out["lyrics"] = str(uslt[0])
    cmp_ = tags.getall("TCMP")
    if cmp_:
        out["compilation"] = str(cmp_[0]) == "1"
    if tags.getall("PCST"):
        out["podcast"] = True
    wfed = tags.getall("WFED")
    if wfed:
        out["podcast_url"] = str(wfed[0])
    popm = tags.getall("POPM")
    if popm:
        pref = [p for p in popm if p.email == opts.popm_email] or popm
        out["rating"] = _popm_to_stars(pref[0].rating)
    for apic in tags.getall("APIC"):
        pics.append(Picture(apic.data, apic.mime or "image/jpeg",
                            int(apic.type), apic.desc))
    v = getattr(tags, "version", None)
    return "ID3v2.%d" % v[1] if v else "ID3v2"


def _write_id3(tags: _id3.ID3, values: dict[str, Any],
               pictures: list[Picture] | None, opts: WriteOptions) -> None:
    enc = 3 if opts.id3_unicode else 0   # 3 = UTF-8, 0 = Latin-1

    def setf(frame_id: str, frame: Any) -> None:
        tags.delall(frame_id)
        if frame is not None:
            tags.add(frame)

    for key, fid in _ID3_TEXT.items():
        if key not in values:
            continue
        text = values[key]
        cls = getattr(_id3, fid)
        setf(fid, cls(encoding=enc, text=[str(text)]) if str(text) else None)
    if "grouping" in values:
        # Clear both spellings so the two never disagree after a save.
        tags.delall("TIT1")
        tags.delall("GRP1")
        if str(values["grouping"]):
            cls = getattr(_id3, opts.id3_grouping_frame)
            tags.add(cls(encoding=enc, text=[str(values["grouping"])]))
    for key, fid in _ID3_PAIRED.items():
        total_key = BY_KEY[key].total_of
        if key not in values and total_key not in values:
            continue
        num = str(values.get(key, "") or "")
        total = str(values.get(total_key, "") or "")
        text = f"{num}/{total}" if total else num
        cls = getattr(_id3, fid)
        setf(fid, cls(encoding=0, text=[text]) if text else None)
    for key, fid in _ID3_URL.items():
        if key in values:
            cls = getattr(_id3, fid)
            setf(fid, cls(url=str(values[key])) if str(values[key]) else None)
    if "url" in values:
        setf("WXXX", _id3.WXXX(encoding=enc, desc="", url=str(values["url"]))
             if str(values["url"]) else None)
    for key, desc in _ID3_TXXX.items():
        if key not in values:
            continue
        for fr in list(tags.getall("TXXX")):
            if fr.desc.upper() == desc:
                tags.delall(f"TXXX:{fr.desc}")
        if str(values[key]):
            tags.add(_id3.TXXX(encoding=enc, desc=desc, text=[str(values[key])]))
    if "comment" in values:
        tags.delall("COMM")
        if str(values["comment"]):
            tags.add(_id3.COMM(encoding=enc, lang="eng", desc="",
                               text=[str(values["comment"])]))
    if "lyrics" in values:
        tags.delall("USLT")
        if str(values["lyrics"]):
            tags.add(_id3.USLT(encoding=enc, lang="eng", desc="",
                               text=str(values["lyrics"])))
    if "compilation" in values:
        setf("TCMP", _id3.TCMP(encoding=0, text=["1"]) if values["compilation"] else None)
    if "podcast" in values:
        setf("PCST", _id3.PCST(value=0) if values["podcast"] else None)
    if "podcast_url" in values:
        setf("WFED", _id3.WFED(encoding=enc, url=str(values["podcast_url"]))
             if str(values["podcast_url"]) else None)
    if "rating" in values:
        tags.delall("POPM")
        stars = int(values["rating"] or 0)
        if stars:
            tags.add(_id3.POPM(email=opts.popm_email,
                               rating=_stars_to_popm(stars), count=0))
    if pictures is not None:
        tags.delall("APIC")
        for p in pictures:
            tags.add(_id3.APIC(encoding=0, mime=p.mime, type=p.kind,
                               desc=p.desc, data=p.data))


# --- MP4 ----------------------------------------------------------------

_MP4_TEXT = {
    "title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb",
    "album_artist": "aART", "composer": "\xa9wrt", "genre": "\xa9gen",
    "year": "\xa9day", "comment": "\xa9cmt", "grouping": "\xa9grp",
    "lyrics": "\xa9lyr", "encoded_by": "\xa9too", "copyright": "cprt",
    "sort_title": "sonm", "sort_artist": "soar", "sort_album_artist": "soaa",
    "sort_album": "soal", "sort_composer": "soco", "sort_show": "sosn",
    "show_title": "tvsh", "episode_id": "tven",
    "podcast_category": "catg", "podcast_id": "egid", "podcast_desc": "desc",
    "podcast_url": "purl",
}
_MP4_INT = {"bpm": "tmpo", "season_number": "tvsn", "episode_number": "tves",
            "media_kind": "stik", "rating": "rate"}
_MP4_BOOL = {"compilation": "cpil", "podcast": "pcst"}
_MP4_PAIRED = {"track": "trkn", "disc": "disk"}
#: everything else rides in an iTunes freeform atom, which is what other
#: taggers do too, so the values stay visible outside RiffTag
_MP4_FREEFORM = {
    "conductor": "CONDUCTOR", "lyricist": "LYRICIST",
    "orig_artist": "ORIGINAL ARTIST", "orig_lyricist": "ORIGINAL LYRICIST",
    "label": "LABEL", "remixed_by": "REMIXER", "initial_key": "initialkey",
    "subtitle": "SUBTITLE", "isrc": "ISRC", "catalog": "CATALOGNUMBER",
    "mood": "MOOD", "release_time": "RELEASETIME", "url": "URL",
    "url_artist": "ARTISTWEBPAGE", "url_file": "FILEWEBPAGE",
    "url_source": "SOURCEWEBPAGE", "url_radio": "RADIOWEBPAGE",
    "url_buy": "BUYWEBPAGE", "radio_station": "RADIOSTATION",
}
_FF = "----:com.apple.iTunes:%s"


def _read_mp4(tags: Any, out: dict[str, Any], pics: list[Picture]) -> str:
    if tags is None:
        return ""
    for key, atom in _MP4_TEXT.items():
        if tags.get(atom):
            out[key] = str(tags[atom][0])
    for key, atom in _MP4_INT.items():
        if tags.get(atom):
            out[key] = int(tags[atom][0])
    for key, atom in _MP4_BOOL.items():
        if tags.get(atom):
            out[key] = bool(tags[atom])
    for key, atom in _MP4_PAIRED.items():
        if tags.get(atom):
            num, total = (list(tags[atom][0]) + [0, 0])[:2]
            if num:
                out[key] = str(num)
            if total:
                out[BY_KEY[key].total_of] = str(total)
    for key, name in _MP4_FREEFORM.items():
        val = tags.get(_FF % name)
        if val:
            raw = val[0]
            out[key] = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    for cover in tags.get("covr", []):
        mime = ("image/png" if getattr(cover, "imageformat", None) == MP4Cover.FORMAT_PNG
                else "image/jpeg")
        pics.append(Picture(bytes(cover), mime))
    return "iTunes (MP4)"


def _write_mp4(tags: Any, values: dict[str, Any],
               pictures: list[Picture] | None) -> None:
    def drop(atom: str) -> None:
        tags.pop(atom, None)

    for key, atom in _MP4_TEXT.items():
        if key in values:
            text = str(values[key] or "")
            tags[atom] = [text] if text else []
            if not text:
                drop(atom)
    for key, atom in _MP4_INT.items():
        if key in values:
            raw = values[key]
            if raw in (None, "", 0):
                drop(atom)
            else:
                tags[atom] = [int(raw)]
    for key, atom in _MP4_BOOL.items():
        if key in values:
            if values[key]:
                tags[atom] = True
            else:
                drop(atom)
    for key, atom in _MP4_PAIRED.items():
        total_key = BY_KEY[key].total_of
        if key not in values and total_key not in values:
            continue
        num = int(str(values.get(key, "") or 0).split("/")[0] or 0)
        total = int(values.get(total_key, "") or 0)
        if num or total:
            tags[atom] = [(num, total)]
        else:
            drop(atom)
    for key, name in _MP4_FREEFORM.items():
        if key not in values:
            continue
        text = str(values[key] or "")
        if text:
            tags[_FF % name] = [MP4FreeForm(text.encode("utf-8"),
                                            AtomDataType.UTF8)]
        else:
            drop(_FF % name)
    if pictures is not None:
        if pictures:
            tags["covr"] = [
                MP4Cover(p.data,
                         MP4Cover.FORMAT_PNG if p.mime == "image/png"
                         else MP4Cover.FORMAT_JPEG)
                for p in pictures]
        else:
            drop("covr")


# --- Vorbis comments and APEv2 -----------------------------------------
# Both are plain key/value stores, so one table drives both; only the few
# keys that genuinely differ are overridden per family.

_VORBIS = {
    "title": "TITLE", "artist": "ARTIST", "album": "ALBUM", "genre": "GENRE",
    "year": "DATE", "comment": "COMMENT", "composer": "COMPOSER",
    "conductor": "CONDUCTOR", "lyricist": "LYRICIST",
    "orig_artist": "ORIGINALARTIST", "orig_lyricist": "ORIGINALLYRICIST",
    "copyright": "COPYRIGHT", "encoded_by": "ENCODEDBY",
    "remixed_by": "REMIXER", "initial_key": "INITIALKEY",
    "subtitle": "SUBTITLE", "isrc": "ISRC", "catalog": "CATALOGNUMBER",
    "mood": "MOOD", "bpm": "BPM", "grouping": "GROUPING",
    "lyrics": "LYRICS", "release_time": "RELEASETIME", "url": "URL",
    "radio_station": "RADIOSTATION",
    "url_file": "FILEWEBPAGE", "url_artist": "ARTISTWEBPAGE",
    "url_source": "SOURCEWEBPAGE", "url_radio": "RADIOWEBPAGE",
    "url_buy": "BUYWEBPAGE",
    "track": "TRACKNUMBER", "track_total": "TRACKTOTAL",
    "disc": "DISCNUMBER", "disc_total": "DISCTOTAL",
    "rating": "RATING", "media_kind": "MEDIAKIND",
    "show_title": "SHOW", "episode_id": "EPISODEID",
    "season_number": "SEASONNUMBER", "episode_number": "EPISODENUMBER",
    "sort_title": "TITLESORT", "sort_artist": "ARTISTSORT",
    "sort_album_artist": "ALBUMARTISTSORT", "sort_album": "ALBUMSORT",
    "sort_composer": "COMPOSERSORT",
}
_APE_OVERRIDES = {
    "year": "Year", "track": "Track", "disc": "Disc",
    "comment": "Comment", "catalog": "Catalog",
}
#: spellings we accept on read even though we write only the configured one
_ALBUM_ARTIST_ALIASES = ("ALBUMARTIST", "ALBUM ARTIST", "ALBUM_ARTIST")
_LABEL_ALIASES = ("LABEL", "ORGANIZATION", "PUBLISHER")


def _kv_table(family: str, opts: WriteOptions) -> dict[str, str]:
    table = dict(_VORBIS)
    if family == "ape":
        table.update(_APE_OVERRIDES)
    table["album_artist"] = opts.album_artist_key
    table["label"] = opts.label_key
    return table


def _read_kv(tags: Any, out: dict[str, Any], family: str,
             opts: WriteOptions) -> None:
    if tags is None:
        return
    upper = {}
    for k in tags.keys():
        try:
            upper.setdefault(k.upper(), tags[k])
        except Exception:                     # binary APE values
            continue

    def first(name: str) -> str | None:
        val = upper.get(name.upper())
        if val is None:
            return None
        if isinstance(val, (list, tuple)):
            return str(val[0]) if val else None
        return str(val)

    for key, name in _kv_table(family, opts).items():
        got = first(name)
        if got:
            out[key] = got
    for alias in _ALBUM_ARTIST_ALIASES:
        got = first(alias)
        if got:
            out["album_artist"] = got
            break
    for alias in _LABEL_ALIASES:
        got = first(alias)
        if got:
            out["label"] = got
            break
    comp = first("COMPILATION")
    if comp is not None:
        out["compilation"] = comp.strip() not in ("", "0", "no", "false")
    # "3/12" is legal in a TRACKNUMBER even though TRACKTOTAL exists.
    for key in ("track", "disc"):
        raw = out.get(key)
        if raw and "/" in str(raw):
            num, _, total = str(raw).partition("/")
            out[key] = num.strip()
            out.setdefault(BY_KEY[key].total_of, total.strip())


def _write_kv(tags: Any, values: dict[str, Any], family: str,
              opts: WriteOptions) -> None:
    table = _kv_table(family, opts)
    existing = {k.upper(): k for k in tags.keys()}

    def put(name: str, text: str) -> None:
        for alias in {name.upper()} | ({a for a in _ALBUM_ARTIST_ALIASES}
                                       if name.upper() in
                                       {a.upper() for a in _ALBUM_ARTIST_ALIASES}
                                       else set()):
            if alias in existing and existing[alias] != name:
                del tags[existing[alias]]
        if text:
            tags[name] = [text] if family == "vorbis" else text
        elif name in tags:
            del tags[name]

    for key, name in table.items():
        if key in values:
            put(name, str(values[key] or ""))
    if "compilation" in values:
        put("COMPILATION", "1" if values["compilation"] else "")


# --- ASF (Windows Media) ------------------------------------------------

_ASF = {
    "title": "Title", "artist": "Author", "album": "WM/AlbumTitle",
    "album_artist": "WM/AlbumArtist", "genre": "WM/Genre",
    "year": "WM/Year", "comment": "Description", "composer": "WM/Composer",
    "conductor": "WM/Conductor", "lyricist": "WM/Writer",
    "copyright": "Copyright", "encoded_by": "WM/EncodedBy",
    "label": "WM/Publisher", "remixed_by": "WM/ModifiedBy",
    "initial_key": "WM/InitialKey", "subtitle": "WM/SubTitle",
    "isrc": "WM/ISRC", "mood": "WM/Mood", "bpm": "WM/BeatsPerMinute",
    "grouping": "WM/ContentGroupDescription", "lyrics": "WM/Lyrics",
    "track": "WM/TrackNumber", "disc": "WM/PartOfSet",
    "url": "WM/AuthorURL", "radio_station": "WM/RadioStationName",
    "sort_title": "WM/TitleSortOrder", "sort_artist": "WM/ArtistSortOrder",
    "sort_album": "WM/AlbumSortOrder",
}


def _read_asf(tags: Any, out: dict[str, Any], pics: list[Picture]) -> None:
    if tags is None:
        return
    for key, name in _ASF.items():
        val = tags.get(name)
        if val:
            out[key] = str(val[0])
    if tags.get("WM/IsCompilation"):
        out["compilation"] = bool(tags["WM/IsCompilation"][0])
    for attr in tags.get("WM/Picture", []):
        raw = bytes(attr.value) if hasattr(attr, "value") else bytes(attr)
        pic = _parse_wm_picture(raw)
        if pic:
            pics.append(pic)


def _parse_wm_picture(raw: bytes) -> Picture | None:
    """WM/Picture is: type byte, 4-byte size, UTF-16LE mime NUL, desc NUL, data."""
    try:
        kind = raw[0]
        size = int.from_bytes(raw[1:5], "little")
        rest = raw[5:]
        end = rest.index(b"\x00\x00", 0)
        end += end % 2
        mime = rest[:end].decode("utf-16-le")
        rest = rest[end + 2:]
        end = rest.index(b"\x00\x00", 0)
        end += end % 2
        desc = rest[:end].decode("utf-16-le")
        return Picture(rest[end + 2:end + 2 + size], mime or "image/jpeg", kind, desc)
    except Exception:
        return None


def _build_wm_picture(p: Picture) -> bytes:
    return (bytes([p.kind]) + len(p.data).to_bytes(4, "little")
            + p.mime.encode("utf-16-le") + b"\x00\x00"
            + p.desc.encode("utf-16-le") + b"\x00\x00" + p.data)


def _write_asf(tags: Any, values: dict[str, Any],
               pictures: list[Picture] | None) -> None:
    for key, name in _ASF.items():
        if key in values:
            text = str(values[key] or "")
            if text:
                tags[name] = [text]
            elif name in tags:
                del tags[name]
    if "compilation" in values:
        tags["WM/IsCompilation"] = [bool(values["compilation"])]
    if pictures is not None:
        if pictures:
            tags["WM/Picture"] = [
                ASFByteArrayAttribute(_build_wm_picture(p)) for p in pictures]
        elif "WM/Picture" in tags:
            del tags["WM/Picture"]


# --- public API ---------------------------------------------------------

def _audio_info(mf: Any) -> AudioInfo:
    i = getattr(mf, "info", None)
    if i is None:
        return AudioInfo()
    return AudioInfo(
        length=float(getattr(i, "length", 0.0) or 0.0),
        bitrate=int((getattr(i, "bitrate", 0) or 0) / 1000),
        sample_rate=int(getattr(i, "sample_rate", 0) or 0),
        channels=int(getattr(i, "channels", 0) or 0),
        bit_depth=int(getattr(i, "bits_per_sample", 0) or 0),
        codec=type(i).__module__.rsplit(".", 1)[-1],
    )


def read(path: os.PathLike[str] | str, *,
         options: WriteOptions | None = None) -> Track:
    """Read one file. Never raises for a bad file — sets ``Track.error``."""
    opts = options or WriteOptions()
    p = Path(path)
    fam = family_for(p) or ""
    track = Track(path=p, family=fam)
    if fam == "matroska":
        _read_matroska(track)
        return track
    try:
        mf = mutagen.File(p)
    except Exception as exc:
        track.error = f"{type(exc).__name__}: {exc}"
        return track
    if mf is None:
        track.error = "unrecognised audio file"
        return track

    track.info = _audio_info(mf)
    out, pics = track.values, track.pictures
    try:
        if fam == "id3" or isinstance(mf.tags, _id3.ID3):
            tags = mf.tags
            if hasattr(tags, "getall"):
                track.tag_version = _read_id3(tags, out, pics, opts)
        elif fam == "mp4":
            track.tag_version = _read_mp4(mf.tags, out, pics)
        elif fam == "vorbis":
            _read_kv(mf.tags, out, "vorbis", opts)
            track.tag_version = "Vorbis Comments"
            pics.extend(_read_vorbis_pictures(mf))
        elif fam == "ape":
            _read_kv(mf.tags, out, "ape", opts)
            track.tag_version = "APEv2"
            pics.extend(_read_ape_pictures(mf.tags))
        elif fam == "asf":
            _read_asf(mf.tags, out, pics)
            track.tag_version = "Windows Media"
    except Exception as exc:
        track.error = f"{type(exc).__name__}: {exc}"
    return track


def _read_matroska(track: Track) -> None:
    try:
        info = _mkv.identify(track.path)
        ai = _mkv.audio_info(track.path, info)
        track.info = AudioInfo(length=ai["length"], bitrate=ai["bitrate"],
                               sample_rate=ai["sample_rate"],
                               channels=ai["channels"],
                               bit_depth=ai["bit_depth"], codec=ai["codec"])
        track.values.update(_mkv.values_from_tags(_mkv.read_tags(track.path)))
        if "title" not in track.values and ai["segment_title"].strip():
            track.values["title"] = ai["segment_title"].strip()
        track.pictures.extend(Picture(d, m, k, desc) for d, m, k, desc
                              in _mkv.read_pictures(track.path, info))
        # mkvmerge calls WebM "Matroska" too, so go by the extension.
        track.tag_version = ("WebM" if track.path.suffix.lower() == ".webm"
                             else "Matroska")
    except _mkv.MatroskaError as exc:
        track.error = str(exc)
    except Exception as exc:
        track.error = f"{type(exc).__name__}: {exc}"


def _read_vorbis_pictures(mf: Any) -> list[Picture]:
    pics: list[Picture] = []
    if isinstance(mf, FLAC):
        pics.extend(Picture(pic.data, pic.mime or "image/jpeg",
                            int(pic.type), pic.desc or "")
                    for pic in mf.pictures)
        return pics
    tags = mf.tags
    if not tags:
        return pics
    for raw in tags.get("metadata_block_picture", []):
        try:
            pic = FLACPicture(base64.b64decode(raw))
            pics.append(Picture(pic.data, pic.mime or "image/jpeg",
                                int(pic.type), pic.desc or ""))
        except Exception:
            continue
    for raw in tags.get("coverart", []):          # the pre-2009 convention
        try:
            pics.append(Picture(base64.b64decode(raw)))
        except Exception:
            continue
    return pics


_APE_COVER_KEYS = ("Cover Art (Front)", "Cover Art (Back)")


def _read_ape_pictures(tags: Any) -> list[Picture]:
    pics: list[Picture] = []
    if not tags:
        return pics
    for key in _APE_COVER_KEYS:
        val = tags.get(key)
        if val is None:
            continue
        raw = bytes(val.value) if hasattr(val, "value") else bytes(val)
        # APE image values are "filename.jpg\0<binary>".
        _, _, data = raw.partition(b"\x00")
        if data:
            mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
            pics.append(Picture(data, mime, 3 if "Front" in key else 4))
    return pics


def _write_vorbis_pictures(mf: Any, pictures: list[Picture],
                           opts: WriteOptions) -> None:
    if isinstance(mf, FLAC):
        mf.clear_pictures()
        for p in pictures:
            fp = FLACPicture()
            fp.data, fp.mime, fp.type, fp.desc = p.data, p.mime, p.kind, p.desc
            mf.add_picture(fp)
        return
    tags = mf.tags
    for key in ("metadata_block_picture", "coverart", "coverartmime"):
        if key in tags:
            del tags[key]          # VCommentDict.pop takes no default
    if not pictures:
        return
    if opts.ogg_new_cover_standard:
        blocks = []
        for p in pictures:
            fp = FLACPicture()
            fp.data, fp.mime, fp.type, fp.desc = p.data, p.mime, p.kind, p.desc
            blocks.append(base64.b64encode(fp.write()).decode("ascii"))
        tags["metadata_block_picture"] = blocks
    else:
        tags["coverart"] = [base64.b64encode(p.data).decode("ascii")
                            for p in pictures]
        tags["coverartmime"] = [p.mime for p in pictures]


def _write_ape_pictures(tags: Any, pictures: list[Picture]) -> None:
    for key in _APE_COVER_KEYS:
        if key in tags:
            del tags[key]
    for p in pictures:
        key = "Cover Art (Front)" if p.kind != 4 else "Cover Art (Back)"
        ext = "png" if p.mime == "image/png" else "jpg"
        tags[key] = APEBinaryValue(f"cover.{ext}".encode() + b"\x00" + p.data)


def write(path: os.PathLike[str] | str, values: dict[str, Any], *,
          pictures: list[Picture] | None = None,
          options: WriteOptions | None = None) -> None:
    """Write only the keys present in ``values``.

    A key mapped to ``""`` clears that field; a key that is absent is left
    exactly as it was. That is what makes the multi-file editor safe: checking
    Album and Year and saving across 33 files cannot touch their Titles.

    ``pictures=None`` leaves cover art untouched; ``pictures=[]`` removes it.
    """
    opts = options or WriteOptions()
    p = Path(path)
    fam = family_for(p)
    if fam is None:
        raise TagError(f"unsupported file type: {p.suffix}")

    if fam == "matroska":
        try:
            _mkv.write(p, values, None if pictures is None else
                       [(x.data, x.mime, x.kind, x.desc) for x in pictures])
        except _mkv.MatroskaError as exc:
            raise TagError(f"{p.name}: {exc}") from exc
        return

    try:
        mf = mutagen.File(p)
        if mf is None:
            raise TagError("unrecognised audio file")
        if mf.tags is None:
            mf.add_tags()

        if fam == "id3" or isinstance(mf.tags, _id3.ID3):
            _write_id3(mf.tags, values, pictures, opts)
            _save_id3(mf, opts)
            return
        if fam == "mp4":
            _write_mp4(mf.tags, values, pictures)
        elif fam == "vorbis":
            _write_kv(mf.tags, values, "vorbis", opts)
            if pictures is not None:
                _write_vorbis_pictures(mf, pictures, opts)
        elif fam == "ape":
            _write_kv(mf.tags, values, "ape", opts)
            if pictures is not None:
                _write_ape_pictures(mf.tags, pictures)
        elif fam == "asf":
            _write_asf(mf.tags, values, pictures)
        mf.save()
    except TagError:
        raise
    except Exception as exc:
        raise TagError(f"{p.name}: {type(exc).__name__}: {exc}") from exc


#: Frames mutagen's ``update_to_v23()`` throws away, verified by probing it
#: rather than read off the spec. ``TDRC`` is excluded on purpose: the
#: downgrade correctly rewrites it as ``TYER``, which is what we want.
#: The rest are frames iTunes, foobar2000 and Mp3tag all happily write into
#: v2.3 tags in the wild, and a reader that does not know a frame id skips it.
#: Dropping them would silently lose Mood and the sort fields on every save.
_V24_ONLY_TO_PRESERVE = ("TMOO", "TDRL", "TSOA", "TSOP", "TSOT", "TSST")


def _save_id3(mf: Any, opts: WriteOptions) -> None:
    """Save an ID3 tag at the configured version.

    mutagen will not downgrade on its own — a v2.4-only frame such as TDRC
    silently fails to appear in a file saved as v2.3 unless the tag is
    converted first.
    """
    tags = mf.tags
    if opts.id3_version == 3:
        carry = [f for fid in _V24_ONLY_TO_PRESERVE for f in tags.getall(fid)]
        tags.update_to_v23()
        for frame in carry:
            tags.add(frame)
        kw = {"v2_version": 3}
    else:
        tags.update_to_v24()
        kw = {"v2_version": 4}
    if hasattr(mf, "save"):
        try:
            mf.save(v1=2 if opts.keep_id3v1 else 0, **kw)
            return
        except TypeError:
            mf.save(**kw)


def remove_tags(path: os.PathLike[str] | str) -> None:
    """Strip every tag from a file. Not undoable, like the original's button."""
    p = Path(path)
    if family_for(p) == "matroska":
        try:
            _mkv.remove_all(p)
        except _mkv.MatroskaError as exc:
            raise TagError(f"{p.name}: {exc}") from exc
        return
    try:
        mf = mutagen.File(p)
        if mf is None:
            raise TagError("unrecognised audio file")
        if isinstance(mf, FLAC):
            mf.clear_pictures()
        mf.delete()
        mf.save() if isinstance(mf, FLAC) else None
    except TagError:
        raise
    except Exception as exc:
        raise TagError(f"{p.name}: {type(exc).__name__}: {exc}") from exc
