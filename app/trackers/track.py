"""Ultralytics-based pose tracker — writes yolo3r-format CSV.

Usage (standalone):
    python track.py <video> <output_csv> \\
        --model '{"model": "/path/to/folder",
                  "instances": [{"type": "oft", "max": 1}],
                  "stride": [30, "ffill"], "batch": 4}' \\
        --model '{"model": "/path/to/folder",
                  "instances": [{"type": "mouse_top", "max": 1}],
                  "batch": 96}'

The CSV format matches what py3r_behaviour's TrackingCollection.from_yolo3r() expects:
    {instance_type}.{instance_type}_{slot}.{keypoint}.x/y/conf
    {instance_type}.{instance_type}_{slot}.x1/y1/x2/y2/conf

Model config fields:
    model      — path to a model folder (<folder>/best.pt, <folder>/output_mapping.csv)
    instances  — list of {"type": str, "max": int}
                   max: number of output slots (_0.._N-1); up to 4x max track
                   IDs are buffered internally, best max selected post-hoc.
                   If fewer tracks than max exist, remaining slots are all-NaN.
    stride     — optional [interval, fill]: run every N frames, fill skipped
                   frames with "ffill" (forward-fill) or "blank" (leave NaN)
    batch      — inference batch size (default 1 if omitted)
    tracker    — optional dict of BoT-SORT param overrides (e.g.
                   {"track_buffer": 90}), layered on ultralytics' defaults

Post-hoc track selection: after a full model pass, detected track IDs are
ranked by total frames present; the top `max` become stable output slots.

--- Extension points ---

To swap in a different model repository format, replace load_model_spec() with
a function that returns a ModelSpec.  Everything else stays the same.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

_MAX_INSTANCES_LIMIT = 16  # hard cap on max per instance type; raise if genuinely needed


@dataclass
class InstanceSpec:
    instance_type: str
    keypoint_names: list[str]
    max_instances: int


@dataclass
class ModelSpec:
    model: YOLO
    name: str
    instances: list[InstanceSpec]
    stride: int = 1
    stride_fill: str | None = None  # "ffill" or None
    batch: int = 1
    tracker: str = "botsort.yaml"  # built-in name, or a temp yaml path with overrides


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _build_tracker_config(overrides: dict | None) -> str:
    """Return the tracker config to pass to model.track(): the built-in
    BoT-SORT name if there are no overrides, else a temp yaml with the
    overrides layered on top of the BoT-SORT defaults (e.g. track_buffer,
    match_thresh — see ultralytics/cfg/trackers/botsort.yaml for the full
    set). The temp file is intentionally left on disk; it's a few bytes in
    the OS temp dir and track.py is a short-lived subprocess."""
    if not overrides:
        return "botsort.yaml"

    from ultralytics.utils import YAML
    from ultralytics.utils.checks import check_yaml

    config = YAML.load(check_yaml("botsort.yaml"))
    config.update(overrides)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="py3r_tracker_", delete=False
    )
    tmp.close()
    YAML.save(tmp.name, config)
    return tmp.name


def load_model_spec(
    model_folder: Path,
    instance_configs: list[dict],
    *,
    stride_tuple=None,
    batch: int = 1,
    tracker_overrides: dict | None = None,
) -> ModelSpec:
    """Load a ModelSpec from a model folder.

    instance_configs: list of {"type": str, "max": int}
    stride_tuple:     (interval, fill_mode) or None
    tracker_overrides: dict of BoT-SORT param overrides (e.g. {"track_buffer": 90}),
                        or None to use ultralytics' defaults unchanged
    """
    weights = model_folder / "best.pt"
    if not weights.exists():
        raise RuntimeError(f"Weights not found: {weights}")

    mapping_csv = model_folder / "output_mapping.csv"
    if not mapping_csv.exists():
        raise RuntimeError(f"output_mapping.csv not found: {mapping_csv}")

    all_entries: dict[str, dict[int, str]] = defaultdict(dict)
    with open(mapping_csv) as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) != 3:
                continue
            itype, kp_name, kp_idx = parts[0], parts[1], int(parts[2])
            all_entries[itype][kp_idx] = kp_name

    instances = []
    for ic in instance_configs:
        itype = ic["type"]
        max_inst = ic["max"]
        if max_inst > _MAX_INSTANCES_LIMIT:
            raise ValueError(
                f"instance_type='{itype}': max={max_inst} exceeds limit of {_MAX_INSTANCES_LIMIT}. "
                f"Increase _MAX_INSTANCES_LIMIT in track.py if this is intentional."
            )
        entries = all_entries.get(itype, {})
        if not entries:
            available = sorted(all_entries.keys())
            raise RuntimeError(
                f"No keypoints found for instance_type='{itype}' in {mapping_csv}\n"
                f"Available types: {available}"
            )
        max_idx = max(entries.keys())
        kp_names = [entries.get(i, f"kp_{i}") for i in range(max_idx + 1)]
        instances.append(InstanceSpec(itype, kp_names, max_inst))

    stride = 1
    fill = None
    if stride_tuple is not None:
        stride, fill = stride_tuple[0], stride_tuple[1]

    return ModelSpec(
        model=YOLO(str(weights)),
        name=model_folder.name,
        instances=instances,
        stride=stride,
        stride_fill=fill,
        batch=batch,
        tracker=_build_tracker_config(tracker_overrides),
    )


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

    Each ModelSpec makes one sequential pass.  All detected track IDs (up to
    4 * max_instances per instance type) are collected, then the top
    max_instances per instance type are selected post-hoc by total frames
    present and written as stable _0/_1/... slots.  If fewer tracks than
    max_instances exist, the remaining slots are all-NaN columns.

    device: "auto" (CUDA if available), "cpu", "cuda", "cuda:0", "mps", etc.
    progress_cb: optional callable(video_frame_index: int).
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

    n_alloc = (total + 1) if total else 200_000

    # flat list of output slots across all models and instance types
    # each entry: (instance_type, slot_idx, kp_names, bboxes, bbox_conf, kps)
    output_slots: list[tuple[str, int, list[str], np.ndarray, np.ndarray, np.ndarray]] = []
    last_frame_seen = 0

    for spec in specs:
        # {instance_type: {track_id: {"frames", "bboxes", "confs", "kps"}}}
        track_data: dict[str, dict[int, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"frames": [], "bboxes": [], "confs": [], "kps": []})
        )
        buffer_caps = {inst.instance_type: 4 * inst.max_instances for inst in spec.instances}

        stream_idx = 0

        for result in spec.model.track(
            str(video),
            stream=True,
            persist=True,
            verbose=False,
            device=device,
            half=half,
            batch=spec.batch,
            vid_stride=spec.stride,
            tracker=spec.tracker,
        ):
            video_frame_idx = stream_idx * spec.stride
            last_frame_seen = max(last_frame_seen, video_frame_idx)

            boxes = result.boxes
            if boxes is not None and boxes.id is not None and len(boxes) > 0:
                for i in range(len(boxes)):
                    cls_id = int(boxes.cls[i])
                    class_name = spec.model.names.get(cls_id)
                    if class_name is None:
                        continue

                    inst_spec = next(
                        (s for s in spec.instances if s.instance_type == class_name), None
                    )
                    if inst_spec is None:
                        continue

                    type_tracks = track_data[class_name]
                    tid = int(boxes.id[i])
                    buf_cap = buffer_caps[class_name]

                    if tid not in type_tracks:
                        if len(type_tracks) >= buf_cap:
                            print(
                                f"Warning: {spec.name}/{class_name} exceeded internal buffer "
                                f"({buf_cap} track IDs); new track {tid} ignored. "
                                f"Consider increasing max for this instance type.",
                                file=sys.stderr,
                            )
                            continue

                    td = type_tracks[tid]
                    td["frames"].append(video_frame_idx)
                    td["bboxes"].append(boxes.xyxy[i].cpu().numpy())
                    td["confs"].append(float(boxes.conf[i].cpu()))

                    kp = None
                    if result.keypoints is not None and i < len(result.keypoints.data):
                        kp = result.keypoints.data[i].cpu().numpy()
                    td["kps"].append(kp)

            stream_idx += 1

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        for inst_spec in spec.instances:
            itype = inst_spec.instance_type
            tracks = track_data.get(itype, {})
            n_kp = len(inst_spec.keypoint_names)

            ranked = sorted(tracks.items(), key=lambda kv: len(kv[1]["frames"]), reverse=True)
            selected = ranked[: inst_spec.max_instances]

            for slot_idx in range(inst_spec.max_instances):
                bboxes = np.full((n_alloc, 4), np.nan, dtype=np.float32)
                bbox_conf = np.full(n_alloc, np.nan, dtype=np.float32)
                kps = np.full((n_alloc, n_kp, 3), np.nan, dtype=np.float32)

                if slot_idx < len(selected):
                    _tid, td = selected[slot_idx]
                    for frame_idx, bbox, conf, kp in zip(
                        td["frames"], td["bboxes"], td["confs"], td["kps"], strict=False
                    ):
                        if frame_idx < n_alloc:
                            bboxes[frame_idx] = bbox
                            bbox_conf[frame_idx] = conf
                            if kp is not None and kp.shape[0] >= n_kp:
                                kps[frame_idx] = kp[:n_kp]

                    if spec.stride > 1 and spec.stride_fill == "ffill":
                        _ffill_arrays(bboxes, bbox_conf, kps)

                output_slots.append(
                    (itype, slot_idx, inst_spec.keypoint_names, bboxes, bbox_conf, kps)
                )

    if not output_slots:
        raise RuntimeError("No output slots produced — check model paths and instance types")

    frame_count = total if total else (last_frame_seen + 1)
    fieldnames = _make_fieldnames(output_slots)

    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for i in range(frame_count):
            row: dict[str, object] = {
                "frame_index": i,
                "max_dim.x": vid_w,
                "max_dim.y": vid_h,
            }
            for itype, slot, kp_names, bboxes, bbox_conf, kps in output_slots:
                if i >= len(bbox_conf) or np.isnan(bbox_conf[i]):
                    continue
                prefix = f"{itype}.{itype}_{slot}"
                row[f"{prefix}.x1"] = float(bboxes[i, 0])
                row[f"{prefix}.y1"] = float(bboxes[i, 1])
                row[f"{prefix}.x2"] = float(bboxes[i, 2])
                row[f"{prefix}.y2"] = float(bboxes[i, 3])
                row[f"{prefix}.conf"] = float(bbox_conf[i])
                for ki, kp_name in enumerate(kp_names):
                    if not np.isnan(kps[i, ki, 0]):
                        row[f"{prefix}.{kp_name}.x"] = float(kps[i, ki, 0])
                        row[f"{prefix}.{kp_name}.y"] = float(kps[i, ki, 1])
                        row[f"{prefix}.{kp_name}.conf"] = float(kps[i, ki, 2])
            writer.writerow(row)

    if progress_cb is None:
        print(f"Done - {frame_count} frames -> {output_csv}")


def _ffill_arrays(bboxes: np.ndarray, bbox_conf: np.ndarray, kps: np.ndarray) -> None:
    last = -1
    for i in range(len(bbox_conf)):
        if not np.isnan(bbox_conf[i]):
            last = i
        elif last >= 0:
            bbox_conf[i] = bbox_conf[last]
            bboxes[i] = bboxes[last]
            kps[i] = kps[last]


def _make_fieldnames(
    output_slots: list[tuple[str, int, list[str], np.ndarray, np.ndarray, np.ndarray]],
) -> list[str]:
    names = ["frame_index", "max_dim.x", "max_dim.y"]
    for itype, slot, kp_names, *_ in output_slots:
        prefix = f"{itype}.{itype}_{slot}"
        names += [
            f"{prefix}.x1",
            f"{prefix}.y1",
            f"{prefix}.x2",
            f"{prefix}.y2",
            f"{prefix}.conf",
        ]
        for kp_name in kp_names:
            names += [
                f"{prefix}.{kp_name}.x",
                f"{prefix}.{kp_name}.y",
                f"{prefix}.{kp_name}.conf",
            ]
    return names


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--device", default="auto")
    parser.add_argument(
        "--model",
        action="append",
        dest="model_jsons",
        metavar="JSON",
        help="JSON model config dict; repeatable, one per model",
    )
    args = parser.parse_args()

    if not args.model_jsons:
        parser.error("At least one --model JSON argument is required")

    specs: list[ModelSpec] = []
    for raw in args.model_jsons:
        config = json.loads(raw)
        folder = Path(config["model"])
        specs.append(
            load_model_spec(
                folder,
                config.get("instances", []),
                stride_tuple=config.get("stride"),
                batch=config.get("batch", 1),
                tracker_overrides=config.get("tracker"),
            )
        )

    track(args.video, specs, args.output_csv, device=args.device)


if __name__ == "__main__":
    main()
