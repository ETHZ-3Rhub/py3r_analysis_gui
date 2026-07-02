"""Open Field Test — py3r_behaviour analysis pipeline.

Receives per-group YOLO3R tracking CSVs and runs:
    load → preprocess → QC plots → features → clustering → summary → export

Nothing in this file knows about the GUI or the tracker. Progress is reported
via print() — the caller captures stdout if needed.

Point names are resolved through ``pts`` (canonical -> actual column), built from
``POINTS`` overlaid with the config's ``[script.point_map]``; defaults to
identity. Heavy imports (py3r, numpy) are deferred into the functions so the
module is cheap to import when the GUI only needs ``POINTS``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.scripts import _shared

if TYPE_CHECKING:
    import py3r.behaviour as p3b

# ── Canonical point names this pipeline uses (identity dict; a config's
# [script.point_map] overrides entries for a lab whose model renames a point) ──
POINTS = {
    name: name
    for name in [
        "tl",
        "tr",
        "br",
        "bl",
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

# Zone scale factors
_CENTRE_SCALE = 0.5
_PERIPHERY_SCALE = 0.8
_CORNER_SCALE = 0.2


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
            pts["tl"]: {"color": (0, 255, 0), "radius": 5},
            pts["tr"]: {"color": (0, 255, 0), "radius": 5},
            pts["br"]: {"color": (0, 255, 0), "radius": 5},
            pts["bl"]: {"color": (0, 255, 0), "radius": 5},
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


# ── Entry point ───────────────────────────────────────────────────────────────
def run(
    *,
    tc: p3b.TrackingCollection,
    output_dir: Path,
    comparisons: list[tuple[str, str]] | None = None,
    group_tag: str = "group",
    arena_size_m: float,
    likelihood_min: float,
    point_map: dict[str, str] | None = None,
    numbins: int | None = None,
    n_clusters: int = _N_CLUSTERS,
) -> None:
    """Full OFT pipeline across all groups."""
    import matplotlib

    matplotlib.use("Agg")  # non-interactive backend — safe in subprocess

    comparisons = comparisons or []
    pts = _shared.resolve_points(POINTS, point_map)
    bc = pts["bodycentre"]

    dirs = _shared.make_output_dirs(output_dir)
    qc_dir = dirs["qc_trajectories"]
    features_dir = dirs["features"]
    summaries_dir = dirs["summaries"]
    figures_dir = dirs["figures"]
    bfa_dir = dirs["bfa"]

    group_names = list(dict.fromkeys(tc[h].tags[group_tag] for h in tc))

    # ── Preprocess ────────────────────────────────────────────────────────────
    print("Preprocessing...")
    _shared.preprocess(tc, likelihood_min)
    tc.each.rescale_by_known_distance(
        point1=pts["tl"], point2=pts["br"], distance_in_metres=arena_size_m
    )

    # ── QC trajectory plots ───────────────────────────────────────────────────
    print("Saving trajectory QC plots...")
    tc_grouped = tc.groupby(tags=[group_tag])
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
    fc = tc.to_features()
    _compute_features(fc, pts)

    # ── Clustering ────────────────────────────────────────────────────────────
    print(f"Clustering (k={n_clusters})...")
    _cluster(fc, n_clusters)

    print("Rendering QC animations...")
    _shared.export_animations(
        fc,
        group_names,
        output_dir,
        points=[pts[p] for p in _ANIM_MOUSE_POINTS + _CORNERS],
        lines=_line(pts, _ANIM_BODY_LINES + _CORNER_LINES),
        boundaries=["oft", "centre"],
        features={
            "Speed (m/s)": f"speed_of_{bc}_in_xy",
            "In centre": f"within_boundary_static_{bc}_in_centre",
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
            sc, numbins, summaries_dir, "OFT_results", lambda s: _compute_summaries(s, pts)
        )

    sc_grouped = sc.groupby(tags=[group_tag])

    # ── Export ────────────────────────────────────────────────────────────────
    print("Exporting tables...")
    _shared.export_results_table(sc, summaries_dir, "OFT_results.csv")

    print("Exporting figures...")
    _shared.export_boxplots(
        sc_grouped,
        group_names,
        comparisons,
        figures_dir,
        metrics=[
            f"total_distance_{bc}",
            "time_in_centre",
            "distance_in_centre",
        ],
        group_tag=group_tag,
    )

    print("Running BFA...")
    _shared.export_bfa(sc_grouped, bfa_dir, _CLUSTER_COL, n_clusters, comparisons)

    print("Pipeline complete.")


# ── Feature computation ───────────────────────────────────────────────────────
def _compute_features(fc: p3b.FeaturesCollection, pts: dict) -> None:
    print("  Spatial boundaries...")
    bc = pts["bodycentre"]
    corner_pts = [pts[c] for c in _CORNERS]

    fc.each.define_static_boundary(corner_pts, name="oft")
    fc.each.define_static_boundary(
        corner_pts, scale_dim1=_CENTRE_SCALE, scale_dim2=_CENTRE_SCALE, name="centre"
    )
    fc.each.define_static_boundary(
        corner_pts, scale_dim1=_PERIPHERY_SCALE, scale_dim2=_PERIPHERY_SCALE, name="not_periphery"
    )
    for c in _CORNERS:
        fc.each.define_static_boundary(
            corner_pts,
            scale_dim1=_CORNER_SCALE,
            scale_dim2=_CORNER_SCALE,
            name=f"{c}_corner",
            anchor=pts[c],
        )

    in_centre = fc.each.within_boundary(bc, "centre")
    in_centre.store()

    (fc.each.within_boundary(bc, "oft") & ~fc.each.within_boundary(bc, "not_periphery")).store(
        "in_periphery"
    )

    in_corners = {c: fc.each.within_boundary(bc, f"{c}_corner") for c in _CORNERS}
    (in_corners["tl"] | in_corners["tr"] | in_corners["bl"] | in_corners["br"]).store("in_corner")
    fc.each.compose_state_from_booleans(in_corners).store("corner_state")

    dist_change = fc.each.distance_change(bc)
    (in_centre.astype("Int64") * dist_change).store("dist_change_bodycentre_in_centre")

    print("  Kinematic features...")

    _shared.compute_body_kinematics(fc, pts)

    for pt in ["nose", "neck", "bodycentre", "tailbase"]:
        fc.each.distance_to_boundary(pts[pt], "oft").store()


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
    sc.each.time_true(f"within_boundary_static_{bc}_in_centre").store("time_in_centre")
    sc.each.sum_column("dist_change_bodycentre_in_centre").store("distance_in_centre")
    sc.each.by_state("corner_state", all_states=_CORNERS).mean_column(f"speed_of_{bc}_in_xy").store(
        "mean_speed_by_corner"
    )
    sc.each.time_in_state(_CLUSTER_COL).store("time_in_cluster")
