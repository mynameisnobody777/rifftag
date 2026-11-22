"""The file list: a tree of folders with their tracks underneath.

This is the part of Tag&Rename its own overview calls "a unique file list which
show all your files in a real folder structure" — a grid you can sort and edit
like a spreadsheet, that still groups by folder and can recurse into
subfolders. Flat-list taggers lose that, so it is worth the custom model.

Row colouring follows the original's legend (help screenshot ``18.png``):
green when both an ID3v1 and an ID3v2 tag are present, pink when one is
missing, and a separate marker column for missing cover art.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import (QAbstractItemModel, QModelIndex, QObject, QRect, Qt,
                          QThread, pyqtSignal)
from PyQt6.QtGui import QBrush, QColor, QPainter, QPalette
from PyQt6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QStyle,
                             QStyleOptionViewItem, QStyledItemDelegate,
                             QTreeView, QVBoxLayout, QWidget)

from ..fields import BY_KEY
from ..tagio import AUDIO_EXTENSIONS, Track, read
from .statusbar import format_size as _fmt_size


class RowState(Enum):
    """What a row's colour is telling you.

    Colouring used to be driven by *field completeness*, which tinted almost
    every row red — one missing Year was enough — and turned the whole list
    maroon. Tag presence is both what the original signals and what leaves a
    neutral striped background to read against: a tinted row now means "this
    one needs you", and an ordinary tagged file is left alone.
    """

    NORMAL = "normal"
    NO_TAG = "no_tag"
    UNREADABLE = "unreadable"


#: Hue per state. Only hues — the background is this blended into the theme's
#: own Base colour, never used raw.
HUES = {
    RowState.NO_TAG: QColor(225, 70, 80),        # red
    RowState.UNREADABLE: QColor(235, 165, 30),   # amber
}
LEGEND = {
    RowState.NORMAL: "Tagged",
    RowState.NO_TAG: "No tag",
    RowState.UNREADABLE: "Unreadable",
}

#: How far to pull Base toward the hue. Enough to read across the row, little
#: enough that the theme's own Text colour still contrasts with the result.
TINT_STRENGTH = 0.22
#: Unreadable files get a stronger tint: amber and red sit close enough in hue
#: that at equal strength a broken file looks like a merely untagged one, and
#: this is the row you most need to notice.
TINT_STRENGTH_ERROR = 0.38


def _blend(base: QColor, hue: QColor, amount: float) -> QColor:
    return QColor(
        round(base.red() + (hue.red() - base.red()) * amount),
        round(base.green() + (hue.green() - base.green()) * amount),
        round(base.blue() + (hue.blue() - base.blue()) * amount),
    )


#: how far the stripe is pulled from Base toward Text
ZEBRA_STRENGTH = 0.055

#: How far the tag half of a row is pulled from its own background toward
#: Text. The original divides File Name from the ID3 columns with a block of
#: shading, which is what lets you find "where the tags start" without
#: counting headers. Deliberately NOT equal to ZEBRA_STRENGTH: at the same
#: value a striped row's name cell and a plain row's tag cell come out
#: identical and the vertical division stops reading.
SECTION_STRENGTH = 0.105

#: Extra pixels per row in the file list. The grid is deliberately dense —
#: fitting an album on screen is the point — but at the style's own row
#: height the lines ran together and were tiring to read. Four pixels is
#: enough to separate them without costing a row on a typical album.
ROW_PADDING = 4


def apply_zebra(view) -> None:
    """Turn on alternating row colours with a *subtle* stripe.

    Qt's own AlternateBase is often far too strong on a dark theme — or
    invisible. Deriving the stripe from Base keeps it at "just enough to track
    a row across the table" in any theme.
    """
    # Start from the APPLICATION palette, never the widget's own. Once this
    # function has run, the widget carries an overridden palette; re-deriving
    # from that after a theme switch blends the new stripe out of the OLD
    # Base, which turned every other row solid black in light mode.
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    palette = QPalette(app.palette() if app else view.palette())
    base = palette.color(QPalette.ColorRole.Base)
    text = palette.color(QPalette.ColorRole.Text)
    palette.setColor(QPalette.ColorRole.AlternateBase,
                     _blend(base, text, ZEBRA_STRENGTH))
    view.setPalette(palette)
    view.setAlternatingRowColors(True)


class RowPalette:
    """Row tints derived from the live theme.

    Hardcoded pastels were unreadable on Dave's dark theme: pale green behind
    the theme's near-white Text is almost no contrast at all. Deriving every
    tint from the palette's own Base colour — and keeping the palette's own
    Text colour on top — means the rows stay legible in any theme, light or
    dark, without the app having to know which one is in use.
    """

    def __init__(self, palette: QPalette | None = None):
        self.refresh(palette)

    def refresh(self, palette: QPalette | None = None) -> None:
        if palette is None:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            palette = app.palette() if app else QPalette()
        base = palette.color(QPalette.ColorRole.Base)
        self.text = palette.color(QPalette.ColorRole.Text)
        self.base = base
        self.tints = {
            RowState.NO_TAG: _blend(base, HUES[RowState.NO_TAG],
                                    TINT_STRENGTH),
            RowState.UNREADABLE: _blend(base, HUES[RowState.UNREADABLE],
                                        TINT_STRENGTH_ERROR),
        }
        self.alternate = _blend(base, self.text, ZEBRA_STRENGTH)

    def tint(self, state: RowState) -> QColor | None:
        """Background for a state, or None to leave the stripe showing."""
        return self.tints.get(state)

    def swatch(self, state: RowState) -> QColor:
        """A visible colour for the legend, even for the untinted state."""
        return self.tints.get(state, self.alternate)


@dataclass
class Column:
    key: str
    title: str
    width: int = 120

    @property
    def is_tag(self) -> bool:
        return self.key in BY_KEY


#: Shown by default. Everything in FIELDS can be added from the View menu.
DEFAULT_COLUMNS: tuple[Column, ...] = (
    Column("__name__", "File Name", 300),
    Column("artist", "Artist", 150),
    Column("album", "Album", 150),
    Column("title", "Title", 200),
    Column("year", "Year", 55),
    Column("genre", "Genre", 100),
    Column("track", "Track", 55),
    Column("__length__", "Length", 70),
    Column("__bitrate__", "Bitrate", 70),
    Column("__tag__", "Tag", 90),
)

OPTIONAL_COLUMNS: tuple[Column, ...] = tuple(
    Column(f.key, f.label) for f in BY_KEY.values()
    if f.key not in {c.key for c in DEFAULT_COLUMNS}
) + (
    Column("__ext__", "Ext", 55),
    Column("__size__", "Size", 80),
    Column("__modified__", "Date Modified", 140),
    Column("__cover__", "Cover", 60),
    Column("__path__", "Folder", 300),
)


@dataclass
class Node:
    """A folder or a file in the list.

    ``children`` is what the view sees; ``all_children`` is everything that
    was scanned. With no filter in force they are the *same list object*, so
    nothing has to be kept in sync and every existing caller — reordering
    included — keeps working unchanged. A filter swaps ``children`` for a
    freshly built subset and leaves ``all_children`` as the record of truth.
    """

    path: Path
    is_dir: bool
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)
    track: Track | None = None
    loaded: bool = False
    all_children: list["Node"] | None = None

    def __post_init__(self) -> None:
        if self.all_children is None:
            self.all_children = self.children

    @property
    def filtered(self) -> bool:
        return self.children is not self.all_children

    @property
    def row(self) -> int:
        return self.parent.children.index(self) if self.parent else 0


def row_state(track) -> RowState:
    if track is None or track.error:
        return RowState.UNREADABLE
    return RowState.NORMAL if track.has_tag else RowState.NO_TAG


class ScanWorker(QObject):
    """Walks a folder and reads tags off the GUI thread.

    Tag reading is the slow part — thousands of files means thousands of seeks,
    and doing it inline freezes the window for as long as it takes.
    """

    found = pyqtSignal(object)        # list[Track]
    progress = pyqtSignal(int, int)
    finished = pyqtSignal()

    def __init__(self, folder: Path, recursive: bool):
        super().__init__()
        self.folder, self.recursive = folder, recursive
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        paths: list[Path] = []
        if self.recursive:
            for root, dirs, files in os.walk(self.folder):
                dirs.sort()
                for name in sorted(files):
                    if name.lower().endswith(AUDIO_EXTENSIONS):
                        paths.append(Path(root) / name)
        else:
            try:
                for entry in sorted(self.folder.iterdir()):
                    if entry.is_file() and entry.name.lower().endswith(AUDIO_EXTENSIONS):
                        paths.append(entry)
            except OSError:
                pass

        batch: list[Track] = []
        for i, p in enumerate(paths, 1):
            if self._stop:
                break
            batch.append(read(p))
            if len(batch) >= 40:
                self.found.emit(batch)
                batch = []
            self.progress.emit(i, len(paths))
        if batch and not self._stop:
            self.found.emit(batch)
        self.finished.emit()


class FileListModel(QAbstractItemModel):
    """Folders at the top level, their tracks as children."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns: list[Column] = list(DEFAULT_COLUMNS)
        self._root = Node(Path("/"), True)
        self._by_folder: dict[Path, Node] = {}
        self.show_extensions = True
        self.row_palette = RowPalette()
        #: the original's "Use check boxes to select files" option. When on,
        #: the ticks drive every action instead of the highlight, so a
        #: selection survives scrolling and clicking elsewhere.
        self.use_checkboxes = False
        self._checked: set[Path] = set()
        #: the address-bar filter: a field key and the text to match
        self.filter_key = "__name__"
        self.filter_text = ""
        #: rows taken out of the way by "Hide Selected Files"
        self._hidden: set[Path] = set()

    def refresh_palette(self) -> None:
        """Re-derive the row tints after a theme change."""
        self.row_palette.refresh()
        if self.rowCount():
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, self.columnCount() - 1))

    # --- population ---

    def clear(self) -> None:
        self.beginResetModel()
        self._root = Node(Path("/"), True)
        self._by_folder = {}
        self._hidden.clear()
        self.endResetModel()

    def add_tracks(self, tracks: list[Track]) -> None:
        for track in tracks:
            folder = self._folder_node(track.path.parent)
            node = Node(track.path, False, folder, track=track)
            if not self.filter_active:
                # The common case: children IS all_children, so one append
                # does both and the view is told about exactly one new row.
                self.beginInsertRows(self._index_of(folder),
                                     len(folder.children), len(folder.children))
                folder.children.append(node)
                self.endInsertRows()
                continue
            folder.all_children.append(node)
            if self._visible(node):
                self._show_folder(folder)
                self.beginInsertRows(self._index_of(folder),
                                     len(folder.children), len(folder.children))
                folder.children.append(node)
                self.endInsertRows()

    def _folder_node(self, path: Path) -> Node:
        """The folder row for a path, created on first sight.

        While a filter is in force a folder stays out of the view until one of
        its files actually matches — otherwise filtering a recursive scan
        leaves a column of empty folder headings behind.
        """
        node = self._by_folder.get(path)
        if node is None:
            node = Node(path, True, self._root)
            self._by_folder[path] = node
            if self.filter_active:
                # Split the two lists apart: a fresh Node starts with
                # children IS all_children, which would otherwise make the
                # hidden row visible the moment it is appended.
                node.children = []
                self._root.all_children.append(node)
            else:
                self.beginInsertRows(QModelIndex(), len(self._root.children),
                                     len(self._root.children))
                self._root.children.append(node)
                self.endInsertRows()
        return node

    def _show_folder(self, folder: Node) -> None:
        if folder in self._root.children:
            return
        self.beginInsertRows(QModelIndex(), len(self._root.children),
                             len(self._root.children))
        self._root.children.append(folder)
        self.endInsertRows()

    # --- filtering ---

    @property
    def filter_active(self) -> bool:
        return bool(self.filter_text) or bool(self._hidden)

    def set_filter(self, key: str, text: str) -> None:
        """Show only rows matching ``text`` in field ``key``."""
        self.beginResetModel()
        self.filter_key = key or "__name__"
        self.filter_text = (text or "").strip()
        self._rebuild_visible()
        self.endResetModel()

    def hide_paths(self, paths) -> None:
        """The original's "Hide Selected Files" — list only, never the disk."""
        self.beginResetModel()
        self._hidden |= set(paths)
        self._rebuild_visible()
        self.endResetModel()

    def show_all(self) -> None:
        self.beginResetModel()
        self.filter_text = ""
        self._hidden.clear()
        self._rebuild_visible()
        self.endResetModel()

    def _rebuild_visible(self) -> None:
        if not self.filter_active:
            # Point every children list back at its full list — the same
            # object, so the no-filter code path is restored exactly.
            self._root.children = self._root.all_children
            for folder in self._root.all_children:
                folder.children = folder.all_children
            return
        showing = []
        for folder in self._root.all_children:
            folder.children = [c for c in folder.all_children
                               if self._visible(c)]
            if folder.children:
                showing.append(folder)
        self._root.children = showing

    def _visible(self, node: Node) -> bool:
        if node.path in self._hidden:
            return False
        return self._matches(node.track)

    def _matches(self, track: Track | None) -> bool:
        if not self.filter_text:
            return True
        needle = self.filter_text.lower()
        for haystack in self._filter_values(track):
            value = str(haystack).lower()
            if any(ch in needle for ch in "*?"):
                if fnmatch.fnmatch(value, needle):
                    return True
            elif needle in value:
                return True
        return False

    def _filter_values(self, track: Track | None):
        """What the filter text is compared against, for one row."""
        if track is None:
            return []
        if self.filter_key == "__name__":
            return [track.path.name]
        if self.filter_key == "__any__":
            return [track.path.name] + [v for v in track.values.values()
                                        if v not in (None, "")]
        value = track.values.get(self.filter_key, "")
        return [value] if value not in (None, "") else [""]

    # --- column sections ---

    def first_tag_column(self) -> int:
        """Where the ID3 half of the grid starts, or -1 if there isn't one."""
        for i, col in enumerate(self.columns):
            if col.is_tag:
                return i
        return -1

    def in_tag_section(self, column: int) -> bool:
        """Columns from the first tag column rightward carry the shading."""
        first = self.first_tag_column()
        return first >= 0 and column >= first

    def _index_of(self, node: Node) -> QModelIndex:
        if node is self._root:
            return QModelIndex()
        return self.createIndex(node.row, 0, node)

    # --- access ---

    def node(self, index: QModelIndex) -> Node | None:
        return index.internalPointer() if index.isValid() else None

    def track(self, index: QModelIndex) -> Track | None:
        node = self.node(index)
        return node.track if node else None

    def tracks_under(self, index: QModelIndex) -> list[Track]:
        node = self.node(index)
        if node is None:
            return []
        if node.track is not None:
            return [node.track]
        return [c.track for c in node.children if c.track is not None]

    def all_tracks(self) -> list[Track]:
        """Every track, in the order the list displays them.

        Display order is what "auto fill track #" numbers by, so this must
        follow the folders and rows on screen, not insertion order.
        """
        return [c.track for f in self._root.children
                for c in f.children if c.track is not None]

    def position_of(self, path: Path) -> int:
        """Index of a file in display order, or -1. Used to sort a selection.

        ``QItemSelectionModel.selectedRows()`` hands back rows in the order
        they were *clicked*, so a ctrl-click selection built bottom-up would
        otherwise number backwards.
        """
        for i, track in enumerate(self.all_tracks()):
            if track.path == path:
                return i
        return -1

    # --- check-box selection ---

    def set_use_checkboxes(self, on: bool) -> None:
        self.beginResetModel()
        self.use_checkboxes = on
        if not on:
            self._checked.clear()
        self.endResetModel()

    def checked_tracks(self) -> list[Track]:
        return [t for t in self.all_tracks() if t.path in self._checked]

    def set_checked(self, paths, checked: bool) -> None:
        for path in paths:
            if checked:
                self._checked.add(path)
            else:
                self._checked.discard(path)
        self._emit_all_changed()

    def set_all_checked(self, checked: bool) -> None:
        self._checked = ({t.path for t in self.all_tracks()} if checked
                         else set())
        self._emit_all_changed()

    def invert_checked(self) -> None:
        everything = {t.path for t in self.all_tracks()}
        self._checked = everything - self._checked
        self._emit_all_changed()

    def is_checked(self, path: Path) -> bool:
        return path in self._checked

    def _emit_all_changed(self) -> None:
        for frow, folder in enumerate(self._root.children):
            if not folder.children:
                continue
            parent = self.createIndex(frow, 0, folder)
            self.dataChanged.emit(self.index(0, 0, parent),
                                  self.index(len(folder.children) - 1, 0, parent))

    # --- reordering ---

    def move_rows(self, paths, delta: int) -> None:
        """Nudge files up or down within their folder.

        freedb-style lookups and track numbering both depend on the order the
        files sit in the list, so this has to be a real reorder, not a sort.
        """
        for folder in self._root.children:
            kids = folder.children
            targets = [i for i, c in enumerate(kids) if c.path in paths]
            if not targets:
                continue
            order = targets if delta < 0 else list(reversed(targets))
            moved = False
            for i in order:
                j = i + delta
                if 0 <= j < len(kids) and kids[j].path not in paths:
                    kids[i], kids[j] = kids[j], kids[i]
                    moved = True
            if moved:
                self._resync(folder)
                self.layoutChanged.emit()

    def _resync(self, folder: Node) -> None:
        """Replay a reorder of the visible rows onto the full list.

        Only matters while a filter is in force: without this, clearing the
        filter would undo a move the user just made.
        """
        if not folder.filtered:
            return
        shown = {id(c) for c in folder.children}
        ordered = iter(folder.children)
        folder.all_children[:] = [next(ordered) if id(c) in shown else c
                                  for c in folder.all_children]

    def replace_track(self, path: Path, track: Track) -> None:
        folder = self._by_folder.get(path.parent)
        if folder is None:
            return
        for child in folder.all_children:
            if child.path == path:
                child.path, child.track = track.path, track
                if child not in folder.children:
                    return      # hidden by the filter: nothing on screen
                top = self.createIndex(child.row, 0, child)
                bottom = self.createIndex(child.row, len(self.columns) - 1, child)
                self.dataChanged.emit(top, bottom)
                return

    # --- QAbstractItemModel ---

    def index(self, row, column, parent=QModelIndex()):
        node = self.node(parent) or self._root
        if 0 <= row < len(node.children):
            return self.createIndex(row, column, node.children[row])
        return QModelIndex()

    def parent(self, index):
        node = self.node(index)
        if node is None or node.parent is None or node.parent is self._root:
            return QModelIndex()
        return self.createIndex(node.parent.row, 0, node.parent)

    def rowCount(self, parent=QModelIndex()):
        if parent.column() > 0:
            return 0
        node = self.node(parent) or self._root
        return len(node.children)

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (orientation is Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole):
            return self.columns[section].title
        return None

    def flags(self, index):
        base = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        node = self.node(index)
        if node is None:
            return base
        if not node.is_dir and self.columns[index.column()].is_tag:
            base |= Qt.ItemFlag.ItemIsEditable
        if self.use_checkboxes and index.column() == 0:
            base |= Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        node = self.node(index)
        if node is None:
            return None
        col = self.columns[index.column()]

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return self._text(node, col, role)
        if role == Qt.ItemDataRole.BackgroundRole and not node.is_dir:
            tint = self.row_palette.tint(row_state(node.track))
            # None lets the view paint its own alternating stripe, which is
            # what keeps an ordinary tagged row neutral.
            return QBrush(tint) if tint is not None else None
        if role == Qt.ItemDataRole.ForegroundRole and not node.is_dir:
            # Set explicitly so the text never depends on the style guessing a
            # contrast against a background we chose ourselves.
            return QBrush(self.row_palette.text)
        if (role == Qt.ItemDataRole.CheckStateRole and self.use_checkboxes
                and index.column() == 0):
            if node.is_dir:
                kids = [c.path for c in node.children]
                ticked = sum(p in self._checked for p in kids)
                if not kids or ticked == 0:
                    return Qt.CheckState.Unchecked
                return (Qt.CheckState.Checked if ticked == len(kids)
                        else Qt.CheckState.PartiallyChecked)
            return (Qt.CheckState.Checked if node.path in self._checked
                    else Qt.CheckState.Unchecked)
        if role == Qt.ItemDataRole.ToolTipRole:
            if node.track and node.track.error:
                return node.track.error
            return str(node.path)
        return None

    def _text(self, node: Node, col: Column, role) -> str:
        if node.is_dir:
            if col.key != "__name__":
                return ""
            # Just the folder's own name. Printing the absolute path here
            # stretches the File Name column to the width of the deepest path
            # in the library; the full path is on the tooltip instead.
            return node.path.name or str(node.path)
        track = node.track
        key = col.key
        if key == "__name__":
            return node.path.name if self.show_extensions else node.path.stem
        if key == "__ext__":
            return node.path.suffix.lstrip(".")
        if key == "__path__":
            return str(node.path.parent)
        if key == "__length__":
            return track.info.play_time if track else ""
        if key == "__bitrate__":
            return f"{track.info.bitrate}" if track and track.info.bitrate else ""
        if key == "__tag__":
            return track.tag_version if track else ""
        if key == "__cover__":
            return "yes" if track and track.pictures else ""
        if key == "__size__":
            try:
                return _fmt_size(node.path.stat().st_size)
            except OSError:
                return ""
        if key == "__modified__":
            try:
                from datetime import datetime
                return datetime.fromtimestamp(
                    node.path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                return ""
        if track is None:
            return ""
        value = track.values.get(key, "")
        if isinstance(value, bool):
            return "*" if value else ""
        return "" if value is None else str(value)



    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        """Edit a tag straight in the grid, the way the original lets you."""
        if role == Qt.ItemDataRole.CheckStateRole:
            node = self.node(index)
            if node is None:
                return False
            on = Qt.CheckState(value) is Qt.CheckState.Checked
            paths = ([c.path for c in node.children] if node.is_dir
                     else [node.path])
            self.set_checked(paths, on)
            return True
        if role != Qt.ItemDataRole.EditRole:
            return False
        node = self.node(index)
        col = self.columns[index.column()]
        if node is None or node.track is None or not col.is_tag:
            return False
        from ..tagio import TagError, write
        try:
            write(node.path, {col.key: str(value)})
        except TagError:
            return False
        node.track.values[col.key] = str(value)
        self.dataChanged.emit(index, index)
        return True

    def set_columns(self, columns: list[Column]) -> None:
        self.beginResetModel()
        self.columns = columns
        self.endResetModel()


class SectionShadeDelegate(QStyledItemDelegate):
    """Shades the tag half of each row, dividing it from the File Name half.

    Done in a delegate rather than from ``BackgroundRole`` because the model
    cannot know whether the view is painting a given row as the alternating
    stripe — two folders of three files each do not alternate on the model's
    own row numbers. ``QStyleOptionViewItem.features`` carries that flag, so
    by the time the delegate sees the option the real background is known and
    the shade can be blended on top of it, stripe or row tint alike.
    """

    def __init__(self, row_palette: "RowPalette", parent=None):
        super().__init__(parent)
        self.row_palette = row_palette

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        model = index.model()
        if not isinstance(model, FileListModel):
            return
        node = model.node(index)
        if node is None or node.is_dir:
            return
        if not model.in_tag_section(index.column()):
            return
        if option.state & QStyle.StateFlag.State_Selected:
            # The highlight is the whole point of a selected row; shading it
            # would just mute it.
            return
        option.backgroundBrush = QBrush(self.shade(option))

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(size.height() + ROW_PADDING)
        return size

    def shade(self, option) -> QColor:
        """The tag-section colour for whatever this row already is."""
        brush = option.backgroundBrush
        if brush.style() != Qt.BrushStyle.NoBrush:
            under = brush.color()           # a row tint from the model
        elif option.features & QStyleOptionViewItem.ViewItemFeature.Alternate:
            under = self.row_palette.alternate
        else:
            under = self.row_palette.base
        return _blend(under, self.row_palette.text, SECTION_STRENGTH)


class _SectionBackgroundMixin:
    """Carries the tag-section shading on past the last row.

    Without this the division between the file-name half and the tag half
    stops dead at the end of the list and the rest of the pane is flat, which
    reads as an unfinished table rather than an empty one.
    """

    def last_row_bottom(self) -> int:
        """Viewport y just below the last visible row, or 0 if there is none."""
        model = self.model()
        if model is None or not model.rowCount():
            return 0
        index = model.index(model.rowCount() - 1, 0)
        while self.isExpanded(index) and model.rowCount(index):
            index = model.index(model.rowCount(index) - 1, 0, index)
        rect = self.visualRect(index)
        return max(0, rect.bottom() + 1)

    def empty_section_rect(self) -> QRect | None:
        """The slab of empty pane that belongs to the tag columns."""
        model = self.model()
        if not isinstance(model, FileListModel):
            return None
        column = model.first_tag_column()
        if column < 0:
            return None
        viewport = self.viewport()
        top = self.last_row_bottom()
        if top >= viewport.height():
            return None
        left = self.header().sectionViewportPosition(column)
        if left >= viewport.width():
            return None
        left = max(0, left)
        return QRect(left, top, viewport.width() - left,
                     viewport.height() - top)

    def _paint_empty_section(self) -> None:
        rect = self.empty_section_rect()
        if rect is None or rect.isEmpty():
            return
        model = self.model()
        palette = model.row_palette
        painter = QPainter(self.viewport())
        painter.fillRect(rect, _blend(palette.base, palette.text,
                                      SECTION_STRENGTH))
        painter.end()


class FileListView(_SectionBackgroundMixin, QTreeView):
    """The file list, with a watermark while it is empty.

    An empty grid gives no hint about what to do next. Drawing the mark into
    the viewport — rather than adding a placeholder widget — means nothing has
    to be shown and hidden as the list fills and empties.
    """

    WATERMARK_OPACITY = 0.10
    HINT = "Choose a folder on the left"

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        model = self.model()
        if model is not None and model.rowCount() > 0:
            self._paint_empty_section()
            return

        from .splash import emblem
        viewport = self.viewport()
        side = int(min(viewport.width(), viewport.height()) * 0.42)
        if side < 48:
            return
        mark = emblem(side, self.devicePixelRatioF())
        if mark.isNull():
            return

        painter = QPainter(viewport)
        painter.setOpacity(self.WATERMARK_OPACITY)
        width = int(mark.width() / mark.devicePixelRatio())
        height = int(mark.height() / mark.devicePixelRatio())
        x = (viewport.width() - width) // 2
        y = (viewport.height() - height) // 2 - 12
        painter.drawPixmap(x, y, mark)

        painter.setOpacity(0.45)
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        painter.drawText(
            QRect(0, y + height + 10, viewport.width(), 24),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self.HINT)
        painter.end()


class LegendPanel(QWidget):
    """The colour key, shown beside the work tabs.

    Swatches are pulled from the live :class:`RowPalette`, so the key always
    shows the colours actually on screen — including after a theme switch.
    """

    def __init__(self, row_palette: "RowPalette", parent=None):
        super().__init__(parent)
        self.row_palette = row_palette
        self._swatches: dict[RowState, QLabel] = {}

        box = QGroupBox("Row colours")
        lay = QVBoxLayout(box)
        lay.setSpacing(4)
        for state in RowState:
            row = QHBoxLayout()
            swatch = QLabel()
            swatch.setFixedSize(16, 16)
            self._swatches[state] = swatch
            row.addWidget(swatch)
            row.addWidget(QLabel(LEGEND[state]))
            row.addStretch(1)
            lay.addLayout(row)
        lay.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        self.refresh()

    def refresh(self) -> None:
        for state, swatch in self._swatches.items():
            colour = self.row_palette.swatch(state)
            swatch.setStyleSheet(
                f"background: {colour.name()}; "
                f"border: 1px solid {self.row_palette.text.name()};")
