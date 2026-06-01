"""Tracking subprocess helpers.

Locates the py3r_pose executable and model weights, then runs tracking
on a folder of video files, one video at a time so the caller can report
per-video progress.

Resolution order for the executable:
  1. PY3R_POSE_EXE env var (dev override / CI)
  2. <exe_dir>/tracking_env/Scripts/py3r_pose.exe  (packaged app, Windows)
  3. <exe_dir>/tracking_env/bin/py3r_pose           (packaged app, non-Windows)
  4. py3r_pose on PATH (development, active environment)

Resolution order for model weights:
  1. PY3R_POSE_MODELS env var — must point to the directory that contains
     environment/environment_main/ and mouse/mouse_top_main/
  2. <exe_dir>/models/                              (packaged app)
  3. ../BohacekLabPoseModels/pose_estimation/       (development, sibling repo)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv"}

_ENV_MODEL_REL = Path("environment") / "environment_main"
_MOUSE_MODEL_REL = Path("mouse") / "mouse_top_main"


def _find_py3r_pose_exe() -> Path:
    if override := os.environ.get("PY3R_POSE_EXE"):
        return Path(override)

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        script = "py3r_pose.exe" if platform.system() == "Windows" else "py3r_pose"
        subdir = "Scripts" if platform.system() == "Windows" else "bin"
        candidate = exe_dir / "tracking_env" / subdir / script
        if candidate.exists():
            return candidate
        raise RuntimeError(
            f"py3r_pose executable not found at expected location: {candidate}\n"
            "The tracking environment may not have been installed correctly."
        )

    # Development: local tracking_env/ created by dev/setup_tracking_env.py
    repo_root = Path(__file__).parent.parent
    subdir = "Scripts" if platform.system() == "Windows" else "bin"
    script = "py3r_pose.exe" if platform.system() == "Windows" else "py3r_pose"
    local_candidate = repo_root / "tracking_env" / subdir / script
    if local_candidate.exists():
        return local_candidate

    found = shutil.which("py3r_pose")
    if found:
        return Path(found)

    raise RuntimeError(
        "py3r_pose executable not found.\n"
        "Run dev/setup_tracking_env.py to create a local tracking environment, "
        "or activate an environment that has py3r_pose installed."
    )


def _find_models_dir() -> Path:
    if override := os.environ.get("PY3R_POSE_MODELS"):
        return Path(override)

    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).parent / "models"
        if candidate.is_dir():
            return candidate
        raise RuntimeError(
            f"Bundled model weights not found at: {candidate}\n" "The installer may be incomplete."
        )

    # Development: sibling BohacekLabPoseModels repo (standard layout)
    repo_root = Path(__file__).parent.parent
    sibling_candidate = repo_root.parent / "BohacekLabPoseModels" / "pose_estimation"
    if sibling_candidate.is_dir():
        return sibling_candidate

    # Development: BohacekLabPoseModels cloned inside the repo root (Windows student machine)
    nested_candidate = repo_root / "BohacekLabPoseModels" / "pose_estimation"
    if nested_candidate.is_dir():
        return nested_candidate

    raise RuntimeError(
        "Model weights directory not found.\n"
        "Expected BohacekLabPoseModels/pose_estimation as a sibling repo or inside the repo root, "
        "or set PY3R_POSE_MODELS to the directory containing the model folders."
    )


def track_group(
    video_dir: Path,
    csv_out_dir: Path,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    """Track all videos in *video_dir*, writing pose CSVs to *csv_out_dir*."""
    py3r_pose = _find_py3r_pose_exe()
    models_dir = _find_models_dir()

    print(f"DEBUG models_dir: {models_dir}", flush=True)
    print(f"DEBUG env_model: {models_dir / _ENV_MODEL_REL}", flush=True)
    print(f"DEBUG mouse_model: {models_dir / _MOUSE_MODEL_REL}", flush=True)

    env_model = models_dir / _ENV_MODEL_REL
    mouse_model = models_dir / _MOUSE_MODEL_REL
    for model in (env_model, mouse_model):
        if not model.is_dir():
            raise RuntimeError(f"Model weights not found: {model}")

    video_files = sorted(
        f for f in video_dir.iterdir() if f.is_file() and f.suffix.lower() in _VIDEO_EXTS
    )
    if not video_files:
        raise RuntimeError(f"No video files found in {video_dir}")

    csv_out_dir.mkdir(parents=True, exist_ok=True)
    n = len(video_files)

    for i, video in enumerate(video_files):
        progress_cb(f"Tracking {video.name} ({i + 1}/{n})…", None)
        cmd = [
            str(py3r_pose),
            "track",
            str(video),
            "--model",
            str(env_model),
            str(mouse_model),
            "--tracker",
            "fixed-instances",
            "--instances",
            "oft",
            "mouse_top",
            "--output-folder",
            str(csv_out_dir),
            "--no-vis",
        ]
        result = subprocess.run(cmd, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"Tracking failed for {video.name} (exit {result.returncode})"
                + (f":\n{detail}" if detail else "")
            )
