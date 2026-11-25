"""Light / dark / follow-the-desktop theming.

Everything else in the GUI derives its colours from the active ``QPalette`` —
row tints, zebra stripes, the legend swatches — so switching themes is a
matter of swapping one palette and telling the views to re-derive.

"System" means "whatever the desktop handed us at startup", which is captured
once before any theme is applied; there is no way to ask Qt for it again after
``setPalette`` has overridden it.

That capture is *not* trusted on its own to decide light vs dark. See
``desktop_prefers_dark``: Qt's startup palette is wrong often enough — a
Windows Fusion palette is always light, and on Linux the palette only goes
dark if the XDG settings portal answered over D-Bus in time — that "Follow
desktop" came up light on a dark desktop. The preference is read directly
instead, and a palette that disagrees with it is replaced with ours.
"""

from __future__ import annotations

from enum import Enum

from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication


class Theme(Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @property
    def label(self) -> str:
        return {"system": "Follow desktop", "light": "Light",
                "dark": "Dark"}[self.value]


#: The desktop's own palette, captured before we touch anything.
_system_palette: QPalette | None = None

_LIGHT = {
    QPalette.ColorRole.Window: "#efefef",
    QPalette.ColorRole.WindowText: "#1b1b1b",
    QPalette.ColorRole.Base: "#ffffff",
    QPalette.ColorRole.AlternateBase: "#f3f3f3",
    QPalette.ColorRole.Text: "#1b1b1b",
    QPalette.ColorRole.Button: "#efefef",
    QPalette.ColorRole.ButtonText: "#1b1b1b",
    QPalette.ColorRole.ToolTipBase: "#ffffdc",
    QPalette.ColorRole.ToolTipText: "#1b1b1b",
    QPalette.ColorRole.Highlight: "#3584e4",
    QPalette.ColorRole.HighlightedText: "#ffffff",
    QPalette.ColorRole.PlaceholderText: "#8a8a8a",
    QPalette.ColorRole.Link: "#1a5fb4",
    QPalette.ColorRole.Mid: "#c0c0c0",
}

_DARK = {
    QPalette.ColorRole.Window: "#353535",
    QPalette.ColorRole.WindowText: "#dcdcdc",
    QPalette.ColorRole.Base: "#232323",
    QPalette.ColorRole.AlternateBase: "#2b2b2b",
    QPalette.ColorRole.Text: "#dcdcdc",
    QPalette.ColorRole.Button: "#353535",
    QPalette.ColorRole.ButtonText: "#dcdcdc",
    QPalette.ColorRole.ToolTipBase: "#3a3a3a",
    QPalette.ColorRole.ToolTipText: "#dcdcdc",
    QPalette.ColorRole.Highlight: "#3d6ea5",
    QPalette.ColorRole.HighlightedText: "#ffffff",
    QPalette.ColorRole.PlaceholderText: "#8a8a8a",
    QPalette.ColorRole.Link: "#7bb2e8",
    QPalette.ColorRole.Mid: "#5a5a5a",
}


def themed_icon(*names: str, fallback=None) -> QIcon:
    """First icon name the desktop theme actually has, else a standard one.

    Icon sets differ wildly — Dave's Mint-L-Yellow has no ``edit-select-none``
    and no ``format-number`` — so every call site passes several names.

    ``fallback`` is a ``QStyle.StandardPixmap``, and it is what makes the
    toolbar presentable on **Windows**, where ``QIcon.fromTheme`` finds
    nothing at all: the first build came up with half the strip rendered as
    text buttons, which blew the width out into an overflow menu and hid real
    tools. Qt carries its own standard set on every platform, so there is
    always something to draw.
    """
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    if fallback is not None:
        app = QApplication.instance()
        if app is not None:
            icon = app.style().standardIcon(fallback)
            if not icon.isNull():
                return icon
    return QIcon()


def use_portable_style(app: QApplication | None = None,
                       platform: str | None = None) -> bool:
    """On Windows, switch to Fusion. Returns whether it was applied.

    The native windows11 style draws text fields with its own notion of light
    and dark and only partly consults the QPalette — which on a dark Windows
    11 came out as dark grey text on a dark grey line edit, with the rename
    mask and the result preview both illegible. Fusion honours the palette
    completely, which is the whole reason it exists, and is what the rest of
    this module assumes: every colour in the app is derived from the palette.

    Left alone everywhere else: on Linux the native style is already
    palette-driven and matches the rest of Dave's desktop.
    """
    import sys as _sys

    app = app or QApplication.instance()
    if app is None:
        return False
    if not (platform or _sys.platform).startswith("win"):
        return False
    app.setStyle("Fusion")
    return True


def capture_system_palette(app: QApplication | None = None) -> None:
    """Remember the desktop's palette. Call once, before applying a theme."""
    global _system_palette
    if _system_palette is None:
        app = app or QApplication.instance()
        if app is not None:
            _system_palette = QPalette(app.palette())


def _build(spec: dict) -> QPalette:
    palette = QPalette()
    for role, colour in spec.items():
        palette.setColor(role, QColor(colour))
    # Greyed-out controls must not stay fully legible, or a disabled button
    # reads as an enabled one.
    disabled = QColor(spec[QPalette.ColorRole.Text])
    disabled.setAlpha(140)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


#: Overrides the detected preference. Tests set it; nothing else should.
_forced_preference: bool | None = None


def _qt_colour_scheme() -> bool | None:
    """Qt's own answer, on the Qt versions that have one (6.5+).

    This is the right answer where it exists — Qt asks the platform, which on
    Windows means the real dark-mode flag and on Linux the settings portal.
    Qt 6.4 (what Mint 22 ships) has no ``colorScheme`` at all, which is why
    everything below this exists.
    """
    app = QApplication.instance()
    if app is None:
        return None
    try:
        scheme = app.styleHints().colorScheme()
    except AttributeError:
        return None
    name = getattr(scheme, "name", "")
    if name == "Dark":
        return True
    if name == "Light":
        return False
    return None                                     # Unknown


def _windows_prefers_dark() -> bool | None:
    """Windows' own flag. ``AppsUseLightTheme`` 0 means dark.

    Needed because the app runs under Fusion on Windows (see
    ``use_portable_style``) and **Fusion's palette is light, always** — it is
    a self-contained style, not a native one, so no amount of staring at the
    captured palette will ever reveal that the desktop is dark.
    """
    try:
        import winreg
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        with key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except (OSError, ValueError, TypeError):
        return None


def _portal_prefers_dark() -> bool | None:
    """The XDG settings portal: 1 = prefer dark, 2 = prefer light, 0 = no idea.

    This is the same source Qt 6.5+ uses, reached over the session bus. It is
    unavailable in exactly the case that made the bug visible — a process
    started without ``DBUS_SESSION_BUS_ADDRESS`` — so it is a first choice,
    not the only one.
    """
    try:
        from PyQt6.QtDBus import QDBusConnection, QDBusInterface
    except ImportError:
        return None
    try:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return None
        iface = QDBusInterface("org.freedesktop.portal.Desktop",
                               "/org/freedesktop/portal/desktop",
                               "org.freedesktop.portal.Settings", bus)
        if not iface.isValid():
            return None
        reply = iface.call("Read", "org.freedesktop.appearance",
                           "color-scheme")
        return _unwrap_scheme(reply.arguments()[0] if reply.arguments()
                              else None)
    except Exception:                               # pragma: no cover - D-Bus
        return None


def _unwrap_scheme(value) -> bool | None:
    """Dig an int out of the portal's reply, which is a variant in a variant."""
    for _ in range(4):
        if isinstance(value, int) and not isinstance(value, bool):
            return {1: True, 2: False}.get(value)
        inner = getattr(value, "value", None)
        if inner is None or inner is value:
            return None
        value = inner() if callable(inner) else inner
    return None


def _gsettings(schema: str, key: str) -> str | None:
    """One gsettings value, or None. Never raises, never blocks for long.

    Reached by subprocess rather than by importing Gio: this app has no GTK
    dependency and is not about to grow one for a colour. dconf reads the
    user's database file directly when there is no session bus, so unlike the
    portal this still answers in a stripped environment.
    """
    import shutil
    import subprocess
    if not shutil.which("gsettings"):
        return None
    try:
        out = subprocess.run(["gsettings", "get", schema, key],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip().strip("'\"")


def _gtk_prefers_dark() -> bool | None:
    """The desktop's own GTK settings, in the order they are authoritative.

    ``color-scheme`` is the explicit preference, but only two of its three
    values say anything: Mint leaves it at ``default`` while running a dark
    theme, so the theme *name* has to be consulted as well. Dave's is
    ``Mint-L-Darker-Aqua`` — which is why the match is a substring and not an
    exact ``-dark`` suffix.
    """
    scheme = _gsettings("org.gnome.desktop.interface", "color-scheme")
    if scheme == "prefer-dark":
        return True
    if scheme == "prefer-light":
        return False

    import os
    names = [_gsettings("org.gnome.desktop.interface", "gtk-theme"),
             _gsettings("org.cinnamon.desktop.interface", "gtk-theme"),
             os.environ.get("GTK_THEME")]
    for name in names:
        if name and "dark" in name.lower():
            return True
    if any(names):
        return False
    return None


def desktop_prefers_dark() -> bool | None:
    """True/False if the desktop says so, None if nothing could be asked.

    Order matters: Qt first where it knows, then the platform's own flag,
    then the portal, then GTK. Each step is allowed to say "no idea" and hand
    on; None at the end means fall back to whatever palette Qt gave us.
    """
    if _forced_preference is not None:
        return _forced_preference
    import sys as _sys
    for source in ((_qt_colour_scheme, _windows_prefers_dark)
                   if _sys.platform.startswith("win")
                   else (_qt_colour_scheme, _portal_prefers_dark,
                         _gtk_prefers_dark)):
        answer = source()
        if answer is not None:
            return answer
    return None


def system_palette() -> QPalette:
    """The desktop's palette, corrected if it disagrees with the desktop.

    The captured palette is preferred when it is already the right polarity,
    because it carries the desktop's real colours — Dave's accent blue, his
    exact greys. It is only replaced when it would show a light window on a
    dark desktop or the reverse, which is the whole of this bug.
    """
    captured = QPalette(_system_palette) if _system_palette else QPalette()
    wants_dark = desktop_prefers_dark()
    if wants_dark is None or wants_dark == is_dark(captured):
        return captured
    return _build(_DARK if wants_dark else _LIGHT)


def palette_for(theme: Theme) -> QPalette:
    if theme is Theme.LIGHT:
        return _build(_LIGHT)
    if theme is Theme.DARK:
        return _build(_DARK)
    return system_palette()


def apply(theme: Theme, app: QApplication | None = None) -> None:
    app = app or QApplication.instance()
    if app is None:
        return
    capture_system_palette(app)
    app.setPalette(palette_for(theme))


def is_dark(palette: QPalette | None = None) -> bool:
    if palette is None:
        app = QApplication.instance()
        palette = app.palette() if app else QPalette()
    return palette.color(QPalette.ColorRole.Base).lightness() < 128
