"""The three work tabs, laid out as in the original.

    Rename Files (F4) | Multi File Tag Editor (Ctrl+F4) | Get Tags from File Name (Shift+F4)

Each one keeps the same shape as its Tag&Rename counterpart: options on the
left, the variable reference on the right, and the verb buttons along the
bottom with the non-destructive one (Preview / Test) first.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFormLayout,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QRadioButton, QTextEdit,
                             QVBoxLayout, QWidget)

from ..casing import Case, CaseRules
from ..fields import DERIVED_VARS, FIELDS, VAR_TO_KEY
from ..masks import MaskError, describe

GENRES = [
    "", "Blues", "Classical", "Country", "Dance", "Electronic", "Folk",
    "Grunge", "Hip-Hop", "Jazz", "Metal", "Pop", "Punk", "R&B", "Reggae",
    "Rock", "Soundtrack", "Techno", "Alternative", "Ambient", "Indie",
]


class CaseBox(QGroupBox):
    """The "Case:" group that appears on all three tabs, identically."""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.enable = QCheckBox("Case:")
        self.group = QButtonGroup(self)
        self.buttons: dict[Case, QRadioButton] = {}
        grid = QGridLayout()
        grid.addWidget(self.enable, 0, 0, 1, 2)
        for i, (mode, label) in enumerate((
                (Case.LOWER, "lower"),
                (Case.TITLE, "Capitalize First Letter"),
                (Case.UPPER, "UPPER"),
                (Case.SENTENCE, "Capitalize first word"))):
            btn = QRadioButton(label)
            btn.setEnabled(False)
            self.group.addButton(btn)
            self.buttons[mode] = btn
            grid.addWidget(btn, 1 + i % 2, i // 2)
        self.buttons[Case.TITLE].setChecked(True)
        self.setLayout(grid)

        self.enable.toggled.connect(self._toggle)
        self.group.buttonToggled.connect(lambda *_: self.changed.emit())

    def _toggle(self, on: bool) -> None:
        for btn in self.buttons.values():
            btn.setEnabled(on)
        self.changed.emit()

    def rules(self) -> CaseRules:
        if not self.enable.isChecked():
            return CaseRules(mode=Case.NONE)
        for mode, btn in self.buttons.items():
            if btn.isChecked():
                return CaseRules(mode=mode)
        return CaseRules(mode=Case.NONE)


def _variable_help(include_derived: bool) -> QLabel:
    """The two-column variable crib sheet printed beside the mask box."""
    rows = [(f.var, f.label) for f in FIELDS if f.var]
    if include_derived:
        rows += [("%7", "File name"), ("%8", "File number"), ("%0", "Bitrate"),
                 ("%t", "Play time"), ("%rnd", "Random number"),
                 ("%sxe", "1x01"), ("%see", "S01E01")]
    else:
        rows += [("%d", "Skip part of the name")]
    rows.sort(key=lambda r: (len(r[0]), r[0]))
    half = (len(rows) + 1) // 2
    left, right = rows[:half], rows[half:]
    lines = []
    for i in range(half):
        a = f"{left[i][0]:>4} - {left[i][1]}"
        b = (f"{right[i][0]:>4} - {right[i][1]}" if i < len(right) else "")
        lines.append(f"{a:<28}{b}")
    label = QLabel("Rename variables:\n" + "\n".join(lines))
    # setFont, not a stylesheet: any stylesheet switches the widget to
    # QStyleSheetStyle, which caches the colours it resolved at parse time —
    # so the label keeps the OLD palette's text colour after a theme switch
    # and renders washed out.
    # "monospace" is a fontconfig alias: on Linux it resolves to something
    # decent, on Windows there is no such family and Qt substitutes a heavy
    # Courier that made the whole crib sheet look like a fax. Name real
    # families in preference order and keep the alias as the last resort.
    mono = QFont()
    mono.setFamilies(["Cascadia Mono", "Consolas", "Segoe UI Mono",
                      "DejaVu Sans Mono", "Liberation Mono", "Menlo",
                      "monospace"])
    mono.setStyleHint(QFont.StyleHint.Monospace)
    mono.setPointSize(max(8, label.font().pointSize() - 1))
    label.setFont(mono)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class MaskRow(QWidget):
    """Mask combo + Mask Editor button, with saved masks in the dropdown."""

    changed = pyqtSignal()

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.editor_button = QPushButton("Mask Editor")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(label))
        row.addWidget(self.combo, 1)
        row.addWidget(self.editor_button)
        self.combo.editTextChanged.connect(lambda *_: self.changed.emit())

    def mask(self) -> str:
        return self.combo.currentText()

    def set_masks(self, masks: list[str]) -> None:
        current = self.combo.currentText()
        self.combo.clear()
        self.combo.addItems(masks)
        self.combo.setEditText(current or (masks[0] if masks else ""))


class RenameTab(QWidget):
    """Rename Files (F4)."""

    preview_requested = pyqtSignal()
    rename_requested = pyqtSignal()
    undo_requested = pyqtSignal()
    mask_editor_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mask_row = MaskRow("File name mask:")
        self.preview = QLineEdit()
        self.preview.setReadOnly(True)
        self.underscores = QCheckBox("Replace spaces with underscores")
        self.case = CaseBox()

        left = QVBoxLayout()
        left.addWidget(self.mask_row)
        form = QFormLayout()
        form.addRow("Result preview:", self.preview)
        left.addLayout(form)
        left.addWidget(self.underscores)
        left.addWidget(self.case)
        left.addStretch(1)

        self.btn_preview = QPushButton("&Test")
        self.btn_rename = QPushButton("&Rename")
        self.btn_undo = QPushButton("&Undo")
        self.btn_undo.setEnabled(False)
        buttons = QHBoxLayout()
        for b in (self.btn_preview, self.btn_rename, self.btn_undo):
            buttons.addWidget(b)

        right = QVBoxLayout()
        right.addWidget(_variable_help(include_derived=True))
        right.addStretch(1)
        right.addLayout(buttons)

        root = QHBoxLayout(self)
        root.addLayout(left, 1)
        root.addLayout(right, 1)

        self.mask_row.changed.connect(self._update_preview)
        self.mask_row.editor_button.clicked.connect(self.mask_editor_requested)
        self.btn_preview.clicked.connect(self.preview_requested)
        self.btn_rename.clicked.connect(self.rename_requested)
        self.btn_undo.clicked.connect(self.undo_requested)
        self._update_preview()

    def _update_preview(self) -> None:
        mask = self.mask_row.mask()
        try:
            self.preview.setText(describe(mask) + ".mp3" if mask else "")
        except MaskError as exc:
            self.preview.setText(str(exc))


class FilenameToTagTab(QWidget):
    """Get Tags from File Name (Shift+F4)."""

    preview_requested = pyqtSignal()
    write_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mask_row = MaskRow("File name mask:")
        self.mask_row.editor_button.hide()
        self.underscores = QCheckBox("Replace underscores with spaces")
        self.case = CaseBox()

        left = QVBoxLayout()
        left.addWidget(self.mask_row)
        left.addWidget(self.underscores)
        left.addWidget(self.case)
        left.addStretch(1)

        self.btn_preview = QPushButton("&Preview")
        self.btn_write = QPushButton("&Write Tags")
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_preview)
        buttons.addWidget(self.btn_write)

        right = QVBoxLayout()
        right.addWidget(_variable_help(include_derived=False))
        right.addStretch(1)
        right.addLayout(buttons)

        root = QHBoxLayout(self)
        root.addLayout(left, 1)
        root.addLayout(right, 1)

        self.btn_preview.clicked.connect(self.preview_requested)
        self.btn_write.clicked.connect(self.write_requested)


class MultiTagTab(QWidget):
    """Multi File Tag Editor (Ctrl+F4).

    The checkbox beside each field is the whole point: only checked fields are
    written, so setting Album and Year across 33 files cannot touch their
    Titles. :meth:`values` returns exactly the checked ones.
    """

    save_requested = pyqtSignal()
    remove_requested = pyqtSignal()
    copy_focused_requested = pyqtSignal()
    edit_all_requested = pyqtSignal()

    QUICK = ("artist", "album", "year", "genre", "comment")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.checks: dict[str, QCheckBox] = {}
        self.inputs: dict[str, QWidget] = {}

        grid = QGridLayout()
        for row, key in enumerate(("artist", "album")):
            self._add_row(grid, row, 0, key, QLineEdit())
        year_row = QHBoxLayout()
        self.inputs["year"] = QLineEdit()
        self.inputs["year"].setMaximumWidth(70)
        self.checks["year"] = QCheckBox("Year")
        self.checks["genre"] = QCheckBox("Genre")
        self.inputs["genre"] = QComboBox()
        self.inputs["genre"].setEditable(True)
        self.inputs["genre"].addItems(GENRES)
        year_row.addWidget(self.checks["year"])
        year_row.addWidget(self.inputs["year"])
        year_row.addWidget(self.checks["genre"])
        year_row.addWidget(self.inputs["genre"], 1)
        grid.addLayout(year_row, 2, 0, 1, 2)

        self.checks["comment"] = QCheckBox("Comment")
        self.inputs["comment"] = QTextEdit()
        self.inputs["comment"].setMaximumHeight(60)
        grid.addWidget(self.checks["comment"], 3, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.inputs["comment"], 3, 1)

        self.case = CaseBox()
        left = QVBoxLayout()
        left.addLayout(grid)
        left.addWidget(self.case)
        left.addStretch(1)

        notes = QLabel(
            "1. Only checked fields of a tag will be changed.\n"
            "2. Select files first — these buttons act on the selection.\n"
            "3. For cover art, lyrics and everything else, press\n"
            "    'Edit All Supported Tag Fields'.")
        self.btn_save = QPushButton("&Save Tags")
        self.btn_remove = QPushButton("Re&move Tags")
        self.btn_copy = QPushButton("&Copy from Focused File")
        self.btn_all = QPushButton("Edit &All Supported Tag Fields")
        buttons = QGridLayout()
        buttons.addWidget(self.btn_save, 0, 0)
        buttons.addWidget(self.btn_remove, 0, 1)
        buttons.addWidget(self.btn_copy, 1, 0)
        buttons.addWidget(self.btn_all, 1, 1)

        right = QVBoxLayout()
        right.addWidget(notes)
        right.addStretch(1)
        right.addLayout(buttons)

        root = QHBoxLayout(self)
        root.addLayout(left, 1)
        root.addLayout(right, 1)

        for key, widget in self.inputs.items():
            self._autocheck(key, widget)

        self.btn_save.clicked.connect(self.save_requested)
        self.btn_remove.clicked.connect(self.remove_requested)
        self.btn_copy.clicked.connect(self.copy_focused_requested)
        self.btn_all.clicked.connect(self.edit_all_requested)

    def _add_row(self, grid, row, col, key, widget) -> None:
        from ..fields import BY_KEY
        self.checks[key] = QCheckBox(BY_KEY[key].label)
        self.inputs[key] = widget
        grid.addWidget(self.checks[key], row, col)
        grid.addWidget(widget, row, col + 1)

    def _autocheck(self, key: str, widget: QWidget) -> None:
        """Typing in a field ticks its box — otherwise everyone forgets."""
        def tick(*_):
            self.checks[key].setChecked(True)
        if isinstance(widget, QLineEdit):
            widget.textEdited.connect(tick)
        elif isinstance(widget, QComboBox):
            widget.editTextChanged.connect(tick)
        elif isinstance(widget, QTextEdit):
            widget.textChanged.connect(tick)

    def _text(self, key: str) -> str:
        w = self.inputs[key]
        if isinstance(w, QLineEdit):
            return w.text()
        if isinstance(w, QComboBox):
            return w.currentText()
        return w.toPlainText()

    def values(self) -> dict[str, str]:
        return {k: self._text(k) for k, box in self.checks.items()
                if box.isChecked()}

    def load_from(self, values: dict) -> None:
        """Fill the boxes from one file, for "Copy from Focused File"."""
        for key, widget in self.inputs.items():
            text = str(values.get(key, "") or "")
            if isinstance(widget, QLineEdit):
                widget.setText(text)
            elif isinstance(widget, QComboBox):
                widget.setEditText(text)
            else:
                widget.setPlainText(text)
            self.checks[key].setChecked(bool(text))

    def clear(self) -> None:
        for key, widget in self.inputs.items():
            if isinstance(widget, QLineEdit):
                widget.clear()
            elif isinstance(widget, QComboBox):
                widget.setEditText("")
            else:
                widget.clear()
            self.checks[key].setChecked(False)
