"""
Usage:
  python scripts/optimize_images.py static/img
  python scripts/optimize_images.py static/uploads
"""

import sys
from pathlib import Path

from PIL import Image

EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def optimize_one(p: Path, quality=82):
    try:
        im = Image.open(p)
        if im.mode in ("RGBA", "P"):
            im = im.convert("RGB")
        # overwrite with optimized JPEG (or keep WEBP as-is)
        if p.suffix.lower() == ".webp":
            im.save(p, method=6, optimize=True, quality=quality)
        else:
            im.save(p, "JPEG", optimize=True, progressive=True, quality=quality)
        print("optimized:", p)
    except Exception as e:
        print("skip:", p, "-", e)


def main(folder: str):
    root = Path(folder)
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in EXTS and p.stat().st_size > 10 * 1024:
            optimize_one(p)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Provide folder path, e.g. static/img or static/uploads")
        sys.exit(1)
    main(sys.argv[1])
