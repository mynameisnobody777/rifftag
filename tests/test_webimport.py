"""Web import parsing.

Fixtures are trimmed copies of real MusicBrainz and Discogs responses. Parsing
is tested offline on purpose — the suite must pass on a machine with no
internet, and a live API would make these tests flaky rather than useful.
"""

import json

import pytest

from rifftag import webimport
from rifftag.webimport import (Album, Discogs, MusicBrainz, TrackInfo,
                                WebImportError, align, _parse_discogs_release,
                                _parse_discogs_search, _parse_mb_release,
                                _parse_mb_search, _year)

MB_SEARCH = {
    "releases": [{
        "id": "b52a8f31-b5ab-34e9-92f4-f5b7110220f0",
        "title": "Nevermind",
        "date": "1991-09-24",
        "country": "US",
        "artist-credit": [{"name": "Nirvana", "joinphrase": ""}],
        "label-info": [{"catalog-number": "DGCD-24425",
                        "label": {"name": "DGC"}}],
        "media": [{"format": "CD", "track-count": 12}],
    }],
}

MB_RELEASE = {
    "title": "Nevermind",
    "media": [{"tracks": [
        {"position": "1", "title": "Smells Like Teen Spirit", "length": 301000,
         "artist-credit": [{"name": "Nirvana", "joinphrase": ""}]},
        {"position": "2", "title": "In Bloom", "length": 254000,
         "artist-credit": [{"name": "Nirvana", "joinphrase": ""}]},
    ]}],
}

DISCOGS_SEARCH = {
    "results": [{
        "id": 367891,
        "title": "Nirvana - Nevermind",
        "year": "1991",
        "label": ["DGC", "Sub Pop"],
        "catno": "DGCD-24425",
        "genre": ["Rock"],
        "country": "US",
        "format": ["CD", "Album"],
        "uri": "/release/367891-Nirvana-Nevermind",
    }],
}

DISCOGS_RELEASE = {
    "year": 1991,
    "labels": [{"name": "DGC", "catno": "DGCD-24425"}],
    "styles": ["Grunge", "Alternative Rock"],
    "tracklist": [
        {"position": "", "title": "Side A", "type_": "heading"},
        {"position": "1", "title": "Smells Like Teen Spirit",
         "duration": "5:01", "type_": "track"},
        {"position": "2", "title": "In Bloom", "duration": "4:14",
         "type_": "track"},
    ],
}


# --- MusicBrainz --------------------------------------------------------

def test_mb_search_pulls_out_the_fields_the_list_shows():
    album = _parse_mb_search(MB_SEARCH)[0]
    assert album.provider == "MusicBrainz"
    assert album.artist == "Nirvana"
    assert album.album == "Nevermind"
    assert album.year == "1991"
    assert album.label == "DGC"
    assert album.catalog == "DGCD-24425"
    assert album.track_count == 12
    assert album.url.endswith(album.id)


def test_mb_joins_a_multi_artist_credit_with_its_join_phrases():
    data = {"releases": [{"id": "x", "title": "t", "artist-credit": [
        {"name": "Queen", "joinphrase": " & "},
        {"name": "David Bowie", "joinphrase": ""}]}]}
    assert _parse_mb_search(data)[0].artist == "Queen & David Bowie"


def test_mb_release_numbers_tracks_and_converts_length_to_seconds():
    album = _parse_mb_release(MB_RELEASE, Album("MusicBrainz", "x"))
    assert [t.number for t in album.tracks] == [1, 2]
    assert album.tracks[0].title == "Smells Like Teen Spirit"
    assert album.tracks[0].length == 301        # milliseconds in the API
    assert album.tracks[0].time == "5:01"


def test_mb_release_numbers_continuously_across_discs():
    data = {"media": [
        {"tracks": [{"position": "1", "title": "a"}]},
        {"tracks": [{"position": "1", "title": "b"}]},
    ]}
    album = _parse_mb_release(data, Album("MusicBrainz", "x"))
    assert [t.title for t in album.tracks] == ["a", "b"]


