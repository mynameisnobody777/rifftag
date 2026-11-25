"""Desktop and panel shortcuts, offered on first run.

A .deb cannot do this itself. Its ``postinst`` runs as **root**, without a
session, and often with no GUI at all — it does not reliably know which human
is installing, cannot write to their Desktop with the right ownership, and
certainly cannot touch their panel, which is per-user session state. So the
package installs the menu entry (which *is* system-wide and correct) and the
app offers the rest the first time it runs, as the user, with a real dialog.

The panel half is Cinnamon-specific and deliberately cautious. Adding a
launcher by dropping it onto an existing one **replaces** that entry — which
is exactly how a Plex button got overwritten during development. Nothing here
ever replaces: it appends if absent, backs the file up first, and if there is
no launcher applet at all it adds one at the *end* of the zone so no existing
applet has to be renumbered.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel,
                             QVBoxLayout)

from ..desktopfile import desktop_entry_text as build_entry

ENTRY = "rifftag.desktop"
APPLET = "panel-launchers@cinnamon.org"
APPLET_DIR = Path.home() / ".config/cinnamon/spices" / APPLET


def exec_line() -> str:
    """How to launch this copy.

    An installed package has ``rifftag`` on PATH; a source checkout does not,
    so the entry has to carry the PYTHONPATH that makes it importable.
    ``%F`` on the end is what hands the clicked file to ``rifftag/cli.py``.
    """
    if shutil.which("rifftag"):
        return "rifftag %F"
    root = Path(__file__).resolve().parents[2]
    return f"env PYTHONPATH={root} python3 -m rifftag %F"


def desktop_entry_text() -> str:
    """The .desktop body, pointing at however this copy is being run.

    The MIME list comes from ``rifftag/desktopfile.py`` so that this entry,
    the packaged one and the Flatpak one cannot disagree — which they did,
    and which is why "Open with" had no RiffTag in it for a .wav.
    """
    return build_entry(exec_line())


def desktop_dir() -> Path:
    """The user's Desktop, which is localised and not always ~/Desktop."""
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        if out:
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Desktop"


def user_entry_path() -> Path:
    return Path.home() / ".local/share/applications" / ENTRY


def system_entry_path() -> Path:
    return Path("/usr/share/applications") / ENTRY


def ensure_user_entry() -> Path:
    """Make sure some entry exists to point a shortcut at."""
    if shutil.which("rifftag") and system_entry_path().exists():
        return system_entry_path()
    target = user_entry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(desktop_entry_text())
    refresh_desktop_database()
    return target


def refresh_desktop_database() -> None:
    """Rebuild the user's application cache. Failure is not interesting.

    Without this the "Open with" list keeps whatever it cached until the next
    sign-in, which makes a fixed association look like it is still broken.
    """
    if not shutil.which("update-desktop-database"):
        return
    try:
        subprocess.run(["update-desktop-database",
                        str(user_entry_path().parent)],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def repair_shadowed_entry() -> str | None:
    """Deal with a user entry that is hiding the packaged one.

    This is a real repair, not a hypothetical. A desktop entry in
    ``~/.local/share/applications`` **completely replaces** the one in
    ``/usr/share/applications`` with the same file name — it does not merge
    with it. Dave ran RiffTag from a source checkout first, which wrote a
    user entry; that version had no ``MimeType`` line at all, so once the
    .deb was installed the packaged entry's associations never took effect
    and no amount of fixing the package could make "Open with" work.

    Only ever touches a file that is recognisably ours. Returns a sentence
    for the log, or None if there was nothing to do.
    """
    user, system = user_entry_path(), system_entry_path()
    if not system.exists() or not user.exists():
        return None
    try:
        body = user.read_text()
    except OSError:
        return None
    if "Name=RiffTag" not in body:
        return None                 # somebody else's file; leave it alone
    if body == system.read_text():
        return None                 # a duplicate, but a harmless one
    try:
        # Kept, not deleted: it costs nothing and it is the user's file.
        user.with_suffix(".desktop.bak").write_text(body)
        user.unlink()
    except OSError as exc:
        return f"Could not remove the stale entry at {user}: {exc}"
    refresh_desktop_database()
    return (f"Removed a stale {ENTRY} from {user.parent} that was hiding the "
            f"installed one (backed up alongside it).")


def install_desktop_shortcut() -> tuple[bool, str]:
    folder = desktop_dir()
    if not folder.is_dir():
        return False, f"No Desktop folder found at {folder}."
    target = folder / ENTRY
    try:
        target.write_text(desktop_entry_text())
        target.chmod(0o755)
        # Cinnamon and GNOME show "Untrusted application launcher" without this.
        subprocess.run(["gio", "set", str(target), "metadata::trusted", "true"],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not write the shortcut: {exc}"
    return True, f"Desktop shortcut created in {folder}."


# --- Cinnamon panel -----------------------------------------------------

def cinnamon_available() -> bool:
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "")
               + os.environ.get("DESKTOP_SESSION", "")).lower()
    return "cinnamon" in desktop and bool(shutil.which("gsettings"))


