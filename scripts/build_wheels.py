"""Pre-build wheels for git-based dependencies.

Run this once after updating versions.yaml, then commit the updated wheels/
directory. setup_tracking_env.py installs from these wheels at install time,
so no source compilation ever happens on the user's machine.

Only py3r_pose and py3r_media are built here — everything else (torch,
ultralytics, numpy, etc.) comes from PyPI/the PyTorch index and is already
distributed as pre-built wheels by their maintainers.

Usage:
    python scripts/build_wheels.py

Requires: uv
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
WHEELS_DIR = REPO_ROOT / "wheels"
VERSIONS_FILE = REPO_ROOT / "versions.yaml"


def _check_uv() -> None:
    if not shutil.which("uv"):
        sys.exit(
            "uv not found. Install it from https://docs.astral.sh/uv/getting-started/installation/"
        )


def _load_versions() -> dict:
    with open(VERSIONS_FILE) as f:
        return yaml.safe_load(f)


def _run(*cmd: str) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(list(cmd), check=True)


def main() -> None:
    _check_uv()

    versions = _load_versions()
    pose = versions["dependencies"]["py3r_pose"]
    media = versions["dependencies"]["py3r_media"]

    pose_ref = f"{pose['repo']}@{pose['commit']}"
    media_ref = f"{media['repo']}@{media['commit']}"

    if WHEELS_DIR.exists():
        shutil.rmtree(WHEELS_DIR)
    WHEELS_DIR.mkdir()

    print(f"Building wheel: py3r_media @ {media['commit'][:8]}")
    _run(
        "uv",
        "pip",
        "wheel",
        "--no-deps",
        f"py3r_media @ git+{media_ref}",
        "--wheel-dir",
        str(WHEELS_DIR),
    )

    print(f"Building wheel: py3r_pose @ {pose['commit'][:8]}")
    _run(
        "uv",
        "pip",
        "wheel",
        "--no-deps",
        f"py3r_pose @ git+{pose_ref}",
        "--wheel-dir",
        str(WHEELS_DIR),
    )

    wheels = list(WHEELS_DIR.glob("*.whl"))
    print(f"\nBuilt {len(wheels)} wheel(s) in {WHEELS_DIR}/:")
    for w in wheels:
        print(f"  {w.name}")
    print("\nCommit the wheels/ directory, then setup_tracking_env.py will use them automatically.")


if __name__ == "__main__":
    main()
