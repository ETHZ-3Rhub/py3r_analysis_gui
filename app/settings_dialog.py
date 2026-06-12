"""Settings dialog — app version, tracking environment status, reinstall."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from app.theme import all_themes, update_theme
from app.theme import get_theme as _get_theme

_T = _get_theme()  # cached at import for inline widget-creation calls


def get_version() -> str:
    try:
        from importlib.metadata import version

        return version("py3r-analysis-gui")
    except Exception:
        return "unknown"


def _find_tracking_python() -> Path | None:
    if override := os.environ.get("PY3R_TRACKER_PYTHON"):
        return Path(override)
    from app.trackers.yolo_tracker import tracking_env_dir

    subdir = "Scripts" if platform.system() == "Windows" else "bin"
    exe = "python.exe" if platform.system() == "Windows" else "python"
    candidate = tracking_env_dir() / subdir / exe
    return candidate if candidate.exists() else None


def parse_env_result(result: str) -> tuple[str, str, str]:
    """Return (colour, short_label, tooltip) for a raw EnvCheckWorker result."""
    t = _get_theme()
    if result.startswith("cuda:"):
        cuda_ver = result[5:]
        return (
            t.success,
            f"GPU (CUDA {cuda_ver})",
            f"Tracking is running on your GPU using CUDA {cuda_ver} — optimal performance.",
        )
    if result == "cpu":
        return (
            t.warn,
            "CPU only",
            "Tracking is running on CPU, which is slower.\n"
            "If you have an NVIDIA GPU, go to Settings → Reinstall tracking environment\n"
            "to enable CUDA acceleration.",
        )
    if result == "not_installed":
        return (
            t.error,
            "Not installed",
            "The tracking environment has not been set up yet.\n"
            "Open Settings and click Reinstall tracking environment.",
        )
    return (t.error, "Status unknown", "Could not determine tracking environment status.")


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------


class EnvCheckWorker(QThread):
    """Emits one of: "cuda:<version>", "cpu", "not_installed", "error"."""

    done = pyqtSignal(str)

    def run(self) -> None:
        python = _find_tracking_python()
        if python is None:
            self.done.emit("not_installed")
            return
        try:
            r = subprocess.run(
                [
                    str(python),
                    "-c",
                    "import torch; "
                    "print(('cuda:' + (torch.version.cuda or 'unknown')) "
                    "if torch.cuda.is_available() else 'cpu')",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.done.emit(r.stdout.strip() if r.returncode == 0 else "error")
        except Exception:
            self.done.emit("error")


class _ReinstallWorker(QThread):
    output = pyqtSignal(str)
    done = pyqtSignal(bool)  # True = success

    def run(self) -> None:
        from app.trackers.yolo_tracker import tracking_env_dir

        target = tracking_env_dir()
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--setup-tracking-env", str(target)]
        else:
            cmd = [sys.executable, "-m", "app.main", "--setup-tracking-env", str(target)]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            for raw in proc.stdout:
                self.output.emit(raw.decode("utf-8", errors="replace"))
            proc.wait()
            self.done.emit(proc.returncode == 0)
        except Exception as exc:
            self.output.emit(f"Error: {exc}\n")
            self.done.emit(False)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self._check_worker: EnvCheckWorker | None = None
        self._reinstall_worker: _ReinstallWorker | None = None
        self._separators: list[QFrame] = []
        self._status_state = (
            "checking"  # "checking" | "installing" | "failed" | EnvCheckWorker result
        )
        self._build_ui()
        self._apply_stylesheet()
        self._start_env_check()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── Version ───────────────────────────────────────────────────────────
        version_lbl = QLabel(f"py3r Analysis  v{get_version()}")
        version_lbl.setObjectName("versionLabel")
        layout.addWidget(version_lbl)

        layout.addWidget(self._sep())

        # ── Tracking environment ──────────────────────────────────────────────
        env_title = QLabel("Tracking Environment")
        env_title.setObjectName("sectionTitle")
        layout.addWidget(env_title)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._status_dot = QLabel("●")
        self._status_dot.setFixedWidth(16)
        self._status_lbl = QLabel("Checking…")
        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_lbl, stretch=1)
        layout.addLayout(status_row)

        self._reinstall_btn = QPushButton("Reinstall tracking environment")
        self._reinstall_btn.setObjectName("secondaryButton")
        self._reinstall_btn.clicked.connect(self._start_reinstall)
        layout.addWidget(self._reinstall_btn)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setObjectName("logBox")
        self._log.setFixedHeight(180)
        self._log.setVisible(False)
        layout.addWidget(self._log)

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

        layout.addWidget(self._sep())

        # ── Close ─────────────────────────────────────────────────────────────
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondaryButton")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    # ── Env check ─────────────────────────────────────────────────────────────

    def _start_env_check(self) -> None:
        self._apply_status("checking", "Checking…", "")
        self._check_worker = EnvCheckWorker()
        self._check_worker.done.connect(self._on_env_check_done)
        self._check_worker.start()

    def _on_env_check_done(self, result: str) -> None:
        _, label, tooltip = parse_env_result(result)
        self._apply_status(result, label, tooltip)

    def _apply_status(self, state: str, text: str, tooltip: str) -> None:
        self._status_state = state
        self._status_lbl.setText(f"Tracking: {text}")
        self._status_lbl.setToolTip(tooltip)
        self._refresh_status_colour()

    def _refresh_status_colour(self) -> None:
        t = _get_theme()
        if self._status_state == "checking" or self._status_state == "installing":
            colour = t.muted
        elif self._status_state == "failed":
            colour = t.error
        else:
            colour = parse_env_result(self._status_state)[0]
        self._status_dot.setStyleSheet(f"color: {colour}; font-size: 16px;")
        self._status_lbl.setStyleSheet(f"color: {colour};")

    # ── Reinstall ─────────────────────────────────────────────────────────────

    def _start_reinstall(self) -> None:
        self._log.clear()
        self._log.setVisible(True)
        self._reinstall_btn.setEnabled(False)
        self._reinstall_btn.setText("Installing…")
        self._apply_status("installing", "Installing…", "")

        self._reinstall_worker = _ReinstallWorker()
        self._reinstall_worker.output.connect(self._on_reinstall_output)
        self._reinstall_worker.done.connect(self._on_reinstall_done)
        self._reinstall_worker.start()

    def _on_reinstall_output(self, text: str) -> None:
        self._log.setTextColor(QColor(_T.muted))
        self._log.insertPlainText(text)
        self._log.ensureCursorVisible()

    def _on_reinstall_done(self, success: bool) -> None:
        self._reinstall_btn.setEnabled(True)
        self._reinstall_btn.setText("Reinstall tracking environment")
        if success:
            self._log.setTextColor(QColor(_T.success))
            self._log.insertPlainText("\nDone.\n")
            self._start_env_check()
        else:
            self._log.setTextColor(QColor(_T.error))
            self._log.insertPlainText("\nInstallation failed — see output above.\n")
            self._apply_status("failed", "Installation failed", "")

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
        self._refresh_status_colour()
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
                color: white;
            }}
            QPushButton#secondaryButton:disabled {{
                color: {_T.muted};
                border-color: {_T.muted};
            }}
            QTextEdit#logBox {{
                background-color: {_T.display};
                border: 1px solid {_T.muted};
                border-radius: 5px;
                font-family: "Consolas", monospace;
                font-size: 11px;
                padding: 4px;
                color: {_T.muted};
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
