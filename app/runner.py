"""Background pipeline runner."""

from __future__ import annotations

import traceback
from pathlib import Path
from types import ModuleType

from PyQt6.QtCore import QThread, pyqtSignal


class PipelineRunner(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        arena: ModuleType,
        groups: dict[str, Path],
        output_dir: Path,
        comparisons: list[tuple[str, str]],
        *,
        skip_tracking: bool = False,
    ) -> None:
        super().__init__()
        self._arena = arena
        self._groups = groups
        self._output_dir = output_dir
        self._comparisons = comparisons
        self._skip_tracking = skip_tracking

    def run(self) -> None:
        try:
            self._arena.run(
                groups=self._groups,
                output_dir=self._output_dir,
                progress_cb=self._progress_cb,
                comparisons=self._comparisons,
                skip_tracking=self._skip_tracking,
            )
            self.finished.emit(str(self._output_dir))
        except Exception:
            self.error.emit(traceback.format_exc())

    def _progress_cb(self, message: str, pct: float | None) -> None:
        self.log.emit(message)
        self.progress.emit(int(pct) if pct is not None else -1)
