import sys

from app import paths


def _frozen(monkeypatch, exe):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))


def test_dev_uses_repo_root(monkeypatch):
    monkeypatch.delenv("PY3R_DATA_DIR", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert paths.user_dir() == paths.data_dir() / "user"
    assert (paths.data_dir() / "app").is_dir()


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PY3R_DATA_DIR", str(tmp_path))
    assert paths.user_dir() == tmp_path / "user"
    assert paths.tracking_env_dir() == tmp_path / "tracking_env"


def test_frozen_uses_platform_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("PY3R_DATA_DIR", raising=False)
    _frozen(monkeypatch, tmp_path / "app" / "Analys3R")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.data_dir() == tmp_path / "Library" / "Application Support" / "Analys3R"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert paths.data_dir() == tmp_path / ".local" / "share" / "Analys3R"


def test_migrate_copies_once(monkeypatch, tmp_path):
    exe = tmp_path / "app" / "Analys3R"
    old = exe.parent / "user" / "configs"
    old.mkdir(parents=True)
    (old / "a.toml").write_text("x")
    monkeypatch.setenv("PY3R_DATA_DIR", str(tmp_path / "data"))
    _frozen(monkeypatch, exe)

    paths.migrate_legacy_user_dir()
    assert (tmp_path / "data" / "user" / "configs" / "a.toml").read_text() == "x"

    (tmp_path / "data" / "user" / "configs" / "a.toml").write_text("edited")
    paths.migrate_legacy_user_dir()  # already migrated: must not overwrite
    assert (tmp_path / "data" / "user" / "configs" / "a.toml").read_text() == "edited"
    assert (old / "a.toml").exists()  # old copy left in place
