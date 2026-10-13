"""Update-available indicator (bottom bar) and release-notes dialog.

Checks GitHub releases once per launch, in the background, and shows a
quiet clickable button next to Help/Settings only when something newer is
available — never a popup. See app/update_check.py for the
network/version-comparison logic this button just displays.
"""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.theme import get_theme as _get_theme
from app.update_check import ReleaseInfo, check_for_updates

# The GitHub release page links a source tarball/zip alongside the actual
# build asset, which confuses non-specialist users — send them to the
# purpose-built download page (docs-site/) instead, which links the build
# asset directly. Keep in sync with the README download link.
DOWNLOAD_PAGE_URL = "https://ethz-3rhub.github.io/py3r_analysis_gui/"


class _UpdateCheckWorker(QThread):
    done = Signal(list)  # list[ReleaseInfo]

    def __init__(self, current_version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_version = current_version

    def run(self) -> None:
        self.done.emit(check_for_updates(self._current_version))


class UpdateIndicator(QPushButton):
    """Hidden until a check finds a newer release; click shows release notes.

    Shown green (the ``fresh`` property, styled in styles.py) until clicked,
    then the same grey as Help/Settings for the rest of the session.
    """

    def __init__(self, current_version: str, parent: QWidget | None = None) -> None:
        super().__init__("⬆  Update", parent)
        self._current_version = current_version
        self._releases: list[ReleaseInfo] = []
        self._worker: _UpdateCheckWorker | None = None

        self.setObjectName("settingsButton")
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setVisible(False)  # nothing to show until a check finds something
        self.clicked.connect(self._show_dialog)

    def kick_check(self) -> None:
        """Start a background check, superseding nothing — at most one runs
        per launch, so a second call while one is in flight is a no-op."""
        if self._worker is not None:
            return
        self._worker = _UpdateCheckWorker(self._current_version, self)
        self._worker.done.connect(self._on_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def shutdown(self) -> None:
        """Join the checker thread if still running — Qt aborts (SIGABRT) if
        a QThread is destroyed mid-run, so the window's closeEvent must call
        this before teardown, same as TrackingEnvPanel.shutdown()."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait()

    def _on_done(self, releases: list[ReleaseInfo]) -> None:
        self._worker = None
        self._releases = releases
        self.setVisible(bool(releases))
        if releases:
            tags = ", ".join(r.tag for r in releases)
            self.setToolTip(f"New version available: {tags}\nClick for release notes.")
            self._set_fresh(True)

    def _set_fresh(self, fresh: bool) -> None:
        self.setProperty("fresh", fresh)
        # Qt only re-reads property selectors on a re-polish.
        self.style().unpolish(self)
        self.style().polish(self)

    def _show_dialog(self) -> None:
        if not self._releases:
            return
        self._set_fresh(False)
        _ReleaseNotesDialog(self._releases, self).exec()


class _ReleaseNotesDialog(QDialog):
    def __init__(self, releases: list[ReleaseInfo], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update available")
        self.setMinimumSize(520, 420)
        self._build_ui(releases)
        self._apply_stylesheet()

    def _build_ui(self, releases: list[ReleaseInfo]) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 16)
        outer.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("notesScroll")
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(16)

        for release in releases:
            inner_layout.addLayout(self._release_section(release))
        inner_layout.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        download_btn = QPushButton("Open download page")
        download_btn.setObjectName("dlgBtn")
        download_btn.clicked.connect(lambda: webbrowser.open(DOWNLOAD_PAGE_URL))
        btn_row.addWidget(download_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("dlgBtnPrimary")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

    def _release_section(self, release: ReleaseInfo) -> QVBoxLayout:
        section = QVBoxLayout()
        section.setSpacing(6)

        kind = "Pre-release" if release.prerelease else "Stable release"
        title = QLabel(f"{release.name}  ·  {kind}")
        title.setObjectName("sectionTitle")
        section.addWidget(title)

        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setObjectName("logBox")
        notes.setMarkdown(release.body or "_No release notes provided._")
        notes.setMinimumHeight(120)
        notes.setMaximumHeight(240)
        section.addWidget(notes)

        return section

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
            QLabel#sectionTitle {{
                color: {t.title};
                font-weight: bold;
                font-size: 12px;
                letter-spacing: 1px;
                text-transform: uppercase;
            }}
            QScrollArea#notesScroll {{ background: transparent; border: none; }}
            QTextEdit#logBox {{
                background-color: {t.display};
                border: 1px solid {t.muted};
                border-radius: 5px;
                font-size: 12px;
                padding: 6px;
                color: {t.text};
            }}
            QScrollBar:vertical {{
                background: {t.display};
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {t.muted};
                border-radius: 4px;
            }}
            QPushButton#dlgBtn {{
                background-color: transparent;
                color: {t.accent};
                border: 1px solid {t.accent};
                border-radius: 5px;
                padding: 6px 20px;
                min-width: 72px;
            }}
            QPushButton#dlgBtn:hover {{ background-color: {t.accent}; color: {t.accent_text}; }}
            QPushButton#dlgBtnPrimary {{
                background-color: {t.accent};
                color: {t.accent_text};
                border: none;
                border-radius: 5px;
                padding: 6px 20px;
                min-width: 72px;
                font-weight: bold;
            }}
            QPushButton#dlgBtnPrimary:hover {{ background-color: {t.accent_hover}; }}
        """)
