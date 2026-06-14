"""Globally-unique, filename-safe handles for a set of grouped input files.

The GUI owns identity assignment — it's the orchestrator's job. Every input
file (a video to track, or a pre-tracked CSV) is given a *handle* that is unique
across **all** groups, derived from its filename and, only where that collides,
its parent directories, then the group name, then a numeric suffix. The handle
becomes:

  * the output CSV filename when tracking a video — so two videos that share a
    stem (e.g. ``dayA/oft.mp4`` and ``dayB/oft.mp4`` in one group) can't
    silently overwrite each other on disk; and
  * the ``TrackingCollection`` key and the video↔recording link the pipeline
    uses, so the pipeline never has to reverse-engineer naming.

This logic is owned locally rather than imported from py3r_behaviour on purpose:
identity assignment is essential to the GUI's role, so it lives here. The handle
scheme mirrors py3r_behaviour's ``from_groups`` so handles read the same whether
produced here or by a script, but the two are independent and need not stay in
sync — the GUI loads via the lower-level ``from_yolo3r`` and never calls
``from_groups``.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path


def _disambiguate_stems(paths: list[Path]) -> list[str]:
    """Shortest unique label per path: the filename stem, widening to prepend
    parent directory names only for the entries whose labels collide. Paths not
    involved in a collision stay as plain stems. Identical paths remain
    duplicated — the caller resolves the remainder (here, by group then index)."""
    resolved = [p.resolve() for p in paths]
    # parts[i]: [stem, parent_dir_name, grandparent_dir_name, ...]
    parts = [[rp.stem, *reversed(rp.parent.parts)] for rp in resolved]
    depth = [1] * len(paths)

    while True:
        labels = ["_".join(reversed(parts[i][: depth[i]])) for i in range(len(paths))]
        counts = Counter(labels)
        dupes = [i for i, label in enumerate(labels) if counts[label] > 1]
        if not dupes:
            break
        progressed = False
        for i in dupes:
            if depth[i] < len(parts[i]):
                depth[i] += 1
                progressed = True
            else:
                depth[i] = 1  # exhausted parents (identical paths) — stop widening
        if not progressed:
            break

    return ["_".join(reversed(parts[i][: depth[i]])) for i in range(len(paths))]


def _sanitize(name: str) -> str:
    """Collapse anything outside ``[A-Za-z0-9._-]`` to a single underscore, so a
    handle is safe to use directly as a filename."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return cleaned or "x"


def assign_handles(groups: dict[str, list[Path]]) -> list[tuple[str, str, Path]]:
    """Map every file in *groups* to a globally-unique, filename-safe handle.

    Returns ``[(handle, group_name, path), ...]`` in group/insertion order.

    Disambiguation is applied only as far as needed: filename stem → prepend
    parent directories → append the (sanitized) group name → numeric suffix.
    """
    all_paths = [p for paths in groups.values() for p in paths]
    all_groups = [g for g, paths in groups.items() for _ in paths]
    if not all_paths:
        return []

    labels = _disambiguate_stems(all_paths)

    # The same file added to more than one group can't be told apart by path —
    # fall back to the group name for those.
    counts = Counter(labels)
    labels = [
        f"{label}_{_sanitize(group)}" if counts[label] > 1 else label
        for label, group in zip(labels, all_groups, strict=True)
    ]

    # Make filename-safe, then break any residual collisions (distinct labels
    # can sanitize to the same token) with a numeric suffix.
    labels = [_sanitize(label) for label in labels]
    counts = Counter(labels)
    seen: dict[str, int] = {}
    for i, label in enumerate(labels):
        if counts[label] > 1:
            seen[label] = seen.get(label, 0) + 1
            labels[i] = f"{label}_{seen[label]}"

    return list(zip(labels, all_groups, all_paths, strict=True))
