"""Light / dark / follow-the-desktop theming.

Everything else in the GUI derives its colours from the active ``QPalette`` —
row tints, zebra stripes, the legend swatches — so switching themes is a
matter of swapping one palette and telling the views to re-derive.

"System" means "whatever the desktop handed us at startup", which is captured
once before any theme is applied; there is no way to ask Qt for it again after
``setPalette`` has overridden it.
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


def palette_for(theme: Theme) -> QPalette:
    if theme is Theme.LIGHT:
        return _build(_LIGHT)
    if theme is Theme.DARK:
        return _build(_DARK)
    return QPalette(_system_palette) if _system_palette else QPalette()


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
