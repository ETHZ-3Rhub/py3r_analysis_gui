"""Tracking-environment status display and install-gating.

Shows the "Tracking: ..." status dot/label and handles checking, offering,
and running the background `tracking_env` install. Single source of truth
for tracking-env status — nothing else (e.g. Settings) duplicates it.
"""

from __future__ import annotations

import datetime
import os
import platform
import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from app.confirm_dialog import ask, info
from app.install_log_window import InstallLogWindow
from app.proc_utils import NO_WINDOW
from app.theme import get_theme as _get_theme
from app.tracking_env_setup import (
    _check_internet,
    _gpu_install_failed,
    _read_install_log,
    _ReinstallWorker,
)


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
            "Use 'Show log' > 'Copy log' to share details, or "
            "try (Re)install tracking environment again.",
        )
    return (t.error, "Status unknown", "Could not determine tracking environment status.")


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


class TrackingEnvPanel(QWidget):
    # Emitted whenever the tracking-env status changes (including while
    # "installing" ticks over) — listeners should re-check anything gated on
    # env readiness (run button, video-source availability).
    status_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _T = _get_theme()

        self._env_status: str = "checking"  # mirrors EnvCheckWorker result strings
        self._env_check_worker: EnvCheckWorker | None = None  # the current (latest) checker
        # Every env checker still running. A new check supersedes the previous
        # one, but the old QThread may still be mid-subprocess — we keep a
        # strong reference here so the GC can't free a live QThread (which
        # aborts the process), retiring each on its own `finished` signal.
        self._env_workers: set[EnvCheckWorker] = set()
        self._tracking_install_worker: _ReinstallWorker | None = None
        self._install_timer: QTimer | None = None
        self._install_start_time: datetime.datetime | None = None
        self._log_window = InstallLogWindow(self)
        self._log_window.set_text(_read_install_log())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._env_dot = QLabel("●")
        self._env_dot.setFixedWidth(14)
        self._env_dot.setStyleSheet(f"color: {_T.muted}; font-size: 13px;")
        self._env_lbl = QLabel("Tracking: Checking…")
        self._env_lbl.setStyleSheet(f"color: {_T.muted}; font-size: 11px;")
        self._log_btn = QPushButton("Show log")
        self._log_btn.setObjectName("settingsButton")
        self._log_btn.clicked.connect(self._log_window.show_and_raise)
        layout.addWidget(self._env_dot)
        layout.addWidget(self._env_lbl)
        layout.addWidget(self._log_btn)
        layout.addStretch()

    # ── Public API ───────────────────────────────────────────────────────────
    def status(self) -> str:
        return self._env_status

    def env_ready(self) -> bool:
        return self._env_status == "cpu" or self._env_status.startswith("cuda:")

    def is_installing(self) -> bool:
        return self._tracking_install_worker is not None

    def start_install(self) -> bool:
        """Start a reinstall if one isn't already running (no-op otherwise).

        Returns False if no install is in flight afterwards (e.g. declined
        by the pre-flight connectivity check) — callers must not show an
        "installing" UI in that case."""
        if self._tracking_install_worker is None:
            self._start_tracking_install()
        return self._tracking_install_worker is not None

    def shutdown(self) -> None:
        """Stop and join any in-flight worker threads. Qt aborts (SIGABRT) if
        a QThread is destroyed while still running, so the window's
        closeEvent must call this before the panel is torn down."""
        for worker in list(self._env_workers):
            try:
                worker.done.disconnect(self._on_env_status)
            except TypeError:
                pass  # already disconnected
            worker.wait()
        if self._tracking_install_worker is not None and self._tracking_install_worker.isRunning():
            self._tracking_install_worker.done.disconnect(self._on_tracking_install_done)
            self._tracking_install_worker.terminate()
            self._tracking_install_worker.wait()

    def kick_env_check(self) -> None:
        """(Re)start the tracking-env status check, superseding any previous
        one. The old checker (if still mid-subprocess) is left to finish on its
        own and retired via `_finalize_env_worker`; its now-stale result is
        ignored by `_on_env_status`. Held in `_env_workers` meanwhile so the GC
        can't destroy a running QThread."""
        worker = EnvCheckWorker()
        self._env_workers.add(worker)
        worker.done.connect(self._on_env_status)
        worker.finished.connect(lambda: self._finalize_env_worker(worker))
        self._env_check_worker = worker
        worker.start()

    def offer_tracking_install_if_needed(self) -> bool:
        """If tracking isn't set up, offer to install it in the background.

        Returns False only if the user declined — callers should treat that
        as "don't proceed with selecting this source"."""
        if self._env_status != "not_installed" and not self._env_status.startswith("error"):
            return True
        if self._tracking_install_worker is not None:
            return True

        if ask(
            self,
            "Set up tracking",
            "Tracking videos requires a one-time setup that downloads PyTorch "
            "and other dependencies — about 2.5GB if you have an NVIDIA GPU, "
            "or about 250MB if not.\n\n"
            "This runs in the background, so you can carry on setting up your "
            "analysis while it installs — tracking just won't be available "
            "until it finishes.",
            yes_label="Set up now",
            no_label="Not now",
        ):
            self._start_tracking_install()
            return True
        return False

    def refresh_theme(self) -> None:
        _T = _get_theme()
        if self._env_status == "checking":
            self._set_env_display(_T.muted, "Checking…", "")
        elif self._env_status in ("installing", "verifying"):
            self._update_install_elapsed()
        else:
            self._apply_env_status(self._env_status)

    # ── Internals ────────────────────────────────────────────────────────────
    def _set_env_display(self, colour: str, label: str, tooltip: str) -> None:
        self._env_dot.setStyleSheet(f"color: {colour}; font-size: 13px;")
        self._env_lbl.setText(f"Tracking: {label}")
        self._env_lbl.setStyleSheet(f"color: {colour}; font-size: 11px;")
        self._env_lbl.setToolTip(tooltip)
        self._env_dot.setToolTip(tooltip)

    def _finalize_env_worker(self, worker: EnvCheckWorker) -> None:
        self._env_workers.discard(worker)
        worker.deleteLater()

    def _on_env_status(self, result: str) -> None:
        # Slot for EnvCheckWorker.done. Ignore a late result from a checker
        # that's since been superseded, so a slow stale reply can't clobber
        # fresh status (e.g. the check kicked right after an install finishes).
        if self.sender() is not self._env_check_worker:
            return
        self._apply_env_status(result)

    def _apply_env_status(self, result: str) -> None:
        if self._install_timer is not None:
            self._install_timer.stop()
            self._install_timer = None
        self._env_status = result
        colour, label, tooltip = parse_env_result(result)
        self._set_env_display(colour, label, tooltip)
        self.status_changed.emit()

    def _start_tracking_install(self) -> None:
        if not _check_internet():
            info(
                self.window(),
                "No internet connection",
                "Tracking setup needs to download dependencies, but no internet "
                "connection was detected.\n\nConnect and try again.",
            )
            return

        self._install_start_time = datetime.datetime.now()
        self._install_timer = QTimer(self)
        self._install_timer.timeout.connect(self._update_install_elapsed)
        self._install_timer.start(1000)
        self._update_install_elapsed()

        self._log_window.set_text("")

        self._tracking_install_worker = _ReinstallWorker()
        self._tracking_install_worker.output.connect(self._log_window.append)
        self._tracking_install_worker.done.connect(self._on_tracking_install_done)
        self._tracking_install_worker.start()

    def _update_install_elapsed(self) -> None:
        _T = _get_theme()
        elapsed = int((datetime.datetime.now() - self._install_start_time).total_seconds())
        m, s = divmod(elapsed, 60)
        if self._env_status != "verifying":
            self._env_status = "installing"
        verb = "Verifying" if self._env_status == "verifying" else "Installing"
        self._set_env_display(
            _T.muted,
            f"{verb}… {m}m {s:02d}s",
            "Setting up the tracking environment for your hardware. This can take several minutes.",
        )
        self.status_changed.emit()

    def _on_tracking_install_done(self, success: bool, diagnosis: str) -> None:
        self._tracking_install_worker.deleteLater()
        self._tracking_install_worker = None
        if success:
            self._env_status = "verifying"
            self._update_install_elapsed()
            self.kick_env_check()
        else:
            self._apply_env_status(f"error:{diagnosis}" if diagnosis else "error")
