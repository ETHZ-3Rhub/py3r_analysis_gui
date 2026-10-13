"""Entry point for Analys3R."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _icon_path() -> Path:
    """Locate the app icon, in source tree or PyInstaller bundle."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    name = "icon.ico" if sys.platform == "win32" else "icon.png"
    return base / "assets" / name


def main() -> None:
    # In a packaged app sys.executable is the app itself, so when a library in
    # the pipeline starts a multiprocessing helper (on macOS e.g. the resource
    # tracker, created by any lock or semaphore) it relaunches the app. Without
    # this, that relaunch opens a second GUI window and the pipeline waits for
    # it to close. freeze_support() spots those relaunches, runs the helper and
    # exits; it does nothing for a normal launch.
    import multiprocessing

    multiprocessing.freeze_support()

    # Checked first and before any PyQt imports — the pipeline subprocess
    # re-invokes this entry point and shouldn't pay PyQt's startup cost.
    if len(sys.argv) > 2 and sys.argv[1] == "--pipeline-worker":
        from app.pipeline_worker import run_worker

        sys.exit(run_worker(Path(sys.argv[2])))

    if len(sys.argv) > 2 and sys.argv[1] == "--setup-tracking-env":
        from app.tracking_env_setup import setup

        sys.exit(setup(Path(sys.argv[2])))

    if sys.platform == "win32":
        # Python 3.8+ no longer searches PATH for DLLs; PySide6 normally registers
        # its own Qt6/bin directory but this can fail on some Windows setups.
        _qt_bin = Path(sys.prefix) / "Lib" / "site-packages" / "PySide6" / "Qt6" / "bin"
        if _qt_bin.exists():
            os.add_dll_directory(str(_qt_bin))

    from app.paths import migrate_legacy_user_dir

    try:
        migrate_legacy_user_dir()
    except OSError:
        pass  # non-fatal: the user just starts with an empty /user folder

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from app.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Analys3R")
    app.setWindowIcon(QIcon(str(_icon_path())))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
