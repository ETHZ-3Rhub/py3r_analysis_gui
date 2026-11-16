"""Collect the licence texts of everything bundled in a build.

PyInstaller copies a package's code but not its ``*.dist-info`` licence files, so
a frozen build ships almost no third-party notices. This script runs after
PyInstaller, in the same Python environment that built the app, and writes a
``THIRD_PARTY_LICENSES/`` folder into the bundle:

* every bundled package's licence/notice files (found by mapping the top-level
  names inside the bundle back to installed distributions),
* the GPL-3.0 / LGPL-3.0 texts from ``licences/`` (PySide6 ships none of its own),
* this project's MIT ``LICENSE``,
* a plain-text ``NOTICE.txt`` with the source/replacement statements the licences
  require (Qt, AGPL weights + Ultralytics, and FFmpeg: GPL or LGPL depending on
  what the bundle actually contains).

Usage:
    python scripts/collect_licences.py <dest_dir> --scan <dir> [--scan <dir> ...]

``--scan`` is where PyInstaller put the bundled packages (Windows/Linux:
``dist/Analys3R/_internal``; macOS: ``Analys3R.app/Contents/Resources`` and
``.../Frameworks``). The build env must be the one the app was frozen from.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO = "ETHZ-3Rhub/py3r_analysis_gui"

# Distributions that are bundled as raw binaries (not importable packages inside
# the bundle), so the scan can't discover them. uv is vendored; its licence
# files come from the pip-installed copy (same MIT/Apache-2.0 terms).
ALWAYS_INCLUDE = ["uv"]
# Never worth a notice: this app itself (covered by LICENSE) and build tooling.
EXCLUDE = {"py3r-analysis-gui", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph"}
# Distributions known to ship no licence file upstream; covered by licences/*.txt.
COVERED_BY_STATIC = {"pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6"}

LICENCE_FILE_RE = re.compile(r"^(licen[cs]e|copying|notice|authors)", re.IGNORECASE)


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def bundled_top_level_names(scan_dirs: list[Path]) -> set[str]:
    """Names of the packages/modules/libraries directly inside the scan dirs."""
    names: set[str] = set()
    for d in scan_dirs:
        for entry in d.iterdir():
            names.add(entry.name.split(".")[0])
    return names


def select_distributions(scan_dirs: list[Path]) -> list[metadata.Distribution]:
    top_to_dists = metadata.packages_distributions()
    wanted: dict[str, metadata.Distribution] = {}
    for top in bundled_top_level_names(scan_dirs):
        for dist_name in top_to_dists.get(top, []):
            wanted[norm(dist_name)] = metadata.distribution(dist_name)
    for dist_name in ALWAYS_INCLUDE:
        wanted.setdefault(norm(dist_name), metadata.distribution(dist_name))
    return sorted(
        (d for key, d in wanted.items() if key not in EXCLUDE),
        key=lambda d: norm(d.metadata["Name"]),
    )


def licence_files(dist: metadata.Distribution) -> list[tuple[Path, Path]]:
    """(source path, path relative to the dist-info dir) for each licence file."""
    found = []
    for f in dist.files or []:
        parts = f.parts
        info_idx = next((i for i, p in enumerate(parts) if p.endswith(".dist-info")), None)
        if info_idx is None:
            continue
        rel = Path(*parts[info_idx + 1 :])
        if not rel.parts:
            continue
        if rel.parts[0] == "licenses" or LICENCE_FILE_RE.match(rel.name):
            src = Path(dist.locate_file(f))
            if src.is_file():
                found.append((src, rel))
    return found


def licence_label(dist: metadata.Distribution) -> str:
    md = dist.metadata
    expr = md.get("License-Expression")
    if expr:
        return expr
    lic = (md.get("License") or "").strip()
    if lic and len(lic) <= 60 and "\n" not in lic:
        return lic
    classifiers = [
        c.split("::")[-1].strip() for c in md.get_all("Classifier", []) if "License ::" in c
    ]
    return ", ".join(classifiers) or "see licence files"


def copy_package_licences(dists: list[metadata.Distribution], dest: Path):
    rows, missing = [], []
    for dist in dists:
        name, version = dist.metadata["Name"], dist.version
        files = licence_files(dist)
        for src, rel in files:
            out = dest / "packages" / f"{name}-{version}" / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
        if not files and norm(name) not in COVERED_BY_STATIC:
            missing.append(f"{name} {version}")
        rows.append((name, version, licence_label(dist), len(files)))
    return rows, missing


def has_gpl_ffmpeg(scan_dirs: list[Path]) -> bool:
    """FFmpeg built with GPL codecs bundles libx264/libx265 alongside libavcodec."""
    for d in scan_dirs:
        for pattern in ("libx264*", "libx265*", "**/libx264*", "**/libx265*"):
            if any(d.glob(pattern)):
                return True
    return False


def has_lgpl_ffmpeg_dll(scan_dirs: list[Path]) -> bool:
    """Windows opencv wheels load an LGPL-only FFmpeg as a separate DLL."""
    return any(any(d.glob("**/opencv_videoio_ffmpeg*.dll")) for d in scan_dirs)


def build_notice(gpl_ffmpeg: bool, lgpl_ffmpeg: bool) -> str:
    """Plain-text notice holding only what the licences require beyond the texts."""
    out = [
        "THIRD-PARTY NOTICES",
        "",
        "Licence texts for bundled components are in this folder (packages/, "
        "GPL-3.0.txt, LGPL-3.0.txt). Analys3R's own source is MIT-licensed "
        "(LICENSE-Analys3R-MIT.txt), except app/trackers/track.py, which is "
        "AGPL-3.0 when combined with Ultralytics.",
        "",
        "Qt / PySide6 (LGPL-3.0): used unmodified and linked dynamically; the Qt "
        "libraries in this bundle can be replaced with a compatible build. Source: "
        "https://code.qt.io",
        "",
        "Model weights (models/), Ultralytics and ultralytics-thop (AGPL-3.0): "
        "source at https://github.com/ETHZ-INS/BohacekLabPoseModels and "
        "https://github.com/ultralytics/ultralytics",
        "",
    ]
    if gpl_ffmpeg:
        out += [
            "FFmpeg and codecs (GPL-3.0): the libav*, libsw*, libpostproc, libx264 and "
            "libx265 libraries in this bundle come from the opencv-python wheel and "
            "are used unmodified. Source: https://ffmpeg.org/download.html, "
            "https://www.videolan.org/developers/x264.html, "
            "https://bitbucket.org/multicoreware/x265_git and "
            "https://github.com/opencv/opencv-python. For three years from the date "
            "of this release, corresponding source is also available on request at "
            f"https://github.com/{REPO}/issues",
            "",
        ]
    elif lgpl_ffmpeg:
        out += [
            "FFmpeg (LGPL-2.1): cv2/opencv_videoio_ffmpeg*.dll, from the opencv-python "
            "wheel, is used unmodified and loaded dynamically; it can be replaced with "
            "a compatible build. Source: https://ffmpeg.org/download.html and "
            "https://github.com/opencv/opencv-python. For three years from the date of "
            "this release, corresponding source is also available on request at "
            f"https://github.com/{REPO}/issues",
            "",
        ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dest", type=Path, help="THIRD_PARTY_LICENSES folder to create")
    ap.add_argument("--scan", type=Path, action="append", required=True, help="bundle dir")
    args = ap.parse_args()

    scan_dirs = [p for p in args.scan if p.is_dir()]
    if not scan_dirs:
        print(f"none of the --scan dirs exist: {args.scan}", file=sys.stderr)
        return 1

    if args.dest.exists():
        shutil.rmtree(args.dest)
    args.dest.mkdir(parents=True)

    dists = select_distributions(scan_dirs)
    rows, missing = copy_package_licences(dists, args.dest)

    for f in sorted((REPO_ROOT / "licences").glob("*.txt")):
        shutil.copy2(f, args.dest / f.name)
    shutil.copy2(REPO_ROOT / "LICENSE", args.dest / "LICENSE-Analys3R-MIT.txt")

    gpl_ffmpeg = has_gpl_ffmpeg(scan_dirs)
    (args.dest / "NOTICE.txt").write_text(
        build_notice(gpl_ffmpeg, has_lgpl_ffmpeg_dll(scan_dirs)), encoding="utf-8"
    )

    print(f"Wrote {args.dest}: {len(rows)} packages, GPL FFmpeg: {gpl_ffmpeg}")
    if missing:
        print("WARNING: no licence file found for: " + ", ".join(missing), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
