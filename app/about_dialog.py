"""About dialog — credit, licence and source links."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from app import paths
from app.styles import base_stylesheet
from app.theme import get_theme
from app.tracking_env_setup import get_version
from app.update_check import REPO

CREDIT = "by Tom Lorimer @ 3R Hub  ·  © 2026"
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

        credit = "Designed and developed by Tom Lorimer at the 3R Hub, ETH Zurich.<br>© 2026"
        layout.addWidget(QLabel(credit))

        def link(url: str, text: str) -> str:
            # Qt ignores colour on <a> itself; a span inside it is honoured.
            return f'<a href="{url}"><span style="color:{t.accent}">{text}</span></a>'

        macos_note = (
            "<br><br>The macOS build also includes FFmpeg with the x264 and x265 codecs "
            f"({link('https://www.gnu.org/licenses/gpl-3.0.html', 'GPL-3.0')}); "
            "see the licence notices for source."
            if sys.platform == "darwin"
            else ""
        )

        body = QLabel(
            "Initial concept: Oliver Sturman<br>"
            "Testing and feedback: Fabienne Rössler, Oliver Sturman, Kimberly Thibeault, "
            "Maria Wilhelm<br><br>"
            "Built using the "
            f"{link('https://github.com/ETHZ-3Rhub/py3r_behaviour', 'py3r_behaviour')} "
            "engine.<br><br>"
            f"Source code: {link(REPO_URL, f'github.com/{REPO}')}<br><br>"
            "The source code is released under the "
            f"{link(f'{REPO_URL}/blob/main/LICENSE', 'MIT Licence')}, except "
            "<code>app/trackers/track.py</code>, which uses Ultralytics and is therefore "
            "AGPL-3.0 when combined with it.<br><br>"
            "Release builds include open-source components under their own licences, "
            "including Qt/PySide6 "
            f"({link('https://www.gnu.org/licenses/lgpl-3.0.html', 'LGPL-3.0')}"
            f"{', and FFmpeg (LGPL-2.1)' if sys.platform == 'win32' else ''}; "
            "dynamically linked and replaceable) and model weights licensed under "
            f"{link('https://www.gnu.org/licenses/agpl-3.0.html', 'AGPL-3.0')}. "
            "The tracking environment installs "
            f"{link('https://github.com/ultralytics/ultralytics', 'Ultralytics YOLO')}"
            " and ultralytics-thop, also AGPL-3.0." + macos_note
        )
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout.addWidget(body)

        notices = QPushButton("Licence notices…")
        notices.setObjectName("secondaryButton")
        licences_dir = paths.bundled_licences_dir()
        if licences_dir.is_dir():
            notices.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(licences_dir)))
            )
        else:
            # Disable, don't hide: only a packaged build ships the notices folder.
            notices.setEnabled(False)
            notices.setToolTip("Third-party licence notices are included in release builds.")
        layout.addWidget(notices)

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
