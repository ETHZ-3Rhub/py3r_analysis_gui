# Configs

A config is a `.toml` file that defines a complete pipeline — which models to run for tracking, how to load the resulting data, and which analysis script to run. Configs live in `/user/configs/`. The filename stem is the pipeline's ID (e.g. `my_oft.toml` → id `my_oft`).

A config does not need to define all sections. `[models]` is only needed if the pipeline runs tracking. `[loader]` and `[script]` are only needed if it runs analysis — `[loader]` defines how tracking files are loaded for the script to consume. A tracking-only config has `[models]` and no `[script]`; an analysis-only config has `[loader]` + `[script]` and no `[models]`.

There are two approaches:

- **Modify a built-in** — use `extends` to inherit from a built-in pipeline and override only what you need. Recommended for most cases.
- **Write from scratch** — define every section yourself. Needed when you're building a genuinely new pipeline type.

---

## Inheritance example

```toml
extends = "oft"           # built-in id: "oft" or "epm"

[script]
arena_size_m = 0.50       # only declare what you're changing
```

Anything not declared is inherited as-is. You can override any section or individual field.

---

## Full example

```toml
name = "Open Field Test"
arena_image = "oft_arena.png"
min_app_version = "0.3.0"

[models.mouse]
weights = "mouse_top_main"
instances = [{ type = "mouse_top", max = 1 }]
batch = 32

[models.environment]
weights = "environment_main"
instances = [{ type = "oft", max = 1 }]
stride = [30, "ffill"]
batch = 32

[loader]
format = "yolo3r"
fps = 30
group_tag = "group"

[script]
entry = "oft:run"
arena_size_m = 0.64
likelihood_min = 0.9

[script.options]
numbins    = { type = "int",   min = 2, max = 20 }
n_clusters = { type = "int",   default = 10, min = 5, max = 50 }
threshold  = { type = "float", default = 0.8, min = 0.5, max = 1.0, label = "Score threshold" }
```

---

## Section details

### Top-level fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Display name shown in the app |
| `extends` | string | no | ID of a built-in pipeline to inherit from |
| `arena_image` | string | no | Reference image filename shown at pipeline select |
| `min_app_version` | string | no | Minimum app version required (e.g. `"0.3.0"`) |

---

### `[models.<role>]`

Defines the YOLO models to run during the tracking step — one section per model file. See [Models](models.md) for how model folders are structured.

The **role name** (e.g. `mouse`, `environment`) is a label of your choosing. It doesn't affect output column names and doesn't need to match anything in your script. It just distinguishes models from each other within the config.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `weights` | string | yes | Model folder name — looked up in bundled models first, then `/user/models/` |
| `instances` | list | yes | What to detect with this model — see below |
| `batch` | int | no | Inference batch size (default: 1) |
| `stride` | `[int, string]` | no | Run every N frames, fill skipped. E.g. `[30, "ffill"]` — useful for slow-moving models like arena detectors |

**`instances`** is a list of `{ type = "...", max = N }` entries:

- `type` — must match an instance name defined in the model's `meta/output_mapping.csv` (e.g. `"mouse_top"`); output columns are named `{type}.{type}_{slot}.{keypoint}.x/y/conf`
- `max` — maximum number of this type to track simultaneously

---

### `[loader]`

Defines how tracking files are loaded into a `py3r.behaviour.TrackingCollection` for the analysis script. Required when `[script]` is present. The `format` must match the model that produced the tracking files.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `format` | string | yes | `"yolo3r"` or `"dlc"` |
| `fps` | int | yes | Frames per second of the source video |
| `group_tag` | string | yes | CSV column name used to assign animals to groups |

---

### `[script]`

Points at the analysis code that runs after loading. All fields here (except `entry`) are fixed deployment parameters — not shown to the user, but passed to the script at run time.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `entry` | string | yes (if no `extends`) | `"module:run"` for bundled scripts; `"scripts/my_script.py:run"` for user scripts |
| `arena_size_m` | float | no | Arena size in metres |
| `likelihood_min` | float | no | Confidence filter threshold (default: 0.9) |
| `point_map` | table | no | Remap canonical point names to your CSV columns: `{ bodycentre = "center" }` |

Any additional fields you add are passed as keyword arguments to the script's `run()` function. See [Scripts](scripts.md) for the full entry function signature.

---

### `[script.options]`

User-facing controls that appear in the **Advanced Options** dialog. The user sets them at run time; the resolved values are passed to the script as keyword arguments. Each key must be a parameter accepted by the script's `run()` function.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | `"int"`, `"float"`, `"bool"`, or `"str"` |
| `default` | any | no | Default value. Omit to make the option a checkbox (off by default) |
| `min` / `max` | number | no | Range for `int` and `float` |
| `label` | string | no | Display label in the dialog (defaults to the key name) |

---

## Hiding a built-in pipeline

Add its ID to `/user/configs/ignore.txt`, one per line.
