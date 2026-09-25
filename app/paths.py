"""Where the app keeps its writable data (user pipelines, tracking env).

A packaged app must not write next to its own executable: a macOS ``.app``
bundle is signed and effectively read-only, and unzipping a new Windows release
over the old folder would wipe the user's files. So packaged builds use the
per-user data folder of each OS; a development run keeps everything in the repo
root. ``PY3R_DATA_DIR`` overrides both (handy for testing).

No imports from the rest of the app, so anything can depend on this.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DIRNAME = "Analys3R"


def data_dir() -> Path:
    if override := os.environ.get("PY3R_DATA_DIR"):
        return Path(override)
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / APP_DIRNAME


def user_dir() -> Path:
    """The ``/user`` folder: pipeline configs, scripts, models, git sources."""
    return data_dir() / "user"


def tracking_env_dir() -> Path:
    return data_dir() / "tracking_env"


def bundled_models_dir() -> Path:
    """Where the build script puts the shipped model weights (read-only)."""
    exe_dir = Path(sys.executable).parent
    if sys.platform == "darwin":
        return exe_dir.parent / "Resources" / "models"  # inside Analys3R.app
    return exe_dir / "models"


def migrate_legacy_user_dir() -> None:
    """Copy a pre-existing ``user/`` folder from next to the executable (the old
    layout) into the data folder, once. The old copy is left in place. The
    tracking env is not migrated: venvs hold absolute paths, so it is reinstalled.
    """
    if not getattr(sys, "frozen", False):
        return
    old = Path(sys.executable).parent / "user"
    new = user_dir()
    if old.is_dir() and not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(old, new)
