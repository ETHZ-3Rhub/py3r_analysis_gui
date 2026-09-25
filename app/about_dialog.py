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

        credit = "Designed and developed by Tom Lorimer in the 3R Hub, ETH Zurich.<br>© 2026"
        layout.addWidget(QLabel(credit))

        def link(url: str, text: str) -> str:
            # Qt ignores colour on <a> itself; a span inside it is honoured.
            return f'<a href="{url}"><span style="color:{t.accent}">{text}</span></a>'

        body = QLabel(
            "Initial concept: Oliver Sturman<br>"
            "Testing and feedback: Fabienne Rössler, Oliver Sturman, Maria Wilhelm<br><br>"
            "Built using the "
            f"{link('https://github.com/ETHZ-3Rhub/py3r_behaviour', 'py3r_behaviour')} "
            "engine.<br><br>"
            f"Source code: {link(REPO_URL, f'github.com/{REPO}')}<br><br>"
            "The source code is released under the "
            f"{link(f'{REPO_URL}/blob/main/LICENSE', 'MIT Licence')}.<br><br>"
            "Release builds bundle model weights licensed under "
            f"{link('https://www.gnu.org/licenses/agpl-3.0.html', 'AGPL-3.0')}. "
            "The tracking environment installs "
            f"{link('https://github.com/ultralytics/ultralytics', 'Ultralytics YOLO')}"
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
