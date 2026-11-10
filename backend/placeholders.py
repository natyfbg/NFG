"""SVG placeholder art for exercises that don't have real images yet.

Placeholders are deliberately abstract: the muscle group's colour, its label and
a soft pattern that varies per exercise. They never depict a body or technique,
so nobody mistakes them for form guidance, and they carry no exercise name
(the page and cards already show it).

Size matches the standard for real exercise images: 1080 x 1350 (4:5 portrait).
Real photos/illustrations replace a placeholder by listing `images:` in the
exercise's content file.
"""

from __future__ import annotations

import hashlib
from typing import List
from xml.sax.saxutils import escape

import taxonomy as tx

WIDTH, HEIGHT = 1080, 1350  # 4:5, the standard exercise image ratio
MARGIN = 88
FONT = "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif"


def _shade(hex_color: str, factor: float) -> str:
    """Darken (factor < 1) a #RRGGBB colour."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, int(c * factor))) for c in (r, g, b)))


def primary_group_key(ex: dict) -> str:
    groups = tx.groups_for_muscles(ex["muscles"]["primary"])
    for gk, group in tx.MUSCLE_GROUPS.items():
        if group.get("category") == ex.get("category"):
            return gk
    if ex.get("full_body") or len(groups) >= tx.FULL_BODY_MIN_GROUPS:
        return "full_body"
    return groups[0] if groups else "full_body"


def equipment_text(equipment: List[str]) -> str:
    return tx.equipment_text(equipment)


def _seeded(slug: str, count: int) -> List[float]:
    """Deterministic numbers in [0, 1) from the slug, so each placeholder differs a bit."""
    digest = hashlib.sha256(slug.encode("utf-8")).digest()
    return [digest[i] / 256 for i in range(count)]


def render_svg(label: str, color: str, seed: str, aria: str) -> str:
    deep = _shade(color, 0.52)
    a, b, c, d = _seeded(seed, 4)
    cx1, cy1, r1 = int(WIDTH * (0.62 + 0.3 * a)), int(HEIGHT * (0.18 + 0.2 * b)), int(300 + 140 * c)
    cx2, cy2, r2 = int(WIDTH * (0.1 + 0.3 * d)), int(HEIGHT * (0.78 + 0.12 * a)), int(220 + 120 * b)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" \
width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="{escape(aria, {'"': '&quot;'})}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.4" y2="1">
      <stop offset="0" stop-color="{color}"/>
      <stop offset="1" stop-color="{deep}"/>
    </linearGradient>
  </defs>
  <rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>
  <circle cx="{cx1}" cy="{cy1}" r="{r1}" fill="#FFFFFF" fill-opacity="0.08"/>
  <circle cx="{cx1}" cy="{cy1}" r="{int(r1 * 0.62)}" fill="#FFFFFF" fill-opacity="0.06"/>
  <circle cx="{cx2}" cy="{cy2}" r="{r2}" fill="#000000" fill-opacity="0.10"/>
  <text x="{MARGIN}" y="{MARGIN + 40}" fill="#FFFFFF" fill-opacity="0.82" font-family="{FONT}" \
font-size="44" font-weight="800" letter-spacing="7">{escape(label.upper())}</text>
  <text x="{MARGIN}" y="{MARGIN + 92}" fill="#FFFFFF" fill-opacity="0.6" font-family="{FONT}" \
font-size="30" font-weight="500">Demo coming soon</text>
  <text x="{MARGIN}" y="{HEIGHT - MARGIN}" fill="#FFFFFF" fill-opacity="0.9" font-family="{FONT}" \
font-size="40" font-weight="900" letter-spacing="5">NFG</text>
</svg>
"""


def render_exercise_svg(ex: dict) -> str:
    group = tx.MUSCLE_GROUPS[primary_group_key(ex)]
    return render_svg(
        group["label"], group["color"], ex["slug"], f"{ex['name'].strip()} (placeholder image)"
    )


def render_generic_svg() -> str:
    """Fallback for exercises with no image and no generated placeholder."""
    return render_svg("Exercise", "#3A3A34", "generic", "Exercise image coming soon")
