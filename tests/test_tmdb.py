"""TMDB movie / TV lookup.

The JSON below is cut down from TMDB's documented v3 response shapes
(developer.themoviedb.org) — only the fields RiffTag reads. Nothing here
touches the network: the client's fetch goes through ``_get_json``, which the
tests replace.
"""

import io
import os
import urllib.error

import pytest

from rifftag import tagio, tmdb
from rifftag.tmdb import TMDB, Video, guess
from rifftag.webimport import WebImportError

MOVIE_SEARCH = {"results": [
    {"id": 603, "title": "The Matrix", "release_date": "1999-03-30",
     "overview": "Set in the 22nd century...", "poster_path": "/m.jpg"},
    {"id": 0, "title": "broken row without an id"},
]}
MOVIE_DETAILS = {
    "id": 603, "title": "The Matrix", "release_date": "1999-03-30",
    "overview": "Set in the 22nd century, The Matrix tells the story...",
    "poster_path": "/m2.jpg",
    "genres": [{"id": 28, "name": "Action"},
               {"id": 878, "name": "Science Fiction"}],
    "credits": {"crew": [
        {"job": "Director", "name": "Lana Wachowski"},
        {"job": "Director", "name": "Lilly Wachowski"},
        {"job": "Producer", "name": "Joel Silver"}]},
}
TV_SEARCH = {"results": [
    {"id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20",
     "overview": "Walter White...", "poster_path": "/bb.jpg"}]}
TV_DETAILS = {
    "id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20",
    "genres": [{"name": "Drama"}, {"name": "Crime"}],
    "poster_path": "/bb.jpg",
    "seasons": [{"season_number": 0, "poster_path": None},
                {"season_number": 1, "poster_path": "/s1.jpg"}],
}
SEASON_1 = {"poster_path": "/s1b.jpg", "episodes": [
    {"season_number": 1, "episode_number": 1, "name": "Pilot",
     "overview": "A chemistry teacher...", "air_date": "2008-01-20"},
    {"season_number": 1, "episode_number": 2, "name": "Cat's in the Bag...",
     "overview": "Walt and Jesse...", "air_date": "2008-01-27"},
]}


@pytest.fixture
def api(monkeypatch):
    """Route the client's requests to canned answers; record what was asked."""
    calls = []
    answers = {
        "/search/movie": MOVIE_SEARCH, "/movie/603": MOVIE_DETAILS,
        "/search/tv": TV_SEARCH, "/tv/1396": TV_DETAILS,
        "/tv/1396/season/1": SEASON_1,
    }

    def fake(url, headers=None, *, auth_hint=""):
        calls.append((url, headers or {}))
        path = url.split(tmdb.API_ROOT, 1)[1].split("?", 1)[0]
        if path not in answers:
            raise WebImportError("Not found.")
        return answers[path]

    monkeypatch.setattr(tmdb, "_get_json", fake)
    return calls


# --- parsing -------------------------------------------------------------

def test_movie_search_and_details(api):
    client = TMDB("abc123")
    hits = client.search("movie", "The Matrix", "1999")
    assert [(v.id, v.title, v.year) for v in hits] == [(603, "The Matrix", "1999")]
    movie = client.details(hits[0])
    assert movie.genres == ["Action", "Science Fiction"]
    assert movie.director == "Lana Wachowski, Lilly Wachowski"
    assert movie.poster_path == "/m2.jpg"
    assert movie.url == "https://www.themoviedb.org/movie/603"


def test_tv_seasons_and_episode_lookup(api):
    client = TMDB("abc123")
    show = client.search("tv", "Breaking Bad")[0]
    client.seasons(show, [1, 1, 9])         # 9 does not exist: no crash
    assert show.episode(1, 2).title == "Cat's in the Bag..."
    assert show.episode(9, 1) is None
    assert show.seasons[9] == []
    # The season's own poster beats the show's.
    assert show.poster_for(1) == "/s1b.jpg"
    assert show.poster_for(None) == "/bb.jpg"


