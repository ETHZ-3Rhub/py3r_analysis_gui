"""Subprocess wrapper for app/trackers/track.py.

Launches track.py inside the tracking_env Python interpreter and returns
the Popen handle for the runner to manage (watchdog, cancel, output).

Resolution order for the tracking Python:
  1. PY3R_TRACKER_PYTHON env var
  2. <exe_dir>/tracking_env/Scripts/python.exe  (packaged app, Windows)
  3. <exe_dir>/tracking_env/bin/python          (packaged app, non-Windows)
  4. <repo_root>/tracking_env/...               (development)

Resolution order for model weights:
  1. PY3R_POSE_MODELS env var
  2. <exe_dir>/models/                          (packaged app)
  3. ../BohacekLabPoseModels/pose_estimation/   (sibling repo, standard layout)
  4. <repo_root>/BohacekLabPoseModels/pose_estimation/
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from app.proc_utils import popen_grouped

_TRACK_SCRIPT = Path(__file__).parent / "track.py"


def tracking_env_dir() -> Path:
    """Where tracking_env/ lives (or should be created): <exe_dir>/tracking_env
    in a packaged app, <repo_root>/tracking_env in development."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "tracking_env"
    return Path(__file__).parent.parent.parent / "tracking_env"


def _find_python() -> Path:
    if override := os.environ.get("PY3R_TRACKER_PYTHON"):
        return Path(override)

    subdir = "Scripts" if platform.system() == "Windows" else "bin"
    exe = "python.exe" if platform.system() == "Windows" else "python"

    candidate = tracking_env_dir() / subdir / exe
    if candidate.exists():
        return candidate

    if getattr(sys, "frozen", False):
        raise RuntimeError(
            f"Tracking Python not found at {candidate}\n"
            "The tracking environment may not have been installed correctly."
        )
    raise RuntimeError(
        "tracking_env not found.\n"
        "Open Settings and click (Re)install tracking environment to create it."
    )


def _find_models_dir() -> Path:
    if override := os.environ.get("PY3R_POSE_MODELS"):
        return Path(override)

    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).parent / "models"
        if candidate.is_dir():
            return candidate
        raise RuntimeError(f"Bundled model weights not found at {candidate}")

    repo_root = Path(__file__).parent.parent.parent
    sibling = repo_root.parent / "BohacekLabPoseModels" / "pose_estimation"
    if sibling.is_dir():
        return sibling

    nested = repo_root / "BohacekLabPoseModels" / "pose_estimation"
    if nested.is_dir():
        return nested

    raise RuntimeError(
        "Model weights not found.\n"
        "Expected BohacekLabPoseModels/pose_estimation as a sibling repo, "
        "or set PY3R_POSE_MODELS."
    )


def track(
    video: Path, output_csv: Path, *, models: list[dict], device: str = "auto"
) -> subprocess.Popen:
    """Launch track.py for a single video, writing to *output_csv*. Returns the
    Popen handle. The caller owns the output filename (the GUI assigns a
    globally-unique handle per recording — see app/naming.py — so two videos
    that share a stem can't overwrite each other here).

    models:  list of model config dicts (resolved by pipeline_config), each with:
               model      — absolute path to the model folder
               instances  — list of {"type": str, "max": int}
               stride     — optional [interval, fill_mode]
               batch      — optional int
    device:  "auto", "cpu", "cuda", "cuda:0", ... (default: "auto")
    """
    python = _find_python()

    cmd = [
        str(python),
        "-u",
        str(_TRACK_SCRIPT),
        str(video),
        str(output_csv),
        "--device",
        device,
    ]

    for mc in models:
        folder = Path(mc["model"])
        if not folder.is_dir():
            raise RuntimeError(f"Model folder not found: {folder}")
        cmd += ["--model", json.dumps(mc)]

    return popen_grouped(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
