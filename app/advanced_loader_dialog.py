"""Outer dialog for the Advanced loader entry point in GroupManifestPanel.

Mode selector (manifest CSV / directory tree) at top, QStackedWidget for
content, shared OK/Cancel bar. Directory tree mode is a placeholder stub.
result_groups() delegates to the active page.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.csv_import_dialog import CsvImportWidget, _csv_widget_stylesheet
from app.theme import get_theme as _get_theme


class AdvancedLoaderDialog(QDialog):
    """Advanced loader: manifest CSV wizard or directory tree (stub)."""

    def __init__(self, file_exts: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file_exts = file_exts
        self.setWindowTitle("Advanced loader")
        self.resize(680, 680)
        self._build_ui()
        self._apply_stylesheet()

    def result_groups(self) -> dict[str, list[Path]]:
        if self._stack.currentIndex() == 0:
            return self._csv_widget.result_groups()
        return {}

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 14)
        outer.setSpacing(12)

        # Mode selector
        mode_row = QHBoxLayout()
        mode_row.setSpacing(20)
        self._manifest_radio = QRadioButton("Groups from manifest CSV")
        self._tree_radio = QRadioButton("Groups from directory tree")
        self._manifest_radio.setChecked(True)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self._manifest_radio)
        mode_group.addButton(self._tree_radio)
        mode_row.addWidget(self._manifest_radio)
        mode_row.addWidget(self._tree_radio)
        mode_row.addStretch()
        outer.addLayout(mode_row)

        # Stacked content
        self._stack = QStackedWidget()

        self._csv_widget = CsvImportWidget(self._file_exts, self)
        self._stack.addWidget(self._csv_widget)  # page 0

        placeholder = QWidget()
        ph_layout = QVBoxLayout(placeholder)
        ph_lbl = QLabel("Coming soon — load groups by walking a directory tree.")
        ph_lbl.setObjectName("mutedLabel")
        ph_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph_layout.addStretch()
        ph_layout.addWidget(ph_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        ph_layout.addStretch()
        self._stack.addWidget(placeholder)  # page 1

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
        self._manifest_radio.toggled.connect(self._on_mode_toggled)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_mode_toggled(self, manifest_checked: bool) -> None:
        if manifest_checked:
            self._stack.setCurrentIndex(0)
            self._ok_btn.setEnabled(self._csv_widget.is_valid())
        else:
            self._stack.setCurrentIndex(1)
            self._ok_btn.setEnabled(False)

    def _on_csv_validity_changed(self, valid: bool) -> None:
        if self._stack.currentIndex() == 0:
            self._ok_btn.setEnabled(valid)

    def _on_ok(self) -> None:
        self.accept()

    # ── Stylesheet ────────────────────────────────────────────────────────────

    def _apply_stylesheet(self) -> None:
        t = _get_theme()
        base = ""
        if self.parent() is not None:
            win = self.parent().window()
            if win is not None:
                base = win.styleSheet()
        self.setStyleSheet(
            base
            + _csv_widget_stylesheet(t)
            + f"""
            QPushButton#importBtn {{
                background-color: {t.accent};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 6px 20px;
                min-width: 80px;
                font-weight: bold;
            }}
            QPushButton#importBtn:hover {{ background-color: {t.accent_hover}; }}
            QPushButton#importBtn:disabled {{ background-color: {t.muted}; color: {t.bg}; }}
        """
        )
