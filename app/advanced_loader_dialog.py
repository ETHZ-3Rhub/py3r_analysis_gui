"""Outer dialog for the Advanced loader entry point in GroupManifestPanel.

Mode selector (manifest CSV / directory tree) at top, QStackedWidget for
content, shared OK/Cancel bar. result_groups() delegates to the active page.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.csv_import_dialog import CsvImportWidget, confirm_partial_import
from app.directory_tree_widget import DirectoryTreeWidget
from app.styles import dialog_stylesheet
from app.theme import get_theme as _get_theme


class AdvancedLoaderDialog(QDialog):
    """Advanced loader: manifest CSV wizard or directory tree."""

    def __init__(self, file_exts: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file_exts = file_exts
        self.setWindowTitle("Advanced loader")
        self._build_ui()
        self._apply_stylesheet()
        self._size_to_screen()

    def _size_to_screen(self) -> None:
        # Native size is 700x860, but that doesn't fit small/scaled screens and
        # this dialog has no OS window chrome we can rely on to reveal the OK
        # button, so clamp to what's actually available and let the content
        # scroll internally (see _build_ui) rather than clip off-screen.
        screen = self.screen() or QGuiApplication.primaryScreen()
        margin = 60
        min_w, min_h = 420, 320
        if screen is not None:
            avail = screen.availableGeometry()
            width = max(min_w, min(700, avail.width() - margin))
            height = max(min_h, min(860, avail.height() - margin))
        else:
            width, height = 700, 860
        self.setMinimumSize(min_w, min_h)
        self.resize(width, height)

    def result_groups(self) -> dict[str, list[Path]]:
        if self._stack.currentIndex() == 1:
            return self._csv_widget.result_groups()
        return self._tree_widget.result_groups()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        t = _get_theme()

        # Everything (header, mode selector, stack, OK/Cancel bar) lives inside
        # a scroll area. At the native 700x860 size it all fits and no scrollbar
        # shows; on small/scaled screens where the dialog gets shrunk below
        # that, the same top-to-bottom flow becomes scrollable instead of
        # clipping the OK button off-screen.
        dialog_layout = QVBoxLayout(self)
        dialog_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        dialog_layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(16, 16, 16, 14)
        outer.setSpacing(12)

        # Step 1 header
        self._hdr1 = QLabel("1.  How do you want to define groups?")
        self._hdr1.setStyleSheet(
            f"color: {t.accent}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        )
        outer.addWidget(self._hdr1)

        # Mode selector: radio buttons (neither selected on open)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        self._csv_btn = QRadioButton("Groups from manifest CSV")
        self._tree_btn = QRadioButton("Groups from directory tree")
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._csv_btn, 0)
        self._mode_group.addButton(self._tree_btn, 1)
        mode_row.addWidget(self._csv_btn)
        mode_row.addWidget(self._tree_btn)
        mode_row.addStretch()
        outer.addLayout(mode_row)

        # Stacked content — page 0 is a blank placeholder so the stack is always
        # visible and the button bar never moves when a mode is selected.
        self._stack = QStackedWidget()
        self._stack.addWidget(QWidget())  # page 0: placeholder

        self._csv_widget = CsvImportWidget(self._file_exts, self)
        self._stack.addWidget(self._csv_widget)  # page 1

        self._tree_widget = DirectoryTreeWidget(self._file_exts, self)
        self._stack.addWidget(self._tree_widget)  # page 2

        outer.addWidget(self._stack, stretch=1)

        # Button bar
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)
        self._ok_btn = QPushButton("OK")
        self._ok_btn.setObjectName("importBtn")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(cancel_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(self._ok_btn)
        outer.addLayout(btn_row)

        # Wiring
        self._csv_widget.validity_changed.connect(self._on_csv_validity_changed)
        self._tree_widget.validity_changed.connect(self._on_tree_validity_changed)
        self._mode_group.idToggled.connect(self._on_mode_toggled)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_mode_toggled(self, btn_id: int, checked: bool) -> None:
        if not checked:
            return
        t = _get_theme()
        self._hdr1.setStyleSheet(
            f"color: {t.muted}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        )
        self._stack.setCurrentIndex(btn_id + 1)  # +1: page 0 is placeholder
        if btn_id == 0:
            self._ok_btn.setEnabled(self._csv_widget.is_valid())
        else:
            self._ok_btn.setEnabled(self._tree_widget.is_valid())

    def _on_csv_validity_changed(self, valid: bool) -> None:
        if self._stack.currentIndex() == 1:
            self._ok_btn.setEnabled(valid)

    def _on_tree_validity_changed(self, valid: bool) -> None:
        if self._stack.currentIndex() == 2:
            self._ok_btn.setEnabled(valid)

    def _on_ok(self) -> None:
        # Only clean matches import; warn if some recordings won't be included so
        # the user can go fix their manifest instead of silently losing data.
        if self._stack.currentIndex() == 1 and not confirm_partial_import(
            self, *self._csv_widget.unmatched_summary()
        ):
            return
        self.accept()

    # ── Stylesheet ────────────────────────────────────────────────────────────

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(dialog_stylesheet(_get_theme()))