def test_movie_values():
    v = Video("movie", 1, "The Matrix", "1999", "Neo...", genres=["Action", "SF"])
    assert tmdb.movie_values(v) == {"title": "The Matrix", "year": "1999",
                                    "genre": "Action, SF", "comment": "Neo..."}


def test_episode_values_use_the_air_date_year_and_sxxexx():
    show = Video("tv", 1, "Breaking Bad", "2008", genres=["Drama"])
    ep = tmdb.Episode(2, 13, "ABQ", "Season finale", "2009-05-31")
    assert tmdb.episode_values(show, ep) == {
        "show_title": "Breaking Bad", "season_number": "2",
        "episode_number": "13", "episode_id": "S02E13", "title": "ABQ",
        "year": "2009", "genre": "Drama", "comment": "Season finale"}


# --- authentication --------------------------------------------------------

def test_a_v3_key_goes_in_the_query_and_a_v4_token_in_the_header():
    url, headers = TMDB("abc123")._url_and_headers("/search/movie", {"query": "x"})
    assert "api_key=abc123" in url and "Authorization" not in headers
    url, headers = TMDB("eyJhbGciOi.token")._url_and_headers("/movie/1")
    assert "api_key" not in url
    assert headers["Authorization"] == "Bearer eyJhbGciOi.token"


def test_no_key_says_how_to_get_one():
    with pytest.raises(WebImportError, match="Options → TMDB API key"):
        TMDB("").search("movie", "anything")


def test_a_rejected_key_names_tmdb_not_discogs(monkeypatch):
    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized",
                                     {}, io.BytesIO(b""))
    monkeypatch.setattr(tmdb._get.__globals__["urllib"].request, "urlopen", refuse)
    with pytest.raises(WebImportError) as exc:
        TMDB("wrong").search("movie", "x")
    assert "TMDB" in str(exc.value) and "Discogs" not in str(exc.value)


# --- guessing from file names ------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Adventures in Babysitting (1987).mkv",
     ("movie", "Adventures in Babysitting", "1987", None, None)),
    ("The.Matrix.1999.1080p.BluRay.x264.mkv",
     ("movie", "The Matrix", "1999", None, None)),
    ("2001 A Space Odyssey (1968).mkv",
     ("movie", "2001 A Space Odyssey", "1968", None, None)),
    ("1917 (2019).mkv", ("movie", "1917", "2019", None, None)),
    ("Home Movie Christmas.mkv", ("movie", "Home Movie Christmas", "", None, None)),
    ("Breaking Bad - S01E02 - Cat's in the Bag.mkv",
     ("tv", "Breaking Bad", "", 1, 2)),
    ("the.office.us.s03e10.720p.mkv", ("tv", "the office us", "", 3, 10)),
    ("Firefly 1x03.mp4", ("tv", "Firefly", "", 1, 3)),
    ("Doctor Who (2005) - S01E01.mkv", ("tv", "Doctor Who", "2005", 1, 1)),
])
def test_guess(name, expected):
    g = guess(name)
    assert (g.kind, g.title, g.year, g.season, g.episode) == expected


# --- the dialog --------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class FakeClient:
    """Answers immediately; no network, no poster."""
    available = True

    def poster(self, path):
        return None


def _wait(qapp, done, seconds=20):
    """Writes now run on a worker thread; pump events until they land."""
    import time
    end = time.monotonic() + seconds
    while not done() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert done(), "timed out waiting for the worker"


def _dialog(tracks):
    from rifftag.gui.videolookup import VideoLookupDialog
    return VideoLookupDialog(tracks, client=FakeClient())


