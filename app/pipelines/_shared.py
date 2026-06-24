"""Generic pipeline plumbing shared across arena pipelines.

Plain, opt-in helper functions — a pipeline imports and calls only what it
needs. Nothing here is part of the GUI/pipeline contract; the GUI never
imports this module.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import py3r.behaviour as p3b

# ── Shared defaults ─────────────────────────────────────────────────────────
INTERPOLATION_LIMIT = 5
SMOOTH_WINDOW = 3


# ── Loading (dumb format parser — does NO science) ───────────────────────────
def load(
    manifest: list[tuple[str, str, Path]],
    video_paths: dict[str, Path],
    loader: dict,
) -> p3b.TrackingCollection:
    """Structural format parse only: turn the per-recording files into a
    TrackingCollection (parser chosen by ``loader['format']``) and tag each
    recording with its group and video path. Reads the confidence column into
    place but never thresholds, strips, or smooths — that is all script-side
    (see ``preprocess``)."""
    import py3r.behaviour as p3b

    fmt = loader.get("format", "yolo3r")
    fps = loader["fps"]
    files = {handle: str(path) for handle, _group, path in manifest}
    if fmt == "yolo3r":
        tc_all = p3b.TrackingCollection.from_yolo3r(files, fps=fps)
    elif fmt == "dlc":
        tc_all = p3b.TrackingCollection.from_dlc(files, fps=fps)
    else:
        raise ValueError(f"unknown loader format: {fmt!r} (expected 'yolo3r' or 'dlc')")

    group_tag = loader.get("group_tag", "group")
    for handle, group_name, _path in manifest:
        tc_all[handle].tags[group_tag] = group_name
        if handle in video_paths:
            tc_all[handle].meta["video_path"] = str(video_paths[handle])
    return tc_all


# ── Preprocessing (science: cardinality strip + likelihood + smooth) ─────────
def preprocess(
    tc: p3b.TrackingCollection,
    likelihood_min: float,
    *,
    limit: int = INTERPOLATION_LIMIT,
    window: int = SMOOTH_WINDOW,
) -> None:
    """Strip instance qualifiers (single-instance assumption — see task 031),
    filter low-confidence points at ``likelihood_min``, interpolate gaps, and
    smooth, in place. The threshold is a script deployment param because its
    meaning is format-dependent (DLC likelihood != YOLO3R confidence)."""
    tc.each.strip_column_names()
    tc.each.filter_likelihood(threshold=likelihood_min)
    tc.each.interpolate(limit=limit)
    tc.each.smooth_all(window=window, method="mean")


# ── Body kinematics ──────────────────────────────────────────────────────────
def compute_body_kinematics(fc: p3b.FeaturesCollection, pts: dict[str, str]) -> None:
    """Speed / azimuth deviation / distance-between / area-of-boundary features
    over the standard mouse keypoint set. Point names are resolved through *pts*
    (canonical -> actual column) so a lab whose model renames a point can remap
    it via the config's ``[script.point_map]`` without touching this code."""
    for pt in ["nose", "neck", "earr", "earl", "bodycentre", "hipl", "hipr", "tailbase"]:
        fc.each.speed(pts[pt]).store()

    for base, p1, p2 in [
        ("tailbase", "hipr", "hipl"),
        ("bodycentre", "tailbase", "neck"),
        ("neck", "bodycentre", "headcentre"),
        ("headcentre", "earr", "earl"),
    ]:
        fc.each.azimuth_deviation(pts[base], pts[p1], pts[p2]).store()

    for p1, p2 in [
        ("nose", "headcentre"),
        ("neck", "headcentre"),
        ("neck", "bodycentre"),
        ("bcr", "bodycentre"),
        ("bcl", "bodycentre"),
        ("tailbase", "bodycentre"),
        ("tailbase", "hipr"),
        ("tailbase", "hipl"),
        ("bcr", "hipr"),
        ("bcl", "hipl"),
        ("bcl", "earl"),
        ("bcr", "earr"),
        ("nose", "earr"),
        ("nose", "earl"),
    ]:
        fc.each.distance_between(pts[p1], pts[p2]).store()

    for boundary_name, pt_names in [
        ("mouse_rear", ["tailbase", "hipr", "hipl"]),
        ("mouse_mid", ["hipr", "hipl", "bcl", "bcr"]),
        ("mouse_front", ["bcr", "earr", "earl", "bcl"]),
        ("mouse_face", ["earr", "nose", "earl"]),
    ]:
        fc.each.define_dynamic_boundary([pts[p] for p in pt_names], name=boundary_name)
        fc.each.area_of_boundary(boundary_name).store()


