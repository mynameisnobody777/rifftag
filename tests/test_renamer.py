"""Rename planning, applying and undo."""

import os
from pathlib import Path

import pytest

from rifftag import renamer, tagio
from rifftag.masks import RenderOptions
from rifftag.renamer import Status


def tagged(sample, ext="mp3", name="track", **values):
    p = sample(ext, name)
    tagio.write(p, values)
    return tagio.read(p)


def test_plan_does_not_touch_the_disk(sample):
    t = tagged(sample, artist="Nirvana", title="Lithium")
    steps = renamer.plan([t], "%1 - %2")
    assert steps[0].dst.name == "Nirvana - Lithium.mp3"
    assert t.path.exists() and not steps[0].dst.exists()


def test_apply_then_undo_restores_everything(sample):
    t = tagged(sample, artist="Nirvana", title="Lithium")
    original = t.path
    steps = renamer.plan([t], "%1 - %2")
    tx = renamer.apply(steps)
    assert tx.count == 1
    assert steps[0].dst.exists() and not original.exists()

    renamer.undo(tx)
    assert original.exists() and not steps[0].dst.exists()


def test_mask_with_a_separator_creates_folders_and_undo_removes_them(sample):
    t = tagged(sample, artist="Nirvana", album="Nevermind", title="Lithium",
               track="5")
    steps = renamer.plan([t], "%1 - %3\\%6 %2")
    tx = renamer.apply(steps)
    made = t.path.parent / "Nirvana - Nevermind" / "05 Lithium.mp3"
    assert made.exists()

    renamer.undo(tx)
    assert t.path.exists()
    assert not made.parent.exists(), "an empty folder we created should go too"


def test_undo_leaves_a_folder_that_still_has_other_files(sample, tmp_path):
    t = tagged(sample, artist="Nirvana", album="Nevermind", title="Lithium")
    steps = renamer.plan([t], "%1 - %3\\%2")
    tx = renamer.apply(steps)
    folder = t.path.parent / "Nirvana - Nevermind"
    (folder / "notes.txt").write_text("mine")

    renamer.undo(tx)
    assert folder.exists(), "must not delete a folder someone else put files in"


def test_unchanged_file_is_reported_not_moved(sample):
    t = tagged(sample, artist="Nirvana", title="Lithium")
    renamer.apply(renamer.plan([t], "%1 - %2"))
    moved = tagio.read(t.path.parent / "Nirvana - Lithium.mp3")
    steps = renamer.plan([moved], "%1 - %2")
    assert steps[0].status is Status.UNCHANGED
    assert renamer.apply(steps).count == 0


def test_two_files_rendering_to_the_same_name_get_numbered(sample):
    a = tagged(sample, name="a", artist="Nirvana", album="Nevermind")
    b = tagged(sample, name="b", artist="Nirvana", album="Nevermind")
    steps = renamer.plan([a, b], "%1 - %3")
    tx = renamer.apply(steps)
    assert tx.count == 2
    names = sorted(p.name for _, p in tx.moves)
    assert names == ["Nirvana - Nevermind (2).mp3", "Nirvana - Nevermind.mp3"]


def test_collision_can_be_reported_instead_of_numbered(sample):
    a = tagged(sample, name="a", artist="Nirvana", album="Nevermind")
    b = tagged(sample, name="b", artist="Nirvana", album="Nevermind")
    steps = renamer.plan([a, b], "%1 - %3", on_collision_number=False)
    assert steps[1].status is Status.COLLISION
    assert renamer.apply(steps).count == 1


def test_case_only_rename_works(sample):
    """Fails without the temp-name hop on NTFS and Samba shares."""
    t = tagged(sample, name="lithium", artist="x", title="Lithium")
    steps = renamer.plan([t], "%2")
    assert steps[0].status is Status.OK
    renamer.apply(steps)
    assert steps[0].dst.name == "Lithium.mp3"
    names = os.listdir(steps[0].dst.parent)
    assert "Lithium.mp3" in names


def test_a_file_missing_the_masked_field_is_flagged_and_skipped(sample):
    good = tagged(sample, name="a", artist="Nirvana", title="Lithium")
    bad = tagged(sample, name="b", title="Polly")          # no artist
    steps = renamer.plan([good, bad], "%1 - %2")
    assert steps[0].status is Status.OK
    assert steps[1].status is Status.ERROR and "Artist" in steps[1].message
    tx = renamer.apply(steps)
    assert tx.count == 1
    assert bad.path.exists(), "a failing file must be left exactly as it was"


