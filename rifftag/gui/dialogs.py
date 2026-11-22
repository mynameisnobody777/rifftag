"""Small prompt dialogs that match the originals."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QLabel, QSpinBox, QVBoxLayout)


class AutoFillTrackDialog(QDialog):
    """Auto Fill Track # — "Start from" plus the xx/xx option.

    Numbers follow the order the files appear in the list, not the order they
    were clicked, so Move Up / Move Down is how you control the result.
    """

    def __init__(self, count: int, parent=None, *, episode: bool = False):
        super().__init__(parent)
        what = "episode #" if episode else "track #"
        self.setWindowTitle(f"Auto Fill {what.title()}")

        self.start = QSpinBox()
        self.start.setRange(0, 99999)
        self.start.setValue(1)
        self.start.setMaximumWidth(90)
        self.use_total = QCheckBox("Use xx/xx format")
        self.use_total.setVisible(not episode)

        form = QFormLayout()
        form.addRow("Start from:", self.start)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes
                                   | QDialogButtonBox.StandardButton.No)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Yes).setDefault(True)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"Auto fill {what} for all {count} selected file(s)?"))
        root.addLayout(form)
        root.addWidget(self.use_total)
        root.addWidget(buttons)

    def options(self) -> tuple[int, bool]:
        return self.start.value(), self.use_total.isChecked()
