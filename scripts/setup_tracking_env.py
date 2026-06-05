"""Set up a local tracking environment for development and testing.

Creates <repo_root>/tracking_env/ using uv, installs PyTorch (CUDA build matched
to the installed driver, or CPU fallback), then installs ultralytics.

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

# PyTorch index URLs — cu124 requires driver ≥ 525, cu118 requires driver ≥ 450.
# Add newer tiers here when ultralytics pins are bumped to require them.
TORCH_INDEX_CU124 = "https://download.pytorch.org/whl/cu124"
TORCH_INDEX_CU118 = "https://download.pytorch.org/whl/cu118"
TORCH_INDEX_CPU = "https://download.pytorch.org/whl/cpu"

# Pinned versions — bump these together when updating the tracking stack.
ULTRALYTICS_VERSION = "8.4.60"
LAP_VERSION = "0.5.13"


def _check_uv() -> None:
    if not shutil.which("uv"):
        sys.exit(
            "uv not found. Install it from https://docs.astral.sh/uv/getting-started/installation/"
        )


def _nvidia_driver_version() -> tuple[int, int] | None:
    """Return (major, minor) of the installed NVIDIA driver, or None if not found."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return None
        version_str = r.stdout.strip().splitlines()[0].strip()
        parts = version_str.split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        return None


def _pick_torch_index() -> tuple[str, str]:
    """Return (index_url, label) for the best torch build for this machine."""
    driver = _nvidia_driver_version()
    if driver is None:
        return TORCH_INDEX_CPU, "CPU (no NVIDIA GPU detected)"
    major, _ = driver
    if major >= 525:
        return TORCH_INDEX_CU124, f"CUDA 12.4 (driver {major} ≥ 525)"
    if major >= 450:
        return TORCH_INDEX_CU118, f"CUDA 11.8 (driver {major} ≥ 450)"
    return TORCH_INDEX_CPU, f"CPU (driver {major} too old for CUDA builds — upgrade to ≥ 450)"


def _python_exe() -> Path:
    if sys.platform == "win32":
        return TRACKING_ENV / "Scripts" / "python.exe"
    return TRACKING_ENV / "bin" / "python"


def _run(*cmd: str) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(list(cmd), check=True)


def main() -> None:
    _check_uv()

    torch_index, torch_label = _pick_torch_index()
    print(f"PyTorch build: {torch_label}")
    print(f"ultralytics:   {ULTRALYTICS_VERSION}")
    print(f"lap:           {LAP_VERSION}")

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

    # Pin ultralytics and lap exactly — these are part of the app's reproducibility
    # guarantee. See ULTRALYTICS_VERSION / LAP_VERSION constants above.
    _run(
        "uv",
        "pip",
        "install",
        "--python",
        python,
        f"ultralytics=={ULTRALYTICS_VERSION}",
        f"lap=={LAP_VERSION}",
    )

    print(f"\nDone. Tracking environment created at: {TRACKING_ENV}")
    print("Run the app normally — it will find tracking_env/ automatically.")


if __name__ == "__main__":
    main()
