"""Real audio fixtures.

Round-tripping tags against files mutagen synthesised would only prove we agree
with ourselves, so every fixture is a genuine encode produced by ffmpeg. They
are a second of silence, built once per session into a tmp dir.
"""

import os
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Qt must be told to run windowless before any widget is built, so that the
# GUI tests work over SSH and in CI as well as on a desktop.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Send QSettings somewhere disposable, BEFORE any QSettings exists.
#
# The GUI tests build real MainWindows, and a MainWindow writes the
# last-opened folder, the recent list, the theme and the window geometry
# straight to the user's own config — twice a test that opened a folder left
# the real app pointing at /tmp/pytest-of-dave/..., and once that had to be
# reconstructed by hand from the recent list.
#
# QSettings.setPath() in a fixture was tried first and did NOT work: the
# organisation/application constructor had already resolved its location by
# then. XDG_CONFIG_HOME is read when the file is opened, so setting it here —
# at import, before PyQt is even loaded — is the one that holds.
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="rifftag-test-config-")

#: extension -> extra ffmpeg args. One entry per tag family we support.
ENCODES = {
    "mp3":  ["-c:a", "libmp3lame", "-b:a", "128k"],
    "flac": ["-c:a", "flac"],
    "ogg":  ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
    "m4a":  ["-c:a", "aac"],
    "wma":  ["-c:a", "wmav2"],
    "wv":   ["-c:a", "wavpack"],
    "wav":  ["-c:a", "pcm_s16le"],
    "aiff": ["-c:a", "pcm_s16be"],
    # Matroska: audio-only, a real video file, and WebM. The two video ones
    # get a tiny picture stream from VIDEO_INPUT so they are genuine films.
    "mka":  ["-c:a", "libvorbis"],
    "mkv":  ["-c:v", "mpeg4", "-c:a", "libvorbis"],
    "webm": ["-c:v", "libvpx", "-b:v", "50k", "-c:a", "libopus"],
}

#: extra ffmpeg inputs for the containers that carry video
VIDEO_INPUT = ["-f", "lavfi", "-i", "color=c=blue:s=64x48:r=10"]
VIDEO = {"mkv", "webm"}

FAMILY = {"mp3": "id3", "wav": "id3", "aiff": "id3",
          "flac": "vorbis", "ogg": "vorbis", "opus": "vorbis",
          "m4a": "mp4", "wma": "asf", "wv": "ape",
          "mka": "matroska", "mkv": "matroska", "webm": "matroska"}


def _encode(dest: Path, ext: str) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
           *(VIDEO_INPUT if ext in VIDEO else []), "-t", "1",
           *ENCODES[ext], str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture(scope="session")
def masters(tmp_path_factory) -> dict[str, Path]:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is needed to build audio fixtures")
    root = tmp_path_factory.mktemp("masters")
    out = {}
    for ext in ENCODES:
        dest = root / f"master.{ext}"
        _encode(dest, ext)
        out[ext] = dest
    return out


@pytest.fixture
def sample(masters, tmp_path):
    """Hand out a private copy, so a test that writes cannot affect another."""
    def make(ext: str, name: str = "track") -> Path:
        dest = tmp_path / f"{name}.{ext}"
        shutil.copy(masters[ext], dest)
        return dest
    return make


@pytest.fixture(scope="session")
def jpeg_bytes() -> bytes:
    """A real 8x8 JPEG, so cover-art dimension probing has something to read."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "c.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=red:s=8x8", "-frames:v", "1", str(out)],
            check=True, capture_output=True)
        return out.read_bytes()
