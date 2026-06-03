"""Ultralytics-based pose tracker — writes yolo3r-format CSV.

Usage (standalone):
    python track.py <video> <output_csv> \\
        <model_folder>:<instance_type> [<model_folder>:<instance_type> ...]

Example:
    python track.py video.mp4 out.csv \\
        models/environment_main:oft \\
        models/mouse_top_main:mouse_top

The CSV format matches what py3r_behaviour's TrackingCollection.from_yolo3r()
expects: one row per frame, columns named
    {instance_type}.{instance_type}_0.{keypoint}.x/y/conf
and bbox columns
    {instance_type}.{instance_type}_0.x1/y1/x2/y2/conf

--- Extension points ---

To swap in a different model repository format, replace load_bohacek_spec()
with a function that returns a ModelSpec.  Everything else stays the same.

To support temporal models (multi-frame context), replace the per-frame
inference call in _fill_top_detection() — the CSV writing logic is unchanged.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
from ultralytics import YOLO

# ---------------------------------------------------------------------------
# Public data type
# ---------------------------------------------------------------------------


@dataclass
class ModelSpec:
    """Everything the tracker needs to know about one pose model.

    Replace load_bohacek_spec() to produce these from a different source;
    nothing else in this file needs to change.
    """

    model: YOLO
    name: str  # human-readable model name, e.g. "mouse_top_main"
    instance_type: str  # e.g. "mouse_top", "oft" — becomes the CSV column prefix
    keypoint_names: list[str]  # names indexed by YOLO keypoint index


# ---------------------------------------------------------------------------
# BohacekLabPoseModels-specific loading  (replace this block for other repos)
# ---------------------------------------------------------------------------


def load_bohacek_spec(model_folder: Path, instance_type: str) -> ModelSpec:
    """Load a ModelSpec from a BohacekLabPoseModels folder.

    model_folder: path to e.g. pose_estimation/mouse/mouse_top_main
    instance_type: which row-set to use from output_mapping.csv,
                   e.g. "mouse_top", "oft", "epm"
    """
    weights = model_folder / "weights" / "best.pt"
    if not weights.exists():
        raise RuntimeError(f"Weights not found: {weights}")

    mapping_csv = model_folder / "meta" / "output_mapping.csv"
    if not mapping_csv.exists():
        raise RuntimeError(f"output_mapping.csv not found: {mapping_csv}")

    entries: dict[int, str] = {}
    with open(mapping_csv) as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) != 3:
                continue
            itype, kp_name, kp_idx = parts[0], parts[1], int(parts[2])
            if itype == instance_type:
                entries[kp_idx] = kp_name

    if not entries:
        available = _available_instance_types(mapping_csv)
        raise RuntimeError(
            f"No keypoints found for instance_type='{instance_type}' in {mapping_csv}\n"
            f"Available types: {available}"
        )

    max_idx = max(entries.keys())
    keypoint_names = [entries.get(i, f"kp_{i}") for i in range(max_idx + 1)]

    return ModelSpec(
        model=YOLO(str(weights)),
        name=model_folder.name,
        instance_type=instance_type,
        keypoint_names=keypoint_names,
    )


def _available_instance_types(mapping_csv: Path) -> list[str]:
    seen: set[str] = set()
    with open(mapping_csv) as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) == 3:
                seen.add(parts[0])
    return sorted(seen)


# ---------------------------------------------------------------------------
# Core tracking logic
# ---------------------------------------------------------------------------


def track(
    video: Path,
    specs: list[ModelSpec],
    output_csv: Path,
    *,
    device: str = "auto",
    progress_cb=None,
) -> None:
    """Run tracking on a single video and write yolo3r-format CSV.

    Each model streams through the full video sequentially (better GPU cache
    utilisation than interleaving models per frame), writing a temp CSV.
    A final merge pass combines the temp CSVs row-by-row into the output CSV.

    device: "auto" (use CUDA if available), "cpu", "cuda", "cuda:0", "mps", etc.
    progress_cb: optional callable(frame_index: int) called after each frame.
    """
    import torch

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    half = device != "cpu"
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or None
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    _CHUNK = 500

    temp_paths: list[Path] = []
    try:
        # One pass per model — weights stay hot for the full video
        for spec_idx, spec in enumerate(specs):
            tmp = output_csv.with_suffix(f".tmp{spec_idx}.csv")
            temp_paths.append(tmp)
            prefix = f"{spec.instance_type}.{spec.instance_type}_0"
            tmp_cols = [
                "frame_index",
                f"{prefix}.x1",
                f"{prefix}.y1",
                f"{prefix}.x2",
                f"{prefix}.y2",
                f"{prefix}.conf",
            ] + [
                c
                for kp in spec.keypoint_names
                for c in (f"{prefix}.{kp}.x", f"{prefix}.{kp}.y", f"{prefix}.{kp}.conf")
            ]
            with open(tmp, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=tmp_cols, extrasaction="ignore")
                writer.writeheader()
                chunk: list = []
                frame_index = 0
                for result in spec.model.track(
                    str(video),
                    stream=True,
                    persist=True,
                    verbose=False,
                    device=device,
                    half=half,
                ):
                    chunk.append((frame_index, result))
                    frame_index += 1
                    if len(chunk) == _CHUNK:
                        _flush_chunk(writer, chunk, spec)
                        chunk.clear()
                        if progress_cb is not None:
                            progress_cb(frame_index)
                        else:
                            progress = f"{frame_index}/{total}" if total else str(frame_index)
                            print(f"{spec.name} frame {progress}")
                if chunk:
                    _flush_chunk(writer, chunk, spec)

        # Merge temp CSVs row-by-row — constant memory regardless of video length
        fieldnames = _make_fieldnames(specs)
        frame_index = 0
        tmp_files = [open(p, newline="") for p in temp_paths]
        try:
            readers = [csv.DictReader(f) for f in tmp_files]
            with open(output_csv, "w", newline="") as out:
                writer = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for rows in zip(*readers, strict=False):
                    merged: dict[str, object] = {
                        "frame_index": frame_index,
                        "max_dim.x": w,
                        "max_dim.y": h,
                    }
                    for r in rows:
                        merged.update(r)
                    writer.writerow(merged)
                    frame_index += 1
        finally:
            for f in tmp_files:
                f.close()

    finally:
        for p in temp_paths:
            p.unlink(missing_ok=True)

    if progress_cb is None:
        print(f"Done — {frame_index} frames → {output_csv}")


def _flush_chunk(writer: csv.DictWriter, chunk: list, spec: ModelSpec) -> None:
    for frame_index, result in chunk:
        row: dict[str, object] = {"frame_index": frame_index}
        _fill_top_detection(row, result, spec)
        writer.writerow(row)


def _make_fieldnames(specs: list[ModelSpec]) -> list[str]:
    names = ["frame_index", "max_dim.x", "max_dim.y"]
    for spec in specs:
        prefix = f"{spec.instance_type}.{spec.instance_type}_0"
        names += [f"{prefix}.x1", f"{prefix}.y1", f"{prefix}.x2", f"{prefix}.y2", f"{prefix}.conf"]
        for kp_name in spec.keypoint_names:
            names += [f"{prefix}.{kp_name}.x", f"{prefix}.{kp_name}.y", f"{prefix}.{kp_name}.conf"]
    return names


def _fill_top_detection(row: dict, result, spec: ModelSpec) -> None:
    """Write the highest-confidence detection for spec into row as instance _0.

    Missing detections leave those columns absent (empty in CSV).
    """
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return

    best = int(boxes.conf.argmax())
    prefix = f"{spec.instance_type}.{spec.instance_type}_0"

    x1, y1, x2, y2 = boxes.xyxy[best].tolist()
    row[f"{prefix}.x1"] = x1
    row[f"{prefix}.y1"] = y1
    row[f"{prefix}.x2"] = x2
    row[f"{prefix}.y2"] = y2
    row[f"{prefix}.conf"] = float(boxes.conf[best])

    kps = result.keypoints
    if kps is None or best >= len(kps.xy):
        return

    kp_xy = kps.xy[best].tolist()
    kp_conf = kps.conf[best].tolist() if kps.conf is not None else []

    for idx, name in enumerate(spec.keypoint_names):
        if idx < len(kp_xy):
            x, y = kp_xy[idx]
            row[f"{prefix}.{name}.x"] = x
            row[f"{prefix}.{name}.y"] = y
            row[f"{prefix}.{name}.conf"] = kp_conf[idx] if idx < len(kp_conf) else ""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video", type=Path, help="Input video file")
    parser.add_argument("output_csv", type=Path, help="Output CSV path")
    parser.add_argument(
        "--device",
        default="auto",
        help="Inference device: auto, cpu, cuda, cuda:0, mps (default: auto)",
    )
    parser.add_argument(
        "model_specs",
        nargs="+",
        metavar="FOLDER:INSTANCE_TYPE",
        help="Model folder and instance type, colon-separated",
    )
    args = parser.parse_args()

    specs = []
    for s in args.model_specs:
        if ":" not in s:
            print(f"Error: expected FOLDER:INSTANCE_TYPE, got '{s}'", file=sys.stderr)
            sys.exit(1)
        folder, instance_type = s.rsplit(":", 1)
        specs.append(load_bohacek_spec(Path(folder), instance_type))

    track(args.video, specs, args.output_csv, device=args.device)


if __name__ == "__main__":
    main()
