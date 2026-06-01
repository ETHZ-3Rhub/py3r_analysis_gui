"""Arena orchestrator — runs in a background QThread.

Owns the full run lifecycle: per-group tracking, per-video watchdog,
error collection, pipeline execution, and warning file output.
Arena modules are pure config; all logic lives here.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import traceback
from pathlib import Path
from types import ModuleType

from PyQt6.QtCore import QThread, pyqtSignal

_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv"}
_WATCHDOG_SECONDS = 60


class PipelineRunner(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    warning = pyqtSignal(str)
    subprocess_output = pyqtSignal(str)  # raw chunks from tracked subprocess
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    stall = pyqtSignal(str)  # emits video_name; main thread must call resolve_stall()

    def __init__(
        self,
        arena: ModuleType,
        groups: dict[str, Path],
        output_dir: Path,
        comparisons: list[tuple[str, str]],
        *,
        skip_tracking: bool = False,
    ) -> None:
        super().__init__()
        self._arena = arena
        self._groups = groups
        self._output_dir = output_dir
        self._comparisons = comparisons
        self._skip_tracking = skip_tracking or os.environ.get("DEV_SKIP_TRACKING", "").lower() in (
            "1",
            "true",
            "yes",
        )
        self._stall_event: threading.Event = threading.Event()
        self._stall_result: str = "skip"
        self._warnings: list[str] = []

    def resolve_stall(self, result: str) -> None:
        self._stall_result = result
        self._stall_event.set()

    def run(self) -> None:
        try:
            self.log.emit(f"Starting {self._arena.NAME}…")
            self._run_arena()
            self.finished.emit(str(self._output_dir))
        except Exception:
            self.error.emit(traceback.format_exc())

    # ── Orchestration ──────────────────────────────────────────────────────────

    def _run_arena(self) -> None:
        arena = self._arena
        csv_dirs: dict[str, Path] = {}
        n_groups = len(self._groups)

        for i, (group_name, video_dir) in enumerate(self._groups.items()):
            if self._skip_tracking:
                csv_dirs[group_name] = video_dir
                continue

            self._progress_cb(f"Group {i + 1}/{n_groups}: {group_name}", None)
            csv_out = self._output_dir / group_name
            csv_out.mkdir(parents=True, exist_ok=True)

            video_files = sorted(
                f for f in video_dir.iterdir() if f.is_file() and f.suffix.lower() in _VIDEO_EXTS
            )
            if not video_files:
                self._warn(f"{group_name}: no video files found in {video_dir}")
                continue

            tracked_any = False
            n_videos = len(video_files)
            for j, video in enumerate(video_files):
                self._progress_cb(f"  Tracking {video.name} ({j + 1}/{n_videos})…", None)
                try:
                    proc = arena.TRACKER.track(video, csv_out, **arena.TRACKER_ARGS)
                    skipped = self._watchdog(proc, video.name)
                    if skipped:
                        self._warn(f"{group_name} / {video.name}: stalled, skipped")
                    else:
                        tracked_any = True
                except Exception as exc:
                    self._warn(f"{group_name} / {video.name}: tracking failed — {exc}")

            if tracked_any:
                csv_dirs[group_name] = csv_out
            else:
                self._warn(f"{group_name}: no videos tracked successfully, skipping pipeline")

        if not csv_dirs:
            raise RuntimeError("No groups were tracked successfully — cannot run pipeline.")

        self._progress_cb("Running analysis pipeline…", 40)
        try:
            arena.PIPELINE.run(
                group_csv_dirs=csv_dirs,
                output_dir=self._output_dir,
                progress_cb=self._progress_cb,
                comparisons=self._comparisons,
            )
        except Exception as exc:
            self._warn(f"Pipeline error: {exc}")

        if self._warnings:
            self._write_warning_file()

        self._progress_cb("Done.", 100)

    # ── Watchdog ───────────────────────────────────────────────────────────────

    def _watchdog(self, proc: subprocess.Popen, video_name: str) -> bool:
        """Monitor proc output. Returns True if skipped, False if completed OK."""
        last_output = [time.monotonic()]

        def _read() -> None:
            try:
                while True:
                    chunk = proc.stdout.read(256)
                    if not chunk:
                        break
                    last_output[0] = time.monotonic()
                    self.subprocess_output.emit(chunk.decode("utf-8", errors="replace"))
            except Exception:
                pass

        threading.Thread(target=_read, daemon=True).start()

        while proc.poll() is None:
            time.sleep(1)
            if time.monotonic() - last_output[0] > _WATCHDOG_SECONDS:
                decision = self._stall_cb(video_name)
                if decision == "wait":
                    last_output[0] = time.monotonic()
                else:
                    proc.kill()
                    proc.wait()
                    return True

        if proc.returncode != 0:
            raise RuntimeError(f"exit code {proc.returncode}")
        return False

    def _stall_cb(self, video_name: str) -> str:
        self._stall_event.clear()
        self.stall.emit(video_name)
        self._stall_event.wait()
        return self._stall_result

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _progress_cb(self, message: str, pct: float | None) -> None:
        self.log.emit(message)
        self.progress.emit(int(pct) if pct is not None else -1)

    def _warn(self, msg: str) -> None:
        self._warnings.append(msg)
        self.warning.emit(msg)

    def _write_warning_file(self) -> None:
        path = self._output_dir / "WARNING_THERE WERE PROCESSING ERRORS!!!.txt"
        with path.open("w") as f:
            f.write(
                f"{len(self._warnings)} issue(s) occurred during processing.\n"
                "Affected videos were skipped — please review them manually.\n\n"
            )
            for w in self._warnings:
                f.write(f"  • {w}\n")
