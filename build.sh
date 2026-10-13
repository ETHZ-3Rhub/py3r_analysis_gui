#!/usr/bin/env bash
# Build Analys3R for macOS or Linux. Run from the repo root with the project
# venv active. Windows uses build.bat.
#
# Requirements:
#   pip install pyinstaller "mkdocs<2" "mkdocs-material<10"
#   pip install -e .   (and py3r-behaviour[viz] installed)
#
# Usage: ./build.sh [path/to/BohacekLabPoseModels/pose_estimation]
#
# Output: dist/Analys3R.app (macOS) or dist/Analys3R/ (Linux)
set -euo pipefail

MODELS_SRC="${1:-../BohacekLabPoseModels/pose_estimation}"

case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)  UV_TARGET=aarch64-apple-darwin ;;
    Darwin-x86_64) UV_TARGET=x86_64-apple-darwin ;;
    Linux-x86_64)  UV_TARGET=x86_64-unknown-linux-gnu ;;
    Linux-aarch64) UV_TARGET=aarch64-unknown-linux-gnu ;;
    *) echo "Unsupported platform: $(uname -s)-$(uname -m)"; exit 1 ;;
esac

if [ ! -x vendor/uv ]; then
    echo "Downloading uv ($UV_TARGET)..."
    mkdir -p vendor
    curl -fsSL "https://github.com/astral-sh/uv/releases/latest/download/uv-$UV_TARGET.tar.gz" \
        | tar -xz -C vendor --strip-components=1 "uv-$UV_TARGET/uv"
fi

mkdocs build

pyinstaller py3r_analysis_gui.spec --clean --noconfirm

if [ "$(uname -s)" = "Darwin" ]; then
    MODELS_DEST="dist/Analys3R.app/Contents/Resources/models"
    OUT="dist/Analys3R.app"
else
    MODELS_DEST="dist/Analys3R/models"
    OUT="dist/Analys3R"
fi

echo "Copying model weights..."
python scripts/materialize_models.py "$MODELS_SRC" "$MODELS_DEST"

if [ "$(uname -s)" = "Darwin" ]; then
    # Adding the models changed the bundle after PyInstaller signed it, which
    # makes other Macs report the app as "damaged". Re-apply the ad-hoc signature.
    echo "Re-signing app bundle..."
    codesign --force --deep --sign - "$OUT"
    codesign --verify --deep --strict "$OUT"
fi

echo "Build complete: $OUT"
