"""Pipeline configs — discover, resolve, and import.

A pipeline *is* a TOML config. Built-ins are bundled in ``app/configs/``; users
drop more into ``/user/configs/``. A config's ``[script].entry`` points at the
analysis code — bundled ``app/scripts/<name>.py`` or a ``/user`` path. Both config
sources normalise into one
``resolved`` dict (see ``resolve``) that the runner and worker consume — so the
built-ins exercise the exact path user configs use and it cannot bit-rot.

Trust is dead simple: anything under ``/user/`` is untrusted (we didn't write
it), full stop — the GUI prompts before running it. We don't inspect *what* it
references; authorship is the whole rule.

Plain functions + dicts. The only class is ``ConfigError``, whose message is
shown verbatim (copyable) to whoever has to fix the config.

No heavy / Qt imports at module scope: this module is imported by the
pipeline-worker subprocess (for ``import_entry``) and must stay light. It also
never imports pipeline *code* on the GUI side — point-name checks live inside
the pipeline run, where py3r is already loaded.
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

_BUNDLED_CONFIGS = Path(__file__).parent / "configs"

# Top-level keys a delta may set even though they don't "override" a base value.
_IDENTITY_KEYS = {"extends", "name", "min_app_version", "arena_image"}

# Subtable a delta may introduce wholesale even if the base omits it (its keys
# are point names handled by the pipeline at run time, not base-config keys).
_ADDABLE_PATH = "script.point_map"


class ConfigError(Exception):
    """A config that can't be loaded/validated. The message is user-facing."""


# ── Locations ────────────────────────────────────────────────────────────────
def user_dir() -> Path:
    """The ``/user`` folder: next to the exe when frozen, repo root in dev."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "user"
    return Path(__file__).parent.parent / "user"


def user_configs_dir() -> Path:
    return user_dir() / "configs"


def user_models_dir() -> Path:
    return user_dir() / "models"


def user_sources_dir() -> Path:
    """Where installed git pipeline sources live, one self-contained folder each."""
    return user_dir() / "sources"


def bundled_configs_dir() -> Path:
    return _BUNDLED_CONFIGS


def _bundled_models_root() -> Path | None:
    """Where bundled model folders live, or None if not locatable."""
    try:
        from app.trackers.yolo_tracker import _find_models_dir

        return _find_models_dir()
    except Exception:
        return None


# ── Startup discovery (cheap: lists files, reads names — never resolves) ──────
def discover(*, include_hidden: bool = False) -> list[dict]:
    """Enumerate bundled + manual-user + git-source configs into list entries for
    the pipeline combo.

    Each entry: ``{config_path, source, id, label}``. ``source`` is "bundled",
    "user" (the manual ``/user/configs`` fallback), or a git source id — it
    drives both above/below-divider placement and the trust prompt, so discovery
    never has to resolve anything. ``hidden.toml`` suppresses entries by
    ``(source, id)``, uniformly across all three roots — pass
    ``include_hidden=True`` (the Manage Pipelines dialog does, to list
    everything with its checked/unchecked state) to skip that filtering. Never
    raises.
    """
    entries: list[dict] = []
    for path in sorted(_BUNDLED_CONFIGS.glob("*.toml")):
        entries.append(_enumerate(path, "bundled"))

    ucfg = user_configs_dir()
    if ucfg.is_dir():
        for path in sorted(ucfg.glob("*.toml")):
            entries.append(_enumerate(path, "user"))

    sdir = user_sources_dir()
    if sdir.is_dir():
        for src_dir in sorted(p for p in sdir.iterdir() if p.is_dir()):
            cfg_dir = src_dir / "configs"
            if not cfg_dir.is_dir():
                continue
            for path in sorted(cfg_dir.glob("*.toml")):
                entries.append(_enumerate(path, src_dir.name))

    if include_hidden:
        return entries
    hidden = read_hidden()
    return [e for e in entries if (e["source"], e["id"]) not in hidden]


def _enumerate(path: Path, source: str) -> dict:
    """A combo entry. Reads only the display name (cheap TOML parse); a file that
    won't parse keeps its filename as a label and surfaces its error on select."""
    label = path.name
    try:
        label = tomllib.loads(path.read_text(encoding="utf-8")).get("name") or path.name
    except Exception:
        pass
    return {"config_path": path, "source": source, "id": path.stem, "label": label}


