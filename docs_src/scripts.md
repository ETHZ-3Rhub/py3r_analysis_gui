# Scripts

A custom analysis script lives in a [pipeline source's](sources.md) `scripts/` folder, or `/user/scripts/` for the manual fallback, and is referenced from a config via `entry`.
This must specify the name of your callable function, e.g. `run`.

```toml
[script]
entry = "scripts/my_script.py:run"
```

Scripts run in the app's bundled environment. The only libraries available are `py3r_behaviour` **{py3r_version}** and its dependencies — your script cannot import anything outside of that.

## Entry function signature

The app loads the tracking data and passes it as a `TrackingCollection` — your script receives it ready to use.

```python
def run(
    *,
    tc: p3b.TrackingCollection,          # already loaded and tagged
    output_dir: Path,                    # save everything here
    comparisons: list[tuple[str, str]],  # [(group_a, group_b), ...] for pairwise stats
    group_tag: str,                      # tag key used to group animals (e.g. "group")
    **options,                           # [script] deployment params + [script.options] values
) -> None:
```

## Three rules

- **Progress via `print()`** — the app streams stdout to the run log.
- **All output goes in `output_dir`** — the app opens this folder when the run finishes. Save every file here or in subdirectories.
- **All groups are defined by a single tag `group_tag`, and comparisons are defined on those groups** – any hierarchical group structures have already been flattened into a single composite group name by the app.

## Template

```python
from __future__ import annotations

from pathlib import Path

import py3r.behaviour as p3b


def run(
    *,
    tc: p3b.TrackingCollection,
    output_dir: Path,
    comparisons: list[tuple[str, str]],
    group_tag: str,
    likelihood_min: float = 0.9,
    **_,
) -> None:

    output_dir.mkdir(parents=True, exist_ok=True)

    # Group the collection and get the group list
    tc_grouped = tc.groupby(tags=[group_tag])
    group_names = [k[0] for k in tc_grouped.group_keys]
    print(f"Groups: {group_names}")

    # ... preprocessing, analysis, etc ...
    
    # --- Pairwise comparisons ---
     for group_a, group_b in comparisons:
        print(f"Comparing {group_a} vs {group_b}...")
        # ... stats, plots, etc ...

    print("Done.")
```
