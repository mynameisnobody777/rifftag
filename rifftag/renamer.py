"""Rename planning, application and undo.

Renaming is always three separate steps — plan, show, apply — because the
original's Test/Rename/Undo buttons are the thing that makes it trustworthy:
you see every before-and-after pair before a single file moves.

Two hazards specific to this setup get explicit handling rather than a comment:

* **Case-only renames.** ``lithium.mp3`` -> ``Lithium.mp3`` is a no-op on NTFS
  and on a Samba share, and on some of them an outright error, because source
  and target are the same file. We detect it and go via a temporary name.
* **Collisions inside one batch.** Two files can render to the same new name
  (a mask of ``%1 - %3`` over an album, say). Planning catches that before
  anything moves, instead of silently eating a track.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .masks import MaskError, RenderOptions, SEP, render, sanitize_component
from .tagio import Track


class Status(Enum):
    OK = "ok"
    UNCHANGED = "unchanged"
    COLLISION = "collision"
    EXISTS = "exists"
    ERROR = "error"


@dataclass
class Step:
    """One planned move. ``dst`` is absolute; parents may not exist yet."""

    src: Path
    dst: Path
    status: Status = Status.OK
    message: str = ""
    #: files dragged along with the main one, e.g. a matching .cdg or .lrc
    companions: list[tuple[Path, Path]] = field(default_factory=list)

    @property
    def will_move(self) -> bool:
        return self.status is Status.OK


@dataclass
class Transaction:
    """What actually happened, in order, so it can be walked backwards."""

    moves: list[tuple[Path, Path]] = field(default_factory=list)
    created_dirs: list[Path] = field(default_factory=list)
    failures: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.moves)


#: Sidecars renamed alongside the track, matching Tag&Rename's .cdg handling.
COMPANION_SUFFIXES = (".cdg", ".lrc", ".cue")


def _unique(dst: Path, taken: set[Path]) -> Path:
    """``name.mp3`` -> ``name (2).mp3``, as the original does on a clash."""
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    n = 2
    while True:
        cand = parent / f"{stem} ({n}){suffix}"
        if cand not in taken and not cand.exists():
            return cand
        n += 1


def plan(tracks: list[Track], mask: str, *,
         options: RenderOptions | None = None,
         root: Path | None = None,
         on_collision_number: bool = True,
         companions: bool = True) -> list[Step]:
    """Work out every move without touching the disk.

    ``root`` is where a mask's folder levels hang off; it defaults to each
    file's own directory, which is what makes ``%1 - %3\\%6 %2`` reorganise an
    album in place.
    """
    opts = options or RenderOptions()
    steps: list[Step] = []
    taken: set[Path] = set()

    for i, track in enumerate(tracks, start=1):
        src = track.path
        if track.error:
            steps.append(Step(src, src, Status.ERROR, track.error))
            continue
        try:
            rendered = render(mask, track.rename_values(file_number=i),
                              options=opts)
        except MaskError as exc:
            steps.append(Step(src, src, Status.ERROR, str(exc)))
            continue

        base = root if root is not None else src.parent
        parts = rendered.split(SEP)
        dst = base.joinpath(*parts).with_suffix(src.suffix)

        if _is_same_path(dst, src):
            steps.append(Step(src, dst, Status.UNCHANGED))
            continue
        if dst in taken:
            if on_collision_number:
                dst = _unique(dst, taken)
            else:
                steps.append(Step(src, dst, Status.COLLISION,
                                  "another file in this batch takes that name"))
                continue
        elif dst.exists() and not _same_file_different_case(src, dst):
            if on_collision_number:
                dst = _unique(dst, taken)
            else:
                steps.append(Step(src, dst, Status.EXISTS,
                                  "a file with that name already exists"))
                continue

        taken.add(dst)
        step = Step(src, dst)
        if companions:
            step.companions = _find_companions(src, dst)
        steps.append(step)

    return steps


def _is_same_path(a: Path, b: Path) -> bool:
    """Exact, case-SENSITIVE path comparison.

    ``Path.__eq__`` ignores case on Windows, so ``lithium.mp3`` and
    ``Lithium.mp3`` compare equal there — which made the planner call a
    case-only rename "unchanged" and silently do nothing. Fixing
    capitalisation is half of what a tagger is for, so the comparison has to
    be on the text. On Linux this is exactly what Path equality already did.
    """
    return str(a) == str(b)


def _same_file_different_case(src: Path, dst: Path) -> bool:
    """True when dst "exists" only because the filesystem ignores case."""
    if _is_same_path(src, dst):
        return True
    try:
        return src.resolve() == dst.resolve() or os.path.samefile(src, dst)
    except OSError:
        return False


def _find_companions(src: Path, dst: Path) -> list[tuple[Path, Path]]:
    out = []
    for suffix in COMPANION_SUFFIXES:
        mate = src.with_suffix(suffix)
        if mate.exists() and mate != src:
            out.append((mate, dst.with_suffix(suffix)))
    return out


def apply(steps: list[Step]) -> Transaction:
    """Perform the planned moves. Carries on past a failure and records it."""
    tx = Transaction()
    for step in steps:
        if not step.will_move:
            continue
        try:
            _ensure_parent(step.dst, tx)
            _move(step.src, step.dst, tx)
            for csrc, cdst in step.companions:
                _ensure_parent(cdst, tx)
                _move(csrc, cdst, tx)
        except OSError as exc:
            tx.failures.append((step.src, f"{type(exc).__name__}: {exc}"))
    return tx


def _ensure_parent(dst: Path, tx: Transaction) -> None:
    parent = dst.parent
    if parent.exists():
        return
    missing = []
    p = parent
    while not p.exists():
        missing.append(p)
        p = p.parent
    parent.mkdir(parents=True, exist_ok=True)
    tx.created_dirs.extend(reversed(missing))


def _move(src: Path, dst: Path, tx: Transaction) -> None:
    if _same_file_different_case(src, dst) and src != dst:
        # NTFS and Samba will not rename a file onto itself, so hop through a
        # name that cannot collide and land on the target in a second step.
        tmp = src.with_name(f".rifftag-{uuid.uuid4().hex[:8]}{src.suffix}")
        os.rename(src, tmp)
        os.rename(tmp, dst)
    else:
        os.rename(src, dst)
    tx.moves.append((src, dst))


def undo(tx: Transaction) -> Transaction:
    """Walk a transaction backwards. Returns what the undo itself managed."""
    back = Transaction()
    for src, dst in reversed(tx.moves):
        try:
            _ensure_parent(src, back)
            _move(dst, src, back)
        except OSError as exc:
            back.failures.append((dst, f"{type(exc).__name__}: {exc}"))
    for d in reversed(tx.created_dirs):
        try:
            d.rmdir()          # only succeeds if we left it empty, which is right
        except OSError:
            pass
    return back


# --- folder renaming ----------------------------------------------------

def plan_folder(folder: Path, tracks: list[Track], mask: str, *,
                options: RenderOptions | None = None) -> Step:
    """Rename a folder from the tags of the files inside it.

    Values come from the first track that has each field, so a stray untagged
    file in the album does not sink the whole rename. Per the original, folder
    masks cannot create sub-folders — a ``\\`` here is an error, not a level.
    """
    opts = options or RenderOptions()
    if SEP in mask or "/" in mask:
        return Step(folder, folder, Status.ERROR,
                    "a folder mask cannot contain a path separator")

    merged: dict[str, object] = {}
    for t in tracks:
        if t.error:
            continue
        for k, v in t.rename_values().items():
            merged.setdefault(k, v)
    if not merged:
        return Step(folder, folder, Status.ERROR, "no tag data in this folder")

    try:
        name = render(mask, merged, options=opts)
    except MaskError as exc:
        return Step(folder, folder, Status.ERROR, str(exc))

    dst = folder.parent / sanitize_component(name,
                                             windows_safe=opts.windows_safe)
    if _is_same_path(dst, folder):
        return Step(folder, dst, Status.UNCHANGED)
    if dst.exists() and not _same_file_different_case(folder, dst):
        return Step(folder, dst, Status.EXISTS, "that folder already exists")
    return Step(folder, dst)
