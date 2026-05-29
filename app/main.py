"""Entry point for py3r Analysis GUI."""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    # Python 3.8+ no longer searches PATH for DLLs; PyQt6 normally registers
    # its own Qt6/bin directory but this can fail on some Windows setups.
    _qt_bin = Path(sys.prefix) / "Lib" / "site-packages" / "PyQt6" / "Qt6" / "bin"
    if _qt_bin.exists():
        os.add_dll_directory(str(_qt_bin))

from PyQt6.QtWidgets import QApplication

from app.window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("py3r Analysis")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