def test_unreadable_file_is_flagged_not_crashed_on(sample, tmp_path):
    junk = tmp_path / "broken.mp3"
    junk.write_bytes(b"not audio")
    steps = renamer.plan([tagio.read(junk)], "%1 - %2")
    assert steps[0].status is Status.ERROR


def test_companion_files_follow_the_track(sample):
    t = tagged(sample, artist="Nirvana", title="Lithium")
    cdg = t.path.with_suffix(".cdg")
    cdg.write_bytes(b"graphics")
    steps = renamer.plan([t], "%1 - %2")
    assert steps[0].companions
    renamer.apply(steps)
    assert (t.path.parent / "Nirvana - Lithium.cdg").exists()
    assert not cdg.exists()


def test_file_number_counts_the_selection_not_the_tag(sample):
    a = tagged(sample, name="z", title="Zed")
    b = tagged(sample, name="y", title="Why")
    steps = renamer.plan([a, b], "%8 %2")
    assert [s.dst.name for s in steps] == ["01 Zed.mp3", "02 Why.mp3"]


def test_root_option_moves_files_into_a_library_tree(sample, tmp_path):
    t = tagged(sample, artist="Nirvana", album="Nevermind", title="Lithium")
    library = tmp_path / "Library"
    library.mkdir()
    steps = renamer.plan([t], "%1\\%3\\%2", root=library)
    renamer.apply(steps)
    assert (library / "Nirvana" / "Nevermind" / "Lithium.mp3").exists()


def test_case_option_applies_to_the_rendered_name(sample):
    from rifftag.casing import Case, CaseRules
    t = tagged(sample, artist="nirvana", title="lithium")
    opts = RenderOptions(case=CaseRules(mode=Case.TITLE))
    steps = renamer.plan([t], "%1 - %2", options=opts)
    assert steps[0].dst.name == "Nirvana - Lithium.mp3"


def test_renaming_preserves_the_tags(sample):
    t = tagged(sample, artist="Nirvana", title="Lithium", album="Nevermind")
    steps = renamer.plan([t], "%1 - %2")
    renamer.apply(steps)
    assert tagio.read(steps[0].dst).values["album"] == "Nevermind"


# --- folders ------------------------------------------------------------

def test_folder_rename_uses_tags_from_the_files_inside(sample, tmp_path):
    folder = tmp_path / "unsorted"
    folder.mkdir()
    t = tagged(sample, artist="Nirvana", album="Nevermind", year="1991")
    dest = folder / t.path.name
    t.path.rename(dest)
    t = tagio.read(dest)

    step = renamer.plan_folder(folder, [t], "%1 [%4] %3")
    assert step.dst.name == "Nirvana [1991] Nevermind"
    renamer.apply([step])
    assert step.dst.is_dir() and (step.dst / dest.name).exists()


def test_folder_mask_may_not_create_sub_folders(sample, tmp_path):
    """The original forbids "\\" in a rename action's folder mask."""
    folder = tmp_path / "x"
    folder.mkdir()
    t = tagged(sample, artist="Nirvana", album="Nevermind")
    step = renamer.plan_folder(folder, [t], "%1\\%3")
    assert step.status is Status.ERROR


def test_folder_rename_tolerates_one_untagged_file(sample, tmp_path):
    folder = tmp_path / "unsorted"
    folder.mkdir()
    good = tagged(sample, name="a", artist="Nirvana", album="Nevermind")
    good.path.rename(folder / "a.mp3")
    junk = folder / "broken.mp3"
    junk.write_bytes(b"not audio")

    tracks = [tagio.read(folder / "a.mp3"), tagio.read(junk)]
    step = renamer.plan_folder(folder, tracks, "%1 - %3")
    assert step.status is Status.OK
    assert step.dst.name == "Nirvana - Nevermind"


def test_a_case_only_change_is_never_called_unchanged():
    """Path equality ignores case on Windows, so `lithium.mp3` and
    `Lithium.mp3` compared equal there and the planner reported "unchanged" —
    the rename silently did nothing. Found by running the suite on Windows."""
    from rifftag.renamer import _is_same_path

    assert not _is_same_path(Path("/music/lithium.mp3"),
                             Path("/music/Lithium.mp3"))
    assert _is_same_path(Path("/music/lithium.mp3"),
                         Path("/music/lithium.mp3"))
