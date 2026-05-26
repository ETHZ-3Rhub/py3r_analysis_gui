"""Background pipeline runner.

Wraps an arena's run() call in a QThread so the GUI stays responsive.
All communication back to the GUI is via Qt signals.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from types import ModuleType

from PyQt6.QtCore import QThread, pyqtSignal


class PipelineRunner(QThread):
    """Runs arena.run() in a background thread.

    Signals
    -------
    log(message)
        A plain-text log line for the GUI log panel.
    progress(pct)
        Integer 0–100 for the progress bar.  -1 means indeterminate.
    finished(output_dir)
        Emitted on successful completion.  Carries the output directory path.
    error(message)
        Emitted on unhandled exception.  The thread stops after this.
    """

    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)   # str(Path) — Path is not QMetaType-registered
    error = pyqtSignal(str)

    def __init__(
        self,
        arena: ModuleType,
        groups: dict[str, Path],
        output_dir: Path,
    ) -> None:
        super().__init__()
        self._arena = arena
        self._groups = groups
        self._output_dir = output_dir

    # ── QThread entry point ───────────────────────────────────────────────────
    def run(self) -> None:
        try:
            self._arena.run(
                groups=self._groups,
                output_dir=self._output_dir,
                progress_cb=self._progress_cb,
            )
            self.finished.emit(str(self._output_dir))
        except Exception:
            self.error.emit(traceback.format_exc())

    # ── Callback forwarded into the pipeline ─────────────────────────────────
    def _progress_cb(self, message: str, pct: float | None) -> None:
        self.log.emit(message)
        if pct is not None:
            self.progress.emit(int(pct))
        else:
            self.progress.emit(-1)
