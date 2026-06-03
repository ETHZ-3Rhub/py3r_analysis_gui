"""Set up a local tracking environment for development and testing.

Creates <repo_root>/tracking_env/ using uv, installs PyTorch (CUDA if an
NVIDIA GPU is detected, CPU otherwise), then installs ultralytics.

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

TORCH_INDEX_CUDA = "https://download.pytorch.org/whl/cu124"
TORCH_INDEX_CPU = "https://download.pytorch.org/whl/cpu"


def _check_uv() -> None:
    if not shutil.which("uv"):
        sys.exit(
            "uv not found. Install it from https://docs.astral.sh/uv/getting-started/installation/"
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

    cuda = _has_nvidia_gpu()
    torch_index = TORCH_INDEX_CUDA if cuda else TORCH_INDEX_CPU
    print(f"GPU detected: {'yes (CUDA)' if cuda else 'no (CPU-only)'}")
    print(f"PyTorch index: {torch_index}")

    if TRACKING_ENV.exists():
        print(f"\nRemoving existing {TRACKING_ENV.name}/")
        shutil.rmtree(TRACKING_ENV)
    _run("uv", "venv", str(TRACKING_ENV), "--python", "3.12")

    python = str(_python_exe())

    # Install PyTorch first from the machine-specific index so we get the right
    # CUDA build. ultralytics will see torch already satisfied and won't replace it.
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

    # ultralytics pulls in all remaining deps (opencv, numpy, etc.) from PyPI.
    _run("uv", "pip", "install", "--python", python, "ultralytics")

    print(f"\nDone. Tracking environment created at: {TRACKING_ENV}")
    print("Run the app normally — it will find tracking_env/ automatically.")


if __name__ == "__main__":
    main()
