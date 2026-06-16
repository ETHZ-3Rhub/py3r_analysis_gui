"""Convert assets/icon_master.png (white bg) into icon.png/.ico/.icns.

White (and near-white) pixels become transparent. Run with:

    python assets/make_icons.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

OUT_DIR = Path(__file__).parent
SRC = OUT_DIR / "icon_master.png"
WHITE_THRESHOLD = 245


def make_transparent(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    data = img.getdata()
    new_data = [
        (r, g, b, 0)
        if r > WHITE_THRESHOLD and g > WHITE_THRESHOLD and b > WHITE_THRESHOLD
        else (r, g, b, a)
        for (r, g, b, a) in data
    ]
    img.putdata(new_data)
    return img


def main() -> None:
    base = make_transparent(Image.open(SRC))
    base.save(OUT_DIR / "icon.png")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    base.resize((sizes[-1], sizes[-1]), Image.LANCZOS).save(
        OUT_DIR / "icon.ico",
        sizes=[(s, s) for s in sizes],
    )

    if sys.platform == "darwin":
        iconset = OUT_DIR / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for s in (16, 32, 64, 128, 256, 512, 1024):
            base.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            if s <= 512:
                base.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT_DIR / "icon.icns")],
            check=True,
        )
        for f in iconset.glob("*.png"):
            f.unlink()
        iconset.rmdir()

    print("Wrote icon.png, icon.ico" + (", icon.icns" if sys.platform == "darwin" else ""))


if __name__ == "__main__":
    main()
