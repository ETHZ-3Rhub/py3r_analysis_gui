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
import os
import pickle
import sys
import traceback
from pathlib import Path


def run_worker(payload_path: Path) -> int:
    # umap defaults to n_jobs=-1, which makes joblib spawn loky worker
    # *processes*. Under the windowed (console-less) frozen GUI those workers
    # have no console to inherit, so Windows gives each a fresh blank console
    # window that flashes for the duration of the parallel section. Disable
    # joblib's process pool: our datasets are small and numba threads still
    # parallelise the heavy work, so the speed cost is negligible.
    os.environ.setdefault("JOBLIB_MULTIPROCESSING", "0")

    # Frozen PyInstaller exes ignore PYTHONUTF8/PYTHONUNBUFFERED/env vars, so
    # force UTF-8 here to match the parent's utf-8 decode of captured output,
    # and force line buffering so each print() flushes immediately to the pipe
    # (otherwise block buffering delays the parent's real-time log streaming).
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    with open(payload_path, "rb") as f:
        payload = pickle.load(f)

    arena = importlib.import_module(payload["arena_module"])
    try:
        arena.PIPELINE(**payload["kwargs"])
    except Exception:
        traceback.print_exc()
        return 1
    return 0
