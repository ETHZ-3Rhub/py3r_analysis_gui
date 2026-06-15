"""Arena discovery.

Any .py file dropped into this package that exposes NAME, TRACKER, TRACKER_ARGS,
and PIPELINE is automatically picked up as an arena option in the GUI.

To add a new arena:
  1. Create app/arenas/<slug>.py with NAME, TRACKER, TRACKER_ARGS, PIPELINE
  2. Create app/pipelines/<slug>_pipeline.py with the py3r_behaviour logic
  3. That's it — the dropdown gains a new entry on next launch.

TRACKER_ARGS["models"][i]["batch"] is a per-model batch size, chosen by the
arena author for their model's footprint. Aim for safe on ~4GB VRAM, and err
low: fixed CUDA/model-load overhead means linear extrapolation from a
higher-VRAM measurement is optimistic.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import ModuleType


def discover() -> list[ModuleType]:
    """Return all valid arena modules in this package, sorted by NAME."""
    here = Path(__file__).parent
    arenas: list[ModuleType] = []
    for _, name, _ in pkgutil.iter_modules([str(here)]):
        mod = importlib.import_module(f"app.arenas.{name}")
        if hasattr(mod, "NAME") and hasattr(mod, "TRACKER") and hasattr(mod, "PIPELINE"):
            arenas.append(mod)
    return sorted(arenas, key=lambda m: m.NAME)
