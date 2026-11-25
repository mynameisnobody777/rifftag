"""Format conversion, driven by ffmpeg.

Tag&Rename has no converter; this is Dave's addition. The design follows the
same split as the rest of the engine — this module is headless and testable,
and ``gui/convert.py`` only wires widgets to it.

Two decisions worth knowing before changing anything here:

**ffmpeg encodes the audio and nothing else.** Every run passes
``-map_metadata -1`` and ``-vn``, so the output starts with no tags and no
embedded art, and :func:`convert` then writes the tags with
:mod:`rifftag.tagio`. Letting ffmpeg copy the metadata would quietly bypass
every rule the app otherwise enforces — the ID3v2.3-vs-2.4 choice, GRP1 vs
TIT1 grouping, the ALBUMARTIST spelling — and would map fields by ffmpeg's
vocabulary rather than ours.

**Originals are never touched.** Output goes to a subfolder of the source
folder (``<album>/MP3/<name>.mp3``), which also makes a second run obviously
re-runnable and leaves nothing to clean up if the result is wrong.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .tagio import TagError, WriteOptions, write


class ConvertError(Exception):
    pass


@dataclass(frozen=True)
class Quality:
    key: str
    label: str
    #: ffmpeg arguments, after the input and before the output
    args: tuple[str, ...]


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    extension: str
    #: name of the subfolder written beside the originals
    subfolder: str
    qualities: tuple[Quality, ...]

    @property
    def default_quality(self) -> Quality:
        return self.qualities[0]

    def quality(self, key: str) -> Quality:
        for q in self.qualities:
            if q.key == key:
                return q
        return self.default_quality


#: Verified against `ffmpeg -encoders` on Ubuntu noble: libmp3lame, flac,
#: aac and pcm_* are all present in the stock build.
FORMATS: tuple[Format, ...] = (
    Format("mp3", "MP3", ".mp3", "MP3", (
        # VBR first: V0 beats 320 CBR on size at the same transparency, and
        # is what most people actually want from "convert to MP3".
        #
        # The labels say "average" and "fixed rate" out loud because the
        # numbers mislead on their own: 245 next to 320 reads as "worse",
        # when V0 varies the bitrate per frame — spending 320 on a cymbal
        # crash and 150 on a quiet bar — and 245 is only what that averages
        # to. CBR spends 320 on the near-silence too, where it buys nothing.
        # The one real reason to pick 320 CBR is a distributor or broadcast
        # spec that demands a fixed rate, so the label says that instead of
        # leaving people to guess that a bigger number must be better.
        Quality("v0", "VBR V0 — best quality for the size (~245 kbps average)",
                ("-c:a", "libmp3lame", "-q:a", "0")),
        Quality("v2", "VBR V2 — smaller (~190 kbps average)",
                ("-c:a", "libmp3lame", "-q:a", "2")),
        Quality("320", "320 kbps CBR — fixed rate, if a distributor asks for it",
                ("-c:a", "libmp3lame", "-b:a", "320k")),
        Quality("256", "256 kbps CBR — fixed rate",
                ("-c:a", "libmp3lame", "-b:a", "256k")),
        Quality("192", "192 kbps CBR — fixed rate",
                ("-c:a", "libmp3lame", "-b:a", "192k")),
    )),
    Format("flac", "FLAC", ".flac", "FLAC", (
        Quality("default", "Lossless — default compression",
                ("-c:a", "flac")),
        Quality("max", "Lossless — maximum compression (slower)",
                ("-c:a", "flac", "-compression_level", "8")),
    )),
    Format("wav", "WAV", ".wav", "WAV", (
        # No -ar: resampling on the way to WAV is a surprise, not a feature.
        Quality("16", "16-bit, keep sample rate", ("-c:a", "pcm_s16le")),
        Quality("24", "24-bit, keep sample rate", ("-c:a", "pcm_s24le")),
        Quality("cd", "16-bit 44.1 kHz (CD)",
                ("-c:a", "pcm_s16le", "-ar", "44100")),
    )),
    Format("m4a", "M4A (AAC)", ".m4a", "M4A", (
        Quality("256", "256 kbps", ("-c:a", "aac", "-b:a", "256k")),
        Quality("192", "192 kbps", ("-c:a", "aac", "-b:a", "192k")),
        Quality("128", "128 kbps", ("-c:a", "aac", "-b:a", "128k")),
    )),
)

BY_KEY = {f.key: f for f in FORMATS}


@dataclass
class Step:
    """One file's worth of work, or one reason there is none."""

    track: object                 # rifftag.tagio.Track
    dst: Path
    #: empty = will be converted; otherwise why it is being skipped
    note: str = ""

    @property
    def src(self) -> Path:
        return self.track.path

    @property
    def skipped(self) -> bool:
        return bool(self.note)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def plan(tracks, fmt: Format, subfolder: str | None = None) -> list[Step]:
    """Work out what would be written, without writing anything.

    Mirrors ``renamer.plan`` deliberately: the dialog shows this table before
    anything is encoded, so a wrong destination is visible rather than
    discovered afterwards.
    """
    folder = (subfolder or fmt.subfolder).strip() or fmt.subfolder
    steps: list[Step] = []
    claimed: set[Path] = set()
    for track in tracks:
        src = track.path
        dst = src.parent / folder / (src.stem + fmt.extension)
        note = ""
        if src.suffix.lower() == fmt.extension:
            note = f"already {fmt.label}"
        elif dst in claimed:
            note = "two files would land on the same name"
        elif dst.exists():
            note = "destination exists"
        if not note:
            claimed.add(dst)
        steps.append(Step(track, dst, note))
    return steps


