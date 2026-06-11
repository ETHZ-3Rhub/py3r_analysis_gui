"""Themed yes/no confirmation dialogs with custom imagery."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.theme import get_theme as _get_theme

_IMG_DIR = Path(__file__).parent / "resources"
_IMG_SIZE = 180


def _load(filename: str | None) -> QPixmap:
    path = _IMG_DIR / filename if filename else None
    if path is not None and path.exists():
        px = QPixmap(str(path))
        return px.scaled(
            _IMG_SIZE,
            _IMG_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    # fallback: solid black square
    px = QPixmap(_IMG_SIZE, _IMG_SIZE)
    px.fill(QColor(0, 0, 0))
    return px


def grumpy_teacher() -> QPixmap:
    """'Are you sure?' — destructive / irreversible action."""
    return _load("grumpy_teacher.png")


def warning_face() -> QPixmap:
    """'Proceed anyway?' — soft warnings before a run."""
    return _load("warning_face.png")


def pipeline_reference_image(pipeline_mod) -> QPixmap:
    """Reference photo of the arena a pipeline expects, shown when
    confirming the user picked the right pipeline for their setup."""
    return _load(getattr(pipeline_mod, "ARENA_IMAGE", None))


class _ConfirmDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        pixmap: QPixmap | None,
        yes_label: str,
        no_label: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self._build_ui(message, pixmap, yes_label, no_label)
        self._apply_stylesheet()

    def _build_ui(
        self,
        message: str,
        pixmap: QPixmap | None,
        yes_label: str,
        no_label: str,
    ) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 16)
        outer.setSpacing(16)

        content = QHBoxLayout()
        content.setSpacing(16)
        content.setAlignment(Qt.AlignmentFlag.AlignTop)

        if pixmap is not None:
            img_lbl = QLabel()
            img_lbl.setPixmap(pixmap)
            img_lbl.setFixedSize(_IMG_SIZE, _IMG_SIZE)
            img_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            content.addWidget(img_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        msg_lbl.setMinimumWidth(240)
        content.addWidget(msg_lbl, stretch=1)

        outer.addLayout(content)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        no_btn = QPushButton(no_label)
        no_btn.setObjectName("dlgBtn")
        no_btn.clicked.connect(self.reject)

        yes_btn = QPushButton(yes_label)
        yes_btn.setObjectName("dlgBtnPrimary")
        yes_btn.setDefault(True)
        yes_btn.clicked.connect(self.accept)

        btn_row.addWidget(no_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(yes_btn)
        outer.addLayout(btn_row)

    def _apply_stylesheet(self) -> None:
        t = _get_theme()
        self.setStyleSheet(f"""
            QDialog, QWidget {{
                background-color: {t.bg};
                color: {t.panel_text};
                font-family: "Helvetica Neue", Arial, sans-serif;
                font-size: 13px;
            }}
            QLabel {{ background: transparent; color: {t.panel_text}; }}
            QPushButton#dlgBtn {{
                background-color: transparent;
                color: {t.accent};
                border: 1px solid {t.accent};
                border-radius: 5px;
                padding: 6px 20px;
                min-width: 72px;
            }}
            QPushButton#dlgBtn:hover {{ background-color: {t.accent}; color: white; }}
            QPushButton#dlgBtnPrimary {{
                background-color: {t.accent};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 6px 20px;
                min-width: 72px;
                font-weight: bold;
            }}
            QPushButton#dlgBtnPrimary:hover {{ background-color: {t.accent_hover}; }}
        """)


def ask(
    parent: QWidget | None,
    title: str,
    message: str,
    pixmap: QPixmap | None = None,
    yes_label: str = "Yes",
    no_label: str = "No",
) -> bool:
    """Show a themed yes/no dialog. Returns True if Yes was clicked."""
    dlg = _ConfirmDialog(parent, title, message, pixmap, yes_label, no_label)
    return dlg.exec() == QDialog.DialogCode.Accepted
