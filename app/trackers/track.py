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

To support temporal models (multi-frame context), replace the array-filling
logic in track() — the CSV conversion pass is unchanged.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
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

_REPORT_INTERVAL = 500


def track(
    video: Path,
    specs: list[ModelSpec],
    output_csv: Path,
    *,
    device: str = "auto",
    progress_cb=None,
) -> None:
    """Run tracking on a single video and write yolo3r-format CSV.

    Each model makes one sequential pass, collecting detections into
    pre-allocated numpy arrays (GIL released during tensor copies so the
    GPU pipeline stays uninterrupted).  A final pass converts the arrays
    to yolo3r CSV — no temp files, no threading.

    device: "auto" (use CUDA if available), "cpu", "cuda", "cuda:0", "mps", etc.
    progress_cb: optional callable(frame_index: int) called every REPORT_INTERVAL frames.
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
    vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    n_alloc = total or 200_000

    # One pass per model — weights stay hot, hot loop does only array assignments
    spec_arrays: list[tuple[ModelSpec, np.ndarray, np.ndarray, np.ndarray]] = []
    for spec in specs:
        bboxes = np.full((n_alloc, 4), np.nan, dtype=np.float32)  # x1 y1 x2 y2
        bbox_conf = np.full(n_alloc, np.nan, dtype=np.float32)
        kps: np.ndarray | None = None  # allocated from first result: (n_alloc, n_kp_model, 3)

        frame_count = 0
        for result in spec.model.track(
            str(video),
            stream=True,
            persist=True,
            verbose=False,
            device=device,
            half=half,
            batch=8,
        ):
            boxes = result.boxes
            if boxes is not None and len(boxes) > 0:
                best = int(boxes.conf.argmax())
                bboxes[frame_count] = boxes.xyxy[best].cpu()
                bbox_conf[frame_count] = boxes.conf[best].cpu()
                if result.keypoints is not None and best < len(result.keypoints.data):
                    kp_data = result.keypoints.data[best]
                    if kps is None:
                        kps = np.full((n_alloc, kp_data.shape[0], 3), np.nan, dtype=np.float32)
                    kps[frame_count] = kp_data.cpu()

            frame_count += 1
            if frame_count % _REPORT_INTERVAL == 0:
                if progress_cb is not None:
                    progress_cb(frame_count)
                else:
                    progress = f"{frame_count}/{total}" if total else str(frame_count)
                    print(f"{spec.name} frame {progress}")

        if kps is None:
            kps = np.full((frame_count, 0, 3), np.nan, dtype=np.float32)
        spec_arrays.append((spec, bboxes[:frame_count], bbox_conf[:frame_count], kps[:frame_count]))

    # Single conversion pass → yolo3r CSV
    frame_count = spec_arrays[0][1].shape[0]
    fieldnames = _make_fieldnames(specs)

    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for i in range(frame_count):
            row: dict[str, object] = {
                "frame_index": i,
                "max_dim.x": vid_w,
                "max_dim.y": vid_h,
            }
            for spec, bboxes, bbox_conf, kps in spec_arrays:
                if np.isnan(bbox_conf[i]):
                    continue
                prefix = f"{spec.instance_type}.{spec.instance_type}_0"
                row[f"{prefix}.x1"] = float(bboxes[i, 0])
                row[f"{prefix}.y1"] = float(bboxes[i, 1])
                row[f"{prefix}.x2"] = float(bboxes[i, 2])
                row[f"{prefix}.y2"] = float(bboxes[i, 3])
                row[f"{prefix}.conf"] = float(bbox_conf[i])
                for ki, kp_name in enumerate(spec.keypoint_names):
                    if not np.isnan(kps[i, ki, 0]):
                        row[f"{prefix}.{kp_name}.x"] = float(kps[i, ki, 0])
                        row[f"{prefix}.{kp_name}.y"] = float(kps[i, ki, 1])
                        row[f"{prefix}.{kp_name}.conf"] = float(kps[i, ki, 2])
            writer.writerow(row)

    if progress_cb is None:
        print(f"Done — {frame_count} frames → {output_csv}")


def _make_fieldnames(specs: list[ModelSpec]) -> list[str]:
    names = ["frame_index", "max_dim.x", "max_dim.y"]
    for spec in specs:
        prefix = f"{spec.instance_type}.{spec.instance_type}_0"
        names += [f"{prefix}.x1", f"{prefix}.y1", f"{prefix}.x2", f"{prefix}.y2", f"{prefix}.conf"]
        for kp_name in spec.keypoint_names:
            names += [f"{prefix}.{kp_name}.x", f"{prefix}.{kp_name}.y", f"{prefix}.{kp_name}.conf"]
    return names


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