def test_mb_search_with_no_terms_is_refused_before_any_request():
    with pytest.raises(WebImportError, match="artist or an album"):
        MusicBrainz().search()


# --- Discogs ------------------------------------------------------------

def test_discogs_search_splits_artist_from_album():
    album = _parse_discogs_search(DISCOGS_SEARCH)[0]
    assert album.artist == "Nirvana"
    assert album.album == "Nevermind"
    assert album.label == "DGC"
    assert album.catalog == "DGCD-24425"
    assert album.year == "1991"
    assert album.url.startswith("https://www.discogs.com/release/")


def test_discogs_title_without_a_dash_is_all_album():
    data = {"results": [{"id": 1, "title": "Untitled", "uri": "/release/1"}]}
    album = _parse_discogs_search(data)[0]
    assert album.artist == "" and album.album == "Untitled"


def test_discogs_release_skips_headings_and_reads_durations():
    album = _parse_discogs_release(DISCOGS_RELEASE, Album("Discogs", "367891"))
    assert [t.title for t in album.tracks] == ["Smells Like Teen Spirit",
                                               "In Bloom"]
    assert album.tracks[0].length == 301
    assert album.genre == "Grunge, Alternative Rock"


def test_discogs_vinyl_positions_fall_back_to_a_running_count():
    data = {"tracklist": [{"position": "A1", "title": "a", "type_": "track"},
                          {"position": "B1", "title": "b", "type_": "track"}]}
    album = _parse_discogs_release(data, Album("Discogs", "x"))
    assert [t.number for t in album.tracks] == [1, 2]


def test_discogs_without_a_token_explains_how_to_get_one():
    with pytest.raises(WebImportError, match="discogs.com/settings/developers"):
        Discogs().search(artist="Nirvana")


def test_discogs_reports_availability():
    assert not Discogs().available
    assert Discogs("abc123").available


# --- shared -------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1991-09-24", "1991"), ("1991", "1991"), (1991, "1991"),
    ("24 Sep 1991", "1991"), ("", ""), (None, ""), ("unknown", ""),
])
def test_year_extraction(raw, expected):
    assert _year(raw) == expected


def test_total_time_sums_the_tracks():
    album = Album("x", "y", tracks=[TrackInfo(1, "a", length=301),
                                    TrackInfo(2, "b", length=254)])
    assert album.total_time == "9:15"


def test_values_for_builds_the_tag_dict():
    album = Album("Discogs", "1", artist="Nirvana", album="Nevermind",
                  year="1991", label="DGC", catalog="DGCD-24425",
                  genre="Grunge", track_count=12)
    values = album.values_for(TrackInfo(3, "Come As You Are"))
    assert values == {"artist": "Nirvana", "album": "Nevermind",
                      "title": "Come As You Are", "track": "3",
                      "track_total": "12", "year": "1991", "genre": "Grunge",
                      "label": "DGC", "catalog": "DGCD-24425"}


def test_values_for_prefers_the_per_track_artist_on_a_compilation():
    album = Album("x", "1", artist="Various Artists", album="Hits")
    values = album.values_for(TrackInfo(1, "Song", artist="Blondie"))
    assert values["artist"] == "Blondie"


def test_values_for_can_also_set_album_artist():
    album = Album("x", "1", artist="Various Artists", album="Hits")
    values = album.values_for(TrackInfo(1, "Song", artist="Blondie"),
                              use_album_artist=True)
    assert values["album_artist"] == "Various Artists"


def test_values_for_omits_fields_the_source_did_not_supply():
    values = Album("x", "1", artist="A", album="B").values_for(TrackInfo(1, "t"))
    assert "label" not in values and "year" not in values


# --- alignment ----------------------------------------------------------

def test_align_pairs_positionally_and_pads_with_none():
    tracks = [TrackInfo(1, "a"), TrackInfo(2, "b")]
    assert align(3, tracks) == [tracks[0], tracks[1], None]


