"""Small, dependency-free text helpers shared across the GUI."""

from __future__ import annotations

import re


def natural_key(s: str) -> list[int | str]:
    """Split a string into alternating text/integer chunks for natural sort.
    "oft2.csv" → ["oft", 2, ".csv"] so numeric runs compare by value."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", s)]
