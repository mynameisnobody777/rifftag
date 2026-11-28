"""Movie and TV lookup from TMDB (themoviedb.org).

Why TMDB and not the others (decided 2026-11-28):

* **IMDb** has no public API; scraping it breaks its terms, and its bulk
  datasets are licensed for non-commercial use only. Not an option.
* **TheTVDB** is TV-only and its v4 API ties keys to a subscription model.
* **TMDB** covers movies *and* TV (show → season → episode), has posters, and
  gives a free personal API key. It is also what Plex, Jellyfin and Kodi use.

Each user brings their own key (Options → TMDB API key), exactly like the
Discogs token: a key baked into a GPL program would be public the day it
shipped. TMDB's settings page shows two credentials and either works here —
the short **API Key** (v3, sent as ``?api_key=``) or the long **API Read
Access Token** (v4, a JWT sent as ``Authorization: Bearer``). They are told
apart by shape: the token is a JWT and starts ``eyJ``.

Same structure as :mod:`rifftag.webimport`: stdlib networking, and parsing
split from fetching so the response shapes are tested from recorded JSON with
no network in the suite.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from .webimport import WebImportError, _get, _get_json, _year

API_ROOT = "https://api.themoviedb.org/3"
IMAGE_ROOT = "https://image.tmdb.org/t/p"
#: w780 is plenty for embedded art (Plex and players downscale anyway) and
#: keeps a 26-file season from carrying 26 multi-megabyte originals.
POSTER_SIZE = "w780"

KEY_HINT = ("TMDB needs a free API key.\n"
            "Make an account at themoviedb.org, then Settings → API, and "
            "paste the key into Options → TMDB API key.")
AUTH_HINT = ("TMDB did not accept the key — check it in Options → TMDB API "
             "key. Either the short API Key or the long Read Access Token "
             "works.")


@dataclass
class Episode:
    season: int
    number: int
    title: str = ""
    overview: str = ""
    air_date: str = ""


@dataclass
class Video:
    """One search hit, filled in further by ``details``."""

    kind: str                       # "movie" or "tv"
    id: int
    title: str                      # movie title, or show name
    year: str = ""
    overview: str = ""
    poster_path: str = ""
    genres: list[str] = field(default_factory=list)
    director: str = ""
    #: TV only: season number -> episodes, filled by ``season``
    seasons: dict[int, list[Episode]] = field(default_factory=dict)
    #: TV only: season number -> that season's poster, preferred over the show's
    season_posters: dict[int, str] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"https://www.themoviedb.org/{self.kind}/{self.id}"

    def episode(self, season: int, number: int) -> Episode | None:
        for ep in self.seasons.get(season, []):
            if ep.number == number:
                return ep
        return None

    def poster_for(self, season: int | None = None) -> str:
        if season is not None and self.season_posters.get(season):
            return self.season_posters[season]
        return self.poster_path


# --- parsing (pure; tested from recorded JSON) ----------------------------

def _parse_movie_search(data: dict) -> list[Video]:
    return [Video(kind="movie", id=int(r.get("id", 0)),
                  title=r.get("title") or r.get("original_title") or "",
                  year=_year(r.get("release_date", "")),
                  overview=r.get("overview") or "",
                  poster_path=r.get("poster_path") or "")
            for r in data.get("results", []) if r.get("id")]


def _parse_tv_search(data: dict) -> list[Video]:
    return [Video(kind="tv", id=int(r.get("id", 0)),
                  title=r.get("name") or r.get("original_name") or "",
                  year=_year(r.get("first_air_date", "")),
                  overview=r.get("overview") or "",
                  poster_path=r.get("poster_path") or "")
            for r in data.get("results", []) if r.get("id")]


def _parse_movie_details(data: dict, video: Video) -> Video:
    video.title = data.get("title") or video.title
    video.year = _year(data.get("release_date", "")) or video.year
    video.overview = data.get("overview") or video.overview
    video.poster_path = data.get("poster_path") or video.poster_path
    video.genres = [g.get("name", "") for g in data.get("genres") or []
                    if g.get("name")]
    crew = (data.get("credits") or {}).get("crew") or []
    video.director = ", ".join(c.get("name", "") for c in crew
                               if c.get("job") == "Director" and c.get("name"))
    return video


def _parse_tv_details(data: dict, video: Video) -> Video:
    video.title = data.get("name") or video.title
    video.year = _year(data.get("first_air_date", "")) or video.year
    video.overview = data.get("overview") or video.overview
    video.poster_path = data.get("poster_path") or video.poster_path
    video.genres = [g.get("name", "") for g in data.get("genres") or []
                    if g.get("name")]
    for s in data.get("seasons") or []:
        if s.get("poster_path") and s.get("season_number") is not None:
            video.season_posters[int(s["season_number"])] = s["poster_path"]
    return video


def _parse_season(data: dict, video: Video, season: int) -> Video:
    video.seasons[season] = [
        Episode(season=int(e.get("season_number", season)),
                number=int(e.get("episode_number", 0)),
                title=e.get("name") or "",
                overview=e.get("overview") or "",
                air_date=e.get("air_date") or "")
        for e in data.get("episodes") or [] if e.get("episode_number")]
    if data.get("poster_path"):
        video.season_posters[season] = data["poster_path"]
    return video


# --- what gets written ------------------------------------------------------

def movie_values(video: Video) -> dict[str, str]:
    values = {"title": video.title}
    if video.year:
        values["year"] = video.year
    if video.genres:
        values["genre"] = ", ".join(video.genres)
    if video.overview:
        values["comment"] = video.overview
    return values


def episode_values(show: Video, ep: Episode) -> dict[str, str]:
    """Tags for one episode file.

    ``title`` is the episode's own title and ``show_title`` the series, which
    is how MP4 (tvsh/tvsn/tves), Matroska (SHOW/SEASON/EPISODE) and Plex's
    local-metadata fallback all expect it. ``episode_id`` gets the familiar
    ``S01E02`` form.
    """
    values = {
        "show_title": show.title,
        "season_number": str(ep.season),
        "episode_number": str(ep.number),
        "episode_id": f"S{ep.season:02d}E{ep.number:02d}",
    }
    if ep.title:
        values["title"] = ep.title
    year = _year(ep.air_date) or show.year
    if year:
        values["year"] = year
    if show.genres:
        values["genre"] = ", ".join(show.genres)
    if ep.overview:
        values["comment"] = ep.overview
    return values


# --- guessing from file names ----------------------------------------------

#: Release-group noise that ends the useful part of a file name.
_JUNK = re.compile(
    r"\b(2160p|1080p|720p|480p|4k|uhd|hdr|bluray|blu-ray|bdrip|brrip|web-?dl|"
    r"webrip|hdtv|dvdrip|x264|x265|h\.?264|h\.?265|hevc|aac|ac3|dts|remux|"
    r"proper|repack|extended|unrated)\b", re.I)
_SXE = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})")
_NXN = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")
_YEAR = re.compile(r"[\s._(\[]((?:19|20)\d{2})(?:[)\]\s._]|$)")


def _clean(text: str) -> str:
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -–—[](){}")


@dataclass
class Guess:
    kind: str                 # "movie" or "tv"
    title: str                # movie title or show name
    year: str = ""
    season: int | None = None
    episode: int | None = None


def guess(path: Path | str) -> Guess:
    """What a file name says it is.

    Handles the usual shapes: ``Title (Year)``, ``Title.Year.1080p...``,
    ``Show - S01E02 - Name``, ``show.s01e02``, ``Show 1x02``. Anything else
    becomes a movie titled by the cleaned-up name, which the user can edit.
    """
    stem = Path(path).stem
    for pattern in (_SXE, _NXN):
        m = pattern.search(stem)
        if m:
            show = stem[:m.start()]
            year_m = _YEAR.search(" " + show + " ")
            year = year_m.group(1) if year_m else ""
            if year_m:
                show = (" " + show + " ")[:year_m.start()]
            return Guess("tv", _clean(show), year,
                         int(m.group(1)), int(m.group(2)))
    junk = _JUNK.search(stem)
    head = stem[:junk.start()] if junk else stem
    year_m = None
    for year_m in _YEAR.finditer(head):
        pass                  # the LAST year: "2001 A Space Odyssey (1968)"
    if year_m and _clean(head[:year_m.start()]):
        return Guess("movie", _clean(head[:year_m.start()]), year_m.group(1))
    return Guess("movie", _clean(head))


# --- the client -------------------------------------------------------------

class TMDB:
    name = "TMDB"

    def __init__(self, key: str = ""):
        self.key = (key or "").strip()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def _url_and_headers(self, path: str, params: dict[str, str] | None = None
                         ) -> tuple[str, dict[str, str]]:
        if not self.key:
            raise WebImportError(KEY_HINT)
        params = dict(params or {})
        headers = {"Accept": "application/json"}
        if self.key.startswith("eyJ"):          # v4 read access token (JWT)
            headers["Authorization"] = f"Bearer {self.key}"
        else:                                   # v3 API key
            params["api_key"] = self.key
        query = urllib.parse.urlencode(params)
        return f"{API_ROOT}{path}" + (f"?{query}" if query else ""), headers

    def _json(self, path: str, params: dict[str, str] | None = None):
        url, headers = self._url_and_headers(path, params)
        return _get_json(url, headers, auth_hint=AUTH_HINT)

    def search(self, kind: str, title: str, year: str = "") -> list[Video]:
        title = title.strip()
        if not title:
            raise WebImportError("Enter a title to search for.")
        params = {"query": title, "include_adult": "false"}
        year = year.strip()
        if kind == "tv":
            if year:
                params["first_air_date_year"] = year
            return _parse_tv_search(self._json("/search/tv", params))
        if year:
            params["year"] = year
        return _parse_movie_search(self._json("/search/movie", params))

    def details(self, video: Video) -> Video:
        if video.kind == "tv":
            return _parse_tv_details(self._json(f"/tv/{video.id}"), video)
        return _parse_movie_details(
            self._json(f"/movie/{video.id}",
                       {"append_to_response": "credits"}), video)

    def season(self, video: Video, season: int) -> Video:
        if season in video.seasons:
            return video
        try:
            data = self._json(f"/tv/{video.id}/season/{season}")
        except WebImportError as exc:
            if str(exc) == "Not found.":
                video.seasons[season] = []   # no such season: nothing matches
                return video
            raise
        return _parse_season(data, video, season)

    def seasons(self, video: Video, wanted: list[int]) -> Video:
        """Details plus every season the files need, in one worker call."""
        self.details(video)
        for s in sorted(set(wanted)):
            self.season(video, s)
        return video

    @staticmethod
    def poster(path: str) -> bytes | None:
        """Image bytes. The image CDN needs no key."""
        if not path:
            return None
        try:
            return _get(f"{IMAGE_ROOT}/{POSTER_SIZE}{path}")
        except WebImportError:
            return None
