"""Dropping pictures onto the cover-art panels.

Three sources have to work, because all three are how people actually move a
picture around: a file dragged from the file manager, an image dragged
straight out of a browser or image viewer (no file involved — the bytes come
in on the clipboard), and a plain file path as text.

**JPEG and PNG pass through byte for byte.** Re-encoding a cover someone
already exported at 3000×3000 would cost quality for nothing. Anything else —
WebP, BMP, GIF, TIFF — is converted to JPEG, because that is what tag readers
and stores reliably understand.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPalette
from PyQt6.QtWidgets import QLabel

#: What a drop is allowed to carry.
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif",
                  ".tif", ".tiff")
#: Kept as-is rather than re-encoded.
PASS_THROUGH = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png"}
#: Quality for the formats that have to be converted.
JPEG_QUALITY = 92


class ArtError(Exception):
    pass


def to_jpeg(image: QImage) -> bytes:
    """Encode a QImage as JPEG. Raises ArtError rather than returning junk."""
    if image.isNull():
        raise ArtError("that image could not be read")
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "JPEG", JPEG_QUALITY):
        raise ArtError("that image could not be converted to JPEG")
    return bytes(buffer.data())


def from_path(path) -> tuple[bytes, str]:
    """Read a picture file. Returns (data, mime type)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ArtError(f"{path.name} is not a picture file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ArtError(f"{path.name}: {exc}") from exc
    if suffix in PASS_THROUGH:
        # Byte for byte: a 3000x3000 cover must not lose a generation to a
        # re-encode it never needed.
        return raw, PASS_THROUGH[suffix]
    return to_jpeg(QImage.fromData(raw)), "image/jpeg"


def from_mime(mime) -> tuple[bytes, str]:
    """Pull a picture out of dropped mime data, whatever shape it arrives in."""
    if mime.hasUrls():
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.lower() in IMAGE_SUFFIXES:
                return from_path(path)
        raise ArtError("none of those files are pictures")
    if mime.hasImage():
        image = QImage(mime.imageData())
        return to_jpeg(image), "image/jpeg"
    if mime.hasText():
        candidate = Path(mime.text().strip().replace("file://", ""))
        if candidate.is_file():
            return from_path(candidate)
    raise ArtError("nothing dropped that looks like a picture")


def carries_image(mime) -> bool:
    """Cheap test for dragEnter — does not read the file."""
    if mime.hasImage():
        return True
    if mime.hasUrls():
        return any(url.isLocalFile()
                   and Path(url.toLocalFile()).suffix.lower() in IMAGE_SUFFIXES
                   for url in mime.urls())
    if mime.hasText():
        text = mime.text().strip().replace("file://", "")
        return Path(text).suffix.lower() in IMAGE_SUFFIXES
    return False


def as_jpeg(data: bytes, mime: str) -> bytes:
    """JPEG bytes, converting only when the source is not already JPEG.

    Used for folder.jpg, where the file name is the convention the whole app
    looks for — writing folder.png beside an existing folder.jpg would leave
    the old one winning the lookup.
    """
    if mime == "image/jpeg":
        return data
    return to_jpeg(QImage.fromData(data))


class ArtDropLabel(QLabel):
    """A cover-art box you can drop a picture onto.

    The highlight is done with the widget's own palette, never a stylesheet:
    a stylesheet switches the widget to QStyleSheetStyle, which caches the
    colours it resolved at parse time and would leave this box showing the
    previous theme after a switch.
    """

    art_dropped = pyqtSignal(bytes, str)     # data, mime type
    drop_failed = pyqtSignal(str)

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAcceptDrops(True)
        self._lit = False

    # --- drag and drop ---

    def dragEnterEvent(self, event) -> None:
        if carries_image(event.mimeData()):
            event.acceptProposedAction()
            self._light(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._light(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._light(False)
        try:
            data, mime = from_mime(event.mimeData())
        except ArtError as exc:
            self.drop_failed.emit(str(exc))
            event.ignore()
            return
        event.acceptProposedAction()
        self.art_dropped.emit(data, mime)

    def _light(self, on: bool) -> None:
        if on == self._lit:
            return
        self._lit = on
        palette = QPalette(self.palette())
        if on:
            palette.setColor(QPalette.ColorRole.Window,
                             palette.color(QPalette.ColorRole.Highlight))
        else:
            palette.setColor(QPalette.ColorRole.Window,
                             QPalette().color(QPalette.ColorRole.Window))
        self.setPalette(palette)
        self.setAutoFillBackground(on)
        self.update()
