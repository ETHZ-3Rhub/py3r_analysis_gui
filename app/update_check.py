"""GitHub-release update checking — network and version-comparison logic only.

No Qt here; see app/update_indicator.py for the bottom-bar indicator widget
and release-notes dialog that use this.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

REPO = "ETHZ-3Rhub/py3r_analysis_gui"
API_URL = f"https://api.github.com/repos/{REPO}/releases"


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    name: str
    body: str
    prerelease: bool


def _parse_version(v: str) -> tuple[int, ...]:
    """ "v0.5.1" / "0.5.1" -> (0, 5, 1). Stops at the first non-numeric dotted
    component, so a dev/local build version (e.g. "0.5.2.dev3") still
    compares sanely against release tags rather than parsing garbage."""
    v = v.strip()
    if v[:1] in ("v", "V"):
        v = v[1:]
    parts: list[int] = []
    for p in v.split("."):
        if not p.isdigit():
            break
        parts.append(int(p))
    return tuple(parts) or (0,)


def fetch_releases(timeout: float = 5.0) -> list[dict]:
    req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        releases = json.load(resp)
    return [r for r in releases if not r.get("draft")]


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_html_comments(body: str) -> str:
    """GitHub's own markdown renderer hides HTML comments — e.g. the PR
    template's placeholder guidance text left in a release's body — but Qt's
    QTextEdit.setMarkdown() doesn't, so it leaks through as visible text if
    left in."""
    return _HTML_COMMENT_RE.sub("", body).strip()


def _to_info(r: dict) -> ReleaseInfo:
    return ReleaseInfo(
        tag=r["tag_name"],
        name=r.get("name") or r["tag_name"],
        body=_strip_html_comments(r.get("body") or ""),
        prerelease=bool(r["prerelease"]),
    )


def check_for_updates(current_version: str) -> list[ReleaseInfo]:
    """Newer releases the user should be told about.

    The installed version string carries no prerelease marker of its own
    (tags/versions are plain "0.5.1"), so whether the *current* install is a
    prerelease is read from that release's own GitHub metadata. A stable
    install is only ever offered the newest stable release; a prerelease
    install is offered the newest stable *and* the newest prerelease,
    whichever (or both) are actually newer.

    Returns [] on any network/parse failure or if nothing is newer — a
    failed check should never surface as an error, just retry next launch.
    """
    try:
        releases = fetch_releases()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return []
    if not releases:
        return []

    current_tuple = _parse_version(current_version)
    current_entry = next(
        (r for r in releases if _parse_version(r["tag_name"]) == current_tuple), None
    )
    current_is_prerelease = bool(current_entry["prerelease"]) if current_entry else False

    stable = [r for r in releases if not r["prerelease"]]
    prereleases = [r for r in releases if r["prerelease"]]

    results: list[ReleaseInfo] = []

    if stable:
        latest_stable = max(stable, key=lambda r: _parse_version(r["tag_name"]))
        if _parse_version(latest_stable["tag_name"]) > current_tuple:
            results.append(_to_info(latest_stable))

    if current_is_prerelease and prereleases:
        latest_pre = max(prereleases, key=lambda r: _parse_version(r["tag_name"]))
        if _parse_version(latest_pre["tag_name"]) > current_tuple:
            results.append(_to_info(latest_pre))

    return results
