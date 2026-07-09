"""Settings dialog — app version, tracking environment status, reinstall."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from app.proc_utils import NO_WINDOW
from app.theme import all_themes, update_theme
from app.theme import get_theme as _get_theme
from app.tracking_env_setup import _INSTALL_LOG_NAME, _uv_exe, _uv_version


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


def _read_install_log() -> str:
    """Raw contents of the tracking-env install log, if one exists."""
    from app.trackers.yolo_tracker import tracking_env_dir

    log_path = tracking_env_dir() / _INSTALL_LOG_NAME
    if log_path.exists():
        return log_path.read_text(encoding="utf-8", errors="replace")
    return "(no install log found)"


def _gpu_install_failed() -> bool:
    """Whether the current tracking_env's install log recorded a GPU install
    that fell back to CPU (see the `GPU_FALLBACK:` marker in tracking_env_setup.py).
    install.log is overwritten on every (re)install, so this always reflects
    the currently active environment, not a stale one."""
    return "GPU_FALLBACK:" in _read_install_log()


def _gather_diagnostics() -> str:
    """Bundle the install log + basic system info for support."""
    lines = [
        f"Analys3R  v{get_version()}",
        f"OS: {platform.platform()}",
    ]
    try:
        usage = shutil.disk_usage(Path.home())
        lines.append(f"Free disk: {usage.free / (1024**3):.1f} GB")
    except OSError:
        pass
    try:
        lines.append(f"uv: {_uv_version(_uv_exe())}")
    except SystemExit:
        lines.append("uv: not found")

    lines.append("")
    lines.append("--- install log ---")
    lines.append(_read_install_log())
    return "\n".join(lines)


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
        if _gpu_install_failed():
            return (
                t.warn,
                "CPU only (GPU install failed)",
                "An NVIDIA GPU was detected, but the GPU-accelerated setup didn't "
                "work, so tracking is running on CPU (slower) instead.\n"
                "Click 'Show log' for details, or try (Re)install tracking "
                "environment again.",
            )
        return (
            t.warn,
            "CPU only",
            "Tracking is running on CPU, which is slower.\n"
            "If you have an NVIDIA GPU, go to Settings → (Re)install tracking environment\n"
            "to enable CUDA acceleration.",
        )
    if result == "not_installed":
        return (
            t.error,
            "Not installed",
            "The tracking environment has not been set up yet.\n"
            "Open Settings and click (Re)install tracking environment.",
        )
    if result.startswith("error:"):
        reason = result[len("error:") :] or "see the install log for details."
        return (
            t.error,
            "Setup failed",
            f"Tracking setup failed: {reason}\n"
            "Open Settings and use 'Copy diagnostics' to share details, or "
            "try (Re)install tracking environment again.",
        )
    return (t.error, "Status unknown", "Could not determine tracking environment status.")


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------


class EnvCheckWorker(QThread):
    """Emits one of: "cuda:<version>", "cpu", "not_installed", "error"."""

    done = Signal(str)

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
                creationflags=NO_WINDOW,
            )
            if r.returncode == 0:
                self.done.emit(r.stdout.strip())
            else:
                reason = r.stderr.strip() or f"exited with code {r.returncode}"
                self.done.emit(f"error:{reason}")
        except Exception as exc:
            self.done.emit(f"error:{exc}")


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class SettingsDialog(QDialog):
    def __init__(self, env_panel, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self._env_panel = env_panel
        self._check_worker: EnvCheckWorker | None = None
        self._reinstalling = False
        self._separators: list[QFrame] = []
        self._status_state = "checking"  # "checking" | "installing" | EnvCheckWorker result
        self._build_ui()
        self._apply_stylesheet()
        self._env_panel.status_changed.connect(self._on_env_panel_status_changed)
        self.finished.connect(self._on_finished)
        if self._env_panel.is_installing():
            self._enter_installing_ui()
        else:
            self._start_env_check()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── Version ───────────────────────────────────────────────────────────
        version_lbl = QLabel(f"Analys3R  v{get_version()}")
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

        self._reinstall_btn = QPushButton("(Re)install tracking environment")
        self._reinstall_btn.setObjectName("secondaryButton")
        self._reinstall_btn.clicked.connect(self._start_reinstall)
        layout.addWidget(self._reinstall_btn)

        self._diag_btn = QPushButton("Copy diagnostics")
        self._diag_btn.setObjectName("secondaryButton")
        self._diag_btn.clicked.connect(self._copy_diagnostics)
        layout.addWidget(self._diag_btn)

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
        else:
            colour = parse_env_result(self._status_state)[0]
        self._status_dot.setStyleSheet(f"color: {colour}; font-size: 16px;")
        self._status_lbl.setStyleSheet(f"color: {colour};")

    # ── Reinstall ─────────────────────────────────────────────────────────────

    def _start_reinstall(self) -> None:
        if not self._env_panel.start_install():
            return  # declined, e.g. by the pre-flight connectivity check
        self._enter_installing_ui()

    def _copy_diagnostics(self) -> None:
        QApplication.clipboard().setText(_gather_diagnostics())
        self._diag_btn.setText("Copied!")
        QTimer.singleShot(1500, lambda: self._diag_btn.setText("Copy diagnostics"))

    def _enter_installing_ui(self) -> None:
        self._reinstalling = True
        self._reinstall_btn.setEnabled(False)
        self._reinstall_btn.setText("Installing…")
        self._apply_status("installing", "Installing…", "")

    def _on_env_panel_status_changed(self) -> None:
        status = self._env_panel.status()
        if status in ("installing", "verifying"):
            label = "Verifying…" if status == "verifying" else "Installing…"
            self._apply_status("installing", label, "")
            return

        if self._reinstalling:
            self._reinstalling = False
            self._reinstall_btn.setEnabled(True)
            self._reinstall_btn.setText("(Re)install tracking environment")

        _, label, tooltip = parse_env_result(status)
        self._apply_status(status, label, tooltip)

    def _on_finished(self) -> None:
        self._env_panel.status_changed.disconnect(self._on_env_panel_status_changed)

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
            QScrollBar:vertical {{
                background: {_T.display};
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {_T.muted};
                border-radius: 4px;
            }}
        """)
