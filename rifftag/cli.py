"""What RiffTag does with the arguments it was started with.

This is what makes "right-click a song -> Open with -> RiffTag" land on the
song rather than on whatever folder was open last time. The desktop file
passes ``%F`` and Windows passes the clicked path as ``argv[1]``; both arrive
here as plain strings.

Kept headless and import-light on purpose so it can be tested without a
display, like the rest of the engine.

Two things make the parsing less obvious than it looks:

* **Qt eats some of argv too.** ``QApplication`` accepts ``-style fusion``,
  ``-platform offscreen`` and friends. Rather than keep a list of them in
  step with Qt's, anything that is not a path to something that exists on
  disk is ignored. A style name is not a file; a song is.
* **Launchers disagree about paths vs URIs.** ``%F`` gives paths, ``%U``
  gives ``file://`` URIs, and some file managers hand over a URI regardless.
  Both are accepted, percent-decoding included.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname


@dataclass(frozen=True)
class Target:
    """Where to open, and what to put the cursor on once it is open."""

    folder: Path | None = None
    files: tuple[Path, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return self.folder is not None


def _as_path(arg: str) -> Path | None:
    """One argument as a path, or None if it is not one.

    ``file:///home/dave/a%20song.mp3`` and ``/home/dave/a song.mp3`` are the
    same file; a bare ``-style`` is not a file at all.
    """
    if not arg:
        return None
    if arg.startswith("file://"):
        parsed = urlparse(arg)
        if parsed.netloc not in ("", "localhost"):
            return None                 # a remote URI: not ours to open
        # url2pathname, not unquote: on Windows "/C:/Users/a%20b.mp3" must
        # become "C:\\Users\\a b.mp3"; unquote left the leading slash and the
        # file was never found (seen in the Windows VM run, 2026-11-28).
        arg = url2pathname(parsed.path)
    elif arg.startswith("-"):
        return None
    try:
        return Path(arg).expanduser()
    except (OSError, ValueError):
        return None


def parse_targets(argv: list[str] | None) -> Target:
    """Turn a command line into a folder to open and files to select.

    The first thing that exists decides the folder: a folder argument opens
    itself, a file argument opens the folder it lives in. Further files are
    taken along only if they are siblings — selecting across folders is not
    something the list can show, and silently opening a different folder than
    the one the user clicked in would be worse than ignoring the extras.

    ``argv[0]`` is the program name and is never treated as a target, which
    matters because ``python3 -m rifftag`` makes argv[0] a real path.
    """
    paths = [p for p in (_as_path(a) for a in (argv or [])[1:]) if p]
    folder: Path | None = None
    files: list[Path] = []
    for path in paths:
        try:
            if path.is_dir():
                if folder is None:
                    folder = path
                continue
            if not path.is_file():
                continue
        except OSError:                 # an unreadable mount, a dead symlink
            continue
        if folder is None:
            folder = path.parent
        if path.parent == folder:
            files.append(path)
    if folder is None:
        return Target()
    return Target(folder=folder, files=tuple(dict.fromkeys(files)))
