"""About dialog — credit, licence and source links."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from app.styles import base_stylesheet
from app.theme import get_theme
from app.tracking_env_setup import get_version
from app.update_check import REPO

CREDIT = "by Tom Lorimer, 3R Hub  ·  © 2026"
REPO_URL = f"https://github.com/{REPO}"


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Analys3R")
        self.setMinimumWidth(460)

        t = get_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel(f"Analys3R  v{get_version()}")
        title.setObjectName("aboutTitle")
        layout.addWidget(title)

        layout.addWidget(QLabel("Created by Tom Lorimer in the 3R Hub, ETH Zurich.<br>© 2026"))

        body = QLabel(
            "Initial concept: [PERSON 1]<br>"
            "Testing and feedback: [PERSON 2], [PERSON 1], [PERSON 3]<br><br>"
            "Built using the "
            '<a href="https://github.com/ETHZ-3Rhub/py3r_behaviour">'
            "py3r_behaviour</a> engine.<br><br>"
            f'Source code: <a href="{REPO_URL}">github.com/{REPO}</a><br><br>'
            "The source code is released under the "
            f'<a href="{REPO_URL}/blob/main/LICENSE">MIT Licence</a>.<br><br>'
            "Release builds bundle model weights licensed under "
            '<a href="https://www.gnu.org/licenses/agpl-3.0.html">AGPL-3.0</a>. '
            "The tracking environment installs "
            '<a href="https://github.com/ultralytics/ultralytics">Ultralytics YOLO</a>'
            ", also AGPL-3.0."
        )
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout.addWidget(body)

        close = QPushButton("Close")
        close.setObjectName("secondaryButton")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

        self.setStyleSheet(
            base_stylesheet(t)
            + f"""
            QDialog {{ background-color: {t.bg}; }}
            QLabel {{ background: transparent; color: {t.panel_text}; }}
            QLabel#aboutTitle {{ font-size: 18px; font-weight: bold; }}
            """
        )
