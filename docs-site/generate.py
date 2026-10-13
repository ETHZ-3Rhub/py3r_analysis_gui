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

# (key, button label, asset filename pattern, note shown under the latest button).
# Windows assets predate the platform suffix, so it matches any zip without one.
PLATFORMS = [
    (
        "windows",
        "Windows",
        re.compile(r"^Analys3R-v(?!.*-(macos|linux)-).*\.zip$"),
        "Windows 10/11 · ZIP archive, unzip and run — no installer required.",
    ),
    (
        "macos",
        "macOS",
        re.compile(r"^Analys3R-v.*-macos-arm64\.zip$"),
        "Apple Silicon Macs (M1 or later) · unzip and drag Analys3R to Applications. "
        "See first-launch steps below.",
    ),
]

MACOS_FIRST_LAUNCH = """\
      <details class="first-launch">
        <summary>Opening on macOS for the first time</summary>
        <p>Analys3R isn't notarised by Apple yet, so macOS blocks it on first launch.
        Do this once after installing:</p>
        <ol>
          <li>Try to open Analys3R from Applications, then click <b>Done</b> on the warning.</li>
          <li>Open <b>System Settings → Privacy &amp; Security</b>, scroll down and click
          <b>Open Anyway</b> next to the Analys3R message.</li>
        </ol>
        <p>If macOS says the app is damaged, or there's no Open Anyway button, run this
        in Terminal instead:</p>
        <pre>xattr -dr com.apple.quarantine /Applications/Analys3R.app</pre>
      </details>"""

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


def platform_urls(release: dict) -> dict[str, str]:
    """Download URL per platform key, in PLATFORMS order, for the platforms built."""
    urls = {}
    for key, _label, pattern, _note in PLATFORMS:
        for asset in release["assets"]:
            if pattern.match(asset["name"]):
                urls[key] = asset["browser_download_url"]
                break
    return urls


def _label(key: str) -> str:
    return next(label for k, label, _p, _n in PLATFORMS if k == key)


def fmt_date(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d")


def _links(release: dict) -> str:
    return " · ".join(
        f'<a href="{url}">{_label(key)}</a>' for key, url in platform_urls(release).items()
    )


def _rows(releases: list[dict], empty_message: str) -> str:
    rows = "\n".join(
        f"        <tr>\n"
        f"          <td>{r['tag_name']}</td>\n"
        f"          <td>{fmt_date(r['published_at'])}</td>\n"
        f"          <td>{_links(r)}</td>\n"
        f"        </tr>"
        for r in releases
    )
    return rows or f'        <tr><td colspan="3">{empty_message}</td></tr>'


def render(releases: list[dict]) -> str:
    with_assets = [r for r in releases if platform_urls(r)]
    if not with_assets:
        sys.exit("No published release has a matching build asset (Analys3R-v*.zip).")

    stable = [r for r in with_assets if not r["prerelease"]]
    prereleases = [r for r in with_assets if r["prerelease"]]

    if stable:
        latest, *older_stable = stable
        urls = platform_urls(latest)
        buttons = "\n".join(
            f'        <a class="btn" href="{url}">Download for {_label(key)}</a>'
            for key, url in urls.items()
        )
        notes = "\n".join(
            f'      <p class="note">{note}</p>' for key, _l, _p, note in PLATFORMS if key in urls
        )
        latest_block = (
            f'      <p class="version">Latest stable release</p>\n'
            f'      <p style="font-size:1.3rem; font-weight:600; margin:0;">'
            f"{latest['tag_name']}</p>\n"
            f'      <div class="buttons">\n{buttons}\n      </div>\n'
            f"{notes}"
        )
        if "macos" in urls:
            latest_block += "\n" + MACOS_FIRST_LAUNCH
        tagline_platforms = " and ".join(_label(key) for key in urls)
    else:
        older_stable = []
        latest_block = (
            '      <p class="no-stable">No stable release yet — '
            "see pre-release / testing builds below.</p>"
        )
        tagline_platforms = "Windows"

    html = TEMPLATE.read_text()
    return (
        html.replace("{{LATEST_BLOCK}}", latest_block)
        .replace("{{TAGLINE_PLATFORMS}}", tagline_platforms)
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
