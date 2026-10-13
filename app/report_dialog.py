"""Report a problem dialog — a prefilled GitHub issue, with email as the fallback.

Nothing is sent from the app. The dialog only builds the report text (questions
to answer plus system details) and either opens it as a new GitHub issue in the
browser or lets the user copy it into an email.
"""

from __future__ import annotations

import platform
import webbrowser
from urllib.parse import urlencode

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.about_dialog import REPO_URL
from app.styles import base_stylesheet
from app.theme import get_theme
from app.tracking_env_panel import parse_env_result
from app.tracking_env_setup import get_version

EMAIL = "3rhub@ethz.ch"

QUESTIONS = (
    "What were you doing?",
    "What happened?",
    "What did you expect to happen?",
    "Does it happen every time?",
)


def os_description() -> str:
    """E.g. "macOS 15.1 (arm64)" or "Windows 11 (AMD64)"."""
    system = platform.system()
    if system == "Darwin":
        name = f"macOS {platform.mac_ver()[0]}"
    elif system == "Windows":
        name = f"Windows {platform.release()}"
    else:
        name = f"{system} {platform.release()}"
    return f"{name} ({platform.machine()})"


def report_subject() -> str:
    return f"[Analys3R v{get_version()}] "


def report_body(env_status: str) -> str:
    """Questions for the user to answer, then the system details."""
    questions = "\n\n\n".join(QUESTIONS)
    return (
        f"{questions}\n\n\n"
        "---\n"
        f"App version: v{get_version()}\n"
        f"System: {os_description()}\n"
        f"Tracking environment: {parse_env_result(env_status)[1]}\n"
    )


def github_issue_url(env_status: str) -> str:
    query = urlencode({"title": report_subject(), "body": report_body(env_status)})
    return f"{REPO_URL}/issues/new?{query}"


class ReportProblemDialog(QDialog):
    def __init__(self, env_status: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Report a problem")
        self.setMinimumWidth(520)
        self._env_status = env_status

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("Report a problem")
        title.setObjectName("reportTitle")
        layout.addWidget(title)

        intro = QLabel(
            "Please describe what you were doing, what happened and what you expected. "
            "Screenshots help. If a run failed, include the run log from the main window."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        github_btn = QPushButton("Open a GitHub issue")
        github_btn.setObjectName("primaryButton")
        github_btn.clicked.connect(lambda: webbrowser.open(github_issue_url(self._env_status)))
        layout.addWidget(github_btn)
        github_note = QLabel(
            "Opens a new issue in your browser with the questions and your system details "
            "filled in. Needs a GitHub account."
        )
        github_note.setObjectName("note")
        github_note.setWordWrap(True)
        layout.addWidget(github_note)

        layout.addSpacing(8)
        layout.addWidget(QLabel("No GitHub account? Email us instead:"))
        layout.addLayout(self._copy_row("To:", EMAIL))
        layout.addLayout(self._copy_row("Subject:", report_subject()))

        message = report_body(env_status)
        message_row = QHBoxLayout()
        message_row.addWidget(QLabel("Message:"))
        message_row.addStretch()
        message_row.addWidget(self._copy_button(lambda: message, "Copy"))
        layout.addLayout(message_row)
        body = QPlainTextEdit(message)
        body.setReadOnly(True)
        body.setMinimumHeight(220)
        layout.addWidget(body)

        close = QPushButton("Close")
        close.setObjectName("secondaryButton")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

        t = get_theme()
        self.setStyleSheet(
            base_stylesheet(t)
            + f"""
            QDialog {{ background-color: {t.bg}; }}
            QLabel {{ background: transparent; color: {t.panel_text}; }}
            QLabel#reportTitle {{ font-size: 18px; font-weight: bold; }}
            QLabel#note {{ color: {t.muted}; font-size: 12px; }}
            QLineEdit, QPlainTextEdit {{
                background-color: {t.display};
                color: {t.text};
                border: 1px solid {t.muted};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            """
        )

    def _copy_row(self, label: str, text: str) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setMinimumWidth(56)
        row.addWidget(lbl)
        field = QLineEdit(text)
        field.setReadOnly(True)
        row.addWidget(field, stretch=1)
        row.addWidget(self._copy_button(lambda: text, "Copy"))
        return row

    def _copy_button(self, get_text, label: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("settingsButton")
        # Wide enough for "Copied" from the start, so the field next to it
        # doesn't shrink while the confirmation shows.
        btn.ensurePolished()
        metrics = btn.fontMetrics()
        btn.setMinimumWidth(
            max(metrics.horizontalAdvance(label), metrics.horizontalAdvance("Copied")) + 8
        )

        def copy() -> None:
            QApplication.clipboard().setText(get_text())
            btn.setText("Copied")
            QTimer.singleShot(1500, lambda: btn.setText(label))

        btn.clicked.connect(copy)
        return btn
