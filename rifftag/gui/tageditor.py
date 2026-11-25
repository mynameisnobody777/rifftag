"""The full Tag Editor dialog — "Edit All Supported Tag Fields".

Five tabs (General / Lyrics / Additional / More / Sorting) built from
:data:`rifftag.fields.FIELDS`, so adding a field to the model adds it here
with no further work.

Every field has a checkbox, exactly as in the original. Over a multi-file
selection an unchecked field is left alone; a field whose files disagree shows
``<different values>`` as a placeholder and stays unchecked until you
deliberately tick it. That is what stops a careless Save flattening 33
different Titles into one.

Single-file mode adds what the original has along the bottom —
**Save and Prev / Save / Cancel / Remove Tag / Save and Next** — so a whole
album can be worked through without the dialog closing between tracks, plus a
Play button that hands the file to the desktop's default player.

The **File** box at the top is editable: changing it renames the file on save.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
                             QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QPushButton,
                             QScrollArea, QSizePolicy, QTabWidget, QTextEdit,
                             QVBoxLayout, QWidget)

from ..casing import CaseRules
from ..fields import BY_KEY, GROUPS, Kind, group
from ..tagio import Picture, Track, WriteOptions, read, remove_tags, write
from .art import ArtDropLabel, ArtError, from_path
from .tabs import GENRES, CaseBox

GROUP_TITLES = {
    "general": "General", "lyrics": "Lyrics", "additional": "Additional",
    "more": "More Additional", "sorting": "Sorting",
}
RATINGS = ["Unrated", "1 star", "2 stars", "3 stars", "4 stars", "5 stars"]
MIXED = "<different values>"

#: General tab, right-hand column — the short numeric and choice fields the
#: original parks beside Title/Artist/Album instead of stacking them below.
GENERAL_RIGHT = ("track", "disc", "rating", "mood", "bpm", "compilation")
#: Pairs that share one row, as in the original.
GENERAL_PAIRS = {"year": "genre"}


#: Filled by a copy, but never ticked by one — so they are visible and one
#: click away, and a reflexive Save does not carry them across.
#:
#: These are the fields that belong to the *song*, not to the release. Dave's
#: workflow is filling in an untagged track from a sibling on the same album:
#: everything about the album is wanted, the title and the words are not.
#: ``sort_title`` goes with ``title`` because it is the same field — leaving
#: it ticked would write one song's sort name onto another.
PER_TRACK_FIELDS = ("title", "sort_title", "lyrics")


class TagEditorDialog(QDialog):
    """Edit one file, or a whole selection at once.

    ``siblings`` is the full file list the Prev/Next buttons walk; it is only
    used when exactly one track is being edited.

    ``source`` is the row the user had highlighted, which is a *different*
    thing from the files being edited whenever check boxes are in use — the
    ticks say where to write, the highlight says what to copy from. That
    separation is the whole point of "Copy from Focused File".
    """

    def __init__(self, tracks: list[Track], parent=None,
                 options: WriteOptions | None = None,
                 siblings: list[Track] | None = None,
                 source: Track | None = None):
        super().__init__(parent)
        self.tracks = list(tracks)
        self.options = options or WriteOptions()
        self.siblings = list(siblings) if siblings else list(tracks)
        #: The highlighted file, to copy every field from. Tag&Rename's
        #: workflow: tick the empty track, highlight one that is already
        #: tagged, copy the lot across, untick what you do not want.
        self.source = source
        #: paths the dialog renamed, so the caller knows to rescan
        self.renamed: list[tuple[Path, Path]] = []
        self.changed_any = False

        self.checks: dict[str, QCheckBox] = {}
        self.inputs: dict[str, QWidget] = {}
        self._pictures: list[Picture] | None = None
        self._pictures_dirty = False

        self.resize(980, 660)
        self._build()
        self._load()
        # Tag&Rename did this the moment the editor opened, with no button
        # to find, and that is the habit worth matching. Only at
        # construction: _load() runs again for Prev/Next, and stepping to
        # another file must show that file's own tags.
        if self.btn_copy.isEnabled():
            self._copy_from_source()

    # --- construction ---

    def _build(self) -> None:
        self.tabs = QTabWidget()
        for name in GROUPS:
            self.tabs.addTab(self._build_group(name), GROUP_TITLES[name])

        body = QHBoxLayout()
        body.addWidget(self.tabs, 3)
        body.addWidget(self._build_art_panel(), 1)

        self.case = CaseBox()

        root = QVBoxLayout(self)
        root.addWidget(self._build_file_row())
        root.addLayout(body)
        root.addWidget(self.case)
        root.addLayout(self._build_buttons())

    def _build_file_row(self) -> QWidget:
        """The editable File box. Renaming here renames on disk at Save."""
        self.file_edit = QLineEdit()
        self.file_edit.setToolTip(
            "Full path. Edit it and press Save to rename the file.")
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 4)
        lay.addWidget(QLabel("File:"))
        lay.addWidget(self.file_edit, 1)
        return row

    def _build_group(self, name: str) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)

        left = QGridLayout()
        right = QGridLayout()
        skip: set[str] = set()
        lrow = rrow = 0

        for fld in group(name):
            if fld.key.endswith("_total") or fld.key in skip:
                continue
            if fld.kind is Kind.PICTURE:
                continue              # the art panel owns this one
            target, row = ((right, rrow) if (name == "general"
                                             and fld.key in GENERAL_RIGHT)
                           else (left, lrow))
            partner = GENERAL_PAIRS.get(fld.key) if name == "general" else None
            self._add_field(target, row, fld, partner)
            if partner:
                skip.add(partner)
            if target is right:
                rrow += 1
            else:
                lrow += 1

        if name == "lyrics":
            # Tag&Rename puts Load/Save right on this tab, and it is the one
            # field people keep in a text file of their own.
            left.addWidget(self._build_lyrics_buttons(), lrow, 0, 1, 3)
            lrow += 1

        left.setColumnStretch(2, 1)
        left.setRowStretch(lrow, 1)
        outer.addLayout(left, 3)
        if name == "general":
            right.setRowStretch(rrow, 1)
            outer.addLayout(right, 1)
            outer.addWidget(self._build_info_panel())

        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _add_field(self, grid: QGridLayout, row: int, fld,
                   partner_key: str | None) -> None:
        widget = self._widget_for(fld)
        self.checks[fld.key] = QCheckBox()
        self.inputs[fld.key] = widget
        align = (Qt.AlignmentFlag.AlignTop if fld.kind is Kind.LONGTEXT
                 else Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(QLabel(fld.label), row, 0, align)
        grid.addWidget(self.checks[fld.key], row, 1, align)
        self._autocheck(fld.key, widget)

        if partner_key is None:
            grid.addWidget(widget, row, 2)
            return

        partner = BY_KEY[partner_key]
        pwidget = self._widget_for(partner)
        self.checks[partner_key] = QCheckBox()
        self.inputs[partner_key] = pwidget
        self._autocheck(partner_key, pwidget)
        widget.setMaximumWidth(90)

        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget)
        lay.addWidget(QLabel(partner.label))
        lay.addWidget(self.checks[partner_key])
        lay.addWidget(pwidget, 1)
        grid.addWidget(holder, row, 2)

    def _widget_for(self, fld) -> QWidget:
        if fld.kind is Kind.LONGTEXT:
            w = QTextEdit()
            w.setMinimumHeight(110 if fld.key == "lyrics" else 60)
            return w
        if fld.kind is Kind.BOOL:
            return QCheckBox("yes")
        if fld.kind is Kind.RATING:
            w = QComboBox()
            w.addItems(RATINGS)
            return w
        if fld.key == "genre":
            w = QComboBox()
            w.setEditable(True)
            w.addItems(GENRES)
            return w
        if fld.total_of:
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            num, total = QLineEdit(), QLineEdit()
            num.setMaximumWidth(60)
            total.setMaximumWidth(60)
            lay.addWidget(num)
            lay.addWidget(QLabel("of"))
            lay.addWidget(total)
            lay.addStretch(1)
            w.num, w.total = num, total            # type: ignore[attr-defined]
            return w
        return QLineEdit()

    def _build_lyrics_buttons(self) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 2, 0, 2)
        load = QPushButton("&Load from file…")
        load.setToolTip("Read a .txt or .lrc into the Lyrics box")
        load.clicked.connect(self._load_lyrics)
        save = QPushButton("Save to &file…")
        save.setToolTip("Write what is in the Lyrics box out to a text file")
        save.clicked.connect(self._save_lyrics)
        lay.addWidget(load)
        lay.addWidget(save)
        lay.addStretch(1)
        self.btn_lyrics_load, self.btn_lyrics_save = load, save
        return row

    def _lyrics_widget(self):
        return self.inputs.get("lyrics")

    def _lyrics_default_path(self) -> Path:
        """Beside the track, named after it — what you would have typed."""
        track = self.tracks[0] if self.tracks else None
        if track is None:
            return Path.home() / "lyrics.txt"
        if self.single:
            return track.path.with_suffix(".txt")
        return track.path.parent / "lyrics.txt"

    def _load_lyrics(self) -> None:
        widget = self._lyrics_widget()
        if widget is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Load lyrics", str(self._lyrics_default_path()),
            "Lyrics (*.txt *.lrc *.text);;All files (*)")
        if not path:
            return
        from ..lyrics import read_text
        try:
            text = read_text(Path(path))
        except OSError as exc:
            QMessageBox.warning(self, "Load lyrics",
                                f"Could not read {path}:\n{exc}")
            return
        # setPlainText fires textChanged, which ticks the Lyrics box — so
        # Save actually writes what was just loaded.
        widget.setPlainText(text)
        self.checks["lyrics"].setChecked(True)

    def _save_lyrics(self) -> None:
        widget = self._lyrics_widget()
        if widget is None:
            return
        text = widget.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Save lyrics",
                                    "There are no lyrics to save.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save lyrics", str(self._lyrics_default_path()),
            "Text (*.txt);;LRC (*.lrc);;All files (*)")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Save lyrics",
                                f"Could not write {path}:\n{exc}")

    def _build_info_panel(self) -> QWidget:
        box = QGroupBox("File information:")
        box.setSizePolicy(QSizePolicy.Policy.Maximum,
                          QSizePolicy.Policy.Preferred)
        self.info_label = QLabel("")
        self.info_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay = QVBoxLayout(box)
        lay.addWidget(self.info_label)
        lay.addStretch(1)
        return box

    def _build_art_panel(self) -> QWidget:
        box = QGroupBox("Art")
        self.art_label = ArtDropLabel("no cover art\n\n(drop a picture here)")
        self.art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art_label.setMinimumSize(200, 200)
        self.art_label.setFrameShape(QFrame.Shape.StyledPanel)
        self.art_label.art_dropped.connect(self._art_dropped)
        self.art_label.drop_failed.connect(
            lambda why: self.art_info.setText(f"Not used: {why}"))
        self.art_info = QLabel("")
        self.art_info.setAlignment(Qt.AlignmentFlag.AlignCenter)

        add, save, remove = (QPushButton("&Add…"), QPushButton("Save to &File…"),
                             QPushButton("&Remove"))
        add.clicked.connect(self._add_art)
        save.clicked.connect(self._save_art)
        remove.clicked.connect(self._remove_art)

        lay = QVBoxLayout(box)
        lay.addWidget(self.art_label)
        lay.addWidget(self.art_info)
        for b in (add, save, remove):
            lay.addWidget(b)
        lay.addStretch(1)
        return box

    def _build_buttons(self) -> QHBoxLayout:
        """Bottom row, in the original's order, with Play on the far left."""
        self.btn_play = QPushButton("▶")
        self.btn_play.setToolTip("Play in the default audio player")
        self.btn_play.setFixedWidth(40)
        self.btn_play.clicked.connect(self._play)

        self.btn_prev = QPushButton("Save and &Prev")
        self.btn_save = QPushButton("&Save")
        self.btn_cancel = QPushButton("&Cancel")
        self.btn_remove_tag = QPushButton("Remove &Tag")
        self.btn_next = QPushButton("Save and &Next")

        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next.clicked.connect(lambda: self._step(+1))
        self.btn_save.clicked.connect(self._save_and_close)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_remove_tag.clicked.connect(self._remove_tag)
        self.btn_save.setDefault(True)

        self.btn_copy = QPushButton("&Copy from Focused File")
        self.btn_copy.clicked.connect(self._copy_from_source)
        self._configure_copy_button()

        row = QHBoxLayout()
        row.addWidget(self.btn_play)
        row.addWidget(self.btn_copy)
        row.addStretch(1)
        for b in (self.btn_prev, self.btn_save, self.btn_cancel,
                  self.btn_remove_tag, self.btn_next):
            row.addWidget(b)
        return row

    def _configure_copy_button(self) -> None:
        """Only offered when there is something to copy *from*.

        Copying a file onto itself does nothing, so the button is disabled
        when the highlighted row is one of the files being edited — which is
        every time check boxes are off, because then the highlight *is* the
        selection. The tooltip says so rather than leaving a dead button.
        """
        editing = {t.path for t in self.tracks}
        usable = self.source is not None and self.source.path not in editing
        self.btn_copy.setEnabled(usable)
        if usable:
            self.btn_copy.setToolTip(
                f"Fill every field from {self.source.path.name}, cover art "
                "included. Done automatically when this window opens; press "
                "it again after highlighting a different file.\n\nTitle and "
                "Lyrics are filled but left unticked, so they are not written "
                "unless you tick them.")
        elif self.source is not None:
            self.btn_copy.setToolTip(
                "The highlighted file is one of the files being edited, so "
                "there is nothing to copy from.\n\nTurn on View \u2192 Use "
                "check boxes to select files, tick the file to write to, then "
                "highlight the file to copy from.")
        else:
            self.btn_copy.setToolTip("Highlight a file to copy its tags from.")

    def _copy_from_source(self) -> None:
        if self.source is None:
            return
        filled = self.copy_from(self.source.values, self.source.pictures)
        self.setWindowTitle(
            f"Tag Editor [{len(self.tracks)} File(s)] \u2014 copied "
            f"{filled} field(s) from {self.source.path.name}; "
            "Title and Lyrics are filled but not ticked")

    def copy_from(self, values: dict, pictures: list | None = None) -> int:
        """Fill every field from another file's tags, ticking what it has.

        Ticking is the point: a field this dialog does not tick is not
        written at all, so an untagged target keeps whatever it already had
        rather than being flattened with blanks. Returns how many were
        ticked, for the title bar.

        Absence and emptiness are different. A key the source does not carry
        leaves its box unticked; a key it carries as an explicit ``False``
        (Compilation, say) is still a value worth copying, so the test is
        "the source has this key" and not "the text is truthy".

        ``PER_TRACK_FIELDS`` are filled but never ticked — see there.

        ``pictures`` comes across too, because cover art is part of the tag
        and an album's art is the same on every track — which is the case
        this is for. It has no tick box of its own, so it is only taken when
        the source actually has some: a source with no art must not wipe art
        the target already had. Remove in the Art panel undoes it before Save.
        """
        filled = 0
        for key, widget in self.inputs.items():
            present = key in values and str(values[key] or "") != ""
            text = str(values.get(key, "") or "")
            if hasattr(widget, "num"):
                total = str(values.get(key + "_total", "") or "")
                widget.num.setText(text)                       # type: ignore[attr-defined]
                widget.total.setText(total)                    # type: ignore[attr-defined]
            elif isinstance(widget, QLineEdit):
                widget.setText(text)
            elif isinstance(widget, QComboBox):
                if widget.isEditable():
                    widget.setEditText(text)
                else:
                    widget.setCurrentIndex(int(text) if text.isdigit() else 0)
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(text)
            elif isinstance(widget, QCheckBox):
                # Not tristate any more: this is one file's answer, not a
                # mixture of several.
                widget.setTristate(False)
                widget.setCheckState(
                    Qt.CheckState.Checked if text in ("True", "1")
                    else Qt.CheckState.Unchecked)
                present = key in values
            # Last, and deliberately: setting a combo or a text box fires the
            # signals _autocheck listens to, so an explicit tick here is what
            # decides, not the side effect.
            if key in PER_TRACK_FIELDS:
                present = False
            self.checks[key].setChecked(present)
            if present:
                filled += 1

        if pictures:
            self._pictures = list(pictures)
            self._pictures_dirty = True
            self._show_art(self._pictures[0])
            filled += 1
        return filled

    def _autocheck(self, key: str, widget: QWidget) -> None:
        """Typing in a field ticks its box — otherwise everyone forgets."""
        def tick(*_):
            self.checks[key].setChecked(True)
        if isinstance(widget, QLineEdit):
            widget.textEdited.connect(tick)
        elif isinstance(widget, QComboBox):
            (widget.editTextChanged if widget.isEditable()
             else widget.currentIndexChanged).connect(tick)
        elif isinstance(widget, QTextEdit):
            widget.textChanged.connect(tick)
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(tick)
        elif hasattr(widget, "num"):
            widget.num.textEdited.connect(tick)       # type: ignore[attr-defined]
            widget.total.textEdited.connect(tick)     # type: ignore[attr-defined]

    # --- loading ---

    @property
    def single(self) -> bool:
        return len(self.tracks) == 1

    def _position(self) -> int:
        if not self.single:
            return -1
        path = self.tracks[0].path
        for i, t in enumerate(self.siblings):
            if t.path == path:
                return i
        return -1

    def _common(self, key: str):
        seen = {str(t.values.get(key, "") or "") for t in self.tracks}
        return seen.pop() if len(seen) == 1 else MIXED

    def _load(self) -> None:
        self.setWindowTitle(f"Tag Editor [{len(self.tracks)} File(s)]")
        for key, widget in self.inputs.items():
            value = self._common(key)
            mixed = value is MIXED
            text = "" if mixed else value
            placeholder = MIXED if mixed else ""
            if hasattr(widget, "num"):
                total = self._common(key + "_total")
                widget.num.setText(text)                       # type: ignore[attr-defined]
                widget.total.setText("" if total is MIXED else total)  # type: ignore[attr-defined]
                widget.num.setPlaceholderText(placeholder)     # type: ignore[attr-defined]
            elif isinstance(widget, QLineEdit):
                widget.setText(text)
                widget.setPlaceholderText(placeholder)
            elif isinstance(widget, QComboBox):
                if widget.isEditable():
                    widget.setEditText(text)
                    widget.lineEdit().setPlaceholderText(placeholder)
                else:
                    widget.setCurrentIndex(int(text) if text.isdigit() else 0)
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(text)
                widget.setPlaceholderText(placeholder)
            elif isinstance(widget, QCheckBox):
                widget.setTristate(mixed)
                widget.setCheckState(
                    Qt.CheckState.PartiallyChecked if mixed
                    else (Qt.CheckState.Checked if text in ("True", "1")
                          else Qt.CheckState.Unchecked))
            self.checks[key].setChecked(False)

        self.file_edit.setText(
            str(self.tracks[0].path) if self.single
            else f"<{len(self.tracks)} files selected>")
        self.file_edit.setEnabled(self.single)
        self._load_info()
        self._pictures = None
        self._pictures_dirty = False
        pics = self.tracks[0].pictures if self.tracks else []
        self._show_art(pics[0] if pics else None)

        pos = self._position()
        self.btn_prev.setVisible(self.single)
        self.btn_next.setVisible(self.single)
        self.btn_play.setVisible(self.single)
        self.btn_prev.setEnabled(pos > 0)
        self.btn_next.setEnabled(0 <= pos < len(self.siblings) - 1)

    def _load_info(self) -> None:
        if not self.single:
            self.info_label.setText(f"{len(self.tracks)} files selected")
            return
        t = self.tracks[0]
        info = t.info
        lines = [f"Duration: {info.play_time}",
                 f"Bitrate: {info.bitrate} kbps",
                 f"Channels: {'stereo' if info.channels == 2 else info.channels}",
                 f"Samplerate: {info.sample_rate}",
                 f"Tag: {t.tag_version or 'none'}"]
        if info.bit_depth:
            lines.insert(3, f"Bit depth: {info.bit_depth}")
        if t.error:
            lines.append(f"Error: {t.error}")
        self.info_label.setText("\n".join(lines))

    def _show_art(self, pic: Picture | None) -> None:
        if pic is None:
            self.art_label.setText("no cover art")
            self.art_label.setPixmap(QPixmap())
            self.art_info.setText("")
            return
        pm = QPixmap()
        pm.loadFromData(pic.data)
        if pm.isNull():
            self.art_label.setText("unreadable image")
            return
        self.art_label.setPixmap(pm.scaled(
            220, 220, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        dims = pic.dimensions()
        size = f"{pic.size / 1024:.0f} KB"
        self.art_info.setText(f"{dims[0]} x {dims[1]}, {size}" if dims else size)

    # --- art buttons ---

    def _art_dropped(self, data: bytes, mime: str) -> None:
        """Same as Add…, minus the file dialog. Saved with the rest."""
        self._set_art(Picture(data, mime))

    def _set_art(self, picture: Picture) -> None:
        self._pictures = [picture]
        self._pictures_dirty = True
        self._show_art(picture)

    def _add_art(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose cover art", "",
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff)")
        if not path:
            return
        try:
            data, mime = from_path(path)
        except ArtError as exc:
            self.art_info.setText(str(exc))
            return
        self._set_art(Picture(data, mime))

    def _save_art(self) -> None:
        pics = (self._pictures if self._pictures is not None
                else (self.tracks[0].pictures if self.tracks else []))
        if not pics:
            return
        ext = "png" if pics[0].mime == "image/png" else "jpg"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save cover art", f"folder.{ext}", f"Images (*.{ext})")
        if path:
            Path(path).write_bytes(pics[0].data)

    def _remove_art(self) -> None:
        self._pictures = []
        self._pictures_dirty = True
        self._show_art(None)

    # --- play ---

    def _play(self) -> None:
        if self.single:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.tracks[0].path)))

    # --- saving ---

    def values(self) -> dict[str, object]:
        out: dict[str, object] = {}
        rules: CaseRules = self.case.rules()
        for key, box in self.checks.items():
            if not box.isChecked():
                continue
            widget = self.inputs[key]
            if hasattr(widget, "num"):
                out[key] = widget.num.text().strip()          # type: ignore[attr-defined]
                out[key + "_total"] = widget.total.text().strip()  # type: ignore[attr-defined]
            elif isinstance(widget, QLineEdit):
                out[key] = rules.apply(widget.text())
            elif isinstance(widget, QComboBox):
                out[key] = (rules.apply(widget.currentText())
                            if widget.isEditable() else widget.currentIndex())
            elif isinstance(widget, QTextEdit):
                out[key] = widget.toPlainText()
            elif isinstance(widget, QCheckBox):
                out[key] = widget.checkState() is Qt.CheckState.Checked
        return out

    def commit(self) -> list[str]:
        """Write tags and apply a file rename. Returns failure messages."""
        failures: list[str] = []
        values = self.values()
        pics = self._pictures if self._pictures_dirty else None

        if values or pics is not None:
            for track in self.tracks:
                try:
                    write(track.path, values, pictures=pics,
                          options=self.options)
                    self.changed_any = True
                except Exception as exc:
                    failures.append(f"{track.path.name}: {exc}")

        if self.single:
            failures.extend(self._apply_rename())
        return failures

    def _apply_rename(self) -> list[str]:
        track = self.tracks[0]
        target = Path(self.file_edit.text().strip()).expanduser()
        if not self.file_edit.text().strip() or target == track.path:
            return []
        if target.exists() and not target.samefile(track.path):
            return [f"{target.name}: a file with that name already exists"]
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(track.path, target)
        except OSError as exc:
            return [f"{track.path.name}: {exc}"]
        self.renamed.append((track.path, target))
        self.changed_any = True
        track.path = target
        return []

    def _report(self, failures: list[str]) -> bool:
        if failures:
            QMessageBox.warning(self, "Some changes were not saved",
                                "\n".join(failures[:12]))
            return False
        return True

    def _save_and_close(self) -> None:
        self._report(self.commit())
        self.accept()

    def _step(self, delta: int) -> None:
        """Save, then load the neighbouring file without closing."""
        if not self._report(self.commit()):
            return
        pos = self._position()
        target = pos + delta
        if not (0 <= target < len(self.siblings)):
            self.accept()
            return
        # Re-read so the fields show what is actually on disk now, not what we
        # believed before the save.
        self.siblings[pos] = read(self.siblings[pos].path)
        self.tracks = [read(self.siblings[target].path)]
        self.siblings[target] = self.tracks[0]
        self._load()

    def _remove_tag(self) -> None:
        if QMessageBox.question(
                self, "Tag Editor",
                f"Remove all tag data from {len(self.tracks)} file(s)?\n"
                "This cannot be undone.") is not QMessageBox.StandardButton.Yes:
            return
        failures = []
        for track in self.tracks:
            try:
                remove_tags(track.path)
                self.changed_any = True
            except Exception as exc:
                failures.append(f"{track.path.name}: {exc}")
        if self._report(failures):
            self.tracks = [read(t.path) for t in self.tracks]
            self._load()

    # kept so existing callers and tests that poke _save still work
    _save = _save_and_close
