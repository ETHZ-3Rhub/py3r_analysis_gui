"""Git-based pipeline source install/update — network and unpack logic only.

No Qt here; see app/pipeline_manager_dialog.py for the GUI that uses this.

A pipeline source is a public GitHub repo laid out as configs/ scripts/ models/
at its root — see pipeline_config.py's discover() for how an installed source's
files are found and resolved. Installed via plain HTTP zipball download — no git
binary, no GitPython/libgit2. Version tracking (including prerelease inference)
reuses update_check.py's logic verbatim, parameterized by repo, so there's one
place that knows how to talk to GitHub Releases.

Trust is untouched by any of this: everything landing under /user/sources/ is
still untrusted-warn-every-run per pipeline_config.py's authorship rule.
"""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import tomllib
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app import pipeline_config
from app.update_check import (
    ReleaseInfo,
    check_for_updates,
    fetch_releases,
    parse_version,
    to_release_info,
)

_LFS_POINTER_HEADER = b"version https://git-lfs.github.com/spec/v1"
_LFS_SNIFF_MAX_BYTES = 4096  # real weight files are always far bigger than a pointer


class SourceError(Exception):
    """A source repo/install problem. The message is shown verbatim to the user."""


@dataclass
class Source:
    id: str
    repo: str  # "owner/name"
    ref: str  # currently installed tag


