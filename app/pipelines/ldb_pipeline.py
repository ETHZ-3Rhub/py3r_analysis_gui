"""Light-Dark Box — py3r_behaviour analysis pipeline.

Receives per-group folders of YOLO3R tracking CSVs and runs:
    load → preprocess → QC plots → features → clustering → summary → export

Nothing in this file knows about the GUI, the tracker, or any other arena.
Progress is reported via print() - the caller captures stdout if needed.

Gated behind app/arenas/ldb.py's READY flag — not yet reachable from the GUI.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import py3r.behaviour as p3b

from app.pipelines import _shared

# ── Constants (proprietary hardware — do not expose to GUI) ───────────────────
_FPS = 30
# Placeholder pending a real measurement of the LDB rig's diagonal.
_ARENA_DIAGONAL_M = 0.5
_N_CLUSTERS = 25
_CLUSTER_COL = f"kmeans_{_N_CLUSTERS}"
_GROUP_TAG = "group"
_BODY_CENTRE = "bodycentre"

# Maze geometry — box split into light (bottom) / dark (top) halves by the
# ml-mr divider
_LDB_CORNERS = ["tl", "tr", "br", "bl"]
_LIGHT_CORNERS = ["ml", "mr", "br", "bl"]
_DARK_CORNERS = ["tl", "tr", "mr", "ml"]

# Body points used for the signed distance-to-dark-boundary BFA features
_BFA_BODY_POINTS = ["nose", "neck", _BODY_CENTRE, "tailbase"]

# Box outline + divider, for the QC trajectory plot
_CORNERS = ["tl", "tr", "br", "bl", "ml", "mr"]
_CORNER_LINES = [
    ("tl", "tr"),
    ("tr", "br"),
    ("br", "bl"),
    ("bl", "tl"),
    ("ml", "mr"),
]

_ANIM_MOUSE_POINTS = [
    "nose",
    "headcentre",
    "earr",
    "earl",
    "neck",
    "bcr",
    "bcl",
    "bodycentre",
    "hipr",
    "hipl",
    "tailbase",
]
_ANIM_BODY_LINES = [
    ("nose", "headcentre"),
    ("headcentre", "earr"),
    ("headcentre", "earl"),
    ("headcentre", "neck"),
    ("neck", "bcr"),
    ("neck", "bcl"),
    ("bcr", "bodycentre"),
    ("bcl", "bodycentre"),
    ("bodycentre", "hipr"),
    ("bodycentre", "hipl"),
    ("hipr", "hipl"),
    ("bodycentre", "tailbase"),
]
_ANIM_STYLE = {
    "points": {
        "default": {"color": (0, 255, 255), "radius": 3},
        "bodycentre": {
            "radius": 5,
            "color": {
                "from": f"speed_of_{_BODY_CENTRE}_in_xy",
                "cmap": "plasma",
                "vmin": 0.0,
                "vmax": 0.5,
                "nan_color": (80, 80, 80),
            },
        },
    },
    "boundaries": {
        "light": {
            "edge_color": (0, 255, 255),
            "edge_width": 1,
            "fill_color": (200, 200, 0),
            "fill_alpha": 0.1,
        },
        "dark": {
            "edge_color": (150, 0, 150),
            "edge_width": 1,
            "fill_color": (50, 0, 50),
            "fill_alpha": 0.15,
        },
    },
}

_CLASSICAL_METRICS = [
    "total_distance_bodycentre",
    "time_true_in_light",
    "time_true_in_dark",
    "distance_moved_in_light",
    "distance_moved_in_dark",
    "count_onset_in_dark",
    "latency_first_dark_entry",
]


# ── Entry point ───────────────────────────────────────────────────────────────
def run(
    manifest: list[tuple[str, str, Path]],
    output_dir: Path,
    comparisons: list[tuple[str, str]] | None = None,
    video_paths: dict[str, Path] | None = None,
    numbins: int | None = None,
    n_clusters: int = _N_CLUSTERS,
) -> None:
    """Full LDB pipeline across all groups.

    Parameters
    ----------
    manifest:
        ``[(handle, group_name, csv_path), ...]`` — every recording's unique
        handle (assigned by the GUI), its group, and its YOLO3R CSV.
    output_dir:
        Root output folder.  Sub-folders are created automatically.
    comparisons:
        List of ``(group_a, group_b)`` pairs for statistical annotations and BFA plots.
        Empty or None → pipeline runs without pairwise stats.
    video_paths:
        ``{handle: Path}`` — source video for handles that have one, used for
        QC animation overlay. None or missing handle → animation renders
        without video background.
    numbins:
        Split each animal's session into this many equal-frame-count bins and
        write per-bin summary CSVs alongside the whole-session results.
        None → no binning.
    """
    import matplotlib

    matplotlib.use("Agg")  # non-interactive backend — safe in QThread

    comparisons = comparisons or []
    video_paths = video_paths or {}

    dirs = _shared.make_output_dirs(output_dir)
    qc_dir = dirs["qc_trajectories"]
    features_dir = dirs["features"]
    summaries_dir = dirs["summaries"]
    figures_dir = dirs["figures"]
    bfa_dir = dirs["bfa"]

    group_names = list(dict.fromkeys(group for _, group, _ in manifest))

    # ── Load ──────────────────────────────────────────────────────────────────
    print("Loading tracking data...")
    tc_all = _shared.load_and_tag(manifest, video_paths, fps=_FPS, group_tag=_GROUP_TAG)

    # ── Preprocess ────────────────────────────────────────────────────────────
    print("Preprocessing...")
    _shared.preprocess(tc_all)
    tc_all.each.rescale_by_known_distance(
        point1="tl", point2="br", distance_in_metres=_ARENA_DIAGONAL_M
    )

    # ── QC trajectory plots ───────────────────────────────────────────────────
    print("Saving trajectory QC plots...")
    tc_grouped = tc_all.groupby(tags=[_GROUP_TAG])
    _shared.plot_trajectory_qc(
        tc_grouped,
        group_names,
        qc_dir,
        trajectories=[_BODY_CENTRE],
        static=_CORNERS,
        lines=_CORNER_LINES,
    )

    # ── Features ──────────────────────────────────────────────────────────────
    print("Computing features...")
    fc = tc_all.to_features()
    _compute_features(fc)

    # ── Clustering ────────────────────────────────────────────────────────────
    print(f"Clustering (k={n_clusters})...")
    _cluster(fc, n_clusters)

    print("Rendering QC animations...")
    _shared.export_animations(
        fc,
        group_names,
        output_dir,
        points=_ANIM_MOUSE_POINTS,
        lines=_ANIM_BODY_LINES,
        boundaries=["light", "dark"],
        features={
            "Speed (m/s)": f"speed_of_{_BODY_CENTRE}_in_xy",
            "In dark": "in_dark",
            "Cluster": _CLUSTER_COL,
        },
        style=_ANIM_STYLE,
        group_tag=_GROUP_TAG,
    )

    print("Saving features...")
    fc.save(str(features_dir), data_format="parquet", overwrite=True)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("Computing summaries...")
    sc = fc.to_summary()
    _compute_summaries(sc)

    if numbins:
        print(f"Computing {numbins}-bin summaries...")
        _shared.export_binned_summaries(
            sc, numbins, summaries_dir, "LDB_results", _compute_summaries
        )

    sc_grouped = sc.groupby(tags=[_GROUP_TAG])

    # ── Export ────────────────────────────────────────────────────────────────
    print("Exporting tables...")
    _shared.export_results_table(sc, summaries_dir, "LDB_results.csv")

    print("Exporting figures...")
    _shared.export_boxplots(
        sc_grouped,
        group_names,
        comparisons,
        figures_dir,
        metrics=_CLASSICAL_METRICS,
        group_tag=_GROUP_TAG,
    )

    print("Running BFA...")
    _shared.export_bfa(sc_grouped, bfa_dir, _CLUSTER_COL, n_clusters, comparisons)

    print("Pipeline complete.")


# ── Feature computation ───────────────────────────────────────────────────────
def _compute_features(fc: p3b.FeaturesCollection) -> None:
    print("  Spatial boundaries...")

    fc.each.define_static_boundary(_LIGHT_CORNERS, name="light")
    fc.each.define_static_boundary(_DARK_CORNERS, name="dark")

    in_light = fc.each.within_boundary(_BODY_CENTRE, "light")
    in_dark = fc.each.within_boundary(_BODY_CENTRE, "dark")
    in_light.store("in_light")
    in_dark.store("in_dark")

    fc.each.compose_state_from_booleans({"light": in_light, "dark": in_dark}).store("zone_state")

    dist_change = fc.each.distance_change(_BODY_CENTRE)
    (in_light.astype("Int64") * dist_change).store("dist_change_bodycentre_in_light")
    (in_dark.astype("Int64") * dist_change).store("dist_change_bodycentre_in_dark")

    print("  Kinematic features...")

    _shared.compute_body_kinematics(fc)

    for pt in _BFA_BODY_POINTS:
        fc.each.distance_to_boundary(pt, "dark", signed=True).store()


# ── Clustering ────────────────────────────────────────────────────────────────
def _cluster(fc: p3b.FeaturesCollection, n_clusters: int) -> None:
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
    print(f"  Fitting k={n_clusters} on {len(bfa_cols)} features...")
    cluster_labels, _ = fc.cluster_embedding_stream(
        embedding_dict=embedding_dict, n_clusters=n_clusters
    )
    cluster_labels.store(_CLUSTER_COL, overwrite=True)


# ── Summary computation ───────────────────────────────────────────────────────
def _compute_summaries(sc: p3b.SummaryCollection) -> None:
    sc.each.total_distance(_BODY_CENTRE).store()
    sc.each.time_true("in_light").store("time_true_in_light")
    sc.each.time_true("in_dark").store("time_true_in_dark")
    sc.each.sum_column("dist_change_bodycentre_in_light").store("distance_moved_in_light")
    sc.each.sum_column("dist_change_bodycentre_in_dark").store("distance_moved_in_dark")
    sc.each.count_onset("in_dark").store("count_onset_in_dark")
    sc.each.calculate_latency_nth_onset("in_dark").store("latency_first_dark_entry")
    sc.each.by_state("zone_state", all_states=["light", "dark", "none"]).mean_column(
        f"speed_of_{_BODY_CENTRE}_in_xy"
    ).store("mean_speed_by_zone")
    sc.each.time_in_state(_CLUSTER_COL).store("time_in_cluster")
