"""Online album lookup — the globe button.

The original searches freedb, Amazon and Discogs. **freedb shut down in 2020**
and Amazon's product API is long gone, so this ports the *feature*, not the
sources:

* **MusicBrainz** — the real successor to freedb. No account, no key, works out
  of the box. Cover art comes from the Cover Art Archive, which is indexed by
  MusicBrainz release id.
* **Discogs** — still has an API and still has the pressing-level detail
  (labels, catalogue numbers) the original used it for. Needs a free personal
  access token from https://www.discogs.com/settings/developers, pasted into
  Options → Web import.

Networking is stdlib ``urllib`` on purpose: no dependency to install, and the
whole module stays importable on a machine with no internet.

Parsing is kept separate from fetching (``_parse_*`` functions take decoded
JSON) so the response shapes are unit-tested from recorded fixtures rather
than by hitting the network in the test suite.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

__version__ = "0.1"
USER_AGENT = f"RiffTag/{__version__} (https://github.com/; personal use)"
TIMEOUT = 20

#: MusicBrainz answers a rate-limit breach with **503**, not 429 — with a
#: ``Retry-After`` header. Taking that 503 at face value makes an ordinary
#: throttle look like a server outage, which is exactly how it first showed up
#: ("Server returned 503 Service Temporarily Unavailable" on a plain search).
RATE_LIMIT_CODES = (429, 503)
MAX_RETRIES = 3
MAX_BACKOFF = 8.0


class WebImportError(RuntimeError):
    """A lookup could not be completed. The message is shown to the user."""


@dataclass
class TrackInfo:
    number: int
    title: str
    artist: str = ""
    length: int = 0            # seconds; 0 when the source does not say

    @property
    def time(self) -> str:
        if not self.length:
            return ""
        m, s = divmod(int(self.length), 60)
        return f"{m}:{s:02d}"


@dataclass
class Album:
    provider: str
    id: str
    artist: str = ""
    album: str = ""
    year: str = ""
    label: str = ""
    catalog: str = ""
    genre: str = ""
    country: str = ""
    format: str = ""
    track_count: int = 0
    url: str = ""
    tracks: list[TrackInfo] = field(default_factory=list)

    @property
    def total_time(self) -> str:
        total = sum(t.length for t in self.tracks)
        if not total:
            return ""
        h, rem = divmod(int(total), 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def values_for(self, track: TrackInfo, *, use_album_artist: bool = False,
                   total: int | None = None) -> dict[str, str]:
        """The tag dict to write for one matched file."""
        values = {
            "artist": track.artist or self.artist,
            "album": self.album,
            "title": track.title,
            "track": str(track.number),
            "track_total": str(total if total is not None else
                               (self.track_count or len(self.tracks))),
        }
        if self.year:
            values["year"] = self.year
        if self.genre:
            values["genre"] = self.genre
        if self.label:
            values["label"] = self.label
        if self.catalog:
            values["catalog"] = self.catalog
        if use_album_artist:
            values["album_artist"] = self.artist
        return values


# --- helpers ------------------------------------------------------------

def _retry_after(exc, attempt: int) -> float:
    """How long to wait before retrying, honouring the server if it says."""
    header = exc.headers.get("Retry-After") if exc.headers else None
    try:
        if header is not None:
            return min(float(header), MAX_BACKOFF)
    except (TypeError, ValueError):
        pass
    return min(0.5 * (2 ** attempt), MAX_BACKOFF)


DISCOGS_AUTH_HINT = ("Not authorised — check the Discogs token in "
                     "Options → Discogs access token.")


def _get(url: str, headers: dict[str, str] | None = None, *,
         retries: int = MAX_RETRIES,
         auth_hint: str = DISCOGS_AUTH_HINT) -> bytes:
    """Fetch a URL, riding out rate limits rather than surfacing them."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in RATE_LIMIT_CODES and attempt < retries:
                time.sleep(_retry_after(exc, attempt))
                continue
            if exc.code == 401:
                # Which key to check depends on the caller; TMDB passes its own.
                raise WebImportError(auth_hint) from exc
            if exc.code in RATE_LIMIT_CODES:
                raise WebImportError(
                    "The server is rate limiting us and did not let up after "
                    f"{retries} retries. Wait half a minute and try again.\n"
                    "(MusicBrainz reports rate limits as 503, so this is "
                    "usually throttling rather than an outage.)") from exc
            if exc.code == 404:
                raise WebImportError("Not found.") from exc
            raise WebImportError(
                f"Server returned {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise WebImportError(
                f"Could not reach the server: {exc.reason}") from exc
        except TimeoutError as exc:
            raise WebImportError("The server took too long to answer.") from exc
    raise WebImportError("The request could not be completed.")


