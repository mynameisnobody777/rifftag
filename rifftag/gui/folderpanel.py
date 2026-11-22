"""The folder tree plus the header bar that sits above it.

Tag&Rename puts a small control strip at the top of its Folders column —
home, favourites, recent folders, new folder, refresh, and a close box — and a
horizontal scroll bar along the bottom. The scroll bar is the point of the
exercise: a QTreeView stretches its only column to the viewport by default, so
a path four levels deep simply disappears behind the indentation with no way
to reach it short of dragging the splitter. Turning the stretch off and
letting the column size to its contents is what gives Qt something to scroll.

Favourites and the recent list live in QSettings as JSON, so they survive a
restart the way the original's do.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                             QInputDialog, QLabel, QMenu, QMessageBox, QStyle,
                             QToolButton, QVBoxLayout, QWidget)

from .theme import themed_icon

#: how many folders the Recent menu remembers
MAX_RECENT = 15


def configure_tree(tree) -> None:
    """Give a single-column tree a working horizontal scroll bar.

    Four settings, all of them necessary: the last section must stop
    stretching (otherwise the column is always exactly the viewport width),
    the column must size to its contents (otherwise there is nothing wider
    than the viewport to scroll to), elision must be off (an elided name fits
    by definition), and scrolling must be per pixel so the bar moves smoothly
    rather than one whole row at a time.
    """
    header = tree.header()
    header.setStretchLastSection(False)
    # The all-sections overload, not setSectionResizeMode(0, ...): the latter
    # indexes into the header's sections and segfaults on a tree that has no
    # model yet. Only column 0 is ever visible here, so they are equivalent.
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    tree.setTextElideMode(Qt.TextElideMode.ElideNone)
    tree.setHorizontalScrollMode(
        QAbstractItemView.ScrollMode.ScrollPerPixel)
    tree.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAsNeeded)


class FolderPanel(QWidget):
    """Header bar + tree. The tree itself is built by the main window."""

    folder_chosen = pyqtSignal(object)      # Path
    refresh_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, tree, settings, parent=None):
        super().__init__(parent)
        self.tree = tree
        self.settings = settings
        self.current: Path | None = None

        configure_tree(tree)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.header = self._build_header()
        lay.addWidget(self.header)
        lay.addWidget(tree, 1)

    def resizeEvent(self, event) -> None:
        """Drop the word "Folders:" before the buttons start getting clipped.

        A QLabel does not elide — it just gets cut off mid-word, which reads
        as a bug rather than as a narrow column. The buttons are the part
        that has to stay usable, so the label is what gives way.
        """
        super().resizeEvent(event)
        self.fit_title(self.width())

    def fit_title(self, width: int) -> None:
        """Show the label only if the buttons can have their space first."""
        if not hasattr(self, "title"):
            return
        buttons = sum(b.sizeHint().width() + 2 for b in (
            self.btn_up, self.btn_home, self.btn_favourites, self.btn_recent,
            self.btn_new, self.btn_refresh, self.btn_close))
        needed = buttons + self.title.sizeHint().width() + 8
        self.title.setVisible(width >= needed)

    # --- header ---

    def _build_header(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(2, 2, 2, 0)
        row.setSpacing(2)
        # Let the strip be narrower than its buttons want to be. Without
        # this the header dictates the whole column's minimum width — and on
        # a desktop with no media icons, where every button falls back to a
        # word, that was 380px of folder column you could not shrink.
        row.setSizeConstraint(QHBoxLayout.SizeConstraint.SetNoConstraint)
        self.title = QLabel("Folders:")
        row.addWidget(self.title)
        row.addStretch(1)

        sp = QStyle.StandardPixmap
        self.btn_up = self._button(
            "Up one folder", self._go_up, sp.SP_FileDialogToParent,
            "go-up", "go-parent-folder", "folder-up")
        self.btn_home = self._button(
            "Home folder", self._go_home, sp.SP_DirHomeIcon,
            "go-home", "user-home")
        self.btn_favourites = self._menu_button(
            "Favourite folders", sp.SP_DialogSaveButton,
            "bookmarks", "user-bookmarks", "emblem-favorite")
        self.btn_recent = self._menu_button(
            "Recently used folders", sp.SP_FileDialogListView,
            "document-open-recent", "appointment-new", "history")
        self.btn_new = self._button(
            "New subfolder…", self._new_folder, sp.SP_FileDialogNewFolder,
            "folder-new")
        self.btn_refresh = self._button(
            "Refresh", self.refresh_requested.emit, sp.SP_BrowserReload,
            "view-refresh")
        self.btn_close = self._button(
            "Hide the folder panel", self.close_requested.emit,
            sp.SP_DialogCloseButton, "window-close", "dialog-close")

        for button in (self.btn_up, self.btn_home, self.btn_favourites,
                       self.btn_recent, self.btn_new, self.btn_refresh,
                       self.btn_close):
            row.addWidget(button)
        # Nothing above the root, and nothing opened yet either.
        self.btn_up.setEnabled(False)

        self.btn_favourites.menu().aboutToShow.connect(self._fill_favourites)
        self.btn_recent.menu().aboutToShow.connect(self._fill_recent)
        return bar

    def _button(self, tip: str, slot, fallback, *icons: str) -> QToolButton:
        button = QToolButton()
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.setIconSize(QSize(16, 16))
        self._decorate(button, tip, fallback, *icons)
        button.clicked.connect(slot)
        return button

    def _menu_button(self, tip: str, fallback, *icons: str) -> QToolButton:
        button = QToolButton()
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.setIconSize(QSize(16, 16))
        self._decorate(button, tip, fallback, *icons)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(QMenu(button))
        return button

    @staticmethod
    def _decorate(button: QToolButton, tip: str, fallback,
                  *icons: str) -> None:
        """Theme icon, else Qt's own, else a short word.

        An icon-only button with no icon is an invisible button. The Qt
        fallback is what keeps this strip looking like buttons on Windows,
        where the desktop theme offers nothing.
        """
        icon = themed_icon(*icons, fallback=fallback)
        if icon.isNull():
            button.setText(tip.split()[0].rstrip("…"))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        else:
            button.setIcon(icon)

    # --- state ---

    def set_current(self, folder: Path) -> None:
        """Called when a folder is opened; feeds the Recent menu."""
        self.current = Path(folder)
        self.btn_up.setEnabled(self.parent_of(self.current) is not None)
        recent = [str(folder)] + [p for p in self._stored("folder_recent")
                                  if p != str(folder)]
        self.settings.setValue("folder_recent",
                               json.dumps(recent[:MAX_RECENT]))

    def _stored(self, key: str) -> list[str]:
        raw = self.settings.value(key)
        try:
            value = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            return []
        return [str(v) for v in value] if isinstance(value, list) else []

    def favourites(self) -> list[str]:
        return self._stored("folder_favourites")

    def recent(self) -> list[str]:
        return self._stored("folder_recent")

    # --- menus ---

    def _fill_favourites(self) -> None:
        menu = self.btn_favourites.menu()
        menu.clear()
        current = str(self.current) if self.current else ""
        saved = self.favourites()
        add = menu.addAction("Add current folder")
        add.setEnabled(bool(current) and current not in saved)
        add.triggered.connect(self._add_favourite)
        remove = menu.addAction("Remove current folder")
        remove.setEnabled(current in saved)
        remove.triggered.connect(self._remove_favourite)
        menu.addSeparator()
        if not saved:
            empty = menu.addAction("No favourites yet")
            empty.setEnabled(False)
        for path in saved:
            action = menu.addAction(self._label(path))
            action.setToolTip(path)
            action.triggered.connect(
                lambda _, p=path: self.folder_chosen.emit(Path(p)))

    def _fill_recent(self) -> None:
        menu = self.btn_recent.menu()
        menu.clear()
        paths = self.recent()
        if not paths:
            empty = menu.addAction("Nothing opened yet")
            empty.setEnabled(False)
            return
        for path in paths:
            action = menu.addAction(self._label(path))
            action.setToolTip(path)
            action.triggered.connect(
                lambda _, p=path: self.folder_chosen.emit(Path(p)))

    @staticmethod
    def _label(path: str) -> str:
        """Last two components — enough to tell two "Disc 1" folders apart."""
        parts = Path(path).parts
        return str(Path(*parts[-2:])) if len(parts) > 2 else path

    def _add_favourite(self) -> None:
        if self.current is None:
            return
        saved = self.favourites()
        if str(self.current) not in saved:
            saved.append(str(self.current))
            self.settings.setValue("folder_favourites", json.dumps(saved))

    def _remove_favourite(self) -> None:
        saved = [p for p in self.favourites() if p != str(self.current)]
        self.settings.setValue("folder_favourites", json.dumps(saved))

    # --- actions ---

    @staticmethod
    def parent_of(folder: Path | None) -> Path | None:
        """The folder above, or None at the root.

        ``Path("/").parent`` is ``Path("/")``, so comparing against the path
        itself is what actually detects the top — a plain ``.parent`` would
        leave the button live forever and do nothing.
        """
        if folder is None:
            return None
        folder = Path(folder)
        parent = folder.parent
        return None if parent == folder else parent

    def _go_up(self) -> None:
        parent = self.parent_of(self.current)
        if parent is not None:
            self.folder_chosen.emit(parent)

    def _go_home(self) -> None:
        self.folder_chosen.emit(Path.home())

    def _new_folder(self) -> None:
        if self.current is None:
            QMessageBox.information(self, "New subfolder",
                                    "Pick a folder on the left first.")
            return
        name, ok = QInputDialog.getText(
            self, "New subfolder", f"Create inside {self.current}:")
        name = name.strip()
        if not ok or not name:
            return
        target = self.current / name
        try:
            target.mkdir(parents=False, exist_ok=False)
        except OSError as exc:
            QMessageBox.warning(self, "New subfolder",
                                f"Could not create {target}:\n{exc}")
            return
        self.folder_chosen.emit(target)