# ── Output layout ────────────────────────────────────────────────────────────
def make_output_dirs(output_dir: Path) -> dict[str, Path]:
    """Create and return the standard pipeline output directory layout."""
    dirs = {
        "qc_trajectories": output_dir / "qc" / "trajectories",
        "qc_animations": output_dir / "qc" / "animations",
        "features": output_dir / "features",
        "summaries": output_dir / "summaries",
        "figures": output_dir / "figures",
        "bfa": output_dir / "bfa",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


# ── QC trajectory plots ──────────────────────────────────────────────────────
def plot_trajectory_qc(
    tc_grouped: p3b.TrackingCollection,
    group_names: list[str],
    qc_dir: Path,
    *,
    trajectories: list[str],
    static: list[str],
    lines: list[tuple[str, str]],
) -> None:
    for group_name in group_names:
        group_qc_dir = qc_dir / group_name
        group_qc_dir.mkdir(parents=True, exist_ok=True)
        tc_grouped[(group_name,)].each.plot(
            trajectories=trajectories,
            static=static,
            lines=lines,
            show=False,
            savedir=group_qc_dir,
        )


# ── QC animations ────────────────────────────────────────────────────────────
def export_animations(
    fc: p3b.FeaturesCollection,
    group_names: list[str],
    output_dir: Path,
    *,
    points: list[str],
    lines: list[tuple[str, str]],
    features: dict[str, str],
    style: dict,
    boundaries: list[str] | None = None,
    group_tag: str = "group",
) -> None:
    anim_dir = output_dir / "qc" / "animations"
    anim_dir.mkdir(parents=True, exist_ok=True)

    fc_grouped = fc.groupby(tags=[group_tag])

    for group_name in group_names:
        group_fc = fc_grouped.get((group_name,))
        if not group_fc:
            continue

        feat = next(iter(group_fc.values()))
        # Features.__init__ doesn't propagate arbitrary tracking meta (only
        # "usermeta"), but keeps a live ref to the Tracking object — read
        # video_path through that.
        video_path = feat.tracking.meta.get("video_path")

        out_path = anim_dir / f"{group_name}.mp4"
        print(f"  {group_name} ({'with video' if video_path else 'no video'})...")

        try:
            has_video = video_path is not None
            stream = feat.animation_stream(
                points=points,
                lines=lines,
                boundaries=boundaries,
                features=features,
                pixel_coords=has_video,
                undo_meta_scaling=has_video,
                style=style,
            )
            save_kwargs = {"out_path": str(out_path)}
            if has_video:
                save_kwargs["video_path"] = str(video_path)
            stream.save(**save_kwargs)
        except Exception as exc:
            print(f"  Warning: animation failed for {group_name}: {exc}")


# ── Table export ─────────────────────────────────────────────────────────────
def export_results_table(
    sc: p3b.SummaryCollection,
    summaries_dir: Path,
    filename: str,
) -> None:
    summary_df, _ = sc.to_df(include_tags=True, series="separate")
    summary_df.to_csv(summaries_dir / filename)
    print(f"  Saved {filename}.")


# ── Binned summaries ─────────────────────────────────────────────────────────
def export_binned_summaries(
    sc: p3b.SummaryCollection,
    numbins: int,
    summaries_dir: Path,
    filename_prefix: str,
    compute_summaries_fn,
) -> None:
    import py3r.behaviour as p3b

    whole_df, _ = sc.to_df(include_tags=True, series="separate")
    bin_dfs = {"whole": whole_df}
    for i, bin_sc in enumerate(sc.make_bins(numbins)):
        print(f"  Bin {i + 1}/{numbins}...")
        compute_summaries_fn(bin_sc)
        bin_df, _ = bin_sc.to_df(include_tags=True, series="separate")
        bin_dfs[f"bin_{i + 1:02d}"] = bin_df
    tall = p3b.SummaryCollection.collate_bin_dfs(bin_dfs, format="tall")
    wide = p3b.SummaryCollection.collate_bin_dfs(bin_dfs, format="wide")
    tall.to_csv(summaries_dir / f"{filename_prefix}_binned_tall.csv")
    wide.to_csv(summaries_dir / f"{filename_prefix}_binned_wide.csv")
    print(f"  Saved binned CSVs ({numbins} bins, tall + wide).")


# ── Figure export ────────────────────────────────────────────────────────────
def export_boxplots(
    sc_grouped: p3b.SummaryCollection,
    group_names: list[str],
    comparisons: list[tuple[str, str]],
    figures_dir: Path,
    metrics: list[str],
    group_tag: str = "group",
) -> None:
    import matplotlib.pyplot as plt

    group_order = {group_tag: group_names}
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

    for metric in metrics:
        print(f"  Plotting {metric}...")
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


# ── BFA export ───────────────────────────────────────────────────────────────
def export_bfa(
    sc_grouped: p3b.SummaryCollection,
    bfa_dir: Path,
    cluster_col: str,
    n_clusters: int,
    comparisons: list[tuple[str, str]],
    random_state: int = 42,
) -> None:
    import json

    import matplotlib.pyplot as plt
    import py3r.behaviour as p3b

    try:
        print("  Computing BFA transition statistics...")
        bfa_results = sc_grouped.bfa(
            column=cluster_col,
            all_states=list(range(n_clusters)),
            pairs=comparisons or None,
            random_state=random_state,
        )
        bfa_stats = p3b.SummaryCollection.bfa_stats(bfa_results)

        with open(bfa_dir / "bfa_results.json", "w") as f:
            json.dump(bfa_results, f, indent=4)
        with open(bfa_dir / "bfa_stats.json", "w") as f:
            json.dump(bfa_stats, f, indent=4)

        print("  Plotting BFA histograms...")
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
            print("  Plotting chord diagrams...")
            sc_grouped.plot_chord(
                column=cluster_col,
                all_states=list(range(n_clusters)),
                save_dir=bfa_dir,
                show=False,
            )
            plt.close("all")
        except ImportError:
            print("  Note: pycirclize not installed - chord diagrams skipped.")

        try:
            print("  Plotting transition UMAP...")
            sc_grouped.plot_transition_umap(
                column=cluster_col,
                all_states=list(range(n_clusters)),
                random_state=random_state,
                save_dir=str(bfa_dir),
                show=False,
            )
            plt.close("all")
        except ImportError:
            print("  Note: umap-learn not installed - UMAP skipped.")

    except Exception as exc:
        print(f"  Warning: BFA failed: {exc}")
