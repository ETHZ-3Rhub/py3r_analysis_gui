"""Open Field Test — py3r_behaviour analysis pipeline.

This module receives folders of YOLO3R tracking CSVs (one folder per group)
and drives the full py3r_behaviour pipeline:
    load → preprocess → features → summary → export (CSV + figures)

All OFT-specific constants live here.  Nothing in this file knows about the
GUI, the tracker, or any other arena.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import py3r.behaviour as p3b

# ── Hard-coded OFT / hardware constants ──────────────────────────────────────
# These reflect the fixed proprietary setup — do not expose to the GUI.
_FPS = 30
_ARENA_SIZE_M = 0.64          # distance between tl and br corners, in metres
_LIKELIHOOD_THRESHOLD = 0.9
_INTERPOLATION_LIMIT = 5      # max consecutive missing frames to interpolate
_SMOOTH_WINDOW = 3            # mean-smoothing window (frames)
_CENTRE_SCALE = 0.5           # centre zone: 50% of arena dims
_PERIPHERY_SCALE = 0.8        # periphery boundary: 80% of arena dims
_CORNER_SCALE = 0.2           # corner zones: 20% of arena dims

# Keypoint names as output by the OFT YOLO3R model
_CORNERS = ["tl", "tr", "br", "bl"]        # arena corner keypoints
_BODY_CENTRE = "bodycentre"
_TRACKING_POINTS = [                        # all animal keypoints
    "nose", "headcentre", "neck", "earl", "earr",
    "bodycentre", "bcl", "bcr",
    "hipl", "hipr", "tailbase",
]


# ── Public entry point ────────────────────────────────────────────────────────
def run(
    group_csv_dirs: dict[str, Path],
    output_dir: Path,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    """Run the full OFT analysis pipeline across all groups.

    Parameters
    ----------
    group_csv_dirs:
        ``{group_name: Path}`` — each Path is a folder of per-animal YOLO3R
        CSVs produced by the tracking stage.
    output_dir:
        Root output folder.  Sub-folders (figures/, summaries/) are created
        here automatically.
    progress_cb:
        ``progress_cb(message, pct_or_None)`` forwarded from the GUI runner.
    """
    figures_dir = output_dir / "figures"
    summaries_dir = output_dir / "summaries"
    figures_dir.mkdir(parents=True, exist_ok=True)
    summaries_dir.mkdir(parents=True, exist_ok=True)

    # ── Load & preprocess ─────────────────────────────────────────────────────
    progress_cb("Loading tracking data…", 45)
    group_tcs: dict[str, p3b.TrackingCollection] = {}
    for group_name, csv_dir in group_csv_dirs.items():
        tc = p3b.TrackingCollection.from_yolo3r_folder(csv_dir, fps=_FPS)
        tc.each.strip_column_names()
        tc.each.filter_likelihood(threshold=_LIKELIHOOD_THRESHOLD)
        tc.each.interpolate(limit=_INTERPOLATION_LIMIT)
        tc.each.smooth_all(window=_SMOOTH_WINDOW, method="mean")
        tc.each.rescale_by_known_distance(
            point1="tl",
            point2="br",
            distance_in_metres=_ARENA_SIZE_M,
        )
        group_tcs[group_name] = tc

    # ── Features ─────────────────────────────────────────────────────────────
    progress_cb("Computing features…", 60)
    group_fcs: dict[str, p3b.FeaturesCollection] = {}
    for group_name, tc in group_tcs.items():
        fc = p3b.FeaturesCollection.from_tracking_collection(tc)
        _compute_oft_features(fc)
        group_fcs[group_name] = fc

    # ── Summary & export ──────────────────────────────────────────────────────
    progress_cb("Building summaries…", 80)
    _export(group_fcs, summaries_dir, figures_dir, progress_cb)

    progress_cb("Pipeline complete.", 95)


# ── Feature computation ───────────────────────────────────────────────────────
def _compute_oft_features(fc: p3b.FeaturesCollection) -> None:
    """Store all OFT features on a FeaturesCollection (in-place)."""

    # Zone boundaries
    fc.each.define_static_boundary(_CORNERS, name="oft")
    fc.each.define_static_boundary(_CORNERS, scale_dim1=_CENTRE_SCALE, scale_dim2=_CENTRE_SCALE, name="centre")
    fc.each.define_static_boundary(_CORNERS, scale_dim1=_PERIPHERY_SCALE, scale_dim2=_PERIPHERY_SCALE, name="not_periphery")

    for c in _CORNERS:
        fc.each.define_static_boundary(
            _CORNERS,
            scale_dim1=_CORNER_SCALE,
            scale_dim2=_CORNER_SCALE,
            name=f"{c}_corner",
            anchor=c,
        )

    # Zone occupancy
    fc.each.within_boundary(_BODY_CENTRE, "centre").store("in_centre")
    (
        fc.each.within_boundary(_BODY_CENTRE, "oft")
        & ~fc.each.within_boundary(_BODY_CENTRE, "not_periphery")
    ).store("in_periphery")
    (
        fc.each.within_boundary(_BODY_CENTRE, "tl_corner")
        | fc.each.within_boundary(_BODY_CENTRE, "tr_corner")
        | fc.each.within_boundary(_BODY_CENTRE, "bl_corner")
        | fc.each.within_boundary(_BODY_CENTRE, "br_corner")
    ).store("in_corner")

    # Speed / distance
    fc.each.speed(_BODY_CENTRE).store()
    fc.each.distance_change(_BODY_CENTRE).store()


# ── Export ────────────────────────────────────────────────────────────────────
def _export(
    group_fcs: dict[str, p3b.FeaturesCollection],
    summaries_dir: Path,
    figures_dir: Path,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    """Build SummaryCollections, write Excel, save figures."""

    # TODO: implement summary → Excel export and group comparison plots.
    #
    # Sketch:
    #   group_scs = {name: p3b.SummaryCollection.from_features_collection(fc)
    #                for name, fc in group_fcs.items()}
    #   combined_df = ... merge group dfs with a 'group' column ...
    #   combined_df.to_excel(summaries_dir / "summary.xlsx", index=False)
    #   _plot_comparisons(group_scs, figures_dir)
    #
    # Fill this in once the summary API usage is confirmed against the
    # pipeline notebook in py3r_behaviour/tests/oft_pipeline/.

    progress_cb("Export: TODO — fill in _export() in oft_pipeline.py", None)
