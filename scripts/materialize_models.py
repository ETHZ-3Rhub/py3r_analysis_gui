"""Flatten BohacekLabPoseModels into the minimal per-model layout we ship.

Each source model folder (``<pose_estimation>/<group>/<model>/``) carries a lot
of training-time baggage (args.yaml, manifest.yaml, results.csv, instance-type
yamls, ...). We only need ``weights/best.pt`` and ``meta/output_mapping.csv``.
This copies just those two files into ``<dest>/<model>/best.pt`` and
``<dest>/<model>/output_mapping.csv``, plus the source repo's root LICENSE (if
any) into every model folder.

Usage: python scripts/materialize_models.py <pose_estimation_dir> <dest_dir>
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def materialize(source: Path, dest: Path) -> None:
    license_src = source.parent / "LICENSE"
    dest.mkdir(parents=True, exist_ok=True)
    seen: dict[str, Path] = {}
    for model_dir in sorted(p for p in source.rglob("*") if p.is_dir()):
        weights = model_dir / "weights" / "best.pt"
        mapping = model_dir / "meta" / "output_mapping.csv"
        if not weights.is_file() or not mapping.is_file():
            continue
        if model_dir.name in seen:
            raise SystemExit(
                f"duplicate model name '{model_dir.name}': {seen[model_dir.name]} and {model_dir}"
            )
        seen[model_dir.name] = model_dir
        out_dir = dest / model_dir.name
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(weights, out_dir / "best.pt")
        shutil.copy2(mapping, out_dir / "output_mapping.csv")
        if license_src.is_file():
            shutil.copy2(license_src, out_dir / "LICENSE")


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: materialize_models.py <pose_estimation_dir> <dest_dir>", file=sys.stderr)
        raise SystemExit(2)
    source, dest = Path(sys.argv[1]), Path(sys.argv[2])
    if not source.is_dir():
        print(f"source not found: {source}", file=sys.stderr)
        raise SystemExit(1)
    materialize(source, dest)


if __name__ == "__main__":
    main()