# ── Hidden pipelines (replaces the old bundled-only ignore.txt) ───────────────
def read_hidden() -> set[tuple[str, str]]:
    """``(source, id)`` pairs to suppress from ``discover()``, across every root.
    Written by the Manage Pipelines dialog; never raises on a bad file."""
    path = user_dir() / "hidden.toml"
    if not path.is_file():
        return set()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    out: set[tuple[str, str]] = set()
    for entry in data.get("hidden", []):
        src, pid = entry.get("source"), entry.get("id")
        if isinstance(src, str) and isinstance(pid, str):
            out.add((src, pid))
    return out


def write_hidden(hidden: set[tuple[str, str]]) -> None:
    path = user_dir() / "hidden.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'[[hidden]]\nsource = "{src}"\nid = "{pid}"\n' for src, pid in sorted(hidden)]
    path.write_text("\n".join(lines), encoding="utf-8")


# ── Resolution (parse + extends + resource resolution + trust) ───────────────
def resolve(config_path: Path) -> dict:
    """Turn a config file into the resolved dict, flattening ``extends`` and
    resolving every ``weights``/``entry`` to a concrete bundled-or-/user target.
    Raises ConfigError on any structural / reference problem (no script import).
    """
    raw = _parse(config_path)
    # Fail closed: trusted only if the resolved path is provably under the
    # bundled dir we ship — anything else (manual /user/ files, or a git source's
    # own folder) is untrusted by default. base_dir is where that config's own
    # entry/weights relative paths resolve against — the manual /user/ tree for
    # the hand-copied fallback, or that source's own self-contained folder.
    source, base_dir = _identify_root(config_path)

    if "extends" in raw:
        base_id = raw["extends"]
        base_path = _BUNDLED_CONFIGS / f"{base_id}.toml"
        if not base_path.is_file():
            raise ConfigError(f'extends = "{base_id}": no bundled pipeline with that id.')
        merged = _merge(_parse(base_path), raw)
    else:
        merged = raw

    return _build(merged, config_path, source, base_dir)


def _identify_root(config_path: Path) -> tuple[str, Path]:
    """Which root a config file was found under, and the base directory its own
    relative ``entry``/``weights`` paths resolve against."""
    if _is_under(config_path, _BUNDLED_CONFIGS):
        return "bundled", _BUNDLED_CONFIGS
    sdir = user_sources_dir()
    try:
        rel = config_path.resolve().relative_to(sdir.resolve())
    except (ValueError, OSError):
        rel = None
    if rel is not None and rel.parts:
        source_id = rel.parts[0]
        return source_id, sdir / source_id
    return "user", user_dir()


def _parse(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"couldn't parse {path.name} — {exc}") from exc


def _merge(base: dict, delta: dict) -> dict:
    """One-level-deep override-by-key of *delta* onto a bundled *base*. New keys
    in content sections (models/loader/script) are rejected as typos; the only
    freely-addable subtable is ``[script.point_map]``."""
    out = copy.deepcopy(base)
    for key, value in delta.items():
        if key == "extends":
            continue
        if key in _IDENTITY_KEYS:
            out[key] = value
            continue
        if key not in base:
            raise ConfigError(f"unknown section '{key}': the base pipeline has no such section.")
        out[key] = _merge_table(base[key], value, key)
    return out


def _merge_table(base: dict, delta: dict, path: str) -> dict:
    if not isinstance(delta, dict) or not isinstance(base, dict):
        return copy.deepcopy(delta)
    out = copy.deepcopy(base)
    for key, value in delta.items():
        child = f"{path}.{key}"
        if key not in base:
            if child == _ADDABLE_PATH or path == _ADDABLE_PATH:
                out[key] = copy.deepcopy(value)
                continue
            raise ConfigError(f"unknown key '{child}': not present in the base pipeline.")
        out[key] = _merge_table(base[key], value, child)
    return out


