"""Set up a local tracking environment for development and testing.

Creates <repo_root>/tracking_env/ using uv, installs PyTorch (CUDA if an
NVIDIA GPU is detected, CPU otherwise), then installs py3r_pose and its
dependencies from pre-built wheels in wheels/.

Run scripts/build_wheels.py first if wheels/ is missing or out of date.

Usage:
    python scripts/setup_tracking_env.py

The app will automatically find tracking_env/ when run from the repo root —
no environment variables needed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TRACKING_ENV = REPO_ROOT / "tracking_env"
WHEELS_DIR = REPO_ROOT / "wheels"
VERSIONS_FILE = REPO_ROOT / "versions.yaml"

TORCH_INDEX_CUDA = "https://download.pytorch.org/whl/cu124"
TORCH_INDEX_CPU = "https://download.pytorch.org/whl/cpu"


def _check_uv() -> None:
    if not shutil.which("uv"):
        sys.exit(
            "uv not found. Install it from https://docs.astral.sh/uv/getting-started/installation/"
        )


def _check_wheels() -> None:
    if not WHEELS_DIR.exists() or not list(WHEELS_DIR.glob("*.whl")):
        sys.exit(
            "wheels/ directory is missing or empty.\n"
            "Run scripts/build_wheels.py first, then commit the result."
        )


def _has_nvidia_gpu() -> bool:
    return (
        shutil.which("nvidia-smi") is not None
        and subprocess.run(["nvidia-smi"], capture_output=True).returncode == 0
    )


def _python_exe() -> Path:
    if sys.platform == "win32":
        return TRACKING_ENV / "Scripts" / "python.exe"
    return TRACKING_ENV / "bin" / "python"


def _run(*cmd: str) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(list(cmd), check=True)


def main() -> None:
    _check_uv()
    _check_wheels()

    cuda = _has_nvidia_gpu()
    torch_index = TORCH_INDEX_CUDA if cuda else TORCH_INDEX_CPU
    print(f"GPU detected: {'yes (CUDA)' if cuda else 'no (CPU-only)'}")
    print(f"PyTorch index: {torch_index}")

    # Create venv
    if TRACKING_ENV.exists():
        print(f"\nRemoving existing {TRACKING_ENV.name}/")
        shutil.rmtree(TRACKING_ENV)
    _run("uv", "venv", str(TRACKING_ENV), "--python", "3.12")

    python = str(_python_exe())

    # Install PyTorch first — machine-specific CUDA version, must come from the
    # official PyTorch index rather than being bundled
    _run(
        "uv",
        "pip",
        "install",
        "--python",
        python,
        "torch",
        "torchvision",
        "--index-url",
        torch_index,
    )

    # Install py3r_pose and py3r_media from pre-built local wheels.
    # --find-links tells uv to prefer local wheels; --no-build-isolation
    # is not needed since we're installing pre-built wheels, not source.
    # Remaining deps (ultralytics, numpy, opencv, etc.) come from PyPI.
    _run(
        "uv",
        "pip",
        "install",
        "--python",
        python,
        "--find-links",
        str(WHEELS_DIR),
        "py3r_pose[yolo]",
        "--extra-index-url",
        torch_index,
    )

    print(f"\nDone. Tracking environment created at: {TRACKING_ENV}")
    print("Run the app normally — it will find tracking_env/ automatically.")


if __name__ == "__main__":
    main()
