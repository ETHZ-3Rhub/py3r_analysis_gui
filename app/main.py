"""Entry point for py3r Analysis GUI."""

from __future__ import annotations

import sys

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
