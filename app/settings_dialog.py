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
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

_COL_BG = "#1e1e2e"
_COL_PANEL = "#2a2a3e"
_COL_ACCENT = "#7c6af7"
_COL_TEXT = "#cdd6f4"
_COL_MUTED = "#6c7086"
_COL_SEP = "#3a3a4e"
_COL_ERROR = "#f38ba8"
_COL_WARN = "#fab387"
_COL_SUCCESS = "#a6e3a1"


def get_version() -> str:
    try:
        from importlib.metadata import version

        return version("py3r-analysis-gui")
    except Exception:
        return "unknown"


def _find_tracking_python() -> Path | None:
    if override := os.environ.get("PY3R_TRACKER_PYTHON"):
        return Path(override)
    subdir = "Scripts" if platform.system() == "Windows" else "bin"
    exe = "python.exe" if platform.system() == "Windows" else "python"
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).parent / "tracking_env" / subdir / exe
        return candidate if candidate.exists() else None
    repo_root = Path(__file__).parent.parent
    local = repo_root / "tracking_env" / subdir / exe
    return local if local.exists() else None


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------


class _EnvCheckWorker(QThread):
    done = pyqtSignal(str)  # "cuda", "cpu", "not_installed", "error"

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
                    "import torch; print('cuda' if torch.cuda.is_available() else 'cpu')",
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
        script = Path(__file__).parent.parent / "scripts" / "setup_tracking_env.py"
        try:
            proc = subprocess.Popen(
                [sys.executable, str(script)],
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
        self._check_worker: _EnvCheckWorker | None = None
        self._reinstall_worker: _ReinstallWorker | None = None
        self._build_ui()
        self._apply_stylesheet()
        self._start_env_check()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── Version ──────────────────────────────────────────────────────────
        version_lbl = QLabel(f"py3r Analysis  v{get_version()}")
        version_lbl.setObjectName("versionLabel")
        layout.addWidget(version_lbl)

        layout.addWidget(_sep())

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

        layout.addWidget(_sep())

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
        self._set_status("checking")
        self._check_worker = _EnvCheckWorker()
        self._check_worker.done.connect(self._on_env_check_done)
        self._check_worker.start()

    def _on_env_check_done(self, result: str) -> None:
        if result == "cuda":
            self._set_status("gpu")
        elif result == "cpu":
            self._set_status("cpu")
        elif result == "not_installed":
            self._set_status("not_installed")
        else:
            self._set_status("error")

    def _set_status(self, state: str) -> None:
        configs = {
            "checking": (_COL_MUTED, "Checking…"),
            "gpu": (_COL_SUCCESS, "GPU (CUDA) — fast inference enabled"),
            "cpu": (_COL_WARN, "CPU only — tracking will be slower"),
            "not_installed": (_COL_ERROR, "Not installed — click Reinstall to set up"),
            "error": (_COL_ERROR, "Could not determine status"),
        }
        colour, text = configs.get(state, (_COL_MUTED, state))
        self._status_dot.setStyleSheet(f"color: {colour}; font-size: 16px;")
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {colour};")

    # ── Reinstall ─────────────────────────────────────────────────────────────

    def _start_reinstall(self) -> None:
        self._log.clear()
        self._log.setVisible(True)
        self._reinstall_btn.setEnabled(False)
        self._reinstall_btn.setText("Installing…")
        self._set_status("checking")

        self._reinstall_worker = _ReinstallWorker()
        self._reinstall_worker.output.connect(self._on_reinstall_output)
        self._reinstall_worker.done.connect(self._on_reinstall_done)
        self._reinstall_worker.start()

    def _on_reinstall_output(self, text: str) -> None:
        self._log.setTextColor(QColor(_COL_MUTED))
        self._log.insertPlainText(text)
        self._log.ensureCursorVisible()

    def _on_reinstall_done(self, success: bool) -> None:
        self._reinstall_btn.setEnabled(True)
        self._reinstall_btn.setText("Reinstall tracking environment")
        if success:
            self._log.setTextColor(QColor(_COL_SUCCESS))
            self._log.insertPlainText("\nDone.\n")
            self._start_env_check()
        else:
            self._log.setTextColor(QColor(_COL_ERROR))
            self._log.insertPlainText("\nInstallation failed — see output above.\n")
            self._set_status("error")

    # ── Stylesheet ────────────────────────────────────────────────────────────

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {_COL_BG};
                color: {_COL_TEXT};
                font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
                font-size: 13px;
            }}
            QLabel {{
                background: transparent;
                color: {_COL_TEXT};
            }}
            QLabel#versionLabel {{
                font-size: 18px;
                font-weight: bold;
                color: {_COL_TEXT};
            }}
            QLabel#sectionTitle {{
                color: {_COL_TEXT};
                font-weight: bold;
                font-size: 12px;
                letter-spacing: 1px;
                text-transform: uppercase;
            }}
            QPushButton#secondaryButton {{
                background-color: transparent;
                color: {_COL_ACCENT};
                border: 1px solid {_COL_ACCENT};
                border-radius: 5px;
                padding: 6px 10px;
            }}
            QPushButton#secondaryButton:hover {{
                background-color: {_COL_ACCENT};
                color: white;
            }}
            QPushButton#secondaryButton:disabled {{
                color: {_COL_MUTED};
                border-color: {_COL_MUTED};
            }}
            QTextEdit#logBox {{
                background-color: {_COL_PANEL};
                border: 1px solid {_COL_MUTED};
                border-radius: 5px;
                font-family: "Consolas", monospace;
                font-size: 11px;
                padding: 4px;
                color: {_COL_MUTED};
            }}
            QScrollBar:vertical {{
                background: {_COL_BG};
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {_COL_MUTED};
                border-radius: 4px;
            }}
        """)


def _sep() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #3a3a4e; margin: 2px 0;")
    return line
