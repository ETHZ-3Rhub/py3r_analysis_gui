"""Pre-build wheels for git-based dependencies.

Run this once after updating versions.yaml, then commit the updated wheels/
directory. setup_tracking_env.py installs from these wheels at install time,
so no source compilation ever happens on the user's machine.

Only py3r_pose and py3r_media are built here — everything else (torch,
ultralytics, numpy, etc.) comes from PyPI/the PyTorch index and is already
distributed as pre-built wheels by their maintainers.

Usage:
    python scripts/build_wheels.py

Requires: pip (standard)
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


def _load_versions() -> dict:
    with open(VERSIONS_FILE) as f:
        return yaml.safe_load(f)


def _run(*cmd: str) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(list(cmd), check=True)


def _build_wheel(package_url: str) -> None:
    _run(
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        package_url,
        "--wheel-dir",
        str(WHEELS_DIR),
    )


def main() -> None:
    versions = _load_versions()
    pose = versions["dependencies"]["py3r_pose"]
    media = versions["dependencies"]["py3r_media"]

    pose_ref = f"{pose['repo']}@{pose['commit']}"
    media_ref = f"{media['repo']}@{media['commit']}"

    if WHEELS_DIR.exists():
        shutil.rmtree(WHEELS_DIR)
    WHEELS_DIR.mkdir()

    print(f"Building wheel: py3r_media @ {media['commit'][:8]}")
    _build_wheel(f"py3r_media @ git+{media_ref}")

    print(f"Building wheel: py3r_pose @ {pose['commit'][:8]}")
    _build_wheel(f"py3r_pose @ git+{pose_ref}")

    wheels = list(WHEELS_DIR.glob("*.whl"))
    print(f"\nBuilt {len(wheels)} wheel(s) in {WHEELS_DIR}/:")
    for w in wheels:
        print(f"  {w.name}")
    print("\nCommit the wheels/ directory, then setup_tracking_env.py will use them automatically.")


if __name__ == "__main__":
    main()