def sanitize_id(owner_repo: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", owner_repo.strip("/")).strip("-").lower()


def parse_repo_url(text: str) -> str | None:
    """Tolerant "owner/repo" extraction from whatever a lab user pastes — a
    full GitHub URL (with or without a scheme, a trailing /tree/main,
    /releases, .git, query string, ...) or a bare "owner/repo". Returns None
    if it can't find at least two path segments to work with."""
    s = text.strip()
    if not s:
        return None
    s = re.sub(r"^\w+://", "", s)  # https:// , git:// , ...
    s = re.sub(r"^www\.", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^github\.com/", "", s, flags=re.IGNORECASE)
    s = s.split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    repo = re.sub(r"\.git$", "", repo, flags=re.IGNORECASE)
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def source_dir(source_id: str) -> Path:
    return pipeline_config.user_sources_dir() / source_id


# ── sources.toml ───────────────────────────────────────────────────────────
def _sources_path() -> Path:
    return pipeline_config.user_dir() / "sources.toml"


def load_sources() -> list[Source]:
    path = _sources_path()
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[Source] = []
    for entry in data.get("source", []):
        try:
            out.append(Source(id=entry["id"], repo=entry["repo"], ref=entry["ref"]))
        except KeyError:
            continue
    return out


def save_sources(sources: list[Source]) -> None:
    path = _sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [f'[[source]]\nid = "{s.id}"\nrepo = "{s.repo}"\nref = "{s.ref}"\n' for s in sources]
    path.write_text("\n".join(blocks), encoding="utf-8")


def add_or_replace(sources: list[Source], updated: Source) -> list[Source]:
    return [s for s in sources if s.id != updated.id] + [updated]


def uninstall_source(source_id: str) -> None:
    """Remove an installed source's folder. Caller is responsible for dropping
    it from sources.toml (via load_sources/save_sources) after this succeeds."""
    d = source_dir(source_id)
    if d.is_dir():
        shutil.rmtree(d)


# ── version tracking (reuses update_check.py verbatim) ──────────────────────
def latest_release(owner_repo: str) -> ReleaseInfo | None:
    """Newest stable release, or None on any failure/empty repo. Used when
    adding a new source with no ref override."""
    try:
        releases = fetch_releases(owner_repo)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    stable = [r for r in releases if not r["prerelease"]]
    if not stable:
        return None
    return to_release_info(max(stable, key=lambda r: parse_version(r["tag_name"])))


def check_source_for_updates(source: Source) -> list[ReleaseInfo]:
    """Same inference update_check.py uses for the app itself: if the currently
    installed ref is itself a prerelease tag, offer the latest stable *and*
    latest prerelease; otherwise only the latest stable. No separate persisted
    toggle — it's read from the ref's own GitHub metadata each check."""
    return check_for_updates(source.ref, owner_repo=source.repo)


# ── install ──────────────────────────────────────────────────────────────
def install_source(owner_repo: str, ref: str, dest: Path) -> None:
    """Fetch *owner_repo* at *ref*, verify it's a pipeline source repo, resolve
    any model pointers, and unpack into *dest* (replacing any existing
    contents). Raises SourceError, with a user-facing message, on any problem
    — nothing under *dest* is touched until the fetch and validation succeed."""
    data = _download(
        f"https://codeload.github.com/{owner_repo}/zip/refs/tags/{ref}",
        timeout=30,
        what=f"{owner_repo}@{ref}",
    )
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SourceError(f"{owner_repo}@{ref} didn't return a valid archive.") from exc

    with tempfile.TemporaryDirectory() as tmp:
        zf.extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise SourceError(f"unexpected archive layout for {owner_repo}@{ref}.")
        root = roots[0]
        if not any((root / d).is_dir() for d in ("configs", "scripts", "models")):
            raise SourceError(
                f"{owner_repo}@{ref} has no configs/, scripts/, or models/ folder "
                "at its root — not a pipeline source repo."
            )

        _guard_lfs_pointers(root / "models")
        resolve_model_pointers(root / "models")

        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(dest))


# ── model pointers (source.toml inside models/<name>/) ──────────────────────
def resolve_model_pointers(models_dir: Path) -> None:
    """For every models/<name>/source.toml pointer, fetch the target (a
    release asset or a bare URL — both plain HTTP, sidestepping LFS entirely)
    and unpack it in place of the pointer. A model folder with no pointer is
    left untouched — its checked-in contents already are the model."""
    if not models_dir.is_dir():
        return
    for model_dir in sorted(p for p in models_dir.iterdir() if p.is_dir()):
        pointer = model_dir / "source.toml"
        if not pointer.is_file():
            continue
        try:
            spec = tomllib.loads(pointer.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SourceError(
                f"couldn't parse {pointer.name} for model '{model_dir.name}': {exc}"
            ) from exc
        url = _pointer_url(spec, model_dir.name)
        data = _download(url, timeout=60, what=f"model '{model_dir.name}'")
        pointer.unlink()
        _unpack_model_asset(data, model_dir)


def _pointer_url(spec: dict, model_name: str) -> str:
    url = spec.get("url")
    if url:
        return url
    repo, ref, asset = spec.get("repo"), spec.get("ref"), spec.get("asset")
    if not (repo and ref and asset):
        raise SourceError(
            f"models/{model_name}/source.toml needs either 'url', or 'repo' + 'ref' + 'asset'."
        )
    return f"https://github.com/{repo}/releases/download/{ref}/{asset}"


def _unpack_model_asset(data: bytes, model_dir: Path) -> None:
    """A model asset must be a zip of the model folder's contents — a bare
    weight file has no filename to recover from an HTTP response, so it can't
    be placed correctly; that's a documented constraint on the asset format,
    not a fallback case to silently guess at."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SourceError(
            f"model asset for '{model_dir.name}' isn't a zip — package it as a zip "
            "of the model folder's contents (weights/, meta/, ...)."
        ) from exc
    zf.extractall(model_dir)


def _download(url: str, *, timeout: float, what: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceError(f"couldn't download {what}: {exc}") from exc


# ── LFS pointer guard ────────────────────────────────────────────────────────
def _guard_lfs_pointers(models_dir: Path) -> None:
    """Catches the common mistake of checking LFS-tracked weights straight
    into a pipeline repo without a source.toml pointer — GitHub's zip download
    silently substitutes tiny pointer text files for the real content, which
    would otherwise install as a corrupt model with no error until tracking."""
    if not models_dir.is_dir():
        return
    for path in models_dir.rglob("*"):
        if path.is_file() and _looks_like_lfs_pointer(path):
            raise SourceError(
                f"{path.relative_to(models_dir.parent)} is a git-lfs pointer, not real "
                "model content — GitHub's zip download doesn't fetch LFS files. Publish "
                "this model as a release asset and add a models/<name>/source.toml "
                "pointer instead."
            )


def _looks_like_lfs_pointer(path: Path) -> bool:
    try:
        if path.stat().st_size > _LFS_SNIFF_MAX_BYTES:
            return False
        head = path.read_bytes()[: len(_LFS_POINTER_HEADER)]
    except OSError:
        return False
    return head == _LFS_POINTER_HEADER
