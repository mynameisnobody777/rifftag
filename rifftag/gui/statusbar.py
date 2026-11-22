"""The totals strip along the bottom of the window.

Tag&Rename keeps a running count of what is listed and what is selected at the
bottom of its window. Those numbers are how you confirm a selection *before* a
write touches the disk, so they are permanent widgets rather than a transient
status message that the next ``showMessage()`` would wipe.

Sizes are stat'd once per file and cached: ``set_selected`` runs on every
click, and re-stat'ing a few thousand files for each arrow-key press is the
kind of thing that makes a list feel sticky.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


def format_duration(seconds: float) -> str:
    """hh:mm:ss — always with the hours, so the panes stay the same width."""
    total = int(round(seconds or 0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_size(nbytes: float) -> str:
    n = float(nbytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class StatusTotals(QWidget):
    """Total files / selected files / total time / selected time / size."""

    #: pane key -> label prefix, in display order
    PANES = (
        ("total_files", "Total files"),
        ("selected_files", "Selected files"),
        ("total_time", "Total time"),
        ("selected_time", "Selected time"),
        ("total_size", "Total size"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.panes: dict[str, QLabel] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for key, prefix in self.PANES:
            label = QLabel()
            # A frame, never a stylesheet: a stylesheet would freeze this
            # widget's colours at the theme in force when it was parsed.
            label.setFrameShape(QFrame.Shape.StyledPanel)
            label.setFrameShadow(QFrame.Shadow.Sunken)
            label.setMinimumWidth(118)
            label.setToolTip(prefix)
            lay.addWidget(label)
            self.panes[key] = label

        self._size_cache: dict[Path, int] = {}
        self.set_listed([])
        self.set_selected([])

    # --- updates ---

    def set_listed(self, tracks) -> None:
        """Call whenever the set of rows on screen changes."""
        tracks = list(tracks)
        self._set("total_files", "Total files", len(tracks))
        self._set("total_time", "Total time",
                  format_duration(sum(t.info.length for t in tracks)))
        self._set("total_size", "Total size",
                  format_size(sum(self._size(t.path) for t in tracks)))

    def set_selected(self, tracks) -> None:
        tracks = list(tracks)
        self._set("selected_files", "Selected files", len(tracks))
        self._set("selected_time", "Selected time",
                  format_duration(sum(t.info.length for t in tracks)))

    def _set(self, key: str, prefix: str, value) -> None:
        self.panes[key].setText(f"{prefix}: {value}")

    def _size(self, path: Path) -> int:
        cached = self._size_cache.get(path)
        if cached is None:
            try:
                cached = path.stat().st_size
            except OSError:
                cached = 0
            self._size_cache[path] = cached
        return cached

    def forget_sizes(self) -> None:
        """Drop the stat cache — after a rename or a write changed the files."""
        self._size_cache.clear()
