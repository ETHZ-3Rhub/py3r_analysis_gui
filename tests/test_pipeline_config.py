"""Unit tests for pipeline_config — the merge/extends/resolve/trust core.

These run without Qt and without py3r: resolve() never imports pipeline code, and
a missing model folder resolves to source "missing" (not an error), so the real
bundled oft/epm configs can be used as `extends` bases here. User-side locations
are redirected by patching `user_dir` to a tmp folder.
"""

import pytest

from app import pipeline_config as pc

# Bundled base config (real file) used as the `extends` target throughout.
BASE = "oft"


@pytest.fixture
def user_root(tmp_path, monkeypatch):
    """Redirect /user to a tmp dir (with a configs/ subdir) and return its root."""
    monkeypatch.setattr(pc, "user_dir", lambda: tmp_path)
    (tmp_path / "configs").mkdir()
    return tmp_path


def write_cfg(user_root, text, name="mine.toml"):
    path = user_root / "configs" / name
    path.write_text(text, encoding="utf-8")
    return path


def bundled(name):
    return pc.bundled_configs_dir() / f"{name}.toml"


# ── Trust = authorship ───────────────────────────────────────────────────────
def test_bundled_config_is_trusted():
    r = pc.resolve(bundled(BASE))
    assert r["source"] == "bundled"
    assert r["trust"] == "trusted"


def test_user_config_is_untrusted_even_when_config_only(user_root):
    # Extends a bundled base, only tweaks a param — runs zero /user code, yet the
    # authorship rule still marks it untrusted (the key decision from the redesign).
    path = write_cfg(user_root, 'extends = "oft"\n[script]\narena_size_m = 0.5\n')
    r = pc.resolve(path)
    assert r["source"] == "user"
    assert r["trust"] == "untrusted"


def test_user_script_entry_is_user_sourced(user_root):
    (user_root / "scripts").mkdir()
    (user_root / "scripts" / "mine.py").write_text("def run(**k):\n    pass\n")
    path = write_cfg(
        user_root,
        'extends = "oft"\n[script]\nentry = "scripts/mine.py:run"\n',
    )
    r = pc.resolve(path)
    assert r["script"]["entry_source"] == "user"
    assert r["trust"] == "untrusted"


def test_user_script_not_found_raises(user_root):
    path = write_cfg(user_root, 'extends = "oft"\n[script]\nentry = "scripts/nope.py:run"\n')
    with pytest.raises(pc.ConfigError, match="script not found"):
        pc.resolve(path)


# ── extends / merge ──────────────────────────────────────────────────────────
def test_extends_overrides_by_key_and_keeps_siblings(user_root):
    path = write_cfg(user_root, 'extends = "oft"\n[script]\narena_size_m = 0.5\n')
    r = pc.resolve(path)
    params = r["script"]["params"]
    assert params["arena_size_m"] == 0.5  # overridden
    assert params["likelihood_min"] == 0.9  # sibling untouched from base


def test_extends_overrides_nested_model_param(user_root):
    base = pc.resolve(bundled(BASE))
    path = write_cfg(user_root, 'extends = "oft"\n[models.mouse]\nbatch = 8\n')
    r = pc.resolve(path)
    assert r["models"]["mouse"]["batch"] == 8
    # instances (a sibling within models.mouse) survive the partial override
    assert r["models"]["mouse"]["instances"] == base["models"]["mouse"]["instances"]
    # the other model role is untouched entirely
    assert r["models"]["environment"]["batch"] == base["models"]["environment"]["batch"]


def test_extends_can_add_point_map(user_root):
    # [script.point_map] is the one subtable a delta may introduce wholesale.
    path = write_cfg(
        user_root,
        'extends = "oft"\n[script.point_map]\nbodycentre = "centroid"\n',
    )
    r = pc.resolve(path)
    assert r["script"]["params"]["point_map"] == {"bodycentre": "centroid"}


def test_extends_identity_keys_settable(user_root):
    path = write_cfg(user_root, 'extends = "oft"\nname = "My OFT"\n')
    r = pc.resolve(path)
    assert r["name"] == "My OFT"


def test_extends_unknown_section_rejected(user_root):
    path = write_cfg(user_root, 'extends = "oft"\n[telemetry]\nfoo = 1\n')
    with pytest.raises(pc.ConfigError, match="unknown section 'telemetry'"):
        pc.resolve(path)