def test_movie_files_are_walked_one_at_a_time(qapp, sample, tmp_path):
    a = sample("m4a", "a").rename(tmp_path / "The Matrix (1999).mp4")
    b = sample("m4a", "b").rename(tmp_path / "Heat (1995).mp4")
    dlg = _dialog([tagio.read(a), tagio.read(b)])
    assert dlg.kind == "movie"
    assert (dlg.title_edit.text(), dlg.year_edit.text()) == ("The Matrix", "1999")

    dlg._search = lambda: None              # no network on "next file"
    dlg._show_movie(Video("movie", 603, "The Matrix", "1999", "Neo...",
                          genres=["Action"]))
    dlg.movie_checks["genre"].setChecked(False)
    assert dlg.planned_movie() == {"title": "The Matrix", "year": "1999",
                                   "comment": "Neo..."}
    dlg._write_movie()
    _wait(qapp, lambda: dlg.index == 1)
    got = tagio.read(a).values
    assert (got["title"], got["year"], got["comment"]) == ("The Matrix", "1999", "Neo...")
    assert "genre" not in got or not got["genre"]
    # On to the second file, prefilled from its name.
    assert dlg.index == 1 and dlg.pages.currentIndex() == 0
    assert (dlg.title_edit.text(), dlg.year_edit.text()) == ("Heat", "1995")
    dlg.close()


@pytest.mark.skipif(not __import__("rifftag.matroska").matroska.available(),
                    reason="MKVToolNix is not installed")
def test_tv_files_match_episodes_by_their_names(qapp, sample, tmp_path):
    e1 = sample("mkv", "e1").rename(tmp_path / "Breaking Bad - S01E01.mkv")
    e2 = sample("mkv", "e2").rename(tmp_path / "Breaking Bad - S01E02.mkv")
    odd = sample("mkv", "odd").rename(tmp_path / "bonus.mkv")
    dlg = _dialog([tagio.read(p) for p in (e1, e2, odd)])
    assert dlg.kind == "tv" and dlg.title_edit.text() == "Breaking Bad"

    show = Video("tv", 1396, "Breaking Bad", "2008", genres=["Drama"])
    tmdb._parse_season(SEASON_1, show, 1)
    dlg.numbers = [[1, 1], [1, 2], [None, None]]
    dlg._show_tv(show)
    plans = dlg.planned_tv()
    assert [t.path.name for t, _ in plans] == [e1.name, e2.name]   # bonus skipped
    assert plans[1][1]["title"] == "Cat's in the Bag..."

    # Typing numbers into the table matches the stray file too.
    dlg.pairs.item(2, 1).setText("1")
    dlg.pairs.item(2, 2).setText("1")
    assert len(dlg.planned_tv()) == 3

    dlg._write_tv()
    _wait(qapp, lambda: dlg.wrote == 3)
    got = tagio.read(e2).values
    assert got["show_title"] == "Breaking Bad"
    assert str(got["season_number"]) == "1" and str(got["episode_number"]) == "2"
    assert got["title"] == "Cat's in the Bag..."


def test_the_file_name_beats_a_junk_embedded_title(qapp, sample, tmp_path):
    p = sample("m4a", "x").rename(tmp_path / "Heat (1995).mp4")
    tagio.write(p, {"title": "Hello", "year": "2020"})
    dlg = _dialog([tagio.read(p)])
    assert (dlg.title_edit.text(), dlg.year_edit.text()) == ("Heat", "1995")
    dlg.close()


def test_about_carries_the_tmdb_notice(qapp):
    """TMDB's API terms require this attribution wherever the API is used."""
    from PyQt6.QtWidgets import QTextBrowser
    from rifftag.gui.about import AboutDialog
    dlg = AboutDialog()
    text = dlg.findChild(QTextBrowser).toPlainText()
    assert "uses the TMDB API but is not endorsed or certified by TMDB" in text
    dlg.close()


def test_the_camera_icon_draws(qapp):
    from PyQt6.QtGui import QColor
    from rifftag.gui.videolookup import camera_icon
    icon = camera_icon(QColor("#202020"))
    assert not icon.isNull()
    image = icon.pixmap(24, 24).toImage()
    opaque = sum(1 for x in range(24) for y in range(24)
                 if image.pixelColor(x, y).alpha() > 0)
    assert 120 < opaque < 400, opaque       # a shape, not empty, not a block
