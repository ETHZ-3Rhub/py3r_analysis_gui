"""py3r_pose tracking backend.

Knows how to locate the py3r_pose executable and model weights, and how to
build the correct command line for this tool. Process management (watchdog,
stall handling, error collection) is the runner's responsibility.

Resolution order for the executable:
  1. PY3R_POSE_EXE env var
  2. <exe_dir>/tracking_env/Scripts/py3r_pose.exe  (packaged app, Windows)
  3. <exe_dir>/tracking_env/bin/py3r_pose           (packaged app, non-Windows)
  4. <repo_root>/tracking_env/...                   (development)
  5. py3r_pose on PATH

Resolution order for model weights:
  1. PY3R_POSE_MODELS env var
  2. <exe_dir>/models/                              (packaged app)
  3. ../BohacekLabPoseModels/pose_estimation/       (sibling repo, standard layout)
  4. <repo_root>/BohacekLabPoseModels/pose_estimation/ (nested fallback)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

_ENV_MODEL_REL = Path("environment") / "environment_main"
_MOUSE_MODEL_REL = Path("mouse") / "mouse_top_main"


def _find_exe() -> Path:
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
            f"py3r_pose not found at {candidate}\n"
            "The tracking environment may not have been installed correctly."
        )

    repo_root = Path(__file__).parent.parent.parent
    subdir = "Scripts" if platform.system() == "Windows" else "bin"
    script = "py3r_pose.exe" if platform.system() == "Windows" else "py3r_pose"
    local = repo_root / "tracking_env" / subdir / script
    if local.exists():
        return local

    found = shutil.which("py3r_pose")
    if found:
        return Path(found)

    raise RuntimeError(
        "py3r_pose not found.\n"
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


def track(video: Path, output_dir: Path, **kwargs) -> subprocess.Popen:
    """Launch py3r_pose for a single video. Returns the Popen handle.

    kwargs (from arena TRACKER_ARGS):
        instances: list[str]   — tracker instance names (e.g. ["oft", "mouse_top"])
        tracker_type: str      — tracker algorithm (default: "fixed-instances")
    """
    exe = _find_exe()
    models_dir = _find_models_dir()

    instances: list[str] = kwargs["instances"]
    tracker_type: str = kwargs.get("tracker_type", "fixed-instances")

    env_model = models_dir / _ENV_MODEL_REL
    mouse_model = models_dir / _MOUSE_MODEL_REL
    for model in (env_model, mouse_model):
        if not model.is_dir():
            raise RuntimeError(f"Model weights not found: {model}")

    cmd = (
        f'"{exe}" track "{video}"'
        f' --model "{env_model}" "{mouse_model}"'
        f" --tracker {tracker_type}"
        f' --instances {" ".join(instances)}'
        f' --output-folder "{output_dir}"'
    )
    return subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
