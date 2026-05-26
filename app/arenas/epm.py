"""Elevated Plus Maze arena — stub.

Not yet implemented.  Exists so the arena dropdown has more than one entry
during UI development.  Replace this stub with real tracking + pipeline calls
when EPM support is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

NAME = "Elevated Plus Maze"
MODEL = "epm"  # YOLO3R model key (TBD)


def run(
    groups: dict[str, Path],
    output_dir: Path,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    raise NotImplementedError(
        "EPM pipeline not yet implemented.  "
        "Add tracking + pipeline calls in app/arenas/epm.py."
    )
