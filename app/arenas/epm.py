"""Elevated Plus Maze arena — stub."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

NAME  = "Elevated Plus Maze"
MODEL = "epm"


def run(
    groups: dict[str, Path],
    output_dir: Path,
    progress_cb: Callable[[str, float | None], None],
    comparisons: list[tuple[str, str]],
    *,
    skip_tracking: bool = False,
) -> None:
    raise NotImplementedError(
        "EPM pipeline not yet implemented. "
        "Add tracking + pipeline calls in app/arenas/epm.py."
    )