def _get_json(url: str, headers: dict[str, str] | None = None, *,
              auth_hint: str = DISCOGS_AUTH_HINT):
    try:
        return json.loads(_get(url, headers, auth_hint=auth_hint).decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise WebImportError("The server sent something that was not JSON.") from exc


def _year(text: str) -> str:
    """First four consecutive digits of a date, or "". Dates vary wildly."""
    digits = ""
    for ch in str(text or ""):
        if ch.isdigit():
            digits += ch
            if len(digits) == 4:
                return digits
        else:
            digits = ""
    return ""


# --- MusicBrainz --------------------------------------------------------

MB_ROOT = "https://musicbrainz.org/ws/2"
CAA_ROOT = "https://coverartarchive.org"
#: MusicBrainz asks for no more than one request a second from a client.
MB_MIN_INTERVAL = 1.0
#: Shared, not per-instance: a new MusicBrainz() is built every time the
#: dialog opens, so a per-instance clock resets to zero and the very first
#: request of the new dialog can land inside the previous one's window.
_mb_clock = {"last": 0.0}
_mb_lock = threading.Lock()


def _parse_mb_search(data: dict) -> list[Album]:
    out = []
    for rel in data.get("releases", []):
        credit = rel.get("artist-credit") or []
        artist = "".join(c.get("name", "") + c.get("joinphrase", "")
                         for c in credit) or rel.get("artist", "")
        labels = rel.get("label-info") or []
        label = catalog = ""
        for info in labels:
            label = label or (info.get("label") or {}).get("name", "")
            catalog = catalog or info.get("catalog-number", "") or ""
        media = rel.get("media") or []
        count = sum(m.get("track-count", 0) for m in media) or rel.get("count", 0)
        out.append(Album(
            provider="MusicBrainz",
            id=rel.get("id", ""),
            artist=artist,
            album=rel.get("title", ""),
            year=_year(rel.get("date", "")),
            label=label,
            catalog=catalog,
            country=rel.get("country", "") or "",
            format=(media[0].get("format", "") if media else "") or "",
            track_count=count,
            url=f"https://musicbrainz.org/release/{rel.get('id', '')}",
        ))
    return out


def _parse_mb_release(data: dict, album: Album) -> Album:
    tracks: list[TrackInfo] = []
    number = 0
    for medium in data.get("media", []):
        for entry in medium.get("tracks", []):
            number += 1
            credit = entry.get("artist-credit") or []
            artist = "".join(c.get("name", "") + c.get("joinphrase", "")
                             for c in credit)
            length = entry.get("length") or (entry.get("recording") or {}).get("length")
            tracks.append(TrackInfo(
                number=int(entry.get("position") or number),
                title=entry.get("title", ""),
                artist=artist,
                length=int(length) // 1000 if length else 0,
            ))
    album.tracks = tracks
    album.track_count = album.track_count or len(tracks)
    return album


class MusicBrainz:
    """freedb's successor. No key needed."""

    name = "MusicBrainz"
    needs_token = False

    def _throttle(self) -> None:
        with _mb_lock:
            wait = MB_MIN_INTERVAL - (time.monotonic() - _mb_clock["last"])
            if wait > 0:
                time.sleep(wait)
            _mb_clock["last"] = time.monotonic()

    def search(self, artist: str = "", album: str = "", year: str = "",
               limit: int = 40) -> list[Album]:
        clauses = []
        if artist:
            clauses.append(f'artist:"{artist}"')
        if album:
            clauses.append(f'release:"{album}"')
        if year:
            clauses.append(f"date:{year}*")
        if not clauses:
            raise WebImportError("Enter an artist or an album to search for.")
        query = urllib.parse.quote(" AND ".join(clauses))
        url = (f"{MB_ROOT}/release?query={query}&limit={limit}&fmt=json")
        self._throttle()
        return _parse_mb_search(_get_json(url))

    def fetch(self, album: Album) -> Album:
        url = (f"{MB_ROOT}/release/{album.id}"
               "?inc=recordings+artist-credits+labels&fmt=json")
        self._throttle()
        return _parse_mb_release(_get_json(url), album)

    def cover(self, album: Album) -> bytes | None:
        """Front cover from the Cover Art Archive. Many releases have none."""
        try:
            return _get(f"{CAA_ROOT}/release/{album.id}/front")
        except WebImportError:
            return None


# --- Discogs ------------------------------------------------------------

DISCOGS_ROOT = "https://api.discogs.com"


def _parse_discogs_search(data: dict) -> list[Album]:
    out = []
    for item in data.get("results", []):
        title = item.get("title", "")
        # Discogs packs "Artist - Album" into one string on search results.
        artist, _, name = title.partition(" - ")
        if not name:
            artist, name = "", title
        labels = item.get("label") or []
        formats = item.get("format") or []
        out.append(Album(
            provider="Discogs",
            id=str(item.get("id", "")),
            artist=artist.strip(),
            album=name.strip(),
            year=_year(item.get("year", "")),
            label=(labels[0] if labels else ""),
            catalog=item.get("catno", "") or "",
            genre=", ".join(item.get("genre") or []),
            country=item.get("country", "") or "",
            format=", ".join(formats),
            url="https://www.discogs.com" + item.get("uri", ""),
        ))
    return out


def _dur_to_seconds(text: str) -> int:
    parts = [p for p in str(text or "").split(":") if p.strip().isdigit()]
    if not parts:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _parse_discogs_release(data: dict, album: Album) -> Album:
    tracks = []
    number = 0
    for entry in data.get("tracklist", []):
        if entry.get("type_") not in (None, "track"):
            continue              # headings and index entries are not tracks
        number += 1
        credits = entry.get("artists") or []
        artist = ", ".join(c.get("name", "") for c in credits)
        position = entry.get("position", "")
        tracks.append(TrackInfo(
            number=int(position) if str(position).isdigit() else number,
            title=entry.get("title", ""),
            artist=artist,
            length=_dur_to_seconds(entry.get("duration", "")),
        ))
    album.tracks = tracks
    album.track_count = album.track_count or len(tracks)
    labels = data.get("labels") or []
    if labels:
        album.label = album.label or labels[0].get("name", "")
        album.catalog = album.catalog or labels[0].get("catno", "") or ""
    album.genre = album.genre or ", ".join(data.get("styles")
                                           or data.get("genres") or [])
    album.year = album.year or _year(data.get("year", ""))
    return album


class Discogs:
    """Needs a free personal access token; without one, search returns 401."""

    name = "Discogs"
    needs_token = True

    def __init__(self, token: str = ""):
        self.token = token.strip()

    @property
    def available(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise WebImportError(
                "Discogs needs a free personal access token.\n"
                "Get one at discogs.com/settings/developers, then paste it "
                "into Options → Web import.")
        return {"Authorization": f"Discogs token={self.token}"}

    def search(self, artist: str = "", album: str = "", year: str = "",
               limit: int = 40, masters_only: bool = False) -> list[Album]:
        params = {"type": "master" if masters_only else "release",
                  "per_page": str(limit)}
        if artist:
            params["artist"] = artist
        if album:
            params["release_title"] = album
        if year:
            params["year"] = year
        if len(params) == 2:
            raise WebImportError("Enter an artist or an album to search for.")
        url = f"{DISCOGS_ROOT}/database/search?{urllib.parse.urlencode(params)}"
        return _parse_discogs_search(_get_json(url, self._headers()))

    def fetch(self, album: Album) -> Album:
        # A master id has to be resolved to its main release before there is a
        # tracklist with durations to read.
        url = f"{DISCOGS_ROOT}/releases/{album.id}"
        data = _get_json(url, self._headers())
        return _parse_discogs_release(data, album)

    def cover(self, album: Album) -> bytes | None:
        try:
            data = _get_json(f"{DISCOGS_ROOT}/releases/{album.id}",
                             self._headers())
        except WebImportError:
            return None
        for image in data.get("images") or []:
            uri = image.get("uri")
            if uri:
                try:
                    return _get(uri, self._headers())
                except WebImportError:
                    return None
        return None


def providers(discogs_token: str = "") -> list:
    return [MusicBrainz(), Discogs(discogs_token)]


# --- matching -----------------------------------------------------------

def align(file_count: int, tracks: list[TrackInfo]) -> list[TrackInfo | None]:
    """Line fetched tracks up against the files, in list order.

    Deliberately positional, like the original: the files are matched in the
    order they appear, and you fix a mismatch with Move Up / Move Down rather
    than trusting a fuzzy title match to be right.
    """
    out: list[TrackInfo | None] = []
    for i in range(file_count):
        out.append(tracks[i] if i < len(tracks) else None)
    return out
