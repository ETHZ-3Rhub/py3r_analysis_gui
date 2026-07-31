"""Settings dialog — app version, tracking environment reinstall, theme."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from app.theme import all_themes, update_theme
from app.theme import get_theme as _get_theme
from app.tracking_env_setup import get_version


class SettingsDialog(QDialog):
    def __init__(self, env_panel, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self._env_panel = env_panel
        self._separators: list[QFrame] = []
        self._build_ui()
        self._apply_stylesheet()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── Version ───────────────────────────────────────────────────────────
        version_lbl = QLabel(f"Analys3R  v{get_version()}")
        version_lbl.setObjectName("versionLabel")
        layout.addWidget(version_lbl)

        layout.addWidget(self._sep())

        # ── Tracking environment ──────────────────────────────────────────────
        # Status/log live in the main window's tracking-env indicator — the
        # single source of truth — not duplicated here.
        env_title = QLabel("Tracking Environment")
        env_title.setObjectName("sectionTitle")
        layout.addWidget(env_title)

        self._reinstall_btn = QPushButton("(Re)install tracking environment")
        self._reinstall_btn.setObjectName("secondaryButton")
        self._reinstall_btn.clicked.connect(self._start_reinstall)
        layout.addWidget(self._reinstall_btn)

        layout.addWidget(self._sep())

        # ── Appearance ────────────────────────────────────────────────────────
        appearance_title = QLabel("Appearance")
        appearance_title.setObjectName("sectionTitle")
        layout.addWidget(appearance_title)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_lbl = QLabel("Theme:")
        theme_row.addWidget(theme_lbl)
        self._theme_combo = QComboBox()
        themes = all_themes()
        for t in themes:
            self._theme_combo.addItem(t.name)
        self._theme_combo.setCurrentText(_get_theme().name)
        self._theme_combo.currentTextChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self._theme_combo, stretch=1)
        layout.addLayout(theme_row)

    # ── Reinstall ─────────────────────────────────────────────────────────────

    def _start_reinstall(self) -> None:
        if not self._env_panel.start_install():
            return  # declined, e.g. by the pre-flight connectivity check
        # Progress is now visible in the main window's indicator - nothing
        # left for this dialog to show.
        self.accept()

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _on_theme_changed(self, name: str) -> None:
        for t in all_themes():
            if t.name == name:
                update_theme(t)
                break
        self._apply_stylesheet()
        parent = self.parent()
        if parent is not None and hasattr(parent, "_apply_stylesheet"):
            parent._apply_stylesheet()

    # ── Stylesheet ────────────────────────────────────────────────────────────

    def _sep(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        self._separators.append(line)
        return line

    def _apply_stylesheet(self) -> None:
        _T = _get_theme()
        for sep in self._separators:
            sep.setStyleSheet(f"color: {_T.sep}; margin: 2px 0;")
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {_T.bg};
                color: {_T.panel_text};
                font-family: "Helvetica Neue", Arial, sans-serif;
                font-size: 13px;
            }}
            QLabel {{
                background: transparent;
                color: {_T.panel_text};
            }}
            QLabel#versionLabel {{
                font-size: 18px;
                font-weight: bold;
                color: {_T.panel_text};
            }}
            QLabel#sectionTitle {{
                color: {_T.title};
                font-weight: bold;
                font-size: 12px;
                letter-spacing: 1px;
                text-transform: uppercase;
            }}
            QComboBox {{
                background-color: {_T.display};
                color: {_T.text};
                border: 1px solid {_T.muted};
                border-radius: 5px;
                padding: 5px 8px;
            }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QComboBox QAbstractItemView {{
                background-color: {_T.display};
                color: {_T.text};
            }}
            QPushButton#secondaryButton {{
                background-color: transparent;
                color: {_T.accent};
                border: 1px solid {_T.accent};
                border-radius: 5px;
                padding: 6px 10px;
            }}
            QPushButton#secondaryButton:hover {{
                background-color: {_T.accent};
                color: {_T.accent_text};
            }}
            QPushButton#secondaryButton:disabled {{
                color: {_T.muted};
                border-color: {_T.muted};
            }}
            QScrollBar:vertical {{
                background: {_T.display};
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {_T.muted};
                border-radius: 4px;
            }}
        """)
