"""Elevated Plus Maze — py3r_behaviour analysis pipeline.

Receives per-group YOLO3R tracking CSVs and runs:
    load → preprocess → QC plots → features → clustering → summary → export

Nothing in this file knows about the GUI or the tracker. Point names resolve
through ``pts`` (canonical -> actual column), built from ``POINTS`` overlaid with
the config's ``[script.point_map]``. Heavy imports are deferred so the module is
cheap to import when the GUI only needs ``POINTS``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.pipelines import _shared

if TYPE_CHECKING:
    import py3r.behaviour as p3b

# ── Canonical point names (identity dict; [script.point_map] overrides) ───────
POINTS = {
    name: name
    for name in [
        "tl",
        "tr",
        "ctl",
        "ctr",
        "cbl",
        "cbr",
        "bl",
        "br",
        "lt",
        "lb",
        "rt",
        "rb",
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
}

# ── Structural constants (canonical names; translated through pts at use) ─────
_N_CLUSTERS = 10
_CLUSTER_COL = f"kmeans_{_N_CLUSTERS}"
_BODY_CENTRE = "bodycentre"

# Maze geometry — corner points per arm (ordered for polygon winding)
_OPEN_ARMS = ["top", "bottom"]
_CLOSED_ARMS = ["left", "right"]
_ARM_CORNERS = {
    "top": ["ctl", "ctr", "tr", "tl"],
    "bottom": ["cbl", "cbr", "br", "bl"],
    "left": ["lt", "ctl", "cbl", "lb"],
    "right": ["ctr", "rt", "rb", "cbr"],
}
_CENTRE_CORNERS = ["ctl", "ctr", "cbr", "cbl"]

# Body points used for the signed distance-to-arm-boundary BFA features
_BFA_BODY_POINTS = ["nose", "neck", _BODY_CENTRE, "tailbase"]

# 12 unique maze corner points + outline edges, for the QC trajectory plot
_CORNERS = ["tl", "tr", "ctl", "ctr", "cbl", "cbr", "bl", "br", "lt", "lb", "rt", "rb"]
_CORNER_LINES = [
    ("tl", "tr"),
    ("tr", "ctr"),
    ("ctr", "rt"),
    ("rt", "rb"),
    ("rb", "cbr"),
    ("cbr", "br"),
    ("br", "bl"),
    ("bl", "cbl"),
    ("cbl", "lb"),
    ("lb", "lt"),
    ("lt", "ctl"),
    ("ctl", "tl"),
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

_CLASSICAL_METRICS = [
    "total_distance",  # bodycentre-derived; resolved through pts in run()
    "time_true_in_open",
    "time_true_in_closed",
    "time_true_in_centre",
    "distance_moved_in_open",
    "distance_moved_in_closed",
    "count_onset_in_open",
    "latency_first_open_entry",
]


def _line(pts: dict, lines: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(pts[a], pts[b]) for a, b in lines]


def _anim_style(pts: dict) -> dict:
    bc = pts["bodycentre"]
    return {
        "points": {
            "default": {"color": (0, 255, 255), "radius": 3},
            bc: {
                "radius": 5,
                "color": {
                    "from": f"speed_of_{bc}_in_xy",
                    "cmap": "plasma",
                    "vmin": 0.0,
                    "vmax": 0.5,
                    "nan_color": (80, 80, 80),
                },
            },
        },
        "boundaries": {
            "top_arm": {"edge_color": (0, 255, 0), "edge_width": 1},
            "bottom_arm": {"edge_color": (0, 255, 0), "edge_width": 1},
            "left_arm": {"edge_color": (255, 0, 0), "edge_width": 1},
            "right_arm": {"edge_color": (255, 0, 0), "edge_width": 1},
            "centre": {
                "edge_color": (0, 150, 255),
                "edge_width": 1,
                "fill_color": (0, 100, 200),
                "fill_alpha": 0.15,
            },
        },
    }


# ── Entry point ───────────────────────────────────────────────────────────────
def run(
    *,
    manifest: list[tuple[str, str, Path]],
    output_dir: Path,
    comparisons: list[tuple[str, str]] | None = None,
    video_paths: dict[str, Path] | None = None,
    loader: dict,
    arena_diagonal_m: float,
    likelihood_min: float,
    point_map: dict[str, str] | None = None,
    numbins: int | None = None,
    n_clusters: int = _N_CLUSTERS,
) -> None:
    """Full EPM pipeline across all groups. See ``oft_pipeline.run`` for the
    shared parameter conventions; ``arena_diagonal_m`` calibrates the tl→br maze
    diagonal."""
    import matplotlib

    matplotlib.use("Agg")  # non-interactive backend — safe in subprocess

    comparisons = comparisons or []
    video_paths = video_paths or {}
    pts = _shared.resolve_points(POINTS, point_map)
    group_tag = loader.get("group_tag", "group")
    bc = pts["bodycentre"]

    dirs = _shared.make_output_dirs(output_dir)
    qc_dir = dirs["qc_trajectories"]
    features_dir = dirs["features"]
    summaries_dir = dirs["summaries"]
    figures_dir = dirs["figures"]
    bfa_dir = dirs["bfa"]

    group_names = list(dict.fromkeys(group for _, group, _ in manifest))

    # ── Load ──────────────────────────────────────────────────────────────────
    print("Loading tracking data...")
    tc_all = _shared.load(manifest, video_paths, loader)

    # ── Preprocess ────────────────────────────────────────────────────────────
    print("Preprocessing...")
    _shared.preprocess(tc_all, likelihood_min)
    tc_all.each.rescale_by_known_distance(
        point1=pts["tl"], point2=pts["br"], distance_in_metres=arena_diagonal_m
    )

    # ── QC trajectory plots ───────────────────────────────────────────────────
    print("Saving trajectory QC plots...")
    tc_grouped = tc_all.groupby(tags=[group_tag])
    _shared.plot_trajectory_qc(
        tc_grouped,
        group_names,
        qc_dir,
        trajectories=[bc],
        static=[pts[c] for c in _CORNERS],
        lines=_line(pts, _CORNER_LINES),
    )

    # ── Features ──────────────────────────────────────────────────────────────
    print("Computing features...")
    fc = tc_all.to_features()
    _compute_features(fc, pts)

    # ── Clustering ────────────────────────────────────────────────────────────
    print(f"Clustering (k={n_clusters})...")
    _cluster(fc, n_clusters)

    print("Rendering QC animations...")
    _shared.export_animations(
        fc,
        group_names,
        output_dir,
        points=[pts[p] for p in _ANIM_MOUSE_POINTS],
        lines=_line(pts, _ANIM_BODY_LINES),
        boundaries=["top_arm", "bottom_arm", "left_arm", "right_arm", "centre"],
        features={
            "Speed (m/s)": f"speed_of_{bc}_in_xy",
            "In open": "in_open",
            "In closed": "in_closed",
            "Cluster": _CLUSTER_COL,
        },
        style=_anim_style(pts),
        group_tag=group_tag,
    )

    print("Saving features...")
    fc.save(str(features_dir), data_format="parquet", overwrite=True)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("Computing summaries...")
    sc = fc.to_summary()
    _compute_summaries(sc, pts)

    if numbins:
        print(f"Computing {numbins}-bin summaries...")
        _shared.export_binned_summaries(
            sc, numbins, summaries_dir, "EPM_results", lambda s: _compute_summaries(s, pts)
        )

    sc_grouped = sc.groupby(tags=[group_tag])

    # ── Export ────────────────────────────────────────────────────────────────
    print("Exporting tables...")
    _shared.export_results_table(sc, summaries_dir, "EPM_results.csv")

    print("Exporting figures...")
    metrics = [f"total_distance_{bc}" if m == "total_distance" else m for m in _CLASSICAL_METRICS]
    _shared.export_boxplots(
        sc_grouped,
        group_names,
        comparisons,
        figures_dir,
        metrics=metrics,
        group_tag=group_tag,
    )

    print("Running BFA...")
    _shared.export_bfa(sc_grouped, bfa_dir, _CLUSTER_COL, n_clusters, comparisons)

    print("Pipeline complete.")


# ── Feature computation ───────────────────────────────────────────────────────
def _compute_features(fc: p3b.FeaturesCollection, pts: dict) -> None:
    print("  Spatial boundaries...")
    bc = pts["bodycentre"]

    for arm, corners in _ARM_CORNERS.items():
        fc.each.define_static_boundary([pts[c] for c in corners], name=f"{arm}_arm")
    fc.each.define_static_boundary([pts[c] for c in _CENTRE_CORNERS], name="centre")

    in_open = fc.each.within_boundary(bc, f"{_OPEN_ARMS[0]}_arm") | fc.each.within_boundary(
        bc, f"{_OPEN_ARMS[1]}_arm"
    )
    in_closed = fc.each.within_boundary(bc, f"{_CLOSED_ARMS[0]}_arm") | fc.each.within_boundary(
        bc, f"{_CLOSED_ARMS[1]}_arm"
    )
    in_centre = fc.each.within_boundary(bc, "centre")

    in_open.store("in_open")
    in_closed.store("in_closed")
    in_centre.store("in_centre")

    fc.each.compose_state_from_booleans(
        {"centre": in_centre, "open": in_open, "closed": in_closed}
    ).store("zone_state")

    dist_change = fc.each.distance_change(bc)
    (in_open.astype("Int64") * dist_change).store("dist_change_bodycentre_in_open")
    (in_closed.astype("Int64") * dist_change).store("dist_change_bodycentre_in_closed")

    print("  Kinematic features...")

    _shared.compute_body_kinematics(fc, pts)

    for arm in ["top", "bottom", "left", "right"]:
        for pt in _BFA_BODY_POINTS:
            fc.each.distance_to_boundary(pts[pt], f"{arm}_arm", signed=True).store()


# ── Clustering ────────────────────────────────────────────────────────────────
def _cluster(fc: p3b.FeaturesCollection, n_clusters: int) -> None:
    import numpy as np

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
def _compute_summaries(sc: p3b.SummaryCollection, pts: dict) -> None:
    bc = pts["bodycentre"]
    sc.each.total_distance(bc).store()
    sc.each.time_true("in_open").store("time_true_in_open")
    sc.each.time_true("in_closed").store("time_true_in_closed")
    sc.each.time_true("in_centre").store("time_true_in_centre")
    sc.each.sum_column("dist_change_bodycentre_in_open").store("distance_moved_in_open")
    sc.each.sum_column("dist_change_bodycentre_in_closed").store("distance_moved_in_closed")
    sc.each.count_onset("in_open").store("count_onset_in_open")
    sc.each.calculate_latency_nth_onset("in_open").store("latency_first_open_entry")
    sc.each.mean_column(f"speed_of_{bc}_in_xy").store("mean_speed")
    sc.each.by_state("zone_state", all_states=["centre", "open", "closed", "none"]).mean_column(
        f"speed_of_{bc}_in_xy"
    ).store("mean_speed_by_zone")
    sc.each.time_in_state(_CLUSTER_COL).store("time_in_cluster")