def _applets() -> list[str]:
    out = subprocess.run(["gsettings", "get", "org.cinnamon", "enabled-applets"],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    return list(ast.literal_eval(out))


def _backup(path: Path) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    shutil.copy2(path, path.with_suffix(path.suffix + f".bak-{stamp}"))


def install_panel_launcher() -> tuple[bool, str]:
    """Append RiffTag to a Cinnamon panel launcher group.

    Never replaces an existing launcher, and never renumbers an existing
    applet — both are how a panel gets quietly rearranged.
    """
    if not cinnamon_available():
        return False, "This only works on the Cinnamon desktop."
    ensure_user_entry()
    try:
        applets = _applets()
    except Exception as exc:
        return False, f"Could not read the panel configuration: {exc}"

    launchers = [a for a in applets if a.split(":", 3)[3].startswith(APPLET)]
    if launchers:
        instance = launchers[0].rsplit(":", 1)[1]
        config = APPLET_DIR / f"{instance}.json"
        if not config.exists():
            return False, f"Panel launcher settings not found ({config})."
        try:
            data = json.loads(config.read_text())
            current = list(data["launcherList"]["value"])
            if ENTRY in current:
                return True, "RiffTag is already on the panel."
            _backup(config)
            data["launcherList"]["value"] = current + [ENTRY]
            config.write_text(json.dumps(data, indent=4))
        except (OSError, ValueError, KeyError) as exc:
            return False, f"Could not update the panel launchers: {exc}"
        return True, ("Added to the panel.\n"
                      "Press Ctrl+Alt+Esc to restart Cinnamon and see it.")

    # No launcher group yet: add one at the END of the left zone, so no
    # existing applet's position has to change.
    used = {int(a.rsplit(":", 1)[1]) for a in applets}
    new_id = max(used) + 1 if used else 1
    left = [a for a in applets if a.startswith("panel1:left:")]
    order = max((int(a.split(":")[2]) for a in left), default=-1) + 1

    APPLET_DIR.mkdir(parents=True, exist_ok=True)
    (APPLET_DIR / f"{new_id}.json").write_text(json.dumps({
        "launcherList": {"type": "generic", "default": [], "value": [ENTRY]},
        "allow-dragging": {"type": "switch", "default": True,
                           "description": "Allow dragging of launchers",
                           "value": True},
    }, indent=4))
    updated = applets + [f"panel1:left:{order}:{APPLET}:{new_id}"]
    try:
        subprocess.run(["gsettings", "set", "org.cinnamon", "enabled-applets",
                        "[" + ", ".join(f"'{a}'" for a in updated) + "]"],
                       check=True, timeout=10)
    except subprocess.SubprocessError as exc:
        return False, f"Could not add the panel launcher: {exc}"
    return True, ("Added a launcher to the panel.\n"
                  "Press Ctrl+Alt+Esc to restart Cinnamon and see it.")


class ShortcutsDialog(QDialog):
    """Offered on first run, and from Options at any time."""

    def __init__(self, parent=None, *, first_run: bool = False):
        super().__init__(parent)
        self.setWindowTitle("RiffTag shortcuts")
        self.results: list[str] = []

        intro = QLabel(
            "Where would you like to start RiffTag from?\n"
            "It is already in your applications menu."
            if first_run else
            "Add RiffTag to your desktop or panel.")
        intro.setWordWrap(True)

        self.want_desktop = QCheckBox("Desktop shortcut")
        self.want_desktop.setChecked(first_run)
        self.want_panel = QCheckBox("Panel shortcut (next to the menu)")
        self.want_panel.setChecked(False)
        if not cinnamon_available():
            self.want_panel.setEnabled(False)
            self.want_panel.setToolTip("Only available on the Cinnamon desktop.")
            self.want_panel.setText("Panel shortcut (Cinnamon only)")

        self.again = QCheckBox("Don't ask again")
        self.again.setChecked(True)
        self.again.setVisible(first_run)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addSpacing(6)
        layout.addWidget(self.want_desktop)
        layout.addWidget(self.want_panel)
        layout.addSpacing(6)
        layout.addWidget(self.again)
        layout.addWidget(buttons)

    def _apply(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        messages, problems = [], []
        if self.want_desktop.isChecked():
            ok, msg = install_desktop_shortcut()
            (messages if ok else problems).append(msg)
        if self.want_panel.isChecked() and self.want_panel.isEnabled():
            ok, msg = install_panel_launcher()
            (messages if ok else problems).append(msg)
        self.results = messages + problems
        if problems:
            QMessageBox.warning(self, "RiffTag shortcuts", "\n\n".join(self.results))
        elif messages:
            QMessageBox.information(self, "RiffTag shortcuts",
                                    "\n\n".join(messages))
        self.accept()
