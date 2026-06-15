"""Arena orchestrator — runs in a background QThread.

Owns the full run lifecycle: per-group tracking,
error collection, pipeline execution, and warning file output.
Arena modules are pure config; all logic lives here.
"""

from __future__ import annotations

import os
import pickle
import queue
import subprocess
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from importlib.metadata import version as _pkg_version
from pathlib import Path
from types import ModuleType

from PySide6.QtCore import QThread, Signal

from app import naming
from app.proc_utils import kill_tree, popen_grouped

_HEARTBEAT_INTERVAL = 1.0  # seconds of silence before emitting a heartbeat tick


class PipelineRunner(QThread):
    log = Signal(str)
    warning = Signal(str)
    subprocess_output = Signal(str)  # raw chunks from tracking subprocess
    heartbeat = Signal()  # emitted on silence, to drive a "still working" spinner
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        arena: ModuleType,
        groups: dict[str, list[Path]],
        output_dir: Path,
        comparisons: list[tuple[str, str]],
        *,
        skip_tracking: bool = False,
        options: dict | None = None,
    ) -> None:
        super().__init__()
        self._arena = arena
        self._groups = groups
        self._output_dir = output_dir
        self._comparisons = comparisons
        self._options = options or {}
        self._skip_tracking = skip_tracking or os.environ.get("DEV_SKIP_TRACKING", "").lower() in (
            "1",
            "true",
            "yes",
        )
        self._warnings: list[str] = []
        self._current_proc: subprocess.Popen | None = None
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._current_proc is not None:
            kill_tree(self._current_proc.pid)

    def run(self) -> None:
        self._csv_files: dict[str, list[Path]] = {}
        error: str | None = None
        try:
            self.log.emit(f"Starting {self._arena.NAME}...")
            self._run_arena()
            if not self._cancelled:
                self.finished.emit(str(self._output_dir))
        except Exception:
            error = traceback.format_exc()
            if not self._cancelled:
                self.error.emit(error)
        finally:
            if not self._cancelled:
                self._write_report(error)

    # ── Orchestration ──────────────────────────────────────────────────────────

    def _run_arena(self) -> None:
        arena = self._arena
        csv_files = self._csv_files
        n_groups = len(self._groups)

        handles = naming.assign_handles(self._groups)
        handle_iter = iter(handles)

        tracking_dir = self._output_dir / "tracking"

        manifest: list[tuple[str, str, Path]] = []
        video_paths: dict[str, Path] = {}

        for i, (group_name, files) in enumerate(self._groups.items()):
            if self._cancelled:
                return

            if self._skip_tracking:
                csv_files[group_name] = files
                for path in files:
                    handle, _group, _path = next(handle_iter)
                    manifest.append((handle, group_name, path))
                continue

            self.log.emit(f"Group {i + 1}/{n_groups}: {group_name}")

            if not files:
                self._warn(f"{group_name}: no video files added")
                continue

            tracking_dir.mkdir(parents=True, exist_ok=True)

            tracked_files: list[Path] = []
            n_videos = len(files)
            for j, video in enumerate(files):
                if self._cancelled:
                    return

                handle, _group, _path = next(handle_iter)
                output_csv = tracking_dir / f"{handle}.csv"

                self.log.emit(f"  Tracking {video.name} ({j + 1}/{n_videos})...")
                try:
                    proc = arena.TRACKER.track(video, output_csv, **arena.TRACKER_ARGS)
                    self._current_proc = proc
                    self._drain_proc(proc)
                    self._current_proc = None
                    if proc.returncode != 0:
                        raise RuntimeError(f"exit code {proc.returncode}")
                    tracked_files.append(output_csv)
                    manifest.append((handle, group_name, output_csv))
                    video_paths[handle] = video
                except Exception as exc:
                    self._warn(f"{group_name} / {video.name}: tracking failed — {exc}")

            if tracked_files:
                csv_files[group_name] = tracked_files
            else:
                self._warn(f"{group_name}: no videos tracked successfully, skipping pipeline")

        if not manifest:
            raise RuntimeError("No groups were tracked successfully — cannot run pipeline.")

        if self._cancelled:
            return

        self.log.emit("Running analysis pipeline...")
        try:
            self._run_pipeline(manifest, video_paths)
        except Exception as exc:
            self._warn(f"Pipeline error: {exc}")

        if self._warnings:
            self._write_warning_file()

        self.log.emit("Done.")

    def _run_pipeline(
        self, manifest: list[tuple[str, str, Path]], video_paths: dict[str, Path]
    ) -> None:
        arena = self._arena

        available = {
            "manifest": manifest,
            "video_paths": video_paths,
            "output_dir": self._output_dir,
            "comparisons": self._comparisons,
        }

        pipeline_inputs = getattr(arena, "PIPELINE_INPUTS", {})
        kwargs = {
            fn_arg: available[gui_concept]
            for gui_concept, fn_arg in pipeline_inputs.items()
            if gui_concept in available
        }

        for opt in getattr(arena, "OPTIONS", []):
            kwargs[opt["name"]] = self._options.get(opt["name"], opt["default"])

        # Run the pipeline in its own subprocess: a long-running in-process call
        # can only be stopped via QThread.terminate(), which is unsafe (it can
        # leave native locks held and hang the GUI). A subprocess can be killed
        # outright via the same kill_tree() used for the tracker.
        payload = {"arena_module": arena.__name__, "kwargs": kwargs}
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            pickle.dump(payload, f)
            payload_path = Path(f.name)

        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--pipeline-worker", str(payload_path)]
            else:
                cmd = [sys.executable, "-m", "app.main", "--pipeline-worker", str(payload_path)]

            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            proc = popen_grouped(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
            self._current_proc = proc
            self._drain_pipeline_output(proc)
            self._current_proc = None

            if self._cancelled:
                return
            if proc.returncode != 0:
                raise RuntimeError("Pipeline subprocess failed — see log above for details.")
        finally:
            payload_path.unlink(missing_ok=True)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _drain_proc(self, proc: subprocess.Popen) -> None:
        self._stream(proc, self.subprocess_output.emit)

    def _drain_pipeline_output(self, proc: subprocess.Popen) -> None:
        buf = ""

        def on_text(text: str) -> None:
            nonlocal buf
            buf += text
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                self.log.emit(line)

        self._stream(proc, on_text)
        if buf:
            self.log.emit(buf)

    def _stream(self, proc: subprocess.Popen, on_text) -> None:
        """Read *proc*'s stdout on a background thread, emitting a "." heartbeat
        to the log if nothing arrives for `_HEARTBEAT_INTERVAL` seconds — long
        gaps in subprocess output (e.g. tracking a single video) would otherwise
        look like the app has frozen."""
        q: queue.Queue[bytes | None] = queue.Queue()

        def reader() -> None:
            while True:
                # read1(), not read(): BufferedReader.read(n) loops accumulating
                # raw reads until n bytes or EOF, even if data is already
                # available — that delays delivery until 256 bytes have built
                # up. read1() returns whatever's available from one raw read.
                chunk = proc.stdout.read1(256)
                if not chunk:
                    break
                q.put(chunk)
            q.put(None)

        threading.Thread(target=reader, daemon=True).start()

        while True:
            try:
                chunk = q.get(timeout=_HEARTBEAT_INTERVAL)
            except queue.Empty:
                self.heartbeat.emit()
                continue
            if chunk is None:
                break
            on_text(chunk.decode("utf-8", errors="replace"))

        proc.wait()

    def _warn(self, msg: str) -> None:
        self._warnings.append(msg)
        self.warning.emit(msg)

    def _write_report(self, error: str | None) -> None:
        try:
            app_version = _pkg_version("py3r-analysis-gui")
        except Exception:
            app_version = "unknown"

        csv_files = self._csv_files
        arena = self._arena
        lines: list[str] = []
        lines.append("py3r Analysis — run report")
        lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
        lines.append(f"App version: {app_version}")
        lines.append(f"Pipeline: {arena.NAME}")
        lines.append("")

        if error:
            lines.append("Status: FAILED")
            lines.append("")
            lines.append("Error:")
            lines.extend(f"  {line}" for line in error.splitlines())
        elif self._warnings:
            lines.append("Status: completed with warnings")
        else:
            lines.append("Status: completed successfully")
        lines.append("")

        if self._warnings:
            lines.append("Warnings:")
            for w in self._warnings:
                lines.append(f"  • {w}")
            lines.append("")

        lines.append("Options:")
        if self._options:
            for name, value in self._options.items():
                lines.append(f"  {name}: {value}")
        else:
            lines.append("  (none)")
        lines.append("")

        lines.append("Comparisons:")
        if self._comparisons:
            for a, b in self._comparisons:
                lines.append(f"  {a} vs {b}")
        else:
            lines.append("  (none)")
        lines.append("")

        lines.append("Groups (input files):")
        for group_name, files in self._groups.items():
            lines.append(f"  {group_name}:")
            for f in files:
                lines.append(f"    {f}")
        lines.append("")

        if self._skip_tracking:
            lines.append("Tracking: skipped — input files used directly as tracking CSVs")
        else:
            lines.append("Tracking: yes")
            lines.append("")
            lines.append("Tracking output CSVs:")
            for group_name, files in csv_files.items():
                lines.append(f"  {group_name}:")
                for f in files:
                    lines.append(f"    {f}")

        (self._output_dir / "py3r_analysis_report.txt").write_text("\n".join(lines) + "\n")

    def _write_warning_file(self) -> None:
        path = self._output_dir / "py3r_analysis_ERRORS.txt"
        with path.open("w") as f:
            f.write(
                f"{len(self._warnings)} issue(s) occurred during processing.\n"
                "Affected videos were skipped — please review them manually.\n\n"
            )
            for w in self._warnings:
                f.write(f"  • {w}\n")
