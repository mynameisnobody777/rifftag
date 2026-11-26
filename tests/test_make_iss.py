"""The Inno Setup script that builds the Windows installer.

Generated, not hand-written, so the file-type list comes from the same place
as the Linux .desktop entries. These tests exist because the installer is
built on a machine none of this runs on — the only chance to catch a mistake
is here.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))

import make_iss                                                 # noqa: E402

from rifftag.desktopfile import extensions                      # noqa: E402


def _iss(version: str = "0.3") -> str:
    return make_iss.render(version, r"C:\RiffTag\dist\windows\RiffTag",
                           r"C:\RiffTag\dist", r"C:\RiffTag\assets\rifftag.ico",
                           r"C:\RiffTag\LICENSE")


def test_every_file_type_is_registered():
    """The same list the .desktop entries and the .ps1 use."""
    text = _iss()
    for ext in extensions():
        assert f'Software\\Classes\\{ext}\\OpenWithProgids' in text, ext


def test_the_app_id_is_the_one_windows_already_knows():
    """Changing it orphans the installed copy's uninstaller.

    An upgrade would install a second RiffTag beside the first, and the old
    entry in Add/Remove Programs would point at files that are gone.
    """
    assert make_iss.APP_ID == "{01FB28A0-3875-4216-8608-FA4ED33E7B54}"
    assert f"AppId={make_iss.APP_ID}" in _iss()


def test_the_uninstaller_is_what_this_was_for():
    text = _iss()
    assert "UninstallDisplayName=" in text
    assert "UninstallDisplayIcon=" in text
    assert "{uninstallexe}" in text, "no Start Menu uninstall entry"


def test_an_extension_key_is_not_deleted_on_uninstall():
    """`.mp3\\OpenWithProgids` belongs to the file type, not to RiffTag.

    Other programs keep their own values in it, so only ours may go —
    uninsdeletevalue, never uninsdeletekey.
    """
    for block in _iss().split("Root: HKA;"):
        if "OpenWithProgids" in block or "SupportedTypes" in block:
            assert "uninsdeletevalue" in block, block
            assert "uninsdeletekey" not in block, block


def test_it_does_not_seize_the_default_handler():
    """Installing a tag editor must not take the double-click from VLC."""
    text = _iss()
    assert "OpenWithProgids" in text
    for forbidden in ("Software\\Classes\\.mp3\"",
                      "HKCR", "DefaultProgram", "Software\\Classes\\.wav\";"):
        assert forbidden not in text, forbidden


def test_the_associations_are_an_optional_task():
    """A task, so the install can be declined without declining the app."""
    text = _iss()
    assert 'Name: "associate"' in text
    for line in text.splitlines():
        if line.startswith("Root: HKA;") or "ValueType: string" in line:
            continue
    assert text.count("Tasks: associate") >= len(extensions())


def test_it_installs_per_user_without_an_admin_prompt():
    text = _iss()
    assert "PrivilegesRequired=lowest" in text
    assert "PrivilegesRequiredOverridesAllowed=dialog" in text, \
        "a machine-wide install must still be possible"


def test_the_whole_pyinstaller_tree_is_copied():
    """Qt's plugins are several levels down; a flat copy makes a dead exe."""
    text = _iss()
    assert "recursesubdirs" in text
    assert "createallsubdirs" in text


def test_the_version_reaches_every_place_that_shows_it():
    text = _iss("9.9")
    assert "AppVersion=9.9" in text
    assert "OutputBaseFilename=RiffTag-9.9-Setup" in text
    assert "UninstallDisplayName={#MyAppName} 9.9" in text


def test_the_generated_script_has_no_unresolved_placeholders():
    """A stray {FOO} would be a format bug that only Inno Setup would catch."""
    text = _iss()
    stray = [m for m in re.findall(r"\{([A-Z_]{3,})\}", text)]
    assert not stray, stray


def test_writing_it_out_works(tmp_path):
    out = tmp_path / "nested" / "rifftag.iss"
    assert make_iss.main(["make_iss.py", str(out), "0.3"]) == 0
    assert out.read_text(encoding="utf-8").startswith("; GENERATED")
