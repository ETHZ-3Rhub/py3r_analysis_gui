"""Light-Dark Box — py3r_behaviour analysis pipeline.

Receives per-group YOLO3R tracking CSVs and runs:
    load → preprocess → QC plots → features → clustering → summary → export

Dormant: no bundled ldb config ships yet (the LDB tracker model isn't ready),
so this isn't reachable from the GUI. Kept on the current pipeline contract
(POINTS + config-driven signature) so it doesn't rot. Point names resolve
through ``pts`` (canonical -> actual column).
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
        "br",
        "bl",
        "ml",
        "mr",
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

_N_CLUSTERS = 25
_CLUSTER_COL = f"kmeans_{_N_CLUSTERS}"
_BODY_CENTRE = "bodycentre"

# Box split into light (bottom) / dark (top) halves by the ml-mr divider
_LIGHT_CORNERS = ["ml", "mr", "br", "bl"]
_DARK_CORNERS = ["tl", "tr", "mr", "ml"]

_BFA_BODY_POINTS = ["nose", "neck", _BODY_CENTRE, "tailbase"]

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

_CLASSICAL_METRICS = [
    "total_distance",  # bodycentre-derived; resolved through pts in run()
    "time_true_in_light",
    "time_true_in_dark",
    "distance_moved_in_light",
    "distance_moved_in_dark",
    "count_onset_in_dark",
    "latency_first_dark_entry",
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
    """Full LDB pipeline across all groups. See ``oft_pipeline.run`` for the
    shared parameter conventions."""
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

    print("Loading tracking data...")
    tc_all = _shared.load(manifest, video_paths, loader)

    print("Preprocessing...")
    _shared.preprocess(tc_all, likelihood_min)
    tc_all.each.rescale_by_known_distance(
        point1=pts["tl"], point2=pts["br"], distance_in_metres=arena_diagonal_m
    )

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

    print("Computing features...")
    fc = tc_all.to_features()
    _compute_features(fc, pts)

    print(f"Clustering (k={n_clusters})...")
    _cluster(fc, n_clusters)

    print("Rendering QC animations...")
    _shared.export_animations(
        fc,
        group_names,
        output_dir,
        points=[pts[p] for p in _ANIM_MOUSE_POINTS],
        lines=_line(pts, _ANIM_BODY_LINES),
        boundaries=["light", "dark"],
        features={
            "Speed (m/s)": f"speed_of_{bc}_in_xy",
            "In dark": "in_dark",
            "Cluster": _CLUSTER_COL,
        },
        style=_anim_style(pts),
        group_tag=group_tag,
    )

    print("Saving features...")
    fc.save(str(features_dir), data_format="parquet", overwrite=True)

    print("Computing summaries...")
    sc = fc.to_summary()
    _compute_summaries(sc, pts)

    if numbins:
        print(f"Computing {numbins}-bin summaries...")
        _shared.export_binned_summaries(
            sc, numbins, summaries_dir, "LDB_results", lambda s: _compute_summaries(s, pts)
        )

    sc_grouped = sc.groupby(tags=[group_tag])

    print("Exporting tables...")
    _shared.export_results_table(sc, summaries_dir, "LDB_results.csv")

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

    fc.each.define_static_boundary([pts[c] for c in _LIGHT_CORNERS], name="light")
    fc.each.define_static_boundary([pts[c] for c in _DARK_CORNERS], name="dark")

    in_light = fc.each.within_boundary(bc, "light")
    in_dark = fc.each.within_boundary(bc, "dark")
    in_light.store("in_light")
    in_dark.store("in_dark")

    fc.each.compose_state_from_booleans({"light": in_light, "dark": in_dark}).store("zone_state")

    dist_change = fc.each.distance_change(bc)
    (in_light.astype("Int64") * dist_change).store("dist_change_bodycentre_in_light")
    (in_dark.astype("Int64") * dist_change).store("dist_change_bodycentre_in_dark")

    print("  Kinematic features...")

    _shared.compute_body_kinematics(fc, pts)

    for pt in _BFA_BODY_POINTS:
        fc.each.distance_to_boundary(pts[pt], "dark", signed=True).store()


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
    sc.each.time_true("in_light").store("time_true_in_light")
    sc.each.time_true("in_dark").store("time_true_in_dark")
    sc.each.sum_column("dist_change_bodycentre_in_light").store("distance_moved_in_light")
    sc.each.sum_column("dist_change_bodycentre_in_dark").store("distance_moved_in_dark")
    sc.each.count_onset("in_dark").store("count_onset_in_dark")
    sc.each.calculate_latency_nth_onset("in_dark").store("latency_first_dark_entry")
    sc.each.by_state("zone_state", all_states=["light", "dark", "none"]).mean_column(
        f"speed_of_{bc}_in_xy"
    ).store("mean_speed_by_zone")
    sc.each.time_in_state(_CLUSTER_COL).store("time_in_cluster")
