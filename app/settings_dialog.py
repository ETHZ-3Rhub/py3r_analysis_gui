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


def parse_env_result(result: str) -> tuple[str, str, str]:
    """Return (colour, short_label, tooltip) for a raw EnvCheckWorker result."""
    if result.startswith("cuda:"):
        cuda_ver = result[5:]
        return (
            _COL_SUCCESS,
            f"GPU (CUDA {cuda_ver})",
            f"Tracking is running on your GPU using CUDA {cuda_ver} — optimal performance.",
        )
    if result == "cpu":
        return (
            _COL_WARN,
            "CPU only",
            "Tracking is running on CPU, which is slower.\n"
            "If you have an NVIDIA GPU, go to Settings → Reinstall tracking environment\n"
            "to enable CUDA acceleration.",
        )
    if result == "not_installed":
        return (
            _COL_ERROR,
            "Not installed",
            "The tracking environment has not been set up yet.\n"
            "Open Settings and click Reinstall tracking environment.",
        )
    return (_COL_ERROR, "Status unknown", "Could not determine tracking environment status.")


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
        self._check_worker: EnvCheckWorker | None = None
        self._reinstall_worker: _ReinstallWorker | None = None
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
        self._apply_status(_COL_MUTED, "Checking…", "")
        self._check_worker = EnvCheckWorker()
        self._check_worker.done.connect(self._on_env_check_done)
        self._check_worker.start()

    def _on_env_check_done(self, result: str) -> None:
        colour, label, tooltip = parse_env_result(result)
        self._apply_status(colour, label, tooltip)

    def _apply_status(self, colour: str, text: str, tooltip: str) -> None:
        self._status_dot.setStyleSheet(f"color: {colour}; font-size: 16px;")
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {colour};")
        self._status_lbl.setToolTip(tooltip)

    # ── Reinstall ─────────────────────────────────────────────────────────────

    def _start_reinstall(self) -> None:
        self._log.clear()
        self._log.setVisible(True)
        self._reinstall_btn.setEnabled(False)
        self._reinstall_btn.setText("Installing…")
        self._apply_status(_COL_MUTED, "Installing…", "")

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
            self._apply_status(_COL_ERROR, "Installation failed", "")

    # ── Stylesheet ────────────────────────────────────────────────────────────

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {_COL_BG};
                color: {_COL_TEXT};
                font-family: "Helvetica Neue", Arial, sans-serif;
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
