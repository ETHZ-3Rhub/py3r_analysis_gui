#!/usr/bin/env python3
"""Render docs-site/template.html into a build dir from published GitHub releases.

Used both by .github/workflows/pages.yml (triggered on release: published) and
locally for previewing the page before it goes live. Requires a GITHUB_TOKEN
with access to the repo (the private repo's API 404s without one).

Usage:
    GITHUB_TOKEN=... python docs-site/generate.py [output_dir]
"""

import json
import os
import re
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = "ETHZ-3Rhub/py3r_analysis_gui"
API_URL = f"https://api.github.com/repos/{REPO}/releases"
ASSET_RE = re.compile(r"^Analys3R-v.*\.zip$")

SITE_DIR = Path(__file__).parent
TEMPLATE = SITE_DIR / "template.html"
ASSETS = SITE_DIR / "assets"


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit(
            "GITHUB_TOKEN is not set. Export a token with access to this repo "
            "(e.g. `export GITHUB_TOKEN=$(gh auth token)`) and re-run."
        )
    return token


def fetch_releases() -> list[dict]:
    req = urllib.request.Request(
        API_URL,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        releases = json.load(resp)
    published = [r for r in releases if not r["draft"]]
    published.sort(key=lambda r: r["published_at"], reverse=True)
    return published


def asset_url(release: dict) -> str | None:
    for asset in release["assets"]:
        if ASSET_RE.match(asset["name"]):
            return asset["browser_download_url"]
    return None


def fmt_date(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d")


def _rows(releases: list[dict], empty_message: str) -> str:
    rows = "\n".join(
        f"        <tr>\n"
        f"          <td>{r['tag_name']}</td>\n"
        f"          <td>{fmt_date(r['published_at'])}</td>\n"
        f'          <td><a href="{asset_url(r)}">{asset_url(r).rsplit("/", 1)[-1]}</a></td>\n'
        f"        </tr>"
        for r in releases
    )
    return rows or f'        <tr><td colspan="3">{empty_message}</td></tr>'


def render(releases: list[dict]) -> str:
    with_assets = [r for r in releases if asset_url(r)]
    if not with_assets:
        sys.exit("No published release has a matching build asset (Analys3R-v*.zip).")

    stable = [r for r in with_assets if not r["prerelease"]]
    prereleases = [r for r in with_assets if r["prerelease"]]

    if stable:
        latest, *older_stable = stable
        latest_block = (
            f'      <p class="version">Latest stable release</p>\n'
            f'      <p style="font-size:1.3rem; font-weight:600; margin:0;">'
            f"{latest['tag_name']}</p>\n"
            f'      <a class="btn" href="{asset_url(latest)}">\n'
            f"        Download for Windows\n"
            f"      </a>\n"
            f'      <p class="note">Windows 10/11 · ZIP archive, unzip and run '
            f"— no installer required.</p>"
        )
    else:
        older_stable = []
        latest_block = (
            '      <p class="no-stable">No stable release yet — '
            "see pre-release / testing builds below.</p>"
        )

    html = TEMPLATE.read_text()
    return (
        html.replace("{{LATEST_BLOCK}}", latest_block)
        .replace("{{STABLE_OLDER_ROWS}}", _rows(older_stable, "No older stable releases yet."))
        .replace("{{PRERELEASE_ROWS}}", _rows(prereleases, "No pre-release builds."))
    )


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else SITE_DIR / "_build"
    releases = fetch_releases()
    html = render(releases)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html)
    shutil.copytree(ASSETS, out_dir / "assets", dirs_exist_ok=True)
    print(f"Wrote {out_dir / 'index.html'} ({len(releases)} published releases found)")


if __name__ == "__main__":
    main()
