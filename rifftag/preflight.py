"""Release pre-flight: what a distributor will reject, before you upload.

This is the part no Linux tagger does. The checks come from the things that
actually bounce a release or hurt it on streaming — missing or undersized
artwork, an album artist that disagrees with itself across the tracks, a gap
in the track numbering, a master that clips once it is transcoded.

Three ground rules:

**Nothing here writes anything.** It reads tags and, when asked, measures
loudness with ffmpeg. Every finding names the file and says what to do.

**Levels mean something.** ``ERROR`` is "this will be rejected or is plainly
wrong"; ``WARNING`` is "this will probably hurt you"; ``NOTE`` is "worth a
look, your call". Nothing is invented as an error that is merely a taste.

**Distributor rules move.** Anything that depends on a particular store's
policy is a NOTE phrased as a question, not an ERROR — the thresholds that
*are* hard-coded (3000x3000 art, -1 dBTP, -14 LUFS) are the widely published
streaming norms and live in one place at the top of this module so they can
be corrected without hunting.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .lyrics import SECTION_WORDS

ERROR, WARNING, NOTE = "error", "warning", "note"

#: Square artwork at least this wide. 3000x3000 is the common store minimum.
ART_MIN = 3000
#: True peak ceiling. Lossy transcoding overshoots, so masters that touch
#: 0 dBFS clip on the listener's side even when the file itself does not.
TRUE_PEAK_MAX = -1.0
#: What the big platforms normalise to. Louder masters just get turned down.
TARGET_LUFS = -14.0
#: How far from the target is worth mentioning.
LUFS_TOLERANCE = 2.0
#: Lossless formats a distributor is happy to ingest.
LOSSLESS = (".wav", ".flac", ".aiff", ".aif")

_FEAT = re.compile(r"\b(feat\.?|ft\.?|featuring)\b", re.IGNORECASE)
_SECTION_IN_LYRICS = re.compile(
    r"^\s*[\[(]\s*(" + "|".join(re.escape(w) for w in SECTION_WORDS)
    + r")\b", re.IGNORECASE | re.MULTILINE)


@dataclass
class Finding:
    level: str
    code: str
    message: str
    #: None means the finding is about the release, not one file
    path: Path | None = None
    fix: str = ""

    @property
    def where(self) -> str:
        return self.path.name if self.path else "the release"


@dataclass
class Loudness:
    integrated: float | None = None
    true_peak: float | None = None
    error: str = ""


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    loudness: dict = field(default_factory=dict)

    def add(self, level, code, message, path=None, fix="") -> None:
        self.findings.append(Finding(level, code, message, path, fix))

    def of(self, level: str) -> list[Finding]:
        return [f for f in self.findings if f.level == level]

    @property
    def summary(self) -> str:
        counts = Counter(f.level for f in self.findings)
        if not counts:
            return "No problems found."
        parts = []
        for level, word in ((ERROR, "problem"), (WARNING, "warning"),
                            (NOTE, "note")):
            n = counts.get(level, 0)
            if n:
                parts.append(f"{n} {word}{'s' if n != 1 else ''}")
        return ", ".join(parts)


def _value(track, key: str) -> str:
    return str((getattr(track, "values", {}) or {}).get(key, "") or "").strip()


def check(tracks, *, loudness: dict | None = None) -> Report:
    """Run every tag-based check. ``loudness`` is measured separately."""
    report = Report(loudness=dict(loudness or {}))
    tracks = [t for t in tracks if t is not None]
    if not tracks:
        return report

    _check_release(tracks, report)
    for track in tracks:
        _check_track(track, report)
        _check_loudness(track, report)
    _collapse_repeats(report, len(tracks))
    return report


def _collapse_repeats(report: Report, track_count: int) -> None:
    """One line per problem the whole release shares, not one per track.

    A 17-track album with the same undersized cover on every file produced 17
    identical warnings, which buried the findings that differed. Only
    *identical* messages on *every* track collapse — anything that varies
    (two different artwork sizes, say) stays per file, because then the file
    name is the information.
    """
    if track_count < 2:
        return
    by_code: dict[str, list[Finding]] = {}
    for finding in report.findings:
        if finding.path is not None:
            by_code.setdefault(finding.code, []).append(finding)

    collapse = set()
    for code, group in by_code.items():
        if len(group) != track_count:
            continue
        if len({f.message for f in group}) != 1:
            continue
        if len({f.path for f in group}) != track_count:
            continue
        collapse.add(code)

    if not collapse:
        return
    kept: list[Finding] = []
    seen: set[str] = set()
    for finding in report.findings:
        if finding.code not in collapse or finding.path is None:
            kept.append(finding)
            continue
        if finding.code in seen:
            continue
        seen.add(finding.code)
        kept.append(Finding(finding.level, finding.code,
                            f"{finding.message} (every track)",
                            None, finding.fix))
    report.findings[:] = kept


def _check_release(tracks, report: Report) -> None:
    def spread(key):
        return {_value(t, key) for t in tracks if _value(t, key)}

    albums = spread("album")
    if not albums:
        report.add(ERROR, "album_missing",
                   "No album title on any track.",
                   fix="Set Album in the Multi File Tag Editor.")
    elif len(albums) > 1:
        report.add(ERROR, "album_inconsistent",
                   "The tracks disagree about the album title: "
                   + ", ".join(sorted(albums)),
                   fix="Tick Album in the Multi File Tag Editor and save it "
                       "across every track.")

    artists = spread("album_artist") or spread("artist")
    if len(artists) > 1:
        report.add(WARNING, "album_artist_inconsistent",
                   "Album Artist differs across the tracks: "
                   + ", ".join(sorted(artists)),
                   fix="Set one Album Artist for the whole release — stores "
                       "use it to keep the album together.")

    years = spread("year")
    if len(years) > 1:
        report.add(WARNING, "year_inconsistent",
                   "More than one release year: " + ", ".join(sorted(years)))

    genres = spread("genre")
    if len(genres) > 1:
        report.add(NOTE, "genre_inconsistent",
                   "More than one genre across the release: "
                   + ", ".join(sorted(genres)))

    _check_numbering(tracks, report)
    _check_casing(tracks, report)
    _check_artwork_consistency(tracks, report)


def _check_numbering(tracks, report: Report) -> None:
    numbers = []
    for track in tracks:
        raw = _value(track, "track").split("/")[0]
        try:
            numbers.append(int(raw))
        except ValueError:
            report.add(ERROR, "track_number_missing",
                       "No track number.", track.path,
                       fix="Tools → Auto fill track # numbers the list in "
                           "order.")
    if not numbers:
        return
    duplicates = [n for n, c in Counter(numbers).items() if c > 1]
    if duplicates:
        report.add(ERROR, "track_number_duplicate",
                   "Two tracks share a number: "
                   + ", ".join(str(n) for n in sorted(duplicates)),
                   fix="Tools → Auto fill track #.")
    expected = set(range(1, len(numbers) + 1))
    missing = sorted(expected - set(numbers))
    if missing and len(numbers) == len(tracks):
        report.add(WARNING, "track_number_gap",
                   "The numbering is not 1..n — missing "
                   + ", ".join(str(n) for n in missing),
                   fix="Fine for a partial selection; fix it before upload "
                       "if this is the whole release.")

    totals = {_value(t, "track_total") for t in tracks if _value(t, "track_total")}
    if len(totals) > 1:
        report.add(WARNING, "track_total_inconsistent",
                   "Track total disagrees across the tracks: "
                   + ", ".join(sorted(totals)))
    elif totals:
        try:
            total = int(next(iter(totals)).split("/")[0])
        except ValueError:
            total = None
        if total is not None and total != len(tracks):
            report.add(NOTE, "track_total_mismatch",
                       f"Track total says {total} but {len(tracks)} file(s) "
                       "are listed.")


def _casing(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "none"
    if all(c.isupper() for c in letters):
        return "UPPER"
    if all(c.islower() for c in letters):
        return "lower"
    words = [w for w in re.split(r"\s+", text) if w and w[0].isalpha()]
    if words and all(w[0].isupper() for w in words):
        return "Title Case"
    return "Sentence case"


def _check_casing(tracks, report: Report) -> None:
    styles = Counter(_casing(_value(t, "title")) for t in tracks
                     if _value(t, "title"))
    styles.pop("none", None)
    if len(styles) > 1:
        shape = ", ".join(f"{n}x {style}" for style, n in styles.most_common())
        report.add(WARNING, "title_casing_inconsistent",
                   f"Track titles use more than one capitalisation: {shape}",
                   fix="The Rename tab's Case box can restyle them, or fix "
                       "them in the grid.")


def _picture(track):
    pictures = getattr(track, "pictures", None) or []
    return pictures[0] if pictures else None


def _check_artwork_consistency(tracks, report: Report) -> None:
    digests = {bytes(p.data[:4096]) for p in
               (_picture(t) for t in tracks) if p is not None}
    if len(digests) > 1:
        report.add(WARNING, "artwork_inconsistent",
                   "The tracks do not all carry the same cover art.",
                   fix="Use Add folder.jpg to files tag to put one image on "
                       "every track.")


def _check_track(track, report: Report) -> None:
    path = track.path
    if getattr(track, "error", None):
        report.add(ERROR, "unreadable", f"Cannot be read: {track.error}", path)
        return

    for key, label in (("title", "Title"), ("artist", "Artist")):
        if not _value(track, key):
            report.add(ERROR, f"{key}_missing", f"No {label}.", path,
                       fix="Every store requires it.")
    if not _value(track, "year"):
        report.add(WARNING, "year_missing", "No release year.", path)
    if not _value(track, "isrc"):
        report.add(NOTE, "isrc_missing",
                   "No ISRC. Your distributor usually assigns one — paste it "
                   "back in afterwards so your files match the release.",
                   path)

    title = _value(track, "title")
    if title and _FEAT.search(title) and not _FEAT.search(_value(track, "artist")):
        report.add(NOTE, "feat_in_title",
                   'Title carries "feat." but the Artist field does not.',
                   path,
                   fix="Check your distributor: some want featured artists "
                       "in the artist field, some want them in the title.")
    if title != title.strip() or "  " in title:
        report.add(WARNING, "title_whitespace",
                   "Title has stray spacing.", path,
                   fix="Tools → Trim leading/trailing spaces in all fields.")

    picture = _picture(track)
    if picture is None:
        report.add(ERROR, "artwork_missing", "No cover art embedded.", path,
                   fix="folder.jpg → tag art, or the Tag Editor's art box.")
    else:
        size = picture.dimensions()
        if size is None:
            report.add(NOTE, "artwork_unreadable",
                       "Cover art is not a JPEG or PNG, so its size could "
                       "not be checked.", path)
        else:
            width, height = size
            if width != height:
                report.add(WARNING, "artwork_not_square",
                           f"Cover art is {width}x{height}, not square.",
                           path, fix="Stores require a square image.")
            if min(width, height) < ART_MIN:
                report.add(WARNING, "artwork_small",
                           f"Cover art is {width}x{height}; "
                           f"{ART_MIN}x{ART_MIN} is the usual minimum.", path,
                           fix=f"Re-export the cover at {ART_MIN}x{ART_MIN} "
                               "and embed it with folder.jpg → tag art.")

    if path.suffix.lower() not in LOSSLESS:
        report.add(NOTE, "lossy_master",
                   f"{path.suffix.lstrip('.').upper()} is a lossy master. "
                   "Distributors want WAV or FLAC.", path,
                   fix="Tools → Convert audio format, if you still have the "
                       "lossless original.")

    info = getattr(track, "info", None)
    if info is not None and info.sample_rate and info.sample_rate < 44100:
        report.add(WARNING, "sample_rate_low",
                   f"{info.sample_rate} Hz — below CD rate.", path)

    lyrics_text = _value(track, "lyrics")
    if lyrics_text and _SECTION_IN_LYRICS.search(lyrics_text):
        report.add(WARNING, "lyrics_scaffolding",
                   "Lyrics still carry section headers like [Verse 1].",
                   path, fix="Lyrics → Clean lyrics for streaming.")


def _check_loudness(track, report: Report) -> None:
    measured = report.loudness.get(track.path)
    if measured is None:
        return
    if measured.error:
        report.add(NOTE, "loudness_unmeasured",
                   f"Loudness could not be measured: {measured.error}",
                   track.path)
        return
    if measured.true_peak is not None and measured.true_peak > TRUE_PEAK_MAX:
        report.add(WARNING, "true_peak_hot",
                   f"True peak {measured.true_peak:+.1f} dBTP — above "
                   f"{TRUE_PEAK_MAX:+.0f}. Lossy transcoding overshoots, so "
                   "this can clip on the listener's side.",
                   track.path,
                   fix="Pull the master down a little before you upload.")
    if measured.integrated is not None:
        off = measured.integrated - TARGET_LUFS
        if abs(off) > LUFS_TOLERANCE:
            direction = "louder" if off > 0 else "quieter"
            report.add(NOTE, "loudness_off_target",
                       f"{measured.integrated:.1f} LUFS — {abs(off):.1f} dB "
                       f"{direction} than the {TARGET_LUFS:.0f} LUFS "
                       "streaming target.",
                       track.path,
                       fix="Platforms turn it down anyway; only worth "
                           "chasing if the album is inconsistent.")


# --- loudness measurement ------------------------------------------------

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def measure(path: Path, timeout: int = 600) -> Loudness:
    """Integrated LUFS and true peak for one file, via ffmpeg's ebur128.

    ``loudnorm`` in analysis mode prints a JSON block on stderr and is the
    cheapest way to get both numbers in one pass; the file is decoded but
    never written.
    """
    if not ffmpeg_available():
        return Loudness(error="ffmpeg is not installed")
    cmd = ["ffmpeg", "-nostats", "-hide_banner", "-i", str(path),
           "-map", "a:0", "-af", "loudnorm=print_format=json",
           "-f", "null", "-"]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Loudness(error="ffmpeg timed out")
    except OSError as exc:
        return Loudness(error=str(exc))

    text = (done.stderr or b"").decode("utf-8", "replace")
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return Loudness(error="no loudness data in ffmpeg's output")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return Loudness(error="could not read ffmpeg's loudness data")

    def number(key):
        try:
            value = float(data.get(key))
        except (TypeError, ValueError):
            return None
        # ffmpeg reports -inf as -99 or similar for silence.
        return None if value <= -70 else value

    return Loudness(integrated=number("input_i"),
                    true_peak=number("input_tp"))


def measure_all(tracks, progress=None, should_stop=None) -> dict:
    """Measure every track. Returns ``{path: Loudness}``."""
    out: dict = {}
    tracks = list(tracks)
    for i, track in enumerate(tracks, 1):
        if should_stop is not None and should_stop():
            break
        out[track.path] = measure(track.path)
        if progress is not None:
            progress(i, len(tracks))
    return out
