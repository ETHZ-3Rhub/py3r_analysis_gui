"""Outer dialog for the Advanced loader entry point in GroupManifestPanel.

Mode selector (manifest CSV / directory tree) at top, QStackedWidget for
content, shared OK/Cancel bar. result_groups() delegates to the active page.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.csv_import_dialog import CsvImportWidget, _csv_widget_stylesheet
from app.directory_tree_widget import DirectoryTreeWidget
from app.theme import get_theme as _get_theme


class AdvancedLoaderDialog(QDialog):
    """Advanced loader: manifest CSV wizard or directory tree."""

    def __init__(self, file_exts: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file_exts = file_exts
        self.setWindowTitle("Advanced loader")
        self.setFixedSize(700, 860)
        self._build_ui()
        self._apply_stylesheet()

    def result_groups(self) -> dict[str, list[Path]]:
        if self._stack.currentIndex() == 0:
            return self._csv_widget.result_groups()
        return self._tree_widget.result_groups()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        t = _get_theme()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 14)
        outer.setSpacing(12)

        # Step 1 header
        self._hdr1 = QLabel("1.  Choose loader")
        self._hdr1.setStyleSheet(
            f"color: {t.accent}; font-size: 11px; font-weight: bold; padding-top: 4px;"
        )
        outer.addWidget(self._hdr1)

        # Mode buttons (checkable, exclusive — neither selected on open)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        self._csv_btn = QPushButton("Groups from manifest CSV")
        self._csv_btn.setObjectName("loaderOptionBtn")
        self._csv_btn.setCheckable(True)
        self._tree_btn = QPushButton("Groups from directory tree")
        self._tree_btn.setObjectName("loaderOptionBtn")
        self._tree_btn.setCheckable(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._csv_btn, 0)
        self._mode_group.addButton(self._tree_btn, 1)
        mode_row.addWidget(self._csv_btn)
        mode_row.addWidget(self._tree_btn)
        outer.addLayout(mode_row)

        # Stacked content (hidden until a mode is chosen)
        self._stack = QStackedWidget()
        self._stack.setVisible(False)

        self._csv_widget = CsvImportWidget(self._file_exts, self)
        self._stack.addWidget(self._csv_widget)  # page 0

        self._tree_widget = DirectoryTreeWidget(self._file_exts, self)
        self._stack.addWidget(self._tree_widget)  # page 1

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
        self._stack.setCurrentIndex(btn_id)
        self._stack.setVisible(True)
        if btn_id == 0:
            self._ok_btn.setEnabled(self._csv_widget.is_valid())
        else:
            self._ok_btn.setEnabled(self._tree_widget.is_valid())

    def _on_csv_validity_changed(self, valid: bool) -> None:
        if self._stack.isVisible() and self._stack.currentIndex() == 0:
            self._ok_btn.setEnabled(valid)

    def _on_tree_validity_changed(self, valid: bool) -> None:
        if self._stack.isVisible() and self._stack.currentIndex() == 1:
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
            QPushButton#loaderOptionBtn {{
                background-color: {t.display};
                color: {t.text};
                border: 1px solid {t.muted};
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
            }}
            QPushButton#loaderOptionBtn:hover {{ border-color: {t.accent}; }}
            QPushButton#loaderOptionBtn:checked {{
                background-color: {t.accent};
                color: white;
                border-color: {t.accent};
            }}
        """
        )
