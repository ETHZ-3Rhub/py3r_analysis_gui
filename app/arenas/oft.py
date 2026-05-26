"""Open Field Test arena.

This module is the glue between the GUI and the OFT pipeline.
It owns two responsibilities:
  1. Invoke the YOLO3R tracker on each group's video folder   (TODO: stub)
  2. Hand the resulting CSV folders to the OFT pipeline

To add a new arena, copy this file, change NAME / MODEL, and point to a
different pipeline module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.pipelines import oft_pipeline

# ── Arena identity ────────────────────────────────────────────────────────────
NAME = "Open Field Test"
MODEL = "oft"  # YOLO3R model key — passed to the tracker subprocess


# ── Public entry point (called by runner.py) ──────────────────────────────────
def run(
    groups: dict[str, Path],
    output_dir: Path,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    """Orchestrate tracking then analysis for all groups.

    Parameters
    ----------
    groups:
        Mapping of group name → folder of raw video files.
    output_dir:
        Root folder where all outputs will be written.
    progress_cb:
        ``progress_cb(message, pct_or_None)`` — called throughout to update
        the GUI log and progress bar.  pct is 0–100 or None if unknown.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: tracking ─────────────────────────────────────────────────────
    # csv_dirs maps each group name to the folder of tracking CSVs produced
    # by YOLO3R.  After tracking, this is what the pipeline consumes.
    csv_dirs: dict[str, Path] = {}

    n_groups = len(groups)
    for i, (group_name, video_dir) in enumerate(groups.items()):
        progress_cb(f"Tracking: {group_name} ({i + 1}/{n_groups})", 10 + (i / n_groups) * 30)
        csv_out = output_dir / "tracking" / group_name
        csv_out.mkdir(parents=True, exist_ok=True)
        _track(video_dir, csv_out, progress_cb)
        csv_dirs[group_name] = csv_out

    # ── Stage 2: analysis pipeline ────────────────────────────────────────────
    progress_cb("Running analysis pipeline…", 40)
    oft_pipeline.run(
        group_csv_dirs=csv_dirs,
        output_dir=output_dir,
        progress_cb=progress_cb,
    )

    progress_cb("Done.", 100)


# ── Tracking stub ─────────────────────────────────────────────────────────────
def _track(
    video_dir: Path,
    csv_out_dir: Path,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    """Invoke YOLO3R on *video_dir*, writing CSVs to *csv_out_dir*.

    TODO: implement once the YOLO3R CLI call signature is confirmed.
          Expected shape:
              yolo3r track --model <MODEL> --input <video_dir> --output <csv_out_dir>
          Each video should produce one CSV named <video_stem>.csv in csv_out_dir.
    """
    raise NotImplementedError(
        "YOLO3R tracking not yet wired up.  "
        "Implement _track() in app/arenas/oft.py once the CLI interface is confirmed."
    )
