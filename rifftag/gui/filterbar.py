"""The address line and the filter row, above the list.

Tag&Rename calls this strip the address bar; what it actually buys you is the
filter underneath it — narrow a 2000-file recursive scan down to the rows you
mean to edit, then act on only those. ``Hide Selected Files`` is the inverse:
take the rows you have already dealt with out of the way.

The filter is applied by the model (see ``FileListModel.set_filter``), not by
this widget — this is only the controls.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QGridLayout, QLabel, QLineEdit,
                             QPushButton, QSizePolicy, QWidget)

#: key -> label. ``__any__`` and ``__name__`` are handled by the model
#: directly; everything else is a tag field key.
FILTER_FIELDS: tuple[tuple[str, str], ...] = (
    ("__name__", "File Name"),
    ("__any__", "Any field"),
    ("artist", "Artist"),
    ("album", "Album"),
    ("title", "Title"),
    ("genre", "Genre"),
    ("year", "Year"),
    ("track", "Track #"),
    ("album_artist", "Album Artist"),
    ("composer", "Composer"),
    ("comment", "Comment"),
)

#: how many addresses the drop-down keeps
MAX_ADDRESSES = 15


class FilterBar(QWidget):
    address_entered = pyqtSignal(object)     # Path
    filter_requested = pyqtSignal(str, str)  # field key, text
    hide_selected_requested = pyqtSignal()
    show_all_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(4, 2, 4, 2)
        grid.setVerticalSpacing(2)

        self.address = QComboBox()
        self.address.setEditable(True)
        self.address.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.address.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Fixed)
        self.address.lineEdit().returnPressed.connect(self._address_entered)
        self.address.activated.connect(self._address_picked)
        grid.addWidget(QLabel("Address:"), 0, 0)
        grid.addWidget(self.address, 0, 1, 1, 5)

        self.field = QComboBox()
        for key, label in FILTER_FIELDS:
            self.field.addItem(label, key)
        self.text = QLineEdit()
        self.text.setPlaceholderText(
            "text to match — * and ? work as wildcards")
        self.text.setClearButtonEnabled(True)
        self.text.returnPressed.connect(self._filter)

        self.btn_filter = QPushButton("Filter!")
        self.btn_filter.setToolTip("Show only the rows that match")
        self.btn_hide = QPushButton("Hide Selected Files")
        self.btn_hide.setToolTip(
            "Take the selected rows out of the list. They are not touched on "
            "disk — Show All brings them back.")
        self.btn_show_all = QPushButton("Show All")
        self.btn_show_all.setToolTip("Clear the filter and unhide everything")

        self.btn_filter.clicked.connect(self._filter)
        self.btn_hide.clicked.connect(self.hide_selected_requested.emit)
        self.btn_show_all.clicked.connect(self._show_all)

        grid.addWidget(QLabel("Filter by"), 1, 0)
        grid.addWidget(self.field, 1, 1)
        grid.addWidget(self.text, 1, 2)
        grid.addWidget(self.btn_filter, 1, 3)
        grid.addWidget(self.btn_hide, 1, 4)
        grid.addWidget(self.btn_show_all, 1, 5)
        grid.setColumnStretch(2, 1)

    # --- address ---

    def set_folder(self, folder: Path) -> None:
        """Show an opened folder without re-emitting a request for it."""
        text = str(folder)
        self.address.blockSignals(True)
        index = self.address.findText(text)
        if index >= 0:
            self.address.removeItem(index)
        self.address.insertItem(0, text)
        while self.address.count() > MAX_ADDRESSES:
            self.address.removeItem(self.address.count() - 1)
        self.address.setCurrentIndex(0)
        self.address.blockSignals(False)

    def _address_entered(self) -> None:
        text = self.address.currentText().strip()
        if text:
            self.address_entered.emit(Path(text).expanduser())

    def _address_picked(self, index: int) -> None:
        text = self.address.itemText(index).strip()
        if text:
            self.address_entered.emit(Path(text).expanduser())

    # --- filter ---

    def field_key(self) -> str:
        return self.field.currentData() or "__name__"

    def _filter(self) -> None:
        self.filter_requested.emit(self.field_key(), self.text.text())

    def _show_all(self) -> None:
        self.text.clear()
        self.show_all_requested.emit()
