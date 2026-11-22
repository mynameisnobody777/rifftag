"""Startup splash, and the icons.

The splash is deliberately cheap: a pixmap, a status line, and both a floor
and a ceiling on how long it stays up.

The window builds in well under a second, so dismissing it the instant the
window is ready made it flash past unread. :data:`MIN_MS` holds it for long
enough to actually see; :data:`MAX_MS` is the backstop, because a splash that
outlives the thing it is covering is worse than none at all. The hold is done
with a timer, never a sleep — the splash has to keep painting while it waits.

Clicking it dismisses it, and Options → "Show splash screen on startup" turns
it off for good.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QElapsedTimer, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import QSplashScreen

def _find_assets() -> Path:
    """Where the artwork lives, running from the repo or from a package.

    A .deb puts the code in dist-packages and the artwork under
    /usr/share/rifftag, so "beside the package" only holds in the source tree.
    Candidates are tried in order and the first that actually contains the
    logo wins; the repo path is first so a developer always gets their own
    working copy rather than an installed one.
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "assets",                     # source checkout
        Path(sys.prefix) / "share" / "rifftag" / "assets",
        # Flatpak: the app lives in /app but runs the RUNTIME's python, so
        # sys.prefix is /usr and the candidate above never finds anything.
        Path("/app/share/rifftag/assets"),
        Path("/usr/share/rifftag/assets"),
        Path("/usr/local/share/rifftag/assets"),
        here.parent / "assets",                         # bundled alongside
    ]
    for path in candidates:
        if (path / "rifftag-logo.png").exists():
            return path
    return candidates[0]


#: assets/ sits beside the package in the source tree, under /usr/share once
#: installed. Resolved once at import.
ASSETS = _find_assets()
LOGO = ASSETS / "rifftag-logo.png"
LOGO_LIGHT = ASSETS / "rifftag-logo-light.png"
ICON = ASSETS / "rifftag-icon.png"
WORDMARK = ASSETS / "rifftag-wordmark.png"
EMBLEM = ASSETS / "rifftag-emblem.png"
#: Logo-05, cropped just below the art. Dave composed it with the mark in
#: the upper part of a square and clear space beneath for the transport,
#: so the asset's own aspect ratio is the art block: scale it to the
#: panel's width and the controls start exactly under the artwork.
PLAYER_LOGO = ASSETS / "rifftag-player-logo.png"

#: width the logo is scaled to; the artwork is square and far too big raw
SPLASH_WIDTH = 420
#: hold it at least this long, so it is readable rather than a flash
MIN_MS = 2000
#: never let the splash outlive the window it is covering
MAX_MS = 6000


def app_icon() -> QIcon:
    """The window/taskbar icon, at every size that was pre-rendered.

    Supplying each size explicitly beats letting Qt downscale one big pixmap —
    the emblem keeps its edges at 16 and 22 px, where a naive scale turns it
    to mush.
    """
    icon = QIcon()
    for path in sorted(ASSETS.glob("icon-*.png")):
        icon.addFile(str(path))
    if icon.isNull() and ICON.exists():
        icon.addFile(str(ICON))
    return icon


def wordmark(height: int, device_ratio: float = 1.0) -> QPixmap:
    """The RiffTag lettering, scaled to sit in a menu bar or a dialog.

    Scaled from the full-resolution art at request time rather than from a
    pre-baked small PNG, so it stays sharp on a HiDPI screen: the pixmap is
    rendered at ``height * device_ratio`` real pixels and then told what ratio
    it is, which is what stops Qt drawing a blurry upscale of a 28px image.

    The lettering carries its own dark outline, so it reads on a light
    background as well as a dark one — no per-theme variant needed.
    """
    if not WORDMARK.exists():
        return QPixmap()
    pixmap = QPixmap(str(WORDMARK))
    if pixmap.isNull():
        return pixmap
    ratio = max(1.0, float(device_ratio))
    scaled = pixmap.scaledToHeight(
        int(round(height * ratio)), Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled


def player_logo(width: int, device_ratio: float = 1.0) -> QPixmap:
    """The big transparent lockup for the player block.

    One asset for both themes: it is a cut-out with no backdrop, so there is
    nothing to clash with a light or a dark window — the same reasoning that
    settled the emblem and the wordmark.
    """
    if not PLAYER_LOGO.exists():
        return QPixmap()
    pixmap = QPixmap(str(PLAYER_LOGO))
    if pixmap.isNull():
        return pixmap
    ratio = max(1.0, float(device_ratio))
    scaled = pixmap.scaledToWidth(
        int(round(width * ratio)), Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled


def logo_path(dark: bool = True) -> Path:
    """The full lockup for the current theme.

    Only the About dialog shows it big enough for the difference to register:
    on dark the artwork's own near-black field reads as a card, while on light
    the cut-out version avoids a black slab. The splash always uses the dark
    one — it is its own window, and the dark treatment is the brand moment.

    The wordmark and the emblem deliberately have no per-theme variant. Both
    were compared against each background at the sizes they are actually used
    and the single asset won in each case: the wordmark's dark outline keeps
    it crisp on white, where the light-background variant's white glow dulls
    it at 20px, and the cut-out emblem simply has no backdrop to clash.
    """
    if not dark and LOGO_LIGHT.exists():
        return LOGO_LIGHT
    return LOGO


def emblem(height: int, device_ratio: float = 1.0) -> QPixmap:
    """The pick-and-tag mark on its own, for the empty-list watermark."""
    if not EMBLEM.exists():
        return QPixmap()
    pixmap = QPixmap(str(EMBLEM))
    if pixmap.isNull():
        return pixmap
    ratio = max(1.0, float(device_ratio))
    scaled = pixmap.scaledToHeight(
        int(round(height * ratio)), Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled


class Splash(QSplashScreen):
    def __init__(self, width: int = SPLASH_WIDTH):
        pixmap = QPixmap(str(LOGO)) if LOGO.exists() else QPixmap()
        if not pixmap.isNull():
            pixmap = pixmap.scaledToWidth(
                width, Qt.TransformationMode.SmoothTransformation)
        super().__init__(pixmap)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self._shown = QElapsedTimer()
        self._shown.start()
        self._guard = QTimer(self)
        self._guard.setSingleShot(True)
        self._guard.timeout.connect(self.close)
        self._guard.start(MAX_MS)

    def say(self, message: str) -> None:
        self.showMessage(
            message,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            QColor("#e8e8e8"))

    def mousePressEvent(self, event) -> None:
        """A click skips the wait entirely."""
        self._shown.invalidate()
        self.close()

    def remaining_ms(self) -> int:
        """How much longer it should stay up before the window appears."""
        if not self._shown.isValid():
            return 0
        return max(0, MIN_MS - int(self._shown.elapsed()))

    def finish_for(self, window) -> None:
        self._guard.stop()
        self.finish(window)


def show_splash(enabled: bool = True) -> Splash | None:
    """Return a visible splash, or None when it is turned off or has no art."""
    if not enabled or not LOGO.exists():
        return None
    splash = Splash()
    if splash.pixmap().isNull():
        return None
    splash.show()
    return splash
