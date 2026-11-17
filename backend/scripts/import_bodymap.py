"""Import the front/back body figures used by the Muscle Mixer picker.

Source: react-native-body-highlighter 3.1.3 (MIT, (c) 2022 ELABBASSI Hicham). 3.1.3 is
pinned on purpose: the 3.2.0 package on npm ships without its assets/ folder.

Run it once (from backend/) and commit what it writes:

    python scripts/import_bodymap.py                 # npm pack into a temp dir
    python scripts/import_bodymap.py path/to/package # an already-unpacked package

It writes bodymap_paths.py (plain data, no app code) and copies the package licence to
LICENSE-react-native-body-highlighter.txt. The app never depends on npm.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

PACKAGE = "react-native-body-highlighter"
VERSION = "3.1.3"
BACKEND = Path(__file__).resolve().parent.parent
OUT_FILE = BACKEND / "bodymap_paths.py"
LICENSE_OUT = BACKEND / "LICENSE-react-native-body-highlighter.txt"
CROP_MARGIN = 6  # viewBox units kept above and below the figure

# Original viewBoxes from components/SvgMaleWrapper.tsx (front and back share one canvas).
VIEW_X = {"front": 0, "back": 724}
VIEW_W, VIEW_H = 724, 1448

# -----------------------------------------------------------------------------
# SVG path bounds (Béziers: control-point hull, which always contains the curve;
# arcs: the real ellipse from the SVG spec's centre parameterisation, sampled)
# -----------------------------------------------------------------------------
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


class _Scanner:
    def __init__(self, d: str):
        self.d, self.i = d, 0

    def _skip(self):
        while self.i < len(self.d) and self.d[self.i] in " ,\t\r\n":
            self.i += 1

    def command(self) -> Optional[str]:
        self._skip()
        if self.i < len(self.d) and self.d[self.i].isalpha():
            self.i += 1
            return self.d[self.i - 1]
        return None

    def at_number(self) -> bool:
        self._skip()
        return self.i < len(self.d) and (self.d[self.i] in "+-." or self.d[self.i].isdigit())

    def number(self) -> float:
        self._skip()
        m = _NUM.match(self.d, self.i)
        if not m:
            raise ValueError(f"bad path data near {self.d[self.i:self.i + 20]!r}")
        self.i = m.end()
        return float(m.group())

    def flag(self) -> float:
        """Arc flags are a single 0/1 and may be written without separators ("01")."""
        self._skip()
        ch = self.d[self.i]
        if ch not in "01":
            raise ValueError(f"bad arc flag near {self.d[self.i:self.i + 20]!r}")
        self.i += 1
        return float(ch)


def _arc_points(x1, y1, rx, ry, rot_deg, large, sweep, x2, y2, samples=32):
    """Points along an SVG elliptical arc (SVG 1.1 spec, appendix F.6.5)."""
    if rx == 0 or ry == 0 or (x1, y1) == (x2, y2):
        return [(x1, y1), (x2, y2)]
    phi = math.radians(rot_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cos_p * dx + sin_p * dy, -sin_p * dx + cos_p * dy
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = (rx * ry) ** 2 - (rx * y1p) ** 2 - (ry * x1p) ** 2
    den = (rx * y1p) ** 2 + (ry * x1p) ** 2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    ccx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    ccy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    start, delta = angle(1, 0, ux, uy), angle(ux, uy, vx, vy)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    pts = []
    for i in range(samples + 1):
        t = start + delta * i / samples
        pts.append(
            (
                ccx + rx * math.cos(t) * cos_p - ry * math.sin(t) * sin_p,
                ccy + rx * math.cos(t) * sin_p + ry * math.sin(t) * cos_p,
            )
        )
    return pts


def path_bounds(d: str) -> Tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y) of the drawn path."""
    sc = _Scanner(d)
    xs: List[float] = []
    ys: List[float] = []
    cx = cy = sx = sy = 0.0
    last_c2: Optional[Tuple[float, float]] = None
    last_q: Optional[Tuple[float, float]] = None
    cmd = None

    def add(x, y):
        xs.append(x)
        ys.append(y)

    while True:
        new = sc.command()
        if new:
            cmd = new
        elif not sc.at_number():
            break
        if cmd is None:
            raise ValueError("path data must start with a command")
        up, rel = cmd.upper(), cmd.islower()
        if up == "Z":
            cx, cy = sx, sy
            last_c2 = last_q = None
            continue
        if up == "A":
            rx, ry = abs(sc.number()), abs(sc.number())
            rot = sc.number()
            large, sweep = sc.flag(), sc.flag()
            x, y = sc.number(), sc.number()
            if rel:
                x, y = x + cx, y + cy
            for px, py in _arc_points(cx, cy, rx, ry, rot, large, sweep, x, y):
                add(px, py)
            cx, cy = x, y
            last_c2 = last_q = None
            continue
        vals = [sc.number() for _ in range(_ARGS[up])]
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        if up == "M":
            cx, cy = vals[0] + ox, vals[1] + oy
            sx, sy = cx, cy
            add(cx, cy)
            cmd = "l" if rel else "L"  # extra pairs after M are line-tos
            last_c2 = last_q = None
        elif up == "L" or up == "T":
            if up == "T":
                qx, qy = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
                add(qx, qy)
                last_q = (qx, qy)
            else:
                last_q = None
            cx, cy = vals[0] + ox, vals[1] + oy
            add(cx, cy)
            last_c2 = None
        elif up == "H":
            cx = vals[0] + ox
            add(cx, cy)
            last_c2 = last_q = None
        elif up == "V":
            cy = vals[0] + oy
            add(cx, cy)
            last_c2 = last_q = None
        elif up == "C":
            pts = [(vals[i] + ox, vals[i + 1] + oy) for i in (0, 2, 4)]
            for p in pts:
                add(*p)
            last_c2, (cx, cy) = pts[1], pts[2]
            last_q = None
        elif up == "S":
            c1 = (2 * cx - last_c2[0], 2 * cy - last_c2[1]) if last_c2 else (cx, cy)
            c2 = (vals[0] + ox, vals[1] + oy)
            end = (vals[2] + ox, vals[3] + oy)
            for p in (c1, c2, end):
                add(*p)
            last_c2, (cx, cy) = c2, end
            last_q = None
        elif up == "Q":
            q = (vals[0] + ox, vals[1] + oy)
            end = (vals[2] + ox, vals[3] + oy)
            add(*q)
            add(*end)
            last_q, (cx, cy) = q, end
            last_c2 = None
    if not xs:
        raise ValueError("empty path")
    return min(xs), min(ys), max(xs), max(ys)


