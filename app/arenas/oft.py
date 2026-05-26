"""Open Field Test arena — orchestration layer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


NAME  = "Open Field Test"
MODEL = "oft"


def run(
    groups: dict[str, Path],
    output_dir: Path,
    progress_cb: Callable[[str, float | None], None],
    comparisons: list[tuple[str, str]],
    *,
    skip_tracking: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # UI checkbox OR dev env-var both skip the tracking step
    dev_skip = os.environ.get("DEV_SKIP_TRACKING", "").lower() in ("1", "true", "yes")
    skip = skip_tracking or dev_skip

    csv_dirs: dict[str, Path] = {}
    n_groups = len(groups)

    for i, (group_name, video_dir) in enumerate(groups.items()):
        if skip:
            progress_cb(
                f"[Skipping tracking] {group_name} — treating folder as CSV dir", None
            )
            csv_dirs[group_name] = video_dir
        else:
            progress_cb(
                f"Tracking: {group_name} ({i + 1}/{n_groups})", 10 + (i / n_groups) * 30
            )
            csv_out = output_dir / "tracking" / group_name
            csv_out.mkdir(parents=True, exist_ok=True)
            _track(video_dir, csv_out, progress_cb)
            csv_dirs[group_name] = csv_out

    # Import deferred — keeps GUI launch fast
    from app.pipelines import oft_pipeline  # noqa: PLC0415

    progress_cb("Running analysis pipeline…", 40)
    oft_pipeline.run(
        group_csv_dirs=csv_dirs,
        output_dir=output_dir,
        progress_cb=progress_cb,
        comparisons=comparisons,
    )
    progress_cb("Done.", 100)


def _track(
    video_dir: Path,
    csv_out_dir: Path,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    """Invoke YOLO3R on *video_dir*, writing CSVs to *csv_out_dir*.

    TODO: implement once the YOLO3R CLI call signature is confirmed.
    """
    raise NotImplementedError(
        "YOLO3R tracking not yet wired up. "
        "Implement _track() in app/arenas/oft.py once the CLI interface is confirmed."
    )
