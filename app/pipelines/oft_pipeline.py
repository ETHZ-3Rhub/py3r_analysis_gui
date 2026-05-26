"""Open Field Test — py3r_behaviour analysis pipeline.

Receives per-group folders of YOLO3R tracking CSVs and runs:
    load → preprocess → QC plots → features → clustering → summary → export

Nothing in this file knows about the GUI, the tracker, or any other arena.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import py3r.behaviour as p3b

# ── Constants (proprietary hardware — do not expose to GUI) ───────────────────
_FPS = 30
_ARENA_SIZE_M = 0.64
_LIKELIHOOD_THRESHOLD = 0.9
_INTERPOLATION_LIMIT = 5
_SMOOTH_WINDOW = 3
_N_CLUSTERS = 10
_CLUSTER_COL = f"kmeans_{_N_CLUSTERS}"
_GROUP_TAG = "group"
_BFA_RANDOM_STATE = 42

# Keypoint names as output by the OFT YOLO3R model (after strip_column_names)
_CORNERS = ["tl", "tr", "br", "bl"]
_CORNER_LINES = [("tl", "tr"), ("tr", "br"), ("br", "bl"), ("bl", "tl")]
_BODY_CENTRE = "bodycentre"

# Zone scale factors
_CENTRE_SCALE = 0.5
_PERIPHERY_SCALE = 0.8
_CORNER_SCALE = 0.2


# ── Entry point ───────────────────────────────────────────────────────────────
def run(
    group_csv_dirs: dict[str, Path],
    output_dir: Path,
    progress_cb: Callable[[str, float | None], None],
    comparisons: list[tuple[str, str]],
) -> None:
    """Full OFT pipeline across all groups.

    Parameters
    ----------
    group_csv_dirs:
        ``{group_name: Path}`` — each Path is a folder of per-animal YOLO3R CSVs.
    output_dir:
        Root output folder.  Sub-folders are created automatically.
    progress_cb:
        ``progress_cb(message, pct_or_None)`` forwarded from the GUI runner.
    comparisons:
        List of ``(group_a, group_b)`` pairs for statistical annotations and BFA plots.
        Empty list → pipeline runs without pairwise stats.
    """
    import matplotlib

    matplotlib.use("Agg")  # non-interactive backend — safe in QThread

    qc_dir = output_dir / "qc" / "trajectories"
    features_dir = output_dir / "features"
    summaries_dir = output_dir / "summaries"
    figures_dir = output_dir / "figures"
    bfa_dir = output_dir / "bfa"
    for d in [qc_dir, features_dir, summaries_dir, figures_dir, bfa_dir]:
        d.mkdir(parents=True, exist_ok=True)

    group_names = list(group_csv_dirs.keys())

    # ── Load ──────────────────────────────────────────────────────────────────
    progress_cb("Loading tracking data…", 42)
    group_tcs: dict[str, p3b.TrackingCollection] = {}
    for group_name, csv_dir in group_csv_dirs.items():
        progress_cb(f"  Loading {group_name}…", None)
        tc = p3b.TrackingCollection.from_yolo3r_folder(csv_dir, fps=_FPS)
        tc.each.strip_column_names()
        for t in tc.values():
            t.tags[_GROUP_TAG] = group_name
        group_tcs[group_name] = tc

    # Collision guard — animal filenames must be unique across groups
    all_handles: list[str] = []
    for tc in group_tcs.values():
        all_handles.extend(tc.keys())
    duplicates = {h for h in all_handles if all_handles.count(h) > 1}
    if duplicates:
        raise ValueError(
            f"Duplicate animal IDs found across groups: {sorted(duplicates)}.\n"
            "Each animal must have a unique CSV filename (without extension)."
        )

    # ── Preprocess ────────────────────────────────────────────────────────────
    progress_cb("Preprocessing…", 48)
    for group_name, tc in group_tcs.items():
        progress_cb(f"  Preprocessing {group_name}…", None)
        tc.each.filter_likelihood(threshold=_LIKELIHOOD_THRESHOLD)
        tc.each.interpolate(limit=_INTERPOLATION_LIMIT)
        tc.each.smooth_all(window=_SMOOTH_WINDOW, method="mean")
        tc.each.rescale_by_known_distance(
            point1="tl", point2="br", distance_in_metres=_ARENA_SIZE_M
        )

    # ── QC trajectory plots ───────────────────────────────────────────────────
    progress_cb("Saving trajectory QC plots…", 53)
    for group_name, tc in group_tcs.items():
        group_qc_dir = qc_dir / group_name
        group_qc_dir.mkdir(parents=True, exist_ok=True)
        tc.each.plot(
            trajectories=[_BODY_CENTRE],
            static=_CORNERS,
            lines=_CORNER_LINES,
            show=False,
            savedir=group_qc_dir,
        )

    # ── Merge all groups into one collection for features + clustering ─────────
    progress_cb("Computing features…", 57)
    all_trackings: dict = {}
    for tc in group_tcs.values():
        all_trackings.update(tc.tracking_dict)
    tc_all = p3b.TrackingCollection(all_trackings)
    fc = tc_all.to_features()
    _compute_features(fc, progress_cb)

    # ── Clustering ────────────────────────────────────────────────────────────
    progress_cb(f"Clustering (k={_N_CLUSTERS})…", 73)
    _cluster(fc, progress_cb)

    progress_cb("Saving features…", 78)
    fc.save(str(features_dir), data_format="parquet", overwrite=True)

    # ── Summary ───────────────────────────────────────────────────────────────
    progress_cb("Computing summaries…", 82)
    sc = fc.to_summary()
    _compute_summaries(sc)
    sc_grouped = sc.groupby(tags=[_GROUP_TAG])

    # ── Export ────────────────────────────────────────────────────────────────
    progress_cb("Exporting tables…", 87)
    _export_tables(sc_grouped, summaries_dir, progress_cb)

    progress_cb("Exporting figures…", 90)
    _export_figures(sc_grouped, group_names, comparisons, figures_dir, progress_cb)

    progress_cb("Running BFA…", 93)
    _export_bfa(sc_grouped, group_names, comparisons, bfa_dir, progress_cb)

    progress_cb("Pipeline complete.", 95)


# ── Feature computation ───────────────────────────────────────────────────────
def _compute_features(
    fc: p3b.FeaturesCollection,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    progress_cb("  Spatial boundaries…", None)

    fc.each.define_static_boundary(_CORNERS, name="oft")
    fc.each.define_static_boundary(
        _CORNERS, scale_dim1=_CENTRE_SCALE, scale_dim2=_CENTRE_SCALE, name="centre"
    )
    fc.each.define_static_boundary(
        _CORNERS, scale_dim1=_PERIPHERY_SCALE, scale_dim2=_PERIPHERY_SCALE, name="not_periphery"
    )
    for c in _CORNERS:
        fc.each.define_static_boundary(
            _CORNERS,
            scale_dim1=_CORNER_SCALE,
            scale_dim2=_CORNER_SCALE,
            name=f"{c}_corner",
            anchor=c,
        )

    in_centre = fc.each.within_boundary(_BODY_CENTRE, "centre")
    in_centre.store()

    (
        fc.each.within_boundary(_BODY_CENTRE, "oft")
        & ~fc.each.within_boundary(_BODY_CENTRE, "not_periphery")
    ).store("in_periphery")

    in_corners = {c: fc.each.within_boundary(_BODY_CENTRE, f"{c}_corner") for c in _CORNERS}
    (in_corners["tl"] | in_corners["tr"] | in_corners["bl"] | in_corners["br"]).store("in_corner")
    fc.each.compose_state_from_booleans(in_corners).store("corner_state")

    dist_change = fc.each.distance_change(_BODY_CENTRE)
    (in_centre.astype("Int64") * dist_change).store("dist_change_bodycentre_in_centre")

    progress_cb("  Kinematic features…", None)

    for pt in ["nose", "neck", "earr", "earl", _BODY_CENTRE, "hipl", "hipr", "tailbase"]:
        fc.each.speed(pt).store()

    for base, p1, p2 in [
        ("tailbase", "hipr", "hipl"),
        (_BODY_CENTRE, "tailbase", "neck"),
        ("neck", _BODY_CENTRE, "headcentre"),
        ("headcentre", "earr", "earl"),
    ]:
        fc.each.azimuth_deviation(base, p1, p2).store()

    for p1, p2 in [
        ("nose", "headcentre"),
        ("neck", "headcentre"),
        ("neck", _BODY_CENTRE),
        ("bcr", _BODY_CENTRE),
        ("bcl", _BODY_CENTRE),
        ("tailbase", _BODY_CENTRE),
        ("tailbase", "hipr"),
        ("tailbase", "hipl"),
        ("bcr", "hipr"),
        ("bcl", "hipl"),
        ("bcl", "earl"),
        ("bcr", "earr"),
        ("nose", "earr"),
        ("nose", "earl"),
    ]:
        fc.each.distance_between(p1, p2).store()

    for boundary_name, pts in [
        ("mouse_rear", ["tailbase", "hipr", "hipl"]),
        ("mouse_mid", ["hipr", "hipl", "bcl", "bcr"]),
        ("mouse_front", ["bcr", "earr", "earl", "bcl"]),
        ("mouse_face", ["earr", "nose", "earl"]),
    ]:
        fc.each.define_dynamic_boundary(pts, name=boundary_name)
        fc.each.area_of_boundary(boundary_name).store()

    for pt in ["nose", "neck", _BODY_CENTRE, "tailbase"]:
        fc.each.distance_to_boundary(pt, "oft").store()


# ── Clustering ────────────────────────────────────────────────────────────────
def _cluster(
    fc: p3b.FeaturesCollection,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    bfa_prefixes = (
        "speed_of_",
        "azimuth_deviation_",
        "distance_between_",
        "area_of_boundary_",
        "distance_to_boundary_",
    )
    bfa_cols = [c for c in fc[0].data.columns if any(c.startswith(p) for p in bfa_prefixes)]
    offset = list(np.arange(-15, 16, 1))
    embedding_dict = {f: offset for f in bfa_cols}
    progress_cb(f"  Fitting k={_N_CLUSTERS} on {len(bfa_cols)} features…", None)
    cluster_labels, _ = fc.cluster_embedding_stream(
        embedding_dict=embedding_dict, n_clusters=_N_CLUSTERS
    )
    cluster_labels.store(_CLUSTER_COL, overwrite=True)


# ── Summary computation ───────────────────────────────────────────────────────
def _compute_summaries(sc: p3b.SummaryCollection) -> None:
    sc.each.total_distance(_BODY_CENTRE).store()
    sc.each.time_true("within_boundary_static_bodycentre_in_centre").store("time_in_centre")
    sc.each.sum_column("dist_change_bodycentre_in_centre").store("distance_in_centre")
    sc.each.by_state("corner_state", all_states=_CORNERS).mean_column(
        f"speed_of_{_BODY_CENTRE}_in_xy"
    ).store("mean_speed_by_corner")
    sc.each.time_in_state(_CLUSTER_COL).store("time_in_cluster")


# ── Table export ──────────────────────────────────────────────────────────────
def _export_tables(
    sc_grouped: p3b.SummaryCollection,
    summaries_dir: Path,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    summary_df, series_dfs = sc_grouped.to_df(include_tags=True, series="separate")
    summary_df.to_csv(summaries_dir / "OFT_results.csv")
    try:
        with pd.ExcelWriter(summaries_dir / "OFT_results.xlsx", engine="openpyxl") as writer:
            summary_df.to_excel(writer, sheet_name="Summary")
            for key, df in series_dfs.items():
                df.to_excel(writer, sheet_name=key[:31])
        progress_cb("  Saved CSV + Excel.", None)
    except ImportError:
        progress_cb("  Note: openpyxl not installed — CSV saved, Excel skipped.", None)


# ── Figure export ─────────────────────────────────────────────────────────────
def _export_figures(
    sc_grouped: p3b.SummaryCollection,
    group_names: list[str],
    comparisons: list[tuple[str, str]],
    figures_dir: Path,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    import matplotlib.pyplot as plt

    group_order = {_GROUP_TAG: group_names}
    annotate = (
        {
            "pairs": comparisons,
            "test": "Mann-Whitney",
            "correction": None,
            "text_format": "star",
            "headroom": 0.1,
        }
        if comparisons
        else None
    )

    for metric in [
        "total_distance_bodycentre",
        "time_in_centre",
        "distance_in_centre",
        "time_in_cluster",
    ]:
        progress_cb(f"  Plotting {metric}…", None)
        try:
            fig, ax, _ = sc_grouped.snsbox(
                metric,
                group_order=group_order,
                annotate=annotate,
                show=False,
                savedir=str(figures_dir),
            )
            plt.close(fig)
        except Exception as exc:
            progress_cb(f"  Warning: could not plot {metric}: {exc}", None)


# ── BFA export ────────────────────────────────────────────────────────────────
def _export_bfa(
    sc_grouped: p3b.SummaryCollection,
    group_names: list[str],
    comparisons: list[tuple[str, str]],
    bfa_dir: Path,
    progress_cb: Callable[[str, float | None], None],
) -> None:
    import matplotlib.pyplot as plt

    try:
        progress_cb("  Computing BFA transition statistics…", None)
        bfa_results = sc_grouped.bfa(
            column=_CLUSTER_COL,
            all_states=list(range(_N_CLUSTERS)),
            random_state=_BFA_RANDOM_STATE,
        )
        bfa_stats = p3b.SummaryCollection.bfa_stats(bfa_results)

        with open(bfa_dir / "bfa_results.json", "w") as f:
            json.dump(bfa_results, f, indent=4)
        with open(bfa_dir / "bfa_stats.json", "w") as f:
            json.dump(bfa_stats, f, indent=4)

        progress_cb("  Plotting BFA histograms…", None)
        p3b.SummaryCollection.plot_bfa_results(
            bfa_results,
            add_stats=True,
            stats=bfa_stats,
            bins=20,
            save_dir=bfa_dir,
            show=False,
        )
        plt.close("all")

        # Chord diagrams (requires pycirclize)
        try:
            progress_cb("  Plotting chord diagrams…", None)
            sc_grouped.plot_chord(
                column=_CLUSTER_COL,
                all_states=list(range(_N_CLUSTERS)),
                save_dir=bfa_dir,
                show=False,
            )
            plt.close("all")
        except ImportError:
            progress_cb("  Note: pycirclize not installed — chord diagrams skipped.", None)

        # Transition UMAP (requires umap-learn)
        try:
            progress_cb("  Plotting transition UMAP…", None)
            sc_grouped.plot_transition_umap(
                column=_CLUSTER_COL,
                all_states=list(range(_N_CLUSTERS)),
                random_state=_BFA_RANDOM_STATE,
                save_dir=str(bfa_dir),
                show=False,
            )
            plt.close("all")
        except ImportError:
            progress_cb("  Note: umap-learn not installed — UMAP skipped.", None)

    except Exception as exc:
        progress_cb(f"  Warning: BFA failed: {exc}", None)
