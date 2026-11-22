"""Main window.

Layout follows Tag&Rename: a folder tree on the left, the file list top right,
the three work tabs beneath it, and the cover-art panel down the right edge.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import (QDir, QModelIndex, QSettings, QSize, Qt,
                          QThread, QTimer)
# QFileSystemModel lives in QtGui in Qt6, not QtWidgets as in Qt5.
from PyQt6.QtGui import (QAction, QFileSystemModel, QIcon, QKeySequence,
                         QPixmap)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                             QStyle,
                             QFrame, QMenu, QToolBar, QToolButton,
                             QDialog, QDialogButtonBox,
                             QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QMainWindow, QMessageBox, QProgressBar,
                             QPushButton, QSplitter, QTabWidget, QTableWidget,
                             QTableWidgetItem, QTreeView, QVBoxLayout, QWidget)

from .about import TAGLINE
from ..casing import Case
from ..masks import MaskError, RenderOptions, parse
from ..renamer import Status, apply as apply_renames, plan, undo as undo_renames
from ..tagio import (Picture, TagError, WriteOptions, read, remove_tags,
                     write)
from .. import tracknum
from .art import ArtDropLabel, ArtError, as_jpeg
from .convert import ConvertDialog
from .dialogs import AutoFillTrackDialog
from .lrcsync import LrcSyncDialog
from .lyrics import LyricsCleanDialog, LyricsImportDialog
from .preflight import PreflightDialog
from .filterbar import FilterBar
from .folderpanel import FolderPanel
from .model import (DEFAULT_COLUMNS, FileListModel, FileListView,
                    LegendPanel, ScanWorker, SectionShadeDelegate, apply_zebra)
from .player import PlayerPanel
from .about import AboutDialog
from .shortcuts import ShortcutsDialog
from .splash import app_icon, show_splash, wordmark
from .statusbar import StatusTotals
from .theme import (Theme, apply as apply_theme, capture_system_palette,
                    themed_icon, use_portable_style)
from .webimport import WebImportDialog
from .tabs import FilenameToTagTab, MultiTagTab, RenameTab
from .tageditor import TagEditorDialog

APP = "RiffTag"
ORG = "rifftag"

DEFAULT_RENAME_MASKS = [
    "%6 %2", "%1 - %2", "%6 - %1 - %2", "%1 - %3 - %6 - %2",
    "%1 - %3\\%6 %2", "%aa\\%3\\%6 %2",
]
DEFAULT_PARSE_MASKS = [
    "%6 %1 - %2", "%1 - %2", "%6 - %2", "%1 - %3\\%6 %2", "%6 %2",
]
DEFAULT_FOLDER_MASKS = ["%1 - %3", "%1 [%4] %3", "%3 (%4)"]


class PreviewDialog(QDialog):
    """The Test window: every before/after pair, nothing moved yet.

    ``action_text`` adds a button that goes ahead and does it, so reviewing
    and applying is one trip rather than "close, then find the other button".
    Accepting the dialog means "do it" — there is deliberately **no** second
    "are you sure?": this window *is* the confirmation, it lists every change
    individually, and a confirm on top of a reviewed list is the kind people
    learn to click through without reading. The button says what it will do
    and how many files it touches instead.
    """

    def __init__(self, rows: list[tuple[str, str, str]], title: str,
                 parent=None, action_text: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 520)
        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels(["Before", "After", "Note"])
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        apply_zebra(table)
        for r, (before, after, note) in enumerate(rows):
            for c, text in enumerate((before, after, note)):
                table.setItem(r, c, QTableWidgetItem(text))
        table.resizeColumnsToContents()
        buttons = QDialogButtonBox()
        self.btn_action = None
        if action_text:
            self.btn_action = QPushButton(action_text)
            self.btn_action.setDefault(True)
            self.btn_action.clicked.connect(self.accept)
            buttons.addButton(self.btn_action,
                              QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        buttons.addButton(self.btn_close,
                          QDialogButtonBox.ButtonRole.RejectRole)
        lay = QVBoxLayout(self)
        lay.addWidget(table)
        lay.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)
        self.settings = QSettings(ORG, APP)
        self.write_options = WriteOptions()
        self.last_transaction = None
        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None
        #: Bumped for every scan. Results carrying an older id are dropped —
        #: see _scan_found.
        self._scan_id = 0

        self._build_folder_tree()
        self._build_player()
        self._build_file_list()
        self._build_tabs()
        self._build_art_panel()
        self._assemble()
        self._build_menu()
        self._build_toolbar()
        self._restore()

    # --- construction ---

    def _build_folder_tree(self) -> None:
        self.fs_model = QFileSystemModel()
        self.fs_model.setRootPath("")
        # Lets a folder be renamed in place. QFileSystemModel performs the
        # rename itself and reports failure by refusing the edit.
        self.fs_model.setReadOnly(False)
        self.fs_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot
                                | QDir.Filter.Drives)
        self.folder_tree = QTreeView()
        self.folder_tree.setModel(self.fs_model)
        for col in range(1, self.fs_model.columnCount()):
            self.folder_tree.hideColumn(col)
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.clicked.connect(self._folder_clicked)
        # Expanding or walking the tree with the arrow keys moves the current
        # row deeper; slide sideways so its name stays readable. Queued, so
        # it runs after the view has laid the new rows out.
        self.folder_tree.expanded.connect(
            lambda *_: QTimer.singleShot(0, self.show_current_folder))
        self.folder_tree.setMinimumWidth(240)
        # Click, pause, click again renames — the original's behaviour. F2
        # works too. DoubleClicked is deliberately absent: a double-click
        # should expand the folder, not start an edit.
        self.folder_tree.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.fs_model.fileRenamed.connect(self._folder_renamed)
        self.folder_tree.selectionModel().currentChanged.connect(
            lambda *_: QTimer.singleShot(0, self.show_current_folder))

        # The panel adds the header strip and, via configure_tree(), the
        # horizontal scroll bar a deep tree needs.
        self.folder_panel = FolderPanel(self.folder_tree, self.settings)
        # A QSplitter clamps to each pane's minimumSizeHint, and the header
        # strip's buttons make that hint ~380px — wider than the column ever
        # needs to be. An explicit minimum overrides the hint, so the folder
        # column can actually be dragged narrow.
        self.folder_panel.setMinimumWidth(170)
        self.folder_panel.folder_chosen.connect(self._goto)
        self.folder_panel.refresh_requested.connect(self._refresh_tree)
        self.folder_panel.close_requested.connect(
            lambda: self.act_folder_panel.setChecked(False))

    def _build_player(self) -> None:
        """The logo-and-transport square in the top-left corner."""
        self.player = PlayerPanel(self.settings)
        self.player.play_all_requested.connect(self._play_all)
        self.player.play_selected_requested.connect(self._play_selected)
        self.player.external_play_requested.connect(self._play_externally)
        self.player.now_playing_changed.connect(self._now_playing)

    def _build_file_list(self) -> None:
        self.model = FileListModel()
        self.files = FileListView()
        self.files.setModel(self.model)
        self.files.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        apply_zebra(self.files)
        self.shade_delegate = SectionShadeDelegate(self.model.row_palette,
                                                   self.files)
        self.files.setItemDelegate(self.shade_delegate)
        self.files.setUniformRowHeights(True)
        self.files.setSortingEnabled(False)
        self.files.setRootIsDecorated(True)
        self.files.doubleClicked.connect(self._edit_one)
        self.files.selectionModel().selectionChanged.connect(self._selection_changed)
        for i, col in enumerate(DEFAULT_COLUMNS):
            self.files.setColumnWidth(i, col.width)

    def _build_tabs(self) -> None:
        self.rename_tab = RenameTab()
        self.multi_tab = MultiTagTab()
        self.parse_tab = FilenameToTagTab()
        self.rename_tab.mask_row.set_masks(
            self._stored("rename_masks", DEFAULT_RENAME_MASKS))
        self.parse_tab.mask_row.set_masks(
            self._stored("parse_masks", DEFAULT_PARSE_MASKS))

        self.legend = LegendPanel(self.model.row_palette)
        self.legend.setMaximumWidth(150)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.rename_tab, "Rename Files (F4)")
        self.tabs.addTab(self.multi_tab, "Multi File Tag Editor (Ctrl+F4)")
        self.tabs.addTab(self.parse_tab, "Get Tags from File Name (Shift+F4)")

        self.rename_tab.preview_requested.connect(lambda: self._do_rename(True))
        self.rename_tab.rename_requested.connect(lambda: self._do_rename(False))
        self.rename_tab.undo_requested.connect(self._undo_rename)
        self.rename_tab.mask_editor_requested.connect(self._save_current_mask)
        self.parse_tab.preview_requested.connect(lambda: self._do_parse(True))
        self.parse_tab.write_requested.connect(lambda: self._do_parse(False))
        self.multi_tab.save_requested.connect(self._save_multi)
        self.multi_tab.remove_requested.connect(self._remove_tags)
        self.multi_tab.copy_focused_requested.connect(self._copy_focused)
        self.multi_tab.edit_all_requested.connect(self._open_tag_editor)

    def _build_art_panel(self) -> None:
        # Drop targets, not plain labels: dragging a picture in is how people
        # expect to set a cover, and the file dialog is the slow path.
        self.file_art = ArtDropLabel("no cover art\n\n(drop a picture here)")
        self.folder_art = ArtDropLabel("no folder.jpg\n\n(drop a picture here)")
        self.file_art.art_dropped.connect(self._file_art_dropped)
        self.folder_art.art_dropped.connect(self._folder_art_dropped)
        for widget in (self.file_art, self.folder_art):
            widget.drop_failed.connect(
                lambda why: self.statusBar().showMessage(f"Not used: {why}"))
        for lbl in (self.file_art, self.folder_art):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setMinimumSize(190, 190)
            # A frame, not a stylesheet border: see the note in tabs.py —
            # a stylesheet freezes this widget's colours at the old theme.
            lbl.setFrameShape(QFrame.Shape.StyledPanel)
            lbl.setFrameShadow(QFrame.Shadow.Sunken)

        self.art_panel = QWidget()
        lay = QVBoxLayout(self.art_panel)
        for title, widget in (("File cover art", self.file_art),
                              ("Folder cover art", self.folder_art)):
            box = QGroupBox(title)
            inner = QVBoxLayout(box)
            inner.addWidget(widget)
            lay.addWidget(box)
        self.btn_art_to_folder = QPushButton("Tag art → folder.jpg")
        self.btn_folder_to_art = QPushButton("folder.jpg → tag art")
        self.btn_art_to_folder.clicked.connect(self._extract_folder_jpg)
        self.btn_folder_to_art.clicked.connect(self._embed_folder_jpg)
        lay.addWidget(self.btn_art_to_folder)
        lay.addWidget(self.btn_folder_to_art)
        lay.addStretch(1)
        self.art_panel.setMaximumWidth(230)

    def _assemble(self) -> None:
        tab_row = QWidget()
        tab_layout = QHBoxLayout(tab_row)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(self.tabs, 1)
        tab_layout.addWidget(self.legend)

        # The file list lives inside its own QMainWindow purely so a QToolBar
        # can dock directly above it. Qt only lets a toolbar dock to the edges
        # of a QMainWindow, so this is the one way to put a movable toolbar
        # between the work tabs and the list, where the original has it.
        self.list_host = QMainWindow()
        self.list_host.setCentralWidget(self.files)
        self.list_host.setWindowFlags(Qt.WindowType.Widget)

        # Two real rows, as in the original: everything you work *with* on
        # top (player, tabs, cover art), the address and filter line across
        # the middle, and everything you work *on* underneath (folder tree,
        # file list). The rows are plain horizontal splitters, so the tree is
        # level with the list by construction — the previous arrangement was
        # two vertical splitters pretending to be rows and needed their
        # heights pushed back and forth to stay in line.
        self.top_row = QSplitter(Qt.Orientation.Horizontal)
        self.top_row.addWidget(self.player)
        self.top_row.addWidget(tab_row)
        self.top_row.setStretchFactor(0, 0)
        self.top_row.setStretchFactor(1, 1)
        self.top_row.setSizes([260, 1000])

        self.bottom_row = QSplitter(Qt.Orientation.Horizontal)
        self.bottom_row.addWidget(self.folder_panel)
        self.bottom_row.addWidget(self.list_host)
        self.bottom_row.setStretchFactor(0, 0)
        self.bottom_row.setStretchFactor(1, 1)
        self.bottom_row.setSizes([260, 1000])

        # Qt will not keep two splitters in step on its own, so each one
        # pushes its widths onto the other. The guard stops the pair from
        # bouncing the signal back and forth.
        self._syncing = False
        self._aligned = False
        self.top_row.splitterMoved.connect(
            lambda *_: self._sync_rows(self.top_row, self.bottom_row))
        self.bottom_row.splitterMoved.connect(
            lambda *_: self._sync_rows(self.bottom_row, self.top_row))

        # Address + filter sits directly above the pair it acts on, which is
        # where Tag&Rename has it. It travels with the bottom row rather than
        # living in the vertical splitter, so there is one handle between the
        # rows instead of two around the strip.
        self.filter_bar = FilterBar()
        self.filter_bar.address_entered.connect(self._goto)
        self.filter_bar.filter_requested.connect(self._apply_filter)
        self.filter_bar.hide_selected_requested.connect(self._hide_selected)
        self.filter_bar.show_all_requested.connect(self._show_all)

        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(2)
        lower_layout.addWidget(self.filter_bar)
        lower_layout.addWidget(self.bottom_row, 1)

        self.stack = QSplitter(Qt.Orientation.Vertical)
        self.stack.addWidget(self.top_row)
        self.stack.addWidget(lower)
        self.stack.setStretchFactor(0, 0)
        self.stack.setStretchFactor(1, 1)
        # The tab panel's own sizeHint is tall because of the variable crib
        # sheet; without this it eats half the window on first launch.
        self.stack.setSizes([250, 700])

        # Cover art keeps the full height of the window down the right edge.
        middle = QSplitter(Qt.Orientation.Horizontal)
        middle.addWidget(self.stack)
        middle.addWidget(self.art_panel)
        middle.setStretchFactor(0, 1)
        middle.setStretchFactor(1, 0)
        self.splitter = middle
        self.setCentralWidget(middle)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        self.totals = StatusTotals()
        self.statusBar().addPermanentWidget(self.totals)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Pick a folder on the left.")

    def showEvent(self, event) -> None:
        """Line the two columns up once, when the real heights are known.

        setSizes() before the window is laid out is only a hint — each
        splitter then rescales to its own minimum size hints, and the tab
        block's differ from the player's, so the two rows came up with their
        column divider in different places. Doing it on first show, from the
        top row (the one with the harder constraint), lines the folder tree
        up under the player block.
        """
        super().showEvent(event)
        if not self._aligned:
            self._aligned = True
            self.align_rows()
            # _reveal() ran during _restore(), before the tree had a real
            # width — visualRect() was meaningless then, so the sideways
            # nudge has to be repeated once the column has been laid out.
            QTimer.singleShot(0, self.show_current_folder)

    def align_rows(self) -> None:
        """Line the folder tree up with the player block above it."""
        self._sync_rows(self.top_row, self.bottom_row)

    def _sync_rows(self, source: QSplitter, target: QSplitter) -> None:
        """Keep the two rows' column divider in the same place."""
        if self._syncing:
            return
        self._syncing = True
        try:
            target.setSizes(source.sizes())
            # The target can refuse — the folder tree has a minimum width
            # the tab block does not — so dragging one row's divider past it
            # would otherwise leave the two rows out of line. Adopt whatever
            # the target actually took.
            actual = target.sizes()
            if actual != source.sizes():
                source.setSizes(actual)
            # One pass only. If the two rows' minimum widths genuinely leave
            # no common position — a very narrow window — they end up as
            # close as the constraints allow rather than oscillating.
        finally:
            self._syncing = False

    def _build_menu(self) -> None:
        self._brand_menu_bar()
        view = self.menuBar().addMenu("&View")
        self.act_recursive = QAction(
            self._icon("folder-open", "edit-find",
                       fallback=QStyle.StandardPixmap.SP_DirOpenIcon),
            "Search &subfolders", self, checkable=True)
        self.act_recursive.setToolTip("Search subfolders   (Ctrl+R)")
        self.act_recursive.setShortcut("Ctrl+R")
        self.act_recursive.toggled.connect(lambda *_: self._rescan())
        view.addAction(self.act_recursive)

        self.act_checkboxes = QAction(
            self._icon("checkbox", "gtk-apply", "object-select",
                       fallback=QStyle.StandardPixmap.SP_DialogApplyButton),
            "Use check &boxes to select files", self, checkable=True)
        self.act_checkboxes.setToolTip(
            "Tick files instead of highlighting them. Ticks survive scrolling "
            "and clicking elsewhere.")
        self.act_checkboxes.toggled.connect(self._toggle_checkboxes)
        view.addAction(self.act_checkboxes)

        self.act_show_ext = QAction("Show file &extensions", self, checkable=True)
        self.act_show_ext.setChecked(True)
        self.act_show_ext.toggled.connect(self._toggle_extensions)
        view.addAction(self.act_show_ext)

        act_fit = QAction("&Auto fit columns", self)
        act_fit.triggered.connect(self._fit_columns)
        view.addAction(act_fit)
        view.addSeparator()

        self.act_folder_panel = QAction("&Folder panel", self, checkable=True)
        self.act_folder_panel.setChecked(True)
        self.act_folder_panel.setShortcut("Ctrl+L")
        self.act_folder_panel.setToolTip(
            "Show the folder column   (Ctrl+L)")
        self.act_folder_panel.toggled.connect(self._toggle_folder_panel)
        view.addAction(self.act_folder_panel)

        self.act_player = QAction("&Player panel", self, checkable=True)
        self.act_player.setChecked(True)
        self.act_player.setToolTip(
            "The logo and transport block above the folder tree")
        self.act_player.toggled.connect(self._toggle_player)
        view.addAction(self.act_player)

        self.act_filter_bar = QAction("&Address and filter bar", self,
                                      checkable=True)
        self.act_filter_bar.setChecked(True)
        self.act_filter_bar.toggled.connect(self._toggle_filter_bar)
        view.addAction(self.act_filter_bar)
        view.addSeparator()
        place = view.addMenu("&Toolbar position")
        self.act_tb_top = QAction("Top of window", self, checkable=True)
        self.act_tb_list = QAction("Above the file list", self, checkable=True)
        self.act_tb_top.triggered.connect(lambda: self._place_toolbar("top"))
        self.act_tb_list.triggered.connect(lambda: self._place_toolbar("list"))
        place.addAction(self.act_tb_top)
        place.addAction(self.act_tb_list)

        theme_menu = view.addMenu("T&heme")
        self._theme_actions = {}
        for candidate in Theme:
            action = QAction(candidate.label, self, checkable=True)
            action.triggered.connect(
                lambda _, t=candidate: self._apply_theme(t))
            theme_menu.addAction(action)
            self._theme_actions[action] = candidate
        view.addSeparator()

        act_refresh = QAction("&Refresh", self)
        act_refresh.setShortcut(QKeySequence.StandardKey.Refresh)
        act_refresh.triggered.connect(self._rescan)
        view.addAction(act_refresh)

        tools = self.menuBar().addMenu("&Tools")
        for label, slot in (
                ("Edit all supported tag fields\tCtrl+F5", self._open_tag_editor),
                ("Get album titles from the Web…", self._web_import),
                ("Auto fill track #…", self._autofill_track),
                ("Auto fill episode #…", self._autofill_episode),
                ("Convert audio format…", self._convert),
                ("Release Check…\tF9", self._release_check),
                ("Clean lyrics for streaming…", self._clean_lyrics),
                ("Import lyrics from text files…", self._import_lyrics),
                ("Sync lyrics to the music…", self._sync_lyrics),
                ("Copy Artist → Album Artist", self._artist_to_album_artist),
                ('Remove leading "The" from Artist', self._strip_leading_the),
                ("Trim leading/trailing spaces in all fields", self._trim_spaces),
                ("Add folder.jpg to files tag", self._embed_folder_jpg),
                ("Extract folder.jpg from files tag", self._extract_folder_jpg)):
            act = QAction(label.split("\t")[0], self)
            act.triggered.connect(slot)
            tools.addAction(act)

        opts = self.menuBar().addMenu("&Options")
        self.act_v24 = QAction("Write ID3v2.&4 (default is 2.3)", self,
                               checkable=True)
        self.act_v24.toggled.connect(
            lambda on: setattr(self.write_options, "id3_version", 4 if on else 3))
        opts.addAction(self.act_v24)
        self.act_grp1 = QAction("Grouping in GRP1 (iTunes)", self, checkable=True)
        self.act_grp1.setChecked(True)
        self.act_grp1.toggled.connect(
            lambda on: setattr(self.write_options, "id3_grouping_frame",
                               "GRP1" if on else "TIT1"))
        opts.addAction(self.act_grp1)
        self.act_spaced_aa = QAction('Write "ALBUM ARTIST" with a space', self,
                                     checkable=True)
        self.act_spaced_aa.toggled.connect(
            lambda on: setattr(self.write_options, "album_artist_key",
                               "ALBUM ARTIST" if on else "ALBUMARTIST"))
        opts.addAction(self.act_spaced_aa)
        opts.addSeparator()
        self.act_splash = QAction("Show &splash screen on startup", self,
                                  checkable=True)
        self.act_splash.setChecked(self.splash_enabled())
        self.act_splash.toggled.connect(
            lambda on: self.settings.setValue("show_splash", on))
        opts.addAction(self.act_splash)

        opts.addSeparator()
        act_token = QAction("Discogs access &token…", self)
        act_token.triggered.connect(self._set_discogs_token)
        opts.addAction(act_token)

        act_shortcuts = QAction("Create &shortcuts…", self)
        act_shortcuts.setToolTip("Add RiffTag to the desktop or the panel")
        act_shortcuts.triggered.connect(lambda: ShortcutsDialog(self).exec())
        opts.addAction(act_shortcuts)

        help_menu = self.menuBar().addMenu("&Help")
        act_about = QAction("&About RiffTag…", self)
        act_about.triggered.connect(lambda: AboutDialog(self).exec())
        help_menu.addAction(act_about)

        for key, index in (("F4", 0), ("Ctrl+F4", 1), ("Shift+F4", 2)):
            act = QAction(self)
            act.setShortcut(key)
            act.triggered.connect(lambda _, i=index: self.tabs.setCurrentIndex(i))
            self.addAction(act)

    #: action -> icon names to try, best first. Mint-L-Yellow (Dave's theme)
    #: has no edit-select-none, edit-select-invert, format-number or
    #: chronometer, so every entry carries fallbacks and anything that still
    #: resolves to nothing is shown as a text button rather than a blank one.
    TOOLBAR_ICONS = {
        "refresh": ("view-refresh",),
        "select_all": ("edit-select-all",),
        "select_none": ("edit-select-none", "edit-clear"),
        "invert": ("edit-select-invert", "object-flip-vertical"),
        "up": ("go-up",),
        "down": ("go-down",),
        "edit": ("document-edit", "document-properties"),
        "remove": ("edit-delete",),
        "play": ("media-playback-start",),
        "embed_art": ("insert-image",),
        "extract_art": ("document-export", "document-save-as"),
        "autofill": ("format-number", "view-sort-ascending"),
        "episode": ("chronometer", "office-calendar", "appointment-new"),
        "web": ("applications-internet", "web-browser", "globe",
                "network-workgroup"),
        "convert": ("media-optical", "audio-x-generic", "system-run"),
    }

    #: Qt's own icons, used when the desktop theme has nothing. This is what
    #: keeps the strip looking like a toolbar on Windows, where
    #: QIcon.fromTheme finds nothing at all and half the buttons came up as
    #: words — which blew the width out into an overflow menu.
    TOOLBAR_FALLBACK = {
        "refresh": QStyle.StandardPixmap.SP_BrowserReload,
        "select_all": QStyle.StandardPixmap.SP_FileDialogDetailedView,
        "select_none": QStyle.StandardPixmap.SP_DialogCancelButton,
        "invert": QStyle.StandardPixmap.SP_FileDialogContentsView,
        "up": QStyle.StandardPixmap.SP_ArrowUp,
        "down": QStyle.StandardPixmap.SP_ArrowDown,
        "edit": QStyle.StandardPixmap.SP_FileDialogInfoView,
        "remove": QStyle.StandardPixmap.SP_TrashIcon,
        "play": QStyle.StandardPixmap.SP_MediaPlay,
        "embed_art": QStyle.StandardPixmap.SP_DialogSaveButton,
        "extract_art": QStyle.StandardPixmap.SP_DialogOpenButton,
        "autofill": QStyle.StandardPixmap.SP_FileDialogListView,
        "episode": QStyle.StandardPixmap.SP_FileDialogListView,
        "web": QStyle.StandardPixmap.SP_DriveNetIcon,
        "convert": QStyle.StandardPixmap.SP_DriveDVDIcon,
    }

    @staticmethod
    def _icon(*names: str, fallback=None) -> QIcon:
        return themed_icon(*names, fallback=fallback)

    #: height of the wordmark in the menu bar, in logical pixels
    BRAND_HEIGHT = 20

    def _brand_menu_bar(self) -> None:
        """Put the wordmark in the menu bar's right-hand corner.

        A corner widget costs no vertical space of its own and stays visible
        whatever is open. The window title bar is drawn by the window manager
        and takes only text plus the icon, so this is the nearest equivalent.
        """
        label = QLabel()
        label.setPixmap(wordmark(self.BRAND_HEIGHT, self.devicePixelRatioF()))
        label.setContentsMargins(8, 0, 8, 0)
        label.setToolTip(f"RiffTag — {TAGLINE}")
        self.brand_label = label
        self.menuBar().setCornerWidget(label, Qt.Corner.TopRightCorner)

    def _build_toolbar(self) -> None:
        """The quick-tools strip, standing in for the original's top row."""
        bar = QToolBar("Tools")
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        bar.setIconSize(QSize(20, 20))
        self.toolbar = bar

        def act(key: str, text: str, slot, shortcut: str = "") -> QAction:
            a = QAction(self._icon(*self.TOOLBAR_ICONS[key],
                                   fallback=self.TOOLBAR_FALLBACK.get(key)),
                        text, self)
            a.setToolTip(text + (f"   ({shortcut})" if shortcut else ""))
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(slot)
            bar.addAction(a)
            if a.icon().isNull():
                # An icon-only button with no icon is an invisible button.
                button = bar.widgetForAction(a)
                if button is not None:
                    button.setToolButtonStyle(
                        Qt.ToolButtonStyle.ToolButtonTextOnly)
            return a

        # Release Check leads the strip. It is the thing you open the app
        # for when an album is finished, and a leading labelled button is the
        # one position that never ends up behind the overflow chevron.
        bar.addWidget(self._build_release_button())
        bar.addWidget(self._build_lyrics_button())
        bar.addSeparator()

        act("refresh", "Refresh", self._rescan)
        bar.addAction(self.act_recursive)
        bar.addSeparator()

        act("select_all", "Select all", self._select_all, "Ctrl+A")
        act("select_none", "Select none", self._select_none)
        act("invert", "Invert selection", self._invert_selection)
        bar.addAction(self.act_checkboxes)
        bar.addSeparator()

        act("up", "Move file up", lambda: self._move(-1), "Alt+Up")
        act("down", "Move file down", lambda: self._move(+1), "Alt+Down")
        bar.addSeparator()

        act("edit", "Edit all supported tag fields", self._open_tag_editor,
            "Ctrl+F5")
        act("remove", "Remove tags", self._remove_tags)
        act("play", "Play", self._play_selected)
        bar.addSeparator()

        act("embed_art", "Add folder.jpg to files tag", self._embed_folder_jpg)
        act("extract_art", "Extract folder.jpg from files tag",
            self._extract_folder_jpg)
        bar.addSeparator()

        bar.addWidget(self._build_track_button())
        act("autofill", "Auto fill track #…", self._autofill_track)
        act("episode", "Auto fill episode #…", self._autofill_episode)
        bar.addSeparator()
        act("web", "Get album titles from the Web…", self._web_import, "Ctrl+W")
        act("convert", "Convert audio format…", self._convert)

        stored = str(self.settings.value("toolbar_place", "list") or "list")
        self._place_toolbar(stored)

    def _build_release_button(self) -> QToolButton:
        """Release Check, carrying its own label.

        This is the feature someone opens the app *for* when an album is
        finished, so it does not get an icon-only button among twenty other
        icon-only buttons.
        """
        button = QToolButton()
        button.setText(" Release Check ")
        icon = self._icon("emblem-ok", "dialog-ok", "gtk-apply",
                          "checkbox-checked",
                          fallback=QStyle.StandardPixmap.SP_DialogApplyButton)
        if icon.isNull():
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        else:
            button.setIcon(icon)
            button.setIconSize(QSize(18, 18))
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setToolTip(
            "Check this release for what a distributor would send back   (F9)")
        button.clicked.connect(self._release_check)
        self.release_button = button

        shortcut = QAction(self)
        shortcut.setShortcut("F9")
        shortcut.triggered.connect(self._release_check)
        self.addAction(shortcut)
        return button

    def _build_lyrics_button(self) -> QToolButton:
        """A labelled dropdown, not another icon.

        The lyrics tools were in the Tools menu and nobody found them. Two
        more silhouettes in an icon-only strip would not have helped, so this
        one carries its own word.
        """
        button = QToolButton()
        button.setText("Lyrics ")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolTip("Clean up or import the lyrics on these files")
        menu = QMenu(button)
        menu.addAction("Clean lyrics for streaming…", self._clean_lyrics)
        menu.addAction("Import lyrics from text files…", self._import_lyrics)
        menu.addSeparator()
        menu.addAction("Sync lyrics to the music…", self._sync_lyrics)
        button.setMenu(menu)
        self.lyrics_menu = menu
        self.lyrics_button = button
        return button

    def _build_track_button(self) -> QToolButton:
        """The "track #" dropdown. Each entry shows what it produces."""
        button = QToolButton()
        button.setText("track # ")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolTip("Restyle the track numbers these files already have")
        menu = QMenu(button)
        for fmt in tracknum.FORMATS:
            action = menu.addAction(f"{fmt.label}\t{fmt.example()}")
            action.triggered.connect(
                lambda _, f=fmt: self._format_track_numbers(f))
        button.setMenu(menu)
        self.track_menu = menu
        return button

    def _place_toolbar(self, where: str) -> None:
        """Move the toolbar between the window top and above the file list."""
        for host in (self, self.list_host):
            host.removeToolBar(self.toolbar)
        host = self.list_host if where == "list" else self
        host.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)
        self.toolbar.show()
        self.toolbar_place = where
        self.settings.setValue("toolbar_place", where)
        if hasattr(self, "act_tb_top"):
            self.act_tb_top.setChecked(where == "top")
            self.act_tb_list.setChecked(where == "list")

    # --- theme ---

    def _apply_theme(self, theme: Theme) -> None:
        apply_theme(theme)
        self.settings.setValue("theme", theme.value)
        self.theme = theme
        for action, candidate in self._theme_actions.items():
            action.setChecked(candidate is theme)
        self._refresh_colours()

    def _refresh_colours(self) -> None:
        """Re-derive everything that was computed from the old palette.

        Widgets given an explicit palette keep it across an app-wide theme
        change, so each one is reset to the new application palette before its
        derived colours are recomputed.
        """
        app = QApplication.instance()
        if app is not None:
            for widget in (self.files, self.folder_tree, self.folder_panel,
                           self.filter_bar, self.totals, self.player,
                           self.tabs, self.legend, self.art_panel):
                widget.setPalette(app.palette())
        # The glyphs are drawn in the palette's ink colour; redraw them.
        self.player.refresh_theme()
        self.model.refresh_palette()
        apply_zebra(self.files)
        self.legend.refresh()
        self.update()

    # --- selection helpers ---

    def _select_all(self) -> None:
        if self.model.use_checkboxes:
            self.model.set_all_checked(True)
        else:
            self.files.selectAll()

    def _select_none(self) -> None:
        if self.model.use_checkboxes:
            self.model.set_all_checked(False)
        else:
            self.files.clearSelection()

    def _invert_selection(self) -> None:
        if self.model.use_checkboxes:
            self.model.invert_checked()
            return
        from PyQt6.QtCore import QItemSelection, QItemSelectionModel
        sel = self.files.selectionModel()
        for folder_row in range(self.model.rowCount()):
            parent = self.model.index(folder_row, 0)
            for row in range(self.model.rowCount(parent)):
                index = self.model.index(row, 0, parent)
                sel.select(index, QItemSelectionModel.SelectionFlag.Toggle
                           | QItemSelectionModel.SelectionFlag.Rows)

    def _toggle_checkboxes(self, on: bool) -> None:
        self.model.set_use_checkboxes(on)
        self.files.expandAll()
        self._selection_changed()

    def _move(self, delta: int) -> None:
        tracks = self.selected_tracks()
        if not tracks:
            return
        self.model.move_rows({t.path for t in tracks}, delta)
        self.files.expandAll()

    def _play_selected(self) -> None:
        """Play just the ticked/highlighted files, in list order."""
        tracks = self._require_selection()
        if tracks:
            self.player.play_tracks(tracks)

    def _play_all(self) -> None:
        """Play the whole list, starting at the selection if there is one."""
        tracks = self.model.all_tracks()
        if not tracks:
            QMessageBox.information(self, APP, "Nothing in the list to play.")
            return
        selected = self.selected_tracks()
        self.player.play_tracks(
            tracks, start=selected[0].path if selected else None)

    @staticmethod
    def _play_externally(path) -> None:
        """Fallback when Qt Multimedia is not installed: hand it to the
        desktop's own player, which is what the Play button always did."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _release_check(self) -> None:
        """The pre-flight report. Selection if there is one, else the list."""
        tracks = self.selected_tracks() or self.model.all_tracks()
        if not tracks:
            QMessageBox.information(
                self, APP, "Open the album you want to check first.")
            return
        PreflightDialog(tracks, self).exec()

    def _lyrics_targets(self) -> list:
        """Lyrics actions work on the selection, or the whole list.

        Both dialogs are previews until you press their button, so defaulting
        to everything costs nothing and matches how an album is worked on.
        """
        return self.selected_tracks() or self.model.all_tracks()

    def _clean_lyrics(self) -> None:
        tracks = self._lyrics_targets()
        if not tracks:
            QMessageBox.information(self, APP, "Nothing in the list.")
            return
        dialog = LyricsCleanDialog(tracks, self.write_options, self)
        dialog.changed.connect(self._lyrics_written)
        dialog.exec()

    def _import_lyrics(self) -> None:
        tracks = self._lyrics_targets()
        if not tracks:
            QMessageBox.information(self, APP, "Nothing in the list.")
            return
        dialog = LyricsImportDialog(tracks, self.write_options, self)
        dialog.changed.connect(self._lyrics_written)
        dialog.exec()

    def _sync_lyrics(self) -> None:
        """Tap-to-sync works on exactly one track — the one you can hear."""
        tracks = self.selected_tracks() or self.model.all_tracks()
        if not tracks:
            QMessageBox.information(self, APP, "Nothing in the list.")
            return
        track = tracks[0]
        if len(tracks) > 1:
            self.statusBar().showMessage(
                f"Syncing {track.path.name} — the first of the selection.")
        dialog = LrcSyncDialog(track, self.player, self.write_options, self)
        dialog.saved.connect(
            lambda path: self.statusBar().showMessage(f"Saved {path}"))
        dialog.exec()

    def _lyrics_written(self, paths) -> None:
        """Re-read what was written, so the grid and the editor agree."""
        for path in paths:
            try:
                self.model.replace_track(path, read(path))
            except TagError:
                continue
        self.statusBar().showMessage(f"Lyrics updated on {len(paths)} file(s)")

    def _convert(self) -> None:
        """Convert the selection, or the whole list when nothing is picked.

        Safe to default to everything because conversion never touches the
        originals — the output goes to a subfolder beside them.
        """
        tracks = self.selected_tracks() or self.model.all_tracks()
        if not tracks:
            QMessageBox.information(self, APP, "Nothing in the list to convert.")
            return
        dialog = ConvertDialog(tracks, self.write_options, self)
        dialog.converted.connect(self._converted)
        dialog.exec()

    def _converted(self) -> None:
        """New files landed in a subfolder — only a recursive view shows them."""
        if self.act_recursive.isChecked():
            self._rescan()

    def _offer_handoff(self) -> None:
        """Quitting mid-track: offer to hand it to the desktop's player.

        RiffTag's player stops when RiffTag does, which is a rude way to end
        a song you were listening to. The desktop player cannot be told where
        to resume from — there is no portable way to pass a start position —
        so this restarts the track rather than continuing it, and the prompt
        says so. Asked only when something is actually playing.
        """
        if not self.player.is_playing():
            return
        track = self.player.current()
        if track is None:
            return
        from .player import clock, track_label
        answer = QMessageBox.question(
            self, APP,
            f"{track_label(track)} is still playing.\n\n"
            f"Hand it to your default media player? It will start again "
            f"from the beginning, not from {clock(self.player.position_ms())}.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        self.player.stop()
        if answer is QMessageBox.StandardButton.Yes:
            self._play_externally(track.path)

    def _now_playing(self, track) -> None:
        if track is None:
            return
        from .player import track_label
        self.statusBar().showMessage(f"Playing: {track_label(track)}")

    # --- settings ---

    def splash_enabled(self) -> bool:
        """QSettings hands booleans back as the string "true"/"false"."""
        raw = self.settings.value("show_splash", True)
        if isinstance(raw, str):
            return raw.lower() not in ("false", "0", "")
        return bool(raw)

    def _flag(self, key: str, default: bool = True) -> bool:
        """QSettings hands booleans back as the string "true"/"false"."""
        raw = self.settings.value(key, default)
        if isinstance(raw, str):
            return raw.lower() not in ("false", "0", "")
        return bool(raw)

    def _stored(self, key: str, default: list[str]) -> list[str]:
        raw = self.settings.value(key)
        try:
            return json.loads(raw) if raw else list(default)
        except (TypeError, ValueError):
            return list(default)

    def maybe_offer_shortcuts(self) -> None:
        """First run only: ask once where the user wants to launch it from.

        Deliberately after the window is up, so the first thing they see is
        the app rather than a prompt from something they have not met yet.
        """
        if self.settings.value("shortcuts_offered"):
            return
        dialog = ShortcutsDialog(self, first_run=True)
        dialog.exec()
        if dialog.again.isChecked() or dialog.results:
            self.settings.setValue("shortcuts_offered", True)

    def _restore(self) -> None:
        stored = str(self.settings.value("theme", Theme.SYSTEM.value)
                     or Theme.SYSTEM.value)
        try:
            self._apply_theme(Theme(stored))
        except ValueError:
            self._apply_theme(Theme.SYSTEM)

        self.act_folder_panel.setChecked(self._flag("show_folder_panel"))
        self.act_player.setChecked(self._flag("show_player"))
        self.act_filter_bar.setChecked(self._flag("show_filter_bar"))

        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        last = self.settings.value("folder")
        start = Path(last) if last and Path(last).is_dir() else Path.home()
        self._reveal(start)
        if last and Path(last).is_dir():
            self._open_folder(start)

    def _reveal(self, folder: Path) -> None:
        """Scroll the folder tree to a path and expand down to it."""
        index = self.fs_model.index(str(folder))
        if not index.isValid():
            return
        self.folder_tree.setCurrentIndex(index)
        self.folder_tree.expand(index)
        self.folder_tree.scrollTo(
            index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.show_current_folder()

    #: how far inside the left edge the current folder's name is parked
    FOLDER_INSET = 24

    def show_current_folder(self) -> None:
        """Slide the folder column sideways until the current folder reads.

        Indentation is the whole problem: four levels down, the name starts
        past the right edge of a 260px column and the only way to read it was
        to drag the scroll bar. Qt's own scrollTo is no help — it either
        leaves the row where it is or parks the *end* of the name against the
        right edge, which hides the start of it.

        So: nudge only when the name is not already readable, and prefer the
        start of the name over the end of it. A folder wider than the whole
        column stays pinned at its left edge rather than chasing its tail.
        """
        tree = self.folder_tree
        index = tree.currentIndex()
        if not index.isValid():
            return
        rect = tree.visualRect(index)
        if rect.isNull() or not rect.isValid():
            return
        bar = tree.horizontalScrollBar()
        width = tree.viewport().width()
        if width < 50:
            return          # not laid out yet; showEvent will call again
        inset = self.FOLDER_INSET

        if rect.left() < inset:
            delta = rect.left() - inset                 # off to the left
        elif rect.right() > width:
            # Never scroll further than it takes to park the name's start at
            # the inset: for a long name that is what keeps it readable.
            delta = min(rect.left() - inset, rect.right() - width)
        else:
            return
        bar.setValue(bar.value() + delta)

    def closeEvent(self, event) -> None:
        # The scan thread goes first, before anything that can raise or block:
        # a QThread still running when its window is destroyed aborts the
        # process, and the hand-off prompt below is modal.
        self._stop_scan()
        self._offer_handoff()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue(
            "rename_masks",
            json.dumps([self.rename_tab.mask_row.combo.itemText(i)
                        for i in range(self.rename_tab.mask_row.combo.count())]))
        self._stop_scan()
        super().closeEvent(event)

    # --- scanning ---

    def _folder_clicked(self, index: QModelIndex) -> None:
        self._open_folder(Path(self.fs_model.filePath(index)))

    def _folder_renamed(self, parent: str, old: str, new: str) -> None:
        """Keep the list pointed at the folder after it is renamed underneath us."""
        current = getattr(self, "folder", None)
        if current is None:
            return
        old_path, new_path = Path(parent) / old, Path(parent) / new
        if current == old_path or old_path in current.parents:
            self.folder = Path(str(current).replace(str(old_path),
                                                    str(new_path), 1))
            self.settings.setValue("folder", str(self.folder))
            self.setWindowTitle(f"{APP} — {self.folder}")
            self._rescan()

    def _open_folder(self, folder: Path) -> None:
        self.folder = folder
        self.settings.setValue("folder", str(folder))
        self.setWindowTitle(f"{APP} — {folder}")
        self.filter_bar.set_folder(folder)
        self.folder_panel.set_current(folder)
        self._rescan()

    def _goto(self, folder) -> None:
        """Open a folder named from the address bar or a folder-panel menu."""
        folder = Path(folder)
        if not folder.is_dir():
            QMessageBox.warning(self, APP, f"No such folder:\n{folder}")
            return
        self._reveal(folder)
        self._open_folder(folder)

    def _refresh_tree(self) -> None:
        """The folder header's Refresh: re-read the tree and the file list."""
        current = getattr(self, "folder", None)
        if current is not None:
            # QFileSystemModel caches a directory it has already listed;
            # dropping the root path and setting it again is what makes it
            # notice folders created outside the app.
            self.fs_model.setRootPath("")
            self._reveal(current)
        self._rescan()

    # --- filtering ---

    def _apply_filter(self, key: str, text: str) -> None:
        self.model.set_filter(key, text)
        self.files.expandAll()
        self._filter_changed()

    def _hide_selected(self) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        self.model.hide_paths({t.path for t in tracks})
        self.files.expandAll()
        self._filter_changed()

    def _show_all(self) -> None:
        self.model.show_all()
        self.files.expandAll()
        self._filter_changed()

    def _filter_changed(self) -> None:
        shown = len(self.model.all_tracks())
        self.totals.set_listed(self.model.all_tracks())
        self._selection_changed()
        self.statusBar().showMessage(
            f"{shown} file(s) shown — filtered" if self.model.filter_active
            else f"{shown} file(s)")

    def _toggle_folder_panel(self, on: bool) -> None:
        self.folder_panel.setVisible(on)
        self.settings.setValue("show_folder_panel", on)

    def _toggle_player(self, on: bool) -> None:
        self.player.setVisible(on)
        self.settings.setValue("show_player", on)

    def _toggle_filter_bar(self, on: bool) -> None:
        self.filter_bar.setVisible(on)
        self.settings.setValue("show_filter_bar", on)

    def _rescan(self) -> None:
        folder = getattr(self, "folder", None)
        if folder is None:
            return
        self._stop_scan()
        self._scan_id += 1
        scan = self._scan_id
        self.model.clear()
        self.progress.show()
        self.progress.setValue(0)

        self._thread = QThread(self)
        self._worker = ScanWorker(folder, self.act_recursive.isChecked())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.found.connect(
            lambda tracks, i=scan: self._scan_found(i, tracks))
        self._worker.progress.connect(
            lambda done, total, i=scan: self._scan_progress(i, done, total))
        self._worker.finished.connect(lambda i=scan: self._scan_done(i))
        self._thread.start()

    def _scan_found(self, scan: int, tracks) -> None:
        """Add a batch, unless a newer scan has already taken over.

        Stopping a worker is not enough. Its ``found`` emissions are queued
        connections, so a batch already sitting in the event queue is still
        delivered *after* ``model.clear()`` — which put files from the
        previous folder into the new folder's list. Selecting all and saving
        would then have written tags to an album you were no longer looking
        at. Every scan carries an id; late results from an old one are
        dropped.
        """
        if scan != self._scan_id:
            return
        self.model.add_tracks(tracks)

    def _scan_progress(self, scan: int, done: int, total: int) -> None:
        if scan != self._scan_id:
            return
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.statusBar().showMessage(f"Reading tags… {done}/{total}")

    def _scan_done(self, scan: int = 0) -> None:
        if scan and scan != self._scan_id:
            return
        self.progress.hide()
        self.files.expandAll()
        n = len(self.model.all_tracks())
        self.totals.forget_sizes()
        self.totals.set_listed(self.model.all_tracks())
        self.totals.set_selected(self.selected_tracks())
        self.statusBar().showMessage(f"{n} file(s)")
        self._stop_scan()

    def _stop_scan(self) -> None:
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = self._worker = None

    # --- selection ---

    def selected_tracks(self) -> list:
        """What the next action applies to, always in display order.

        With check boxes on, the ticks win — that is the whole point of the
        mode. Otherwise it is the highlighted rows, expanded so that selecting
        a folder means every file in it.

        The sort matters: ``selectedRows()`` returns rows in the order they
        were clicked, so a ctrl-click selection built bottom-up would number
        backwards under Auto Fill Track #.
        """
        if self.model.use_checkboxes:
            return self.model.checked_tracks()
        seen, out = set(), []
        for index in self.files.selectionModel().selectedRows():
            for track in self.model.tracks_under(index):
                if track.path not in seen:
                    seen.add(track.path)
                    out.append(track)
        out.sort(key=lambda t: self.model.position_of(t.path))
        return out

    def _selection_changed(self, *_) -> None:
        tracks = self.selected_tracks()
        self.totals.set_selected(tracks)
        self.statusBar().showMessage(
            f"{len(tracks)} selected" if tracks
            else f"{len(self.model.all_tracks())} file(s)")
        self._show_art(tracks[0] if tracks else None)

    def _show_art(self, track) -> None:
        if track and track.pictures:
            pm = QPixmap()
            pm.loadFromData(track.pictures[0].data)
            self.file_art.setPixmap(pm.scaled(
                190, 190, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.file_art.setPixmap(QPixmap())
            self.file_art.setText("no cover art\n\n(drop a picture here)")

        jpg = self._folder_jpg(track.path.parent) if track else None
        if jpg:
            pm = QPixmap(str(jpg))
            self.folder_art.setPixmap(pm.scaled(
                190, 190, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.folder_art.setPixmap(QPixmap())
            self.folder_art.setText("no folder.jpg\n\n(drop a picture here)")

    def _file_art_dropped(self, data: bytes, mime: str) -> None:
        """A picture dropped on File cover art goes into the selected tags."""
        tracks = self.selected_tracks()
        if not tracks:
            QMessageBox.information(
                self, APP, "Select the files to put that cover on first.")
            return
        picture = Picture(data, mime)
        written, failed = [], []
        for track in tracks:
            try:
                write(track.path, {}, pictures=[picture],
                      options=self.write_options)
            except TagError as exc:
                failed.append(f"{track.path.name}: {exc}")
                continue
            written.append(track.path)
            try:
                self.model.replace_track(track.path, read(track.path))
            except TagError:
                pass
        self._show_art(self.selected_tracks()[0] if self.selected_tracks()
                       else None)
        message = f"Cover art set on {len(written)} file(s)."
        if failed:
            message += f" {len(failed)} failed — {failed[0]}"
        self.statusBar().showMessage(message)

    def _folder_art_dropped(self, data: bytes, mime: str) -> None:
        """A picture dropped on Folder cover art is written as folder.jpg.

        Always folder.jpg, converting if need be: that name is what the rest
        of the app looks for, and writing folder.png beside an existing
        folder.jpg would leave the stale one winning the lookup.
        """
        folder = getattr(self, "folder", None)
        tracks = self.selected_tracks()
        if folder is None and tracks:
            folder = tracks[0].path.parent
        if folder is None:
            QMessageBox.information(self, APP, "Open a folder first.")
            return
        try:
            jpeg = as_jpeg(data, mime)
        except ArtError as exc:
            self.statusBar().showMessage(f"Not used: {exc}")
            return
        target = Path(folder) / "folder.jpg"
        try:
            target.write_bytes(jpeg)
        except OSError as exc:
            QMessageBox.warning(self, APP, f"Could not write {target}:\n{exc}")
            return
        self._show_art(tracks[0] if tracks else None)
        converted = "" if mime == "image/jpeg" else " (converted to JPEG)"
        self.statusBar().showMessage(f"Saved {target}{converted}")

    @staticmethod
    def _folder_jpg(folder: Path) -> Path | None:
        """folder.jpg, then folder.png — the 3.9.6 fallback."""
        for name in ("folder.jpg", "folder.png", "cover.jpg", "cover.png"):
            candidate = folder / name
            if candidate.exists():
                return candidate
        return None

    def _require_selection(self) -> list:
        tracks = self.selected_tracks()
        if not tracks:
            QMessageBox.information(self, APP, "Select some files first.")
        return tracks

    # --- rename ---

    def _render_options(self) -> RenderOptions:
        return RenderOptions(case=self.rename_tab.case.rules(),
                             underscores=self.rename_tab.underscores.isChecked())

    def _do_rename(self, preview_only: bool) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        mask = self.rename_tab.mask_row.mask().strip()
        if not mask:
            QMessageBox.information(self, APP, "Enter a file name mask.")
            return
        steps = plan(tracks, mask, options=self._render_options())

        if preview_only:
            rows = [(s.src.name,
                     s.dst.name if s.status is not Status.ERROR else "",
                     "" if s.status is Status.OK else
                     f"{s.status.value}: {s.message}".strip(": "))
                    for s in steps]
            movable = [s for s in steps if s.will_move]
            dialog = PreviewDialog(
                rows, "Test — nothing has been renamed yet", self,
                action_text=(f"Rename {len(movable)} file(s)" if movable
                             else None))
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._do_rename(False)
            return

        movable = [s for s in steps if s.will_move]
        if not movable:
            QMessageBox.information(self, APP, "Nothing to rename.")
            return
        tx = apply_renames(steps)
        self.last_transaction = tx
        self.rename_tab.btn_undo.setEnabled(bool(tx.moves))
        self._report(tx, f"Renamed {tx.count} file(s).")
        self._rescan()

    def _undo_rename(self) -> None:
        if not self.last_transaction:
            return
        back = undo_renames(self.last_transaction)
        self.last_transaction = None
        self.rename_tab.btn_undo.setEnabled(False)
        self._report(back, f"Undid {back.count} rename(s).")
        self._rescan()

    def _report(self, tx, message: str) -> None:
        if tx.failures:
            QMessageBox.warning(
                self, APP,
                message + "\n\nFailed:\n" +
                "\n".join(f"{p.name}: {e}" for p, e in tx.failures[:12]))
        else:
            self.statusBar().showMessage(message)

    def _save_current_mask(self) -> None:
        mask = self.rename_tab.mask_row.mask().strip()
        combo = self.rename_tab.mask_row.combo
        if mask and combo.findText(mask) < 0:
            combo.addItem(mask)
            self.statusBar().showMessage(f"Saved mask: {mask}")

    # --- filename to tag ---

    def _do_parse(self, preview_only: bool) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        mask = self.parse_tab.mask_row.mask().strip()
        if not mask:
            QMessageBox.information(self, APP, "Enter a file name mask.")
            return
        rules = self.parse_tab.case.rules()
        rows, writes = [], []
        for track in tracks:
            stem = str(track.path.with_suffix(""))
            if self.parse_tab.underscores.isChecked():
                stem = stem.replace("_", " ")
            try:
                values = parse(mask, stem, case=rules)
                rows.append((track.path.name,
                             ", ".join(f"{k}={v}" for k, v in values.items()), ""))
                writes.append((track, values))
            except MaskError as exc:
                rows.append((track.path.name, "", str(exc)))

        if preview_only:
            dialog = PreviewDialog(
                rows, "Preview — no tags have been written yet", self,
                action_text=(f"Write tags to {len(writes)} file(s)" if writes
                             else None))
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._do_parse(False)
            return

        failures = []
        for track, values in writes:
            try:
                write(track.path, values, options=self.write_options)
            except TagError as exc:
                failures.append(str(exc))
        self._finish_write(len(writes), failures)

    # --- multi-file tag editing ---

    def _save_multi(self) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        values = self.multi_tab.values()
        if not values:
            QMessageBox.information(
                self, APP, "Tick the fields you want written first.")
            return
        rules = self.multi_tab.case.rules()
        if rules.mode is not Case.NONE:
            values = {k: rules.apply(v) for k, v in values.items()}
        failures = []
        for track in tracks:
            try:
                write(track.path, values, options=self.write_options)
            except TagError as exc:
                failures.append(str(exc))
        self._finish_write(len(tracks), failures)

    def _remove_tags(self) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        if QMessageBox.question(
                self, APP,
                f"Remove all tag data from {len(tracks)} file(s)?\n"
                "This cannot be undone.") is not QMessageBox.StandardButton.Yes:
            return
        failures = []
        for track in tracks:
            try:
                remove_tags(track.path)
            except TagError as exc:
                failures.append(str(exc))
        self._finish_write(len(tracks), failures)

    def _edit_one(self, index) -> None:
        """Double-click opens just that file, with Prev/Next to walk the rest."""
        track = self.model.track(index)
        if track is None:
            self.files.setExpanded(index, not self.files.isExpanded(index))
            return
        dialog = TagEditorDialog([track], self, self.write_options,
                                 siblings=self.model.all_tracks())
        dialog.exec()
        if dialog.renamed:
            self._rescan()
        elif dialog.changed_any:
            self._refresh_tracks(dialog.siblings)

    def _web_import(self) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        token = str(self.settings.value("discogs_token", "") or "")
        dialog = WebImportDialog(tracks, self, discogs_token=token,
                                 write_options=self.write_options)
        dialog.exec()
        if dialog.wrote:
            self.statusBar().showMessage(
                f"Imported tags into {dialog.wrote} file(s)")
            self._refresh_tracks(tracks)

    def _set_discogs_token(self) -> None:
        from PyQt6.QtWidgets import QInputDialog, QLineEdit as _LineEdit
        current = str(self.settings.value("discogs_token", "") or "")
        token, ok = QInputDialog.getText(
            self, "Discogs token",
            "Personal access token from discogs.com/settings/developers.\n"
            "MusicBrainz needs no token and works without this.",
            _LineEdit.EchoMode.Normal, current)
        if ok:
            self.settings.setValue("discogs_token", token.strip())

    def _copy_focused(self) -> None:
        index = self.files.currentIndex()
        track = self.model.track(index)
        if track is None:
            QMessageBox.information(self, APP, "Click a file first.")
            return
        self.multi_tab.load_from(track.values)

    def _open_tag_editor(self, *_) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        # Siblings are every file on screen, so Save and Next walks the whole
        # folder rather than only the handful that happened to be selected.
        dialog = TagEditorDialog(tracks, self, self.write_options,
                                 siblings=self.model.all_tracks())
        dialog.exec()
        if dialog.renamed:
            self._rescan()              # paths moved; the model is stale
        elif dialog.changed_any:
            self._refresh_tracks(dialog.siblings)

    # --- tools ---

    def _apply_to_selection(self, fn, label: str) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        failures = []
        for i, track in enumerate(tracks, 1):
            values = fn(track, i)
            if not values:
                continue
            try:
                write(track.path, values, options=self.write_options)
            except TagError as exc:
                failures.append(str(exc))
        self._finish_write(len(tracks), failures, label)

    def _autofill_track(self) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        dialog = AutoFillTrackDialog(len(tracks), self)
        if not dialog.exec():
            return
        start, use_total = dialog.options()
        plans = tracknum.autofill(len(tracks), start=start, use_total=use_total)
        self._write_each(tracks, plans, "Filled track numbers in")

    def _autofill_episode(self) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        dialog = AutoFillTrackDialog(len(tracks), self, episode=True)
        if not dialog.exec():
            return
        start, _ = dialog.options()
        plans = [{"episode_number": str(start + i)} for i in range(len(tracks))]
        self._write_each(tracks, plans, "Filled episode numbers in")

    def _format_track_numbers(self, fmt) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        self._write_each(tracks, tracknum.reformat(tracks, fmt),
                         f"Reformatted track # ({fmt.key}) in")

    def _write_each(self, tracks, plans, label: str) -> None:
        """Write a different value-dict to each track, skipping empty ones."""
        failures, done = [], 0
        for track, values in zip(tracks, plans):
            if not values:
                continue
            try:
                write(track.path, values, options=self.write_options)
                done += 1
            except TagError as exc:
                failures.append(str(exc))
        self._finish_write(done, failures, label)

    def _artist_to_album_artist(self) -> None:
        self._apply_to_selection(
            lambda t, i: ({"album_artist": t.values["artist"]}
                          if t.values.get("artist") else {}),
            "Copied Artist to Album Artist")

    def _strip_leading_the(self) -> None:
        def fix(track, _):
            artist = str(track.values.get("artist", ""))
            return ({"artist": artist[4:].strip()}
                    if artist.lower().startswith("the ") else {})
        self._apply_to_selection(fix, 'Removed leading "The"')

    def _trim_spaces(self) -> None:
        def fix(track, _):
            return {k: v.strip() for k, v in track.values.items()
                    if isinstance(v, str) and v != v.strip()}
        self._apply_to_selection(fix, "Trimmed spaces")

    def _embed_folder_jpg(self) -> None:
        from ..tagio import Picture
        tracks = self._require_selection()
        if not tracks:
            return
        failures, done = [], 0
        for track in tracks:
            jpg = self._folder_jpg(track.path.parent)
            if jpg is None:
                continue
            mime = "image/png" if jpg.suffix == ".png" else "image/jpeg"
            try:
                write(track.path, {},
                      pictures=[Picture(jpg.read_bytes(), mime)],
                      options=self.write_options)
                done += 1
            except TagError as exc:
                failures.append(str(exc))
        self._finish_write(done, failures, "Embedded folder cover art")

    def _extract_folder_jpg(self) -> None:
        tracks = self._require_selection()
        if not tracks:
            return
        written = set()
        for track in tracks:
            if not track.pictures or track.path.parent in written:
                continue
            pic = track.pictures[0]
            name = "folder.png" if pic.mime == "image/png" else "folder.jpg"
            try:
                (track.path.parent / name).write_bytes(pic.data)
                written.add(track.path.parent)
            except OSError:
                pass
        self.statusBar().showMessage(f"Wrote cover art to {len(written)} folder(s)")
        self._selection_changed()

    # --- shared ---

    def _finish_write(self, count: int, failures: list[str],
                      label: str = "Wrote tags to") -> None:
        if failures:
            QMessageBox.warning(self, APP, "Some files failed:\n"
                                + "\n".join(failures[:12]))
        else:
            self.statusBar().showMessage(f"{label} {count} file(s)")
        self._refresh_tracks(self.selected_tracks())

    def _refresh_tracks(self, tracks) -> None:
        for track in tracks:
            if track.path.exists():
                self.model.replace_track(track.path, read(track.path))
        self._selection_changed()

    def changeEvent(self, event) -> None:
        """Re-derive the row tints when the desktop theme changes."""
        from PyQt6.QtCore import QEvent
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange,
                            QEvent.Type.ApplicationPaletteChange):
            self.model.refresh_palette()

    def _toggle_extensions(self, on: bool) -> None:
        self.model.show_extensions = on
        self.model.layoutChanged.emit()

    def _fit_columns(self) -> None:
        for i in range(self.model.columnCount()):
            self.files.resizeColumnToContents(i)


def run(argv: list[str] | None = None) -> int:
    import sys
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP)
    app.setOrganizationName(ORG)
    # Before the palette is captured: changing style changes the standard
    # palette, and everything in this app is derived from the palette.
    use_portable_style(app)
    # Must happen before any theme is applied: once setPalette has overridden
    # it there is no way to ask Qt for the desktop's own palette again.
    capture_system_palette(app)
    app.setWindowIcon(app_icon())

    # Read the preference before building the window: QMainWindow's own
    # construction (folder scan, theme) is the slow part the splash covers.
    wanted = QSettings(ORG, APP).value("show_splash", True)
    if isinstance(wanted, str):
        wanted = wanted.lower() not in ("false", "0", "")
    splash = show_splash(bool(wanted))
    if splash is not None:
        splash.say("Starting…")
        app.processEvents()

    window = MainWindow()

    def reveal() -> None:
        window.show()
        if splash is not None:
            splash.finish_for(window)
        QTimer.singleShot(300, window.maybe_offer_shortcuts)

    if splash is None:
        reveal()
    else:
        splash.say("Ready")
        # Build first, then wait out whatever is left of the minimum — the
        # window is ready either way, so the hold costs nothing but the look.
        QTimer.singleShot(splash.remaining_ms(), reveal)
    return app.exec()