def test_align_ignores_extra_fetched_tracks():
    tracks = [TrackInfo(i, str(i)) for i in range(1, 6)]
    assert align(2, tracks) == tracks[:2]


# --- network errors -----------------------------------------------------

@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """Retries and the MusicBrainz throttle both sleep for real otherwise."""
    monkeypatch.setattr(webimport.time, "sleep", lambda seconds: None)


class _Headers(dict):
    """Just enough of an HTTPMessage for Retry-After handling."""


def _http_error(code, retry_after=None):
    import urllib.error
    headers = _Headers()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("u", code, "boom", headers, None)


@pytest.mark.parametrize("code", [429, 503])
def test_a_rate_limit_is_retried_rather_than_surfaced(code, monkeypatch):
    """MusicBrainz reports throttling as 503, which read as a server outage."""
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            raise _http_error(code, "1")
        return _FakeResponse(b'{"releases": []}')

    monkeypatch.setattr(webimport.urllib.request, "urlopen", flaky)
    assert MusicBrainz().search(artist="Stryper") == []
    assert len(calls) == 3, "should have retried twice before succeeding"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_retry_after_is_honoured(monkeypatch):
    waits = []
    monkeypatch.setattr(webimport.time, "sleep", waits.append)
    monkeypatch.setattr(webimport.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(503, "2")))
    with pytest.raises(WebImportError):
        webimport._get("https://example.invalid")
    assert waits == [2.0, 2.0, 2.0]


def test_without_retry_after_the_backoff_grows(monkeypatch):
    waits = []
    monkeypatch.setattr(webimport.time, "sleep", waits.append)
    monkeypatch.setattr(webimport.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(503)))
    with pytest.raises(WebImportError):
        webimport._get("https://example.invalid")
    assert waits == [0.5, 1.0, 2.0]


def test_a_nonsense_retry_after_falls_back_to_the_backoff(monkeypatch):
    waits = []
    monkeypatch.setattr(webimport.time, "sleep", waits.append)
    monkeypatch.setattr(webimport.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            _http_error(503, "later")))
    with pytest.raises(WebImportError):
        webimport._get("https://example.invalid")
    assert waits == [0.5, 1.0, 2.0]


def test_a_persistent_rate_limit_says_it_is_throttling_not_an_outage(monkeypatch):
    monkeypatch.setattr(webimport.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(503)))
    with pytest.raises(WebImportError, match="rate limiting"):
        MusicBrainz().search(artist="Stryper")


def test_a_real_server_error_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(webimport.urllib.request, "urlopen",
                        lambda *a, **k: calls.append(1) or (
                            _ for _ in ()).throw(_http_error(500)))
    with pytest.raises(WebImportError, match="500"):
        MusicBrainz().search(artist="Nirvana")
    assert len(calls) == 1


def test_the_musicbrainz_throttle_is_shared_between_instances(monkeypatch):
    """A fresh dialog builds a fresh provider; a per-instance clock reset to
    zero and fired straight into the previous dialog's rate-limit window."""
    waits = []
    monkeypatch.setattr(webimport.time, "sleep", waits.append)
    monkeypatch.setattr(webimport.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(b'{"releases": []}'))
    webimport._mb_clock["last"] = 0.0

    MusicBrainz().search(artist="a")
    MusicBrainz().search(artist="b")          # a brand new instance
    assert any(w > 0 for w in waits), "the second call should have waited"


def test_an_unreachable_server_is_reported_not_raised_raw(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("Network is unreachable")

    monkeypatch.setattr(webimport.urllib.request, "urlopen", boom)
    with pytest.raises(WebImportError, match="Could not reach"):
        MusicBrainz().search(album="Nevermind")


def test_missing_cover_art_returns_none_rather_than_failing(monkeypatch):
    monkeypatch.setattr(webimport, "_get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            WebImportError("Not found.")))
    assert MusicBrainz().cover(Album("MusicBrainz", "x")) is None