def test_extends_unknown_nested_key_rejected(user_root):
    path = write_cfg(user_root, 'extends = "oft"\n[models.mouse]\nwibble = 1\n')
    with pytest.raises(pc.ConfigError, match="unknown key 'models.mouse.wibble'"):
        pc.resolve(path)


def test_extends_missing_base_rejected(user_root):
    path = write_cfg(user_root, 'extends = "does_not_exist"\n')
    with pytest.raises(pc.ConfigError, match="no bundled pipeline"):
        pc.resolve(path)


# ── structural validation ────────────────────────────────────────────────────
def test_no_models_rejected(user_root):
    text = 'name = "x"\n[script]\nentry = "oft_pipeline:run"\n[loader]\nfps = 30\n'
    with pytest.raises(pc.ConfigError, match="no \\[models"):
        pc.resolve(write_cfg(user_root, text))


def test_model_missing_weights_rejected(user_root):
    path = write_cfg(user_root, 'name = "x"\n[models.mouse]\nbatch = 1\n')
    with pytest.raises(pc.ConfigError, match="missing 'weights'"):
        pc.resolve(path)


def test_script_missing_entry_rejected(user_root):
    text = (
        'name = "x"\n[models.m]\nweights = "mouse/mouse_top_main"\n'
        "[script]\narena_size_m = 0.5\n"
    )
    with pytest.raises(pc.ConfigError, match="\\[script\\] is missing 'entry'"):
        pc.resolve(write_cfg(user_root, text))


def test_bad_toml_raises_configerror(user_root):
    with pytest.raises(pc.ConfigError, match="couldn't parse"):
        pc.resolve(write_cfg(user_root, "this is = = not toml\n"))


def test_min_app_version_too_high_rejected(user_root):
    path = write_cfg(user_root, 'extends = "oft"\nmin_app_version = "999.0.0"\n')
    with pytest.raises(pc.ConfigError, match="needs app version"):
        pc.resolve(path)


# ── loader validation (only when a script is present) ────────────────────────
def _standalone(models=True, script=True, loader=""):
    parts = ['name = "x"']
    if models:
        parts.append('[models.m]\nweights = "mouse/mouse_top_main"')
    if script:
        parts.append('[script]\nentry = "oft_pipeline:run"')
    if loader:
        parts.append(loader)
    return "\n".join(parts) + "\n"


def test_loader_fps_required_with_script(user_root):
    with pytest.raises(pc.ConfigError, match="positive numeric 'fps'"):
        pc.resolve(write_cfg(user_root, _standalone(loader="")))


def test_loader_fps_must_be_positive_number(user_root):
    with pytest.raises(pc.ConfigError, match="positive numeric 'fps'"):
        pc.resolve(write_cfg(user_root, _standalone(loader="[loader]\nfps = 0")))


def test_loader_format_validated(user_root):
    text = _standalone(loader='[loader]\nfps = 30\nformat = "sleap"')
    with pytest.raises(pc.ConfigError, match="format = 'sleap' is not supported"):
        pc.resolve(write_cfg(user_root, text))


def test_tracking_only_config_skips_loader_validation(user_root):
    # No [script] → loader never runs → fps not required.
    r = pc.resolve(write_cfg(user_root, _standalone(script=False, loader="")))
    assert r["script"] is None


# ── discover ─────────────────────────────────────────────────────────────────
def test_discover_lists_bundled_only_when_user_empty(user_root):
    entries = pc.discover()
    by_id = {e["id"]: e for e in entries}
    assert {"oft", "epm"} <= set(by_id)
    assert all(by_id[i]["source"] == "bundled" for i in ("oft", "epm"))


def test_discover_includes_user_config_marked_user(user_root):
    write_cfg(user_root, 'extends = "oft"\n', name="custom.toml")
    by_id = {e["id"]: e for e in pc.discover()}
    assert by_id["custom"]["source"] == "user"


def test_discover_ignore_txt_suppresses_bundled(user_root):
    (user_root / "configs" / "ignore.txt").write_text("oft\n")
    ids = {e["id"] for e in pc.discover()}
    assert "oft" not in ids
    assert "epm" in ids  # only the listed id is suppressed


def test_discover_lists_unparseable_user_config_by_filename(user_root):
    write_cfg(user_root, "= = broken", name="bad.toml")
    by_id = {e["id"]: e for e in pc.discover()}
    assert by_id["bad"]["source"] == "user"
    assert by_id["bad"]["label"] == "bad.toml"  # falls back to filename, never dropped
