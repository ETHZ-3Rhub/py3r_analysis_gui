"""Unit tests for pipeline_sources — install/update logic, no real network.

urllib.request.urlopen is monkeypatched to serve canned bytes from a URL->bytes
map, so these run offline and fast. Zip archives are built in-memory to mirror
GitHub's zipball shape (a single top-level "<repo>-<ref>/" folder).
"""

import io
import zipfile

import pytest

from app import pipeline_config as pc
from app import pipeline_sources as ps
from app import update_check


@pytest.fixture
def user_root(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "user_dir", lambda: tmp_path)
    return tmp_path


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data


def _fake_urlopen(routes: dict[str, bytes]):
    def _open(url, timeout=None):
        if url not in routes:
            raise ps.urllib.error.URLError(f"no route for {url}")
        return _FakeResponse(routes[url])

    return _open


def _zip_of(root_name: str, files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, content in files.items():
            zf.writestr(f"{root_name}/{rel}", content)
    return buf.getvalue()


# ── sanitize_id ────────────────────────────────────────────────────────────
def test_sanitize_id():
    assert ps.sanitize_id("Ethz-INS/OFT-Pipeline") == "ethz-ins-oft-pipeline"


# ── parse_repo_url (tolerant paste-a-URL handling) ───────────────────────────
@pytest.mark.parametrize(
    "text,expected",
    [
        ("ETHZ-INS/oft-pipeline", "ETHZ-INS/oft-pipeline"),
        ("https://github.com/ETHZ-INS/oft-pipeline", "ETHZ-INS/oft-pipeline"),
        ("http://github.com/ETHZ-INS/oft-pipeline", "ETHZ-INS/oft-pipeline"),
        ("https://www.github.com/ETHZ-INS/oft-pipeline", "ETHZ-INS/oft-pipeline"),
        ("github.com/ETHZ-INS/oft-pipeline", "ETHZ-INS/oft-pipeline"),
        ("https://github.com/ETHZ-INS/oft-pipeline.git", "ETHZ-INS/oft-pipeline"),
        ("https://github.com/ETHZ-INS/oft-pipeline/", "ETHZ-INS/oft-pipeline"),
        ("https://github.com/ETHZ-INS/oft-pipeline/tree/main", "ETHZ-INS/oft-pipeline"),
        ("https://github.com/ETHZ-INS/oft-pipeline/releases/tag/v1.0.0", "ETHZ-INS/oft-pipeline"),
        ("https://github.com/ETHZ-INS/oft-pipeline?tab=readme-ov-file", "ETHZ-INS/oft-pipeline"),
        ("  https://github.com/ETHZ-INS/oft-pipeline  ", "ETHZ-INS/oft-pipeline"),
    ],
)
def test_parse_repo_url_tolerant(text, expected):
    assert ps.parse_repo_url(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "just-a-name", "https://github.com/"])
def test_parse_repo_url_rejects_incomplete(text):
    assert ps.parse_repo_url(text) is None


# ── sources.toml round-trip ──────────────────────────────────────────────────
def test_sources_roundtrip(user_root):
    ps.save_sources([ps.Source(id="a-b", repo="a/b", ref="v1.0.0")])
    loaded = ps.load_sources()
    assert loaded == [ps.Source(id="a-b", repo="a/b", ref="v1.0.0")]


def test_load_sources_missing_file_returns_empty(user_root):
    assert ps.load_sources() == []


# ── install_source ───────────────────────────────────────────────────────────
def test_install_source_happy_path(user_root, monkeypatch):
    zip_bytes = _zip_of(
        "oft-pipeline-v1.0.0",
        {
            "configs/oft.toml": b'name = "x"\n',
            "scripts/oft.py": b"def run(**k):\n    pass\n",
            "models/mouse/weights/best.pt": b"0" * 100,
        },
    )
    url = "https://codeload.github.com/ethz-ins/oft-pipeline/zip/refs/tags/v1.0.0"
    monkeypatch.setattr(ps.urllib.request, "urlopen", _fake_urlopen({url: zip_bytes}))

    dest = ps.source_dir("ethz-ins-oft-pipeline")
    ps.install_source("ethz-ins/oft-pipeline", "v1.0.0", dest)

    assert (dest / "configs" / "oft.toml").is_file()
    assert (dest / "scripts" / "oft.py").is_file()
    assert (dest / "models" / "mouse" / "weights" / "best.pt").is_file()


def test_install_source_rejects_repo_without_expected_layout(user_root, monkeypatch):
    zip_bytes = _zip_of("random-repo-main", {"README.md": b"hello"})
    url = "https://codeload.github.com/someone/random-repo/zip/refs/tags/main"
    monkeypatch.setattr(ps.urllib.request, "urlopen", _fake_urlopen({url: zip_bytes}))

    with pytest.raises(ps.SourceError, match="not a pipeline source repo"):
        ps.install_source("someone/random-repo", "main", ps.source_dir("x"))


