"""Arena orchestrator — runs in a background QThread.

Owns the full run lifecycle: per-group tracking,
error collection, pipeline execution, and warning file output.
Arena modules are pure config; all logic lives here.
"""

from __future__ import annotations

import os
import subprocess
import traceback
from pathlib import Path
from types import ModuleType

from PyQt6.QtCore import QThread, pyqtSignal


def _kill_tree(pid: int) -> None:
    """Kill a process and all its children (Windows: taskkill /F /T, Unix: SIGKILL pgid)."""
    import platform

    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        else:
            import os
            import signal

            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass


class PipelineRunner(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    warning = pyqtSignal(str)
    subprocess_output = pyqtSignal(str)  # raw chunks from tracked subprocess
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        arena: ModuleType,
        groups: dict[str, list[Path]],
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
        self._warnings: list[str] = []
        self._current_proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        if self._current_proc is not None:
            _kill_tree(self._current_proc.pid)
        self.terminate()

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
        csv_files: dict[str, list[Path]] = {}
        n_groups = len(self._groups)

        for i, (group_name, files) in enumerate(self._groups.items()):
            if self._skip_tracking:
                csv_files[group_name] = files
                continue

            self._progress_cb(f"Group {i + 1}/{n_groups}: {group_name}", None)
            csv_out = self._output_dir / "tracking" / group_name
            csv_out.mkdir(parents=True, exist_ok=True)

            if not files:
                self._warn(f"{group_name}: no video files added")
                continue

            tracked_any = False
            n_videos = len(files)
            for j, video in enumerate(files):
                self._progress_cb(f"  Tracking {video.name} ({j + 1}/{n_videos})…", None)
                try:
                    proc = arena.TRACKER.track(video, csv_out, **arena.TRACKER_ARGS)
                    self._current_proc = proc
                    self._drain_proc(proc)
                    self._current_proc = None
                    if proc.returncode != 0:
                        raise RuntimeError(f"exit code {proc.returncode}")
                    tracked_any = True
                except Exception as exc:
                    self._warn(f"{group_name} / {video.name}: tracking failed — {exc}")

            if tracked_any:
                csv_files[group_name] = sorted(
                    p
                    for p in csv_out.iterdir()
                    if p.is_file() and not p.name.startswith(".") and p.suffix.lower() == ".csv"
                )
            else:
                self._warn(f"{group_name}: no videos tracked successfully, skipping pipeline")

        if not csv_files:
            raise RuntimeError("No groups were tracked successfully — cannot run pipeline.")

        self._progress_cb("Running analysis pipeline…", 40)
        try:
            arena.PIPELINE.run(
                group_csv_files=csv_files,
                output_dir=self._output_dir,
                progress_cb=self._progress_cb,
                comparisons=self._comparisons,
                group_video_files=self._groups,
            )
        except Exception as exc:
            self._warn(f"Pipeline error: {exc}")

        if self._warnings:
            self._write_warning_file()

        self._progress_cb("Done.", 100)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _drain_proc(self, proc: subprocess.Popen) -> None:
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            self.subprocess_output.emit(chunk.decode("utf-8", errors="replace"))
        proc.wait()

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
