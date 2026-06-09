"""Open Field Test — py3r_behaviour analysis pipeline.

Receives per-group folders of YOLO3R tracking CSVs and runs:
    load → preprocess → QC plots → features → clustering → summary → export

Nothing in this file knows about the GUI, the tracker, or any other arena.
Progress is reported via print() — the caller captures stdout if needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
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

_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv"}

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


# ── Helpers ───────────────────────────────────────────────────────────────────
def _sanitize_group_name(name: str) -> str:
    """Mirror of from_groups() internal sanitization — used to reverse handle stems."""
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return sanitized or "group"


# ── Entry point ───────────────────────────────────────────────────────────────
def run(
    group_csv_files: dict[str, list[Path]],
    output_dir: Path,
    comparisons: list[tuple[str, str]] | None = None,
    group_video_files: dict[str, list[Path]] | None = None,
    numbins: int | None = None,
    n_clusters: int = _N_CLUSTERS,
) -> None:
    """Full OFT pipeline across all groups.

    Parameters
    ----------
    group_csv_files:
        ``{group_name: [Path, ...]}`` — each group's per-animal YOLO3R CSVs.
    output_dir:
        Root output folder.  Sub-folders are created automatically.
    comparisons:
        List of ``(group_a, group_b)`` pairs for statistical annotations and BFA plots.
        Empty or None → pipeline runs without pairwise stats.
    group_video_files:
        ``{group_name: [Path, ...]}`` — source videos for QC animation overlay.
        None → animations render without video background.
    numbins:
        Split each animal's session into this many equal-frame-count bins and
        write per-bin summary CSVs alongside the whole-session results.
        None → no binning.
    """
    import matplotlib

    matplotlib.use("Agg")  # non-interactive backend — safe in QThread

    comparisons = comparisons or []

    qc_dir = output_dir / "qc" / "trajectories"
    features_dir = output_dir / "features"
    summaries_dir = output_dir / "summaries"
    figures_dir = output_dir / "figures"
    bfa_dir = output_dir / "bfa"
    for d in [qc_dir, features_dir, summaries_dir, figures_dir, bfa_dir]:
        d.mkdir(parents=True, exist_ok=True)

    group_names = list(group_csv_files.keys())

    # ── Load ──────────────────────────────────────────────────────────────────
    print("Loading tracking data…")
    tc_all = p3b.TrackingCollection.from_groups(group_csv_files, fps=_FPS)

    # ── Preprocess ────────────────────────────────────────────────────────────
    print("Preprocessing…")
    tc_all.each.filter_likelihood(threshold=_LIKELIHOOD_THRESHOLD)
    tc_all.each.interpolate(limit=_INTERPOLATION_LIMIT)
    tc_all.each.smooth_all(window=_SMOOTH_WINDOW, method="mean")
    tc_all.each.rescale_by_known_distance(
        point1="tl", point2="br", distance_in_metres=_ARENA_SIZE_M
    )

    # ── QC trajectory plots ───────────────────────────────────────────────────
    print("Saving trajectory QC plots…")
    tc_grouped = tc_all.groupby(tags=[_GROUP_TAG])
    for group_name in group_names:
        group_qc_dir = qc_dir / group_name
        group_qc_dir.mkdir(parents=True, exist_ok=True)
        tc_grouped[(group_name,)].each.plot(
            trajectories=[_BODY_CENTRE],
            static=_CORNERS,
            lines=_CORNER_LINES,
            show=False,
            savedir=group_qc_dir,
        )

    # ── Features ──────────────────────────────────────────────────────────────
    print("Computing features…")
    fc = tc_all.to_features()
    _compute_features(fc)

    # ── Clustering ────────────────────────────────────────────────────────────
    print(f"Clustering (k={n_clusters})…")
    _cluster(fc, n_clusters)

    print("Rendering QC animations…")
    _export_animations(fc, group_names, group_video_files or {}, output_dir)

    print("Saving features…")
    fc.save(str(features_dir), data_format="parquet", overwrite=True)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("Computing summaries…")
    sc = fc.to_summary()
    _compute_summaries(sc)

    if numbins:
        print(f"Computing {numbins}-bin summaries…")
        whole_df, _ = sc.to_df(include_tags=True, series="separate")
        bin_dfs = {"whole": whole_df}
        for i, bin_sc in enumerate(sc.make_bins(numbins)):
            print(f"  Bin {i + 1}/{numbins}…")
            _compute_summaries(bin_sc)
            bin_df, _ = bin_sc.to_df(include_tags=True, series="separate")
            bin_dfs[f"bin_{i + 1:02d}"] = bin_df
        tall = p3b.SummaryCollection.collate_bin_dfs(bin_dfs, format="tall")
        wide = p3b.SummaryCollection.collate_bin_dfs(bin_dfs, format="wide")
        tall.to_csv(summaries_dir / "OFT_results_binned_tall.csv")
        wide.to_csv(summaries_dir / "OFT_results_binned_wide.csv")
        print(f"  Saved binned CSVs ({numbins} bins, tall + wide).")

    sc_grouped = sc.groupby(tags=[_GROUP_TAG])

    # ── Export ────────────────────────────────────────────────────────────────
    print("Exporting tables…")
    _export_tables(sc, summaries_dir)

    print("Exporting figures…")
    _export_figures(sc_grouped, group_names, comparisons, figures_dir)

    print("Running BFA…")
    _export_bfa(sc_grouped, bfa_dir, n_clusters)

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


# ── QC animations ────────────────────────────────────────────────────────────
def _export_animations(
    fc: p3b.FeaturesCollection,
    group_names: list[str],
    group_video_files: dict[str, list[Path]],
    output_dir: Path,
) -> None:
    anim_dir = output_dir / "qc" / "animations"
    anim_dir.mkdir(parents=True, exist_ok=True)

    fc_grouped = fc.groupby(tags=[_GROUP_TAG])

    for group_name in group_names:
        group_fc = fc_grouped.get((group_name,))
        if not group_fc:
            continue

        feat = next(iter(group_fc.values()))

        video_path = None
        if group_name in group_video_files:
            safe = _sanitize_group_name(group_name)
            video_stem = feat.handle.removesuffix(f"_{safe}")
            video_path = next(
                (
                    p
                    for p in group_video_files[group_name]
                    if p.stem == video_stem and p.suffix.lower() in _VIDEO_EXTS
                ),
                None,
            )

        out_path = anim_dir / f"{group_name}.mp4"
        print(f"  {group_name} ({'with video' if video_path else 'no video'})…")

        try:
            has_video = video_path is not None
            stream = feat.animation_stream(
                points=_ANIM_MOUSE_POINTS + _CORNERS,
                lines=_ANIM_BODY_LINES + _CORNER_LINES,
                boundaries=["oft", "centre"],
                features={
                    "Speed (m/s)": f"speed_of_{_BODY_CENTRE}_in_xy",
                    "In centre": "within_boundary_static_bodycentre_in_centre",
                    "Cluster": _CLUSTER_COL,
                },
                pixel_coords=has_video,
                undo_meta_scaling=has_video,
                style=_ANIM_STYLE,
            )
            save_kwargs = {"out_path": str(out_path)}
            if has_video:
                save_kwargs["video_path"] = str(video_path)
            stream.save(**save_kwargs)
        except Exception as exc:
            print(f"  Warning: animation failed for {group_name}: {exc}")


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


# ── Table export ──────────────────────────────────────────────────────────────
def _export_tables(
    sc: p3b.SummaryCollection,
    summaries_dir: Path,
) -> None:
    summary_df, _ = sc.to_df(include_tags=True, series="separate")
    summary_df.to_csv(summaries_dir / "OFT_results.csv")
    print("  Saved OFT_results.csv.")


# ── Figure export ─────────────────────────────────────────────────────────────
def _export_figures(
    sc_grouped: p3b.SummaryCollection,
    group_names: list[str],
    comparisons: list[tuple[str, str]],
    figures_dir: Path,
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
    ]:
        print(f"  Plotting {metric}…")
        try:
            fig, ax, _ = sc_grouped.snsbox(
                metric,
                group_order=group_order,
                annotate=annotate,
                show=False,
            )
            ax.tick_params(axis="x", rotation=45)
            for lbl in ax.get_xticklabels():
                lbl.set_ha("right")
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", metric).strip("_").lower()
            fig.savefig(figures_dir / f"{slug}_boxplot.png", dpi=150, bbox_inches="tight")
            plt.close(fig)
        except Exception as exc:
            print(f"  Warning: could not plot {metric}: {exc}")


# ── BFA export ────────────────────────────────────────────────────────────────
def _export_bfa(
    sc_grouped: p3b.SummaryCollection,
    bfa_dir: Path,
    n_clusters: int,
) -> None:
    import matplotlib.pyplot as plt

    try:
        print("  Computing BFA transition statistics…")
        bfa_results = sc_grouped.bfa(
            column=_CLUSTER_COL,
            all_states=list(range(n_clusters)),
            random_state=_BFA_RANDOM_STATE,
        )
        bfa_stats = p3b.SummaryCollection.bfa_stats(bfa_results)

        with open(bfa_dir / "bfa_results.json", "w") as f:
            json.dump(bfa_results, f, indent=4)
        with open(bfa_dir / "bfa_stats.json", "w") as f:
            json.dump(bfa_stats, f, indent=4)

        print("  Plotting BFA histograms…")
        p3b.SummaryCollection.plot_bfa_results(
            bfa_results,
            add_stats=True,
            stats=bfa_stats,
            bins=20,
            save_dir=bfa_dir,
            show=False,
        )
        plt.close("all")

        try:
            print("  Plotting chord diagrams…")
            sc_grouped.plot_chord(
                column=_CLUSTER_COL,
                all_states=list(range(n_clusters)),
                save_dir=bfa_dir,
                show=False,
            )
            plt.close("all")
        except ImportError:
            print("  Note: pycirclize not installed — chord diagrams skipped.")

        try:
            print("  Plotting transition UMAP…")
            sc_grouped.plot_transition_umap(
                column=_CLUSTER_COL,
                all_states=list(range(n_clusters)),
                random_state=_BFA_RANDOM_STATE,
                save_dir=str(bfa_dir),
                show=False,
            )
            plt.close("all")
        except ImportError:
            print("  Note: umap-learn not installed — UMAP skipped.")

    except Exception as exc:
        print(f"  Warning: BFA failed: {exc}")
