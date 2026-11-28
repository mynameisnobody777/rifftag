"""About RiffTag.

The full logo, what the app is, and — more usefully than a credits list — the
format table, so "can it do .wv?" is answerable without leaving the program.

This text is the one place a user reads, so it describes RiffTag on its own
terms and names no other product. The lineage is recorded in the source
comments and the README, which is where a maintainer looks; it does not belong
in a dialog Dave may hand to someone else.
"""

from __future__ import annotations

from collections import defaultdict

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QTextBrowser,
                             QVBoxLayout)

from ..tagio import EXT_FAMILY
from ..webimport import __version__ as _web_version
from .splash import logo_path
from .theme import is_dark

VERSION = "0.6"
TAGLINE = "Edit. Rename. Organize."

#: Where "support RiffTag" points. One link, in this dialog, and nowhere
#: else — no startup prompt, no timed reminder, no counter that decides you
#: owe something. A tool that nags is a tool people stop opening.
SUPPORT_URL = "https://ko-fi.com/nobody777thankyou"
LICENCE = "GPL-3.0-or-later"

FAMILY_NAMES = {
    "id3": "ID3v2.3 / ID3v2.4",
    "mp4": "iTunes (MP4)",
    "vorbis": "Vorbis comments",
    "ape": "APEv2",
    "asf": "Windows Media",
    "matroska": "Matroska video &amp; audio (needs MKVToolNix)",
}


def format_table_html() -> str:
    by_family: dict[str, list[str]] = defaultdict(list)
    for ext, family in sorted(EXT_FAMILY.items()):
        by_family[family].append(ext)
    rows = "".join(
        f"<tr><td><b>{FAMILY_NAMES.get(family, family)}</b></td>"
        f"<td>{' '.join(exts)}</td></tr>"
        for family, exts in by_family.items())
    return f"<table cellspacing='6'>{rows}</table>"


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About RiffTag")

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        path = logo_path(dark=is_dark())
        pixmap = QPixmap(str(path)) if path.exists() else QPixmap()
        if not pixmap.isNull():
            ratio = max(1.0, float(self.devicePixelRatioF()))
            scaled = pixmap.scaledToWidth(
                int(260 * ratio), Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            logo.setPixmap(scaled)

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setHtml(f"""
            <p style='text-align:center'><b>RiffTag {VERSION}</b><br>
            {TAGLINE}</p>
            <p>A tag editor and organiser for Linux and Windows: rename files
            and folders from their tags, read tags back out of file names,
            edit whole albums at once, and pull album details from the web.</p>
            <p>Music first, and video too: Matroska files (<b>.mkv</b>,
            <b>.mka</b>, <b>.webm</b>) get their titles, artists, years and
            cover art edited in place, without re-encoding or remuxing.</p>
            <p><b>Supported formats</b></p>
            {format_table_html()}
            <p><b>Online lookup</b>: MusicBrainz (no account needed) and
            Discogs (free personal token) for albums;
            <a href='https://www.themoviedb.org/'>TMDB</a> (free API key) for
            movies and TV episodes. freedb, which the original used, shut down
            in 2020.</p>
            <p style='font-size:small'>This product uses the TMDB API but is
            not endorsed or certified by TMDB.</p>
            <p>Tag reading and writing by
            <a href='https://mutagen.readthedocs.io/'>mutagen</a>, and by
            <a href='https://mkvtoolnix.download/'>MKVToolNix</a> for Matroska;
            interface by PyQt6.</p>
            <p><b>Free software</b>, under the {LICENCE}. You may use, study,
            change and share it; anything you pass on stays free too.</p>
            <p>RiffTag is free and always will be. If it saved you an evening,
            you can <a href='{SUPPORT_URL}'>buy me a coffee</a> — entirely
            optional, and it will never ask you again.</p>
        """)
        # Tall enough that the whole format table shows without scrolling: the
        # Matroska row sat exactly on the fold and Dave missed it (2026-11-28).
        body.setMinimumSize(460, 470)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(logo)
        layout.addWidget(body)
        layout.addWidget(buttons)
