"""Non-modal, hideable window showing the tracking-env install log.

There is exactly one instance of this per app run, owned by
`TrackingEnvPanel`. It's the *only* place the install log is displayed —
"Hide log" (or the window's [x]) just hides it rather than closing it, so its
content survives being shown again, and the same instance is reused whether
an install is currently running or you're looking at the last one.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.theme import get_theme as _get_theme


class InstallLogWindow(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tracking environment install log")
        self.setMinimumSize(560, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setObjectName("logBox")
        layout.addWidget(self._text)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy log")
        copy_btn.setObjectName("dlgBtn")
        copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch()
        hide_btn = QPushButton("Hide log")
        hide_btn.setObjectName("dlgBtnPrimary")
        hide_btn.clicked.connect(self.hide)
        btn_row.addWidget(hide_btn)
        layout.addLayout(btn_row)

        self._apply_stylesheet()

    def set_text(self, text: str) -> None:
        self._text.setPlainText(text)
        self._scroll_to_end()

    def append(self, text: str) -> None:
        self._text.insertPlainText(text)
        self._scroll_to_end()

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _scroll_to_end(self) -> None:
        scrollbar = self._text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._text.toPlainText())

    def closeEvent(self, event) -> None:
        # The window is never destroyed - the [x] button just hides it, same
        # as "Hide log", so content and any live connection survive.
        event.ignore()
        self.hide()

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
