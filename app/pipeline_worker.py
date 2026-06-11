"""Subprocess entry point for running a pipeline in isolation.

Invoked via `<python> -m app.main --pipeline-worker <payload.pkl>` (or, in a
packaged build, `<bundled-exe> --pipeline-worker <payload.pkl>`). Loads the
arena module and kwargs from *payload_path*, then calls `arena.PIPELINE`.
stdout/stderr pass straight through to the parent's pipe so PipelineRunner
can stream them; running this off the main process means "Cancel" can kill
it outright without risking a hung GUI.
"""

from __future__ import annotations

import importlib
import pickle
import traceback
from pathlib import Path


def run_worker(payload_path: Path) -> int:
    with open(payload_path, "rb") as f:
        payload = pickle.load(f)

    arena = importlib.import_module(payload["arena_module"])
    try:
        arena.PIPELINE(**payload["kwargs"])
    except Exception:
        traceback.print_exc()
        return 1
    return 0
