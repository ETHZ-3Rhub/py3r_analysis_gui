"""Open Field Test (DLC format) — TESTING ONLY.

Variant of the OFT arena that accepts folders of DeepLabCut CSVs instead of
YOLO3R CSVs.  Used for pipeline development/testing when YOLO3R-tracked data
is not yet available.

TO REMOVE: delete this file and app/pipelines/oft_dlc_pipeline.py.
The arena registry auto-discovers modules, so no other changes are needed.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

NAME = "Open Field Test — DLC (testing)"
MODEL = "oft_dlc"


def run(
    groups: dict[str, Path],
    output_dir: Path,
    progress_cb: Callable[[str, float | None], None],
    comparisons: list[tuple[str, str]],
    *,
    skip_tracking: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # DLC data is always pre-tracked — the skip_tracking flag is implicit.
    if not skip_tracking:
        progress_cb(
            "[DLC arena] 'Groups contain pre-tracked CSV files' not ticked, "
            "but DLC arena always skips tracking — proceeding.",
            None,
        )

    from app.pipelines import oft_dlc_pipeline  # noqa: PLC0415

    progress_cb("Running DLC analysis pipeline…", 40)
    oft_dlc_pipeline.run(
        group_csv_dirs=groups,
        output_dir=output_dir,
        progress_cb=progress_cb,
        comparisons=comparisons,
    )
    progress_cb("Done.", 100)
