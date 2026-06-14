"""Open Field Test — py3r_behaviour analysis pipeline.

Receives per-group folders of YOLO3R tracking CSVs and runs:
    load → preprocess → QC plots → features → clustering → summary → export

Nothing in this file knows about the GUI, the tracker, or any other arena.
Progress is reported via print() — the caller captures stdout if needed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import py3r.behaviour as p3b

from app.pipelines import _shared

# ── Constants (proprietary hardware — do not expose to GUI) ───────────────────
_FPS = 30
_ARENA_SIZE_M = 0.64
_N_CLUSTERS = 10
_CLUSTER_COL = f"kmeans_{_N_CLUSTERS}"
_GROUP_TAG = "group"

# Keypoint names as output by the OFT YOLO3R model (after strip_column_names)
_CORNERS = ["tl", "tr", "br", "bl"]
_CORNER_LINES = [("tl", "tr"), ("tr", "br"), ("br", "bl"), ("bl", "tl")]
_BODY_CENTRE = "bodycentre"

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
        "tl": {"color": (0, 255, 0), "radius": 5},
        "tr": {"color": (0, 255, 0), "radius": 5},
        "br": {"color": (0, 255, 0), "radius": 5},
        "bl": {"color": (0, 255, 0), "radius": 5},
    },
    "boundaries": {
        "oft": {"edge_color": (0, 200, 0), "edge_width": 1},
        "centre": {
            "edge_color": (0, 150, 255),
            "edge_width": 1,
            "fill_color": (0, 100, 200),
            "fill_alpha": 0.15,
        },
    },
}

# Zone scale factors
_CENTRE_SCALE = 0.5
_PERIPHERY_SCALE = 0.8
_CORNER_SCALE = 0.2


# ── Entry point ───────────────────────────────────────────────────────────────
def run(
    manifest: list[tuple[str, str, Path]],
    output_dir: Path,
    comparisons: list[tuple[str, str]] | None = None,
    video_paths: dict[str, Path] | None = None,
    numbins: int | None = None,
    n_clusters: int = _N_CLUSTERS,
) -> None:
    """Full OFT pipeline across all groups.

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
    print("Loading tracking data…")
    tc_all = _shared.load_and_tag(manifest, video_paths, fps=_FPS, group_tag=_GROUP_TAG)

    # ── Preprocess ────────────────────────────────────────────────────────────
    print("Preprocessing…")
    _shared.preprocess(tc_all)
    tc_all.each.rescale_by_known_distance(
        point1="tl", point2="br", distance_in_metres=_ARENA_SIZE_M
    )

    # ── QC trajectory plots ───────────────────────────────────────────────────
    print("Saving trajectory QC plots…")
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
    print("Computing features…")
    fc = tc_all.to_features()
    _compute_features(fc)

    # ── Clustering ────────────────────────────────────────────────────────────
    print(f"Clustering (k={n_clusters})…")
    _cluster(fc, n_clusters)

    print("Rendering QC animations…")
    _shared.export_animations(
        fc,
        group_names,
        output_dir,
        points=_ANIM_MOUSE_POINTS + _CORNERS,
        lines=_ANIM_BODY_LINES + _CORNER_LINES,
        boundaries=["oft", "centre"],
        features={
            "Speed (m/s)": f"speed_of_{_BODY_CENTRE}_in_xy",
            "In centre": "within_boundary_static_bodycentre_in_centre",
            "Cluster": _CLUSTER_COL,
        },
        style=_ANIM_STYLE,
        group_tag=_GROUP_TAG,
    )

    print("Saving features…")
    fc.save(str(features_dir), data_format="parquet", overwrite=True)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("Computing summaries…")
    sc = fc.to_summary()
    _compute_summaries(sc)

    if numbins:
        print(f"Computing {numbins}-bin summaries…")
        _shared.export_binned_summaries(
            sc, numbins, summaries_dir, "OFT_results", _compute_summaries
        )

    sc_grouped = sc.groupby(tags=[_GROUP_TAG])

    # ── Export ────────────────────────────────────────────────────────────────
    print("Exporting tables…")
    _shared.export_results_table(sc, summaries_dir, "OFT_results.csv")

    print("Exporting figures…")
    _shared.export_boxplots(
        sc_grouped,
        group_names,
        comparisons,
        figures_dir,
        metrics=[
            "total_distance_bodycentre",
            "time_in_centre",
            "distance_in_centre",
        ],
        group_tag=_GROUP_TAG,
    )

    print("Running BFA…")
    _shared.export_bfa(sc_grouped, bfa_dir, _CLUSTER_COL, n_clusters, comparisons)

    print("Pipeline complete.")


# ── Feature computation ───────────────────────────────────────────────────────
def _compute_features(fc: p3b.FeaturesCollection) -> None:
    print("  Spatial boundaries…")

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

    print("  Kinematic features…")

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
    print(f"  Fitting k={n_clusters} on {len(bfa_cols)} features…")
    cluster_labels, _ = fc.cluster_embedding_stream(
        embedding_dict=embedding_dict, n_clusters=n_clusters
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