def test_install_source_download_failure_raises_source_error(user_root, monkeypatch):
    monkeypatch.setattr(ps.urllib.request, "urlopen", _fake_urlopen({}))
    with pytest.raises(ps.SourceError, match="couldn't download"):
        ps.install_source("ethz-ins/oft-pipeline", "v1.0.0", ps.source_dir("x"))


def test_install_source_rejects_unpointed_lfs_pointer(user_root, monkeypatch):
    lfs_pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:aaaa\nsize 123456\n"
    zip_bytes = _zip_of(
        "oft-pipeline-v1.0.0",
        {
            "configs/oft.toml": b'name = "x"\n',
            "models/mouse/weights/best.pt": lfs_pointer,
        },
    )
    url = "https://codeload.github.com/ethz-ins/oft-pipeline/zip/refs/tags/v1.0.0"
    monkeypatch.setattr(ps.urllib.request, "urlopen", _fake_urlopen({url: zip_bytes}))

    with pytest.raises(ps.SourceError, match="git-lfs pointer"):
        ps.install_source("ethz-ins/oft-pipeline", "v1.0.0", ps.source_dir("x"))


# ── model pointers ────────────────────────────────────────────────────────
def test_resolve_model_pointers_fetches_and_replaces(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    model_dir = models_dir / "mouse_ft"
    model_dir.mkdir(parents=True)
    (model_dir / "source.toml").write_text(
        'repo = "ethz-ins/models"\nref = "v2.0.0"\nasset = "mouse_ft.zip"\n',
        encoding="utf-8",
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("weights/best.pt", b"real weights")
    asset_zip = buf.getvalue()

    url = "https://github.com/ethz-ins/models/releases/download/v2.0.0/mouse_ft.zip"
    monkeypatch.setattr(ps.urllib.request, "urlopen", _fake_urlopen({url: asset_zip}))

    ps.resolve_model_pointers(models_dir)

    assert not (model_dir / "source.toml").exists()
    assert (model_dir / "weights" / "best.pt").read_bytes() == b"real weights"


def test_resolve_model_pointers_leaves_unpointed_models_alone(tmp_path):
    models_dir = tmp_path / "models"
    (models_dir / "already-local" / "weights").mkdir(parents=True)
    (models_dir / "already-local" / "weights" / "best.pt").write_bytes(b"x")
    ps.resolve_model_pointers(models_dir)  # no network calls, no error
    assert (models_dir / "already-local" / "weights" / "best.pt").is_file()


def test_pointer_missing_fields_raises(tmp_path):
    models_dir = tmp_path / "models"
    model_dir = models_dir / "m"
    model_dir.mkdir(parents=True)
    (model_dir / "source.toml").write_text('ref = "v1"\n', encoding="utf-8")
    with pytest.raises(ps.SourceError, match="needs either 'url'"):
        ps.resolve_model_pointers(models_dir)


# ── LFS sniff ────────────────────────────────────────────────────────────
def test_looks_like_lfs_pointer_true(tmp_path):
    p = tmp_path / "best.pt"
    p.write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:x\nsize 1\n")
    assert ps._looks_like_lfs_pointer(p)


def test_looks_like_lfs_pointer_false_for_real_binary(tmp_path):
    p = tmp_path / "best.pt"
    p.write_bytes(b"\x80\x02}q\x00(X\x04\x00\x00\x00" * 1000)
    assert not ps._looks_like_lfs_pointer(p)


# ── version tracking reuses update_check.py ─────────────────────────────────
def test_latest_release_picks_newest_stable(monkeypatch):
    releases = [
        {"tag_name": "v1.0.0", "name": "v1.0.0", "body": "", "prerelease": False, "draft": False},
        {"tag_name": "v1.2.0", "name": "v1.2.0", "body": "", "prerelease": False, "draft": False},
        {
            "tag_name": "v2.0.0-rc1",
            "name": "v2.0.0-rc1",
            "body": "",
            "prerelease": True,
            "draft": False,
        },
    ]
    monkeypatch.setattr(ps, "fetch_releases", lambda owner_repo: releases)
    info = ps.latest_release("ethz-ins/oft-pipeline")
    assert info.tag == "v1.2.0"


def test_check_source_for_updates_offers_newer_stable(monkeypatch):
    releases = [
        {"tag_name": "v1.0.0", "name": "v1.0.0", "body": "", "prerelease": False, "draft": False},
        {"tag_name": "v1.2.0", "name": "v1.2.0", "body": "", "prerelease": False, "draft": False},
    ]
    monkeypatch.setattr(update_check, "fetch_releases", lambda owner_repo, timeout=5.0: releases)
    source = ps.Source(id="x", repo="ethz-ins/oft-pipeline", ref="v1.0.0")
    updates = ps.check_source_for_updates(source)
    assert [u.tag for u in updates] == ["v1.2.0"]