def to_convert(steps) -> list[Step]:
    return [s for s in steps if not s.skipped]


def convert(step: Step, fmt: Format, quality: Quality, *,
            options: WriteOptions | None = None,
            copy_tags: bool = True, timeout: int = 1800) -> None:
    """Encode one file, then put the tags on it. Raises ConvertError."""
    if not ffmpeg_available():
        raise ConvertError(
            "ffmpeg is not installed — sudo apt install ffmpeg")
    src, dst = step.src, step.dst
    if not src.exists():
        raise ConvertError(f"{src.name}: the source file is gone")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConvertError(f"{dst.parent}: {exc}") from exc

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-vn", "-map_metadata", "-1", *quality.args, str(dst)]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ConvertError(f"{src.name}: ffmpeg timed out") from exc
    except OSError as exc:
        raise ConvertError(f"{src.name}: could not run ffmpeg — {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or b"").decode("utf-8", "replace").strip()
        # ffmpeg's last line is the useful one; the rest is context.
        last = detail.splitlines()[-1] if detail else "no error text"
        # A half-written file is worse than none: a later run would see it as
        # "destination exists" and skip the file for good.
        dst.unlink(missing_ok=True)
        raise ConvertError(f"{src.name}: {last}")

    if not copy_tags:
        return
    try:
        write(dst, _tag_values(step.track),
              pictures=list(getattr(step.track, "pictures", []) or []),
              options=options)
    except TagError as exc:
        raise ConvertError(f"{dst.name}: converted, but tagging failed — {exc}")


def _tag_values(track) -> dict:
    """Everything the source carried, minus the blanks.

    Blanks are dropped rather than written as "" because an absent key leaves
    the (brand new, empty) tag alone, while "" would write an explicit empty
    frame for every field the source happened not to have.
    """
    values = getattr(track, "values", {}) or {}
    return {k: v for k, v in values.items() if v not in (None, "")}


def run(steps, fmt: Format, quality: Quality, *,
        options: WriteOptions | None = None,
        progress=None, should_stop=None) -> list[tuple[Step, str]]:
    """Convert every step that is not skipped. Returns (step, error) pairs.

    Errors are collected rather than raised: one unreadable file in an album
    of thirty must not abandon the other twenty-nine.
    """
    failures: list[tuple[Step, str]] = []
    work = to_convert(steps)
    for i, step in enumerate(work, 1):
        if should_stop is not None and should_stop():
            break
        try:
            convert(step, fmt, quality, options=options)
        except ConvertError as exc:
            failures.append((step, str(exc)))
        if progress is not None:
            progress(i, len(work))
    return failures