def union_bounds(ds: Iterable[str]) -> Tuple[float, float, float, float]:
    boxes = [path_bounds(d) for d in ds]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


# -----------------------------------------------------------------------------
# Package parsing
# -----------------------------------------------------------------------------
def parse_regions(ts_source: str) -> List[Dict]:
    """[{slug, paths}] from assets/body{Front,Back}.ts (left/right/common merged)."""
    regions = []
    chunks = re.split(r"\bslug:\s*", ts_source)[1:]
    for chunk in chunks:
        slug = re.match(r'"([a-z-]+)"', chunk).group(1)
        paths = re.findall(r'"([Mm][^"]*)"', chunk)
        if not paths:
            raise ValueError(f"region {slug} has no paths")
        regions.append({"slug": slug, "paths": paths})
    return regions


def parse_outlines(wrapper_source: str) -> Tuple[str, str]:
    front = re.search(r'side === "front"[\s\S]*?\bd="([^"]+)"', wrapper_source)
    back = re.search(r'side === "back"[\s\S]*?\bd="([^"]+)"', wrapper_source)
    if not (front and back):
        raise ValueError("could not find the front/back outline paths")
    return front.group(1), back.group(1)


def crop_viewbox(side: str, ds: Iterable[str]) -> str:
    _, min_y, _, max_y = union_bounds(ds)
    top = max(0, math.floor(min_y) - CROP_MARGIN)
    bottom = min(VIEW_H, math.ceil(max_y) + CROP_MARGIN)
    return f"{VIEW_X[side]} {top} {VIEW_W} {bottom - top}"


def fetch_package(dest: Path) -> Path:
    subprocess.run(
        ["npm", "pack", f"{PACKAGE}@{VERSION}", "--silent"],
        cwd=dest,
        check=True,
        capture_output=True,
    )
    tgz = next(dest.glob(f"{PACKAGE}-{VERSION}.tgz"))
    with tarfile.open(tgz) as tar:
        tar.extractall(dest, filter="data")
    return dest / "package"


def render(front, back, outline_front, outline_back, vb_front, vb_back) -> str:
    lines = [
        '"""GENERATED by scripts/import_bodymap.py. Do not edit; re-run the script instead.',
        "",
        f"Source: {PACKAGE} {VERSION} (male front/back figures).",
        "Licence: MIT, (c) 2022 ELABBASSI Hicham. See LICENSE-react-native-body-highlighter.txt.",
        '"""',
        "",
        "# fmt: off",
        "# flake8: noqa",
        f"VIEWBOX_FRONT = {vb_front!r}",
        f"VIEWBOX_BACK = {vb_back!r}",
        f"OUTLINE_FRONT = {outline_front!r}",
        f"OUTLINE_BACK = {outline_back!r}",
    ]
    for name, regions in (("FRONT", front), ("BACK", back)):
        lines.append(f"{name} = [")
        for r in regions:
            lines.append(f"    {{'slug': {r['slug']!r}, 'paths': [")
            lines.extend(f"        {p!r}," for p in r["paths"])
            lines.append("    ]},")
        lines.append("]")
    return "\n".join(lines) + "\n"


def main(argv: List[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(argv[0]) if argv else fetch_package(Path(tmp))
        front = parse_regions((pkg / "assets" / "bodyFront.ts").read_text(encoding="utf-8"))
        back = parse_regions((pkg / "assets" / "bodyBack.ts").read_text(encoding="utf-8"))
        wrapper = (pkg / "components" / "SvgMaleWrapper.tsx").read_text(encoding="utf-8")
        outline_front, outline_back = parse_outlines(wrapper)
        vb_front = crop_viewbox("front", [outline_front] + [p for r in front for p in r["paths"]])
        vb_back = crop_viewbox("back", [outline_back] + [p for r in back for p in r["paths"]])
        OUT_FILE.write_text(
            render(front, back, outline_front, outline_back, vb_front, vb_back), encoding="utf-8"
        )
        shutil.copyfile(pkg / "LICENSE", LICENSE_OUT)
    print(f"front: {len(front)} regions, viewBox {vb_front}")
    print(f"back:  {len(back)} regions, viewBox {vb_back}")
    print(f"wrote {OUT_FILE.name} and {LICENSE_OUT.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
