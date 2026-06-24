"""Themed yes/no confirmation dialogs, with optional arena reference imagery."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.theme import get_theme as _get_theme

_IMG_DIR = Path(__file__).parent / "resources"
_IMG_SIZE = 180


def pipeline_reference_image(resolved: dict) -> QPixmap | None:
    """Reference photo of the arena a pipeline expects. Returns None if the
    config has no ``arena_image`` or the file doesn't exist. Bundled images live
    in app/resources/; a user config's image sits beside it in /user/configs/."""
    filename = resolved.get("arena_image")
    if not filename:
        return None
    if resolved["source"] == "user":
        from app import pipeline_config

        base = pipeline_config.user_configs_dir()
    else:
        base = _IMG_DIR
    path = base / filename
    if not path.exists():
        return None
    px = QPixmap(str(path))
    return px.scaled(
        _IMG_SIZE,
        _IMG_SIZE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class _ConfirmDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        pixmap: QPixmap | None,
        yes_label: str,
        no_label: str | None,
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
        no_label: str | None,
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

        if no_label is not None:
            no_btn = QPushButton(no_label)
            no_btn.setObjectName("dlgBtn")
            no_btn.clicked.connect(self.reject)
            btn_row.addWidget(no_btn)
            btn_row.addSpacing(8)

        yes_btn = QPushButton(yes_label)
        yes_btn.setObjectName("dlgBtnPrimary")
        yes_btn.setDefault(True)
        yes_btn.clicked.connect(self.accept)

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


def info(
    parent: QWidget | None,
    title: str,
    message: str,
    pixmap: QPixmap | None = None,
    ok_label: str = "OK",
) -> None:
    """Show a themed single-button informational dialog."""
    dlg = _ConfirmDialog(parent, title, message, pixmap, ok_label, None)
    dlg.exec()


def ask_trust(parent: QWidget | None, name: str) -> tuple[bool, bool]:
    """Trust confirmation for a pipeline that executes /user code or weights.
    Returns ``(accepted, dont_warn_again)``."""
    dlg = _TrustDialog(parent, name)
    accepted = dlg.exec() == QDialog.DialogCode.Accepted
    return accepted, (accepted and dlg.dont_warn())


class _TrustDialog(_ConfirmDialog):
    def __init__(self, parent: QWidget | None, name: str) -> None:
        message = (
            f"“{name}” runs custom code or model weights from your /user folder — "
            "this did not come from the py3r team.\n\n"
            "Custom code can do anything your account can. Only run it if you trust "
            "whoever sent you this pipeline."
        )
        # Build the standard confirm UI, then slip a checkbox above the buttons.
        super().__init__(parent, "Run custom pipeline?", message, None, "Run", "Cancel")
        self._dont_warn = QCheckBox("Don't warn me again for this pipeline")
        layout = self.layout()
        layout.insertWidget(layout.count() - 1, self._dont_warn)

    def dont_warn(self) -> bool:
        return self._dont_warn.isChecked()


def error_with_copy(parent: QWidget | None, title: str, message: str) -> None:
    """Show a config error in full with a Copy button — aimed at forwarding the
    error back to whoever authored the config."""
    _CopyErrorDialog(parent, title, message).exec()


class _CopyErrorDialog(QDialog):
    def __init__(self, parent: QWidget | None, title: str, message: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self._message = message

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 16)
        outer.setSpacing(12)

        box = QTextEdit()
        box.setReadOnly(True)
        box.setObjectName("logBox")
        box.setPlainText(message)
        box.setMinimumHeight(140)
        outer.addWidget(box)

        btn_row = QHBoxLayout()
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setObjectName("dlgBtn")
        self._copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(self._copy_btn)
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("dlgBtnPrimary")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        outer.addLayout(btn_row)

        self._apply_stylesheet()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._message)
        self._copy_btn.setText("Copied!")

    def _apply_stylesheet(self) -> None:
        t = _get_theme()
        self.setStyleSheet(f"""
            QDialog, QWidget {{
                background-color: {t.bg};
                color: {t.panel_text};
                font-family: "Helvetica Neue", Arial, sans-serif;
                font-size: 13px;
            }}
            QTextEdit#logBox {{
                background-color: {t.display};
                border: 1px solid {t.muted};
                border-radius: 5px;
                font-family: "Consolas", monospace;
                font-size: 12px;
                padding: 6px;
                color: {t.text};
            }}
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