def _build(merged: dict, config_path: Path, source: str, base_dir: Path) -> dict:
    name = merged.get("name") or config_path.stem

    mav = merged.get("min_app_version")
    if mav and _version_tuple(_app_version()) < _version_tuple(mav):
        raise ConfigError(f"this pipeline needs app version ≥ {mav} (you have {_app_version()}).")

    models: dict[str, dict] = {}
    for role, m in merged.get("models", {}).items():
        if "weights" not in m:
            raise ConfigError(f"[models.{role}] is missing 'weights'.")
        weights_dir, weights_source = _resolve_weights(m["weights"], base_dir)
        models[role] = {
            "weights_dir": weights_dir,
            "weights_source": weights_source,
            "instances": m.get("instances", []),
            "stride": m.get("stride"),
            "batch": m.get("batch", 1),
            "tracker": m.get("tracker"),
        }
    if not models:
        raise ConfigError("config declares no [models.*] — nothing to track.")

    loader = merged.get("loader", {})

    script = None
    script_raw = merged.get("script")
    if script_raw is not None:
        if "entry" not in script_raw:
            raise ConfigError("[script] is missing 'entry'.")
        entry = _resolve_entry(script_raw["entry"], base_dir)
        params = {k: v for k, v in script_raw.items() if k not in ("entry", "options")}
        script = {
            "entry": script_raw["entry"],
            "entry_source": entry["source"],
            "params": params,
            "options": script_raw.get("options", {}),
            "_entry": entry,
        }
        # The loader only runs when there's an analysis script to read CSVs back
        # in (tracking-only configs never use it). Validate here so a missing/bad
        # field is a clean select-time ConfigError, not a raw KeyError mid-run.
        fps = loader.get("fps")
        if not isinstance(fps, int | float) or isinstance(fps, bool) or fps <= 0:
            raise ConfigError("[loader] needs a positive numeric 'fps' (recording frame rate).")
        fmt = loader.get("format", "yolo3r")
        if fmt not in ("yolo3r", "dlc"):
            raise ConfigError(
                f"[loader] format = {fmt!r} is not supported (use 'yolo3r' or 'dlc')."
            )

    # Trust is authorship, nothing else: a /user config we didn't write is
    # untrusted even if it only references bundled code (it could still extend a
    # base, swap params, or be edited later — and the rule stays trivial to reason
    # about). The GUI prompts before running anything untrusted.
    trust = "trusted" if source == "bundled" else "untrusted"

    return {
        "config_path": config_path,
        "source": source,
        "id": config_path.stem,
        "name": name,
        "arena_image": merged.get("arena_image"),
        "models": models,
        "loader": loader,
        "script": script,
        "trust": trust,
    }


def _resolve_weights(weights: str, base_dir: Path) -> tuple[Path, str]:
    """Resolve a ``weights`` reference to (folder, source).

    source is "bundled", "user", or "missing". Bundled name wins over a
    same-named user-side folder (a user can't shadow ours). *base_dir* is the
    manual ``/user`` tree for the hand-copied fallback, or a git source's own
    folder — either way its ``models/`` subfolder is where a bare weights name
    is looked up. A missing folder is *not* an error here — weights only load
    during tracking (track.py raises a clear per-video error then), so a missing
    model never blocks a CSV-only run, and only a model that actually loads is a
    trust risk."""
    bundled_root = _bundled_models_root()
    if bundled_root is not None and (bundled_root / weights).is_dir():
        return bundled_root / weights, "bundled"
    user_folder = base_dir / "models" / weights
    if user_folder.is_dir():
        return user_folder, "user"
    fallback = (bundled_root / weights) if bundled_root is not None else user_folder
    return fallback, "missing"


def _resolve_entry(entry: str, base_dir: Path) -> dict:
    """``module:fn`` → bundled ``app.scripts.<module>``; a path ending ``.py``
    or containing ``/`` → resolved under *base_dir* (untrusted) — the manual
    ``/user`` tree, or a git source's own folder."""
    module, sep, func = entry.partition(":")
    if not sep or not func:
        raise ConfigError(f"entry '{entry}' must be 'module:function' or 'path.py:function'.")
    if "/" in module or module.endswith(".py"):
        path = (base_dir / module).resolve()
        if not path.is_file():
            raise ConfigError(f"script not found: {module} (in {base_dir}).")
        return {"kind": "user", "source": "user", "path": path, "func": func}
    return {
        "kind": "bundled",
        "source": "bundled",
        "module": f"app.scripts.{module}",
        "func": func,
    }


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


# ── Import (the one place pipeline code is loaded — in the worker) ────────────
def import_entry(resolved: dict) -> tuple[ModuleType, object]:
    """Import the script module and return (module, entry_fn). Bundled modules
    import normally; /user scripts load from file path (code execution — the
    caller must have cleared trust first)."""
    e = resolved["script"]["_entry"]
    if e["kind"] == "bundled":
        module = importlib.import_module(e["module"])
    else:
        spec = importlib.util.spec_from_file_location(f"_user_pipeline_{e['path'].stem}", e["path"])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module, getattr(module, e["func"])


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version("py3r-analysis-gui")
    except Exception:
        return "0"


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)
