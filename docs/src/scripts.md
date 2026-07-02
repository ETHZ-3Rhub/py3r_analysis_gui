# Scripts

A custom analysis script lives in `/user/scripts/` and is referenced from a config via `entry`.

```toml
[script]
entry = "scripts/my_pipeline.py:run"
```

## Entry function signature

```python
def run(
    *,
    manifest: list[tuple[str, str, Path]],   # [(handle, group, csv_path), ...]
    output_dir: Path,
    comparisons: list[tuple[str, str]],      # [(group_a, group_b), ...]
    video_paths: dict[str, Path],            # {handle: video_path}
    loader: dict,                            # {format, fps, group_tag}
    **options,                               # all [script] params + resolved [script.options] values
) -> None:
```

All values from `[script]` (except `entry`) and all `[script.options]` values are passed as kwargs.

## Loading tracking data

```python
import py3r.behaviour as p3b

tc = p3b.TrackingCollection.from_yolo3r(manifest, video_paths, **loader)
# or for DLC:
tc = p3b.TrackingCollection.from_dlc(manifest, video_paths, **loader)
```

From there, use the `py3r.behaviour` API to compute features, cluster, export, etc. See the bundled scripts in `app/scripts/` for working examples.

## Trust

Any pipeline whose config, script, or model weights live under `/user/` will prompt the user for confirmation before running.
