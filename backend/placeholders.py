"""SVG muscle cards for exercises that don't have real images yet.

The card is dark, with front and back figures where the exercise's primary muscles are
red and secondary muscles yellow (musclecard.py), the muscle group's colour on the label
chip and the glow behind the figures, and a small legend. It shows anatomy, never
technique, so nobody mistakes it for form guidance, and it carries no exercise name (the
page and cards already show it).

Size matches the standard for real exercise images: 1080 x 1920 (9:16 portrait), with
everything important inside the centre 4:5 band that cards crop to. Real photos sit in
front of the muscle card on the exercise page once they're listed under `images:` in the
exercise's content file.
"""

from __future__ import annotations

import hashlib
from typing import Iterable, List
from xml.sax.saxutils import escape

import bodymap
import musclecard
import taxonomy as tx

# 9:16 portrait (phone-native, the exercise viewer's frame). Cards crop the centre to
# 4:5, so everything that matters sits inside SAFE_TOP..SAFE_BOTTOM.
WIDTH, HEIGHT = 1080, 1920
SAFE_TOP = (HEIGHT - WIDTH * 5 // 4) // 2  # 285
SAFE_BOTTOM = HEIGHT - SAFE_TOP  # 1635
MARGIN = 88
MAP_TOP, MAP_HEIGHT, MAP_GAP = SAFE_TOP + 215, 920, 44  # figures placement
FONT = "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif"
BRAND_MARK = "QILTSIM"  # top-right mark on every card


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


def _chip_colour(color: str) -> str:
    """The group colour, unless it would vanish on the dark card (Full Body is charcoal)."""
    return color if bodymap.contrast_with_white(color) < 9 else "#77776E"


def render_svg(
    label: str,
    color: str,
    seed: str,
    aria: str,
    primary: Iterable[str] = (),
    secondary: Iterable[str] = (),
    subline: str = "Muscles worked",
) -> str:
    chip = bodymap.readable_on_white(_chip_colour(color), 3.0)
    a, b = _seeded(seed, 2)
    glow_x = 46 + 8 * a  # the group-colour glow drifts a little per exercise
    glow_y = 40 + 6 * b
    primary, secondary = list(primary), list(secondary)
    fig_h = MAP_HEIGHT
    sex = musclecard.figure_for(primary)
    total = (
        musclecard.figure_width(sex, "front", fig_h)
        + MAP_GAP
        + musclecard.figure_width(sex, "back", fig_h)
    )
    x0 = (WIDTH - total) / 2
    figures, _, _ = musclecard.figures(
        primary, secondary, x=x0, y=MAP_TOP, height=fig_h, gap=MAP_GAP, sex=sex
    )
    w_front = musclecard.figure_width(sex, "front", fig_h)
    label_y = MAP_TOP + fig_h + 58
    front_x = x0 + w_front / 2
    back_x = x0 + w_front + MAP_GAP + musclecard.figure_width(sex, "back", fig_h) / 2
    chip_text = escape(label.upper())
    chip_w = int(64 + len(label) * 27)
    chip_y = SAFE_TOP + 64
    legend_y = label_y + 92
    legend = ""
    if primary or secondary:
        legend = f"""  <g font-family="{FONT}" font-size="28" font-weight="600" fill="#FFFFFF" fill-opacity="0.8">
    <circle cx="{WIDTH / 2 - 196:.0f}" cy="{legend_y - 10}" r="13" fill="{musclecard.PRIMARY}"/>
    <text x="{WIDTH / 2 - 172:.0f}" y="{legend_y}">Primary</text>
    <circle cx="{WIDTH / 2 + 30:.0f}" cy="{legend_y - 10}" r="13" fill="{musclecard.SECONDARY}"/>
    <text x="{WIDTH / 2 + 54:.0f}" y="{legend_y}">Secondary</text>
  </g>"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" \
width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="{escape(aria, {'"': '&quot;'})}">
  <defs>
    <radialGradient id="glow" cx="{glow_x:.0f}%" cy="{glow_y:.0f}%" r="58%">
      <stop offset="0" stop-color="{color}" stop-opacity="0.34"/>
      <stop offset="0.6" stop-color="{color}" stop-opacity="0.08"/>
      <stop offset="1" stop-color="{color}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="shade" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#000000" stop-opacity="0"/>
      <stop offset="1" stop-color="#000000" stop-opacity="0.45"/>
    </linearGradient>
  </defs>
  <rect width="{WIDTH}" height="{HEIGHT}" fill="{musclecard.BACKGROUND}"/>
  <rect width="{WIDTH}" height="{HEIGHT}" fill="url(#glow)"/>
  <rect y="{HEIGHT // 2}" width="{WIDTH}" height="{HEIGHT // 2}" fill="url(#shade)"/>
  <rect x="{MARGIN}" y="{chip_y}" width="{chip_w}" height="64" rx="32" fill="{chip}"/>
  <text x="{MARGIN + chip_w / 2:.0f}" y="{chip_y + 43}" text-anchor="middle" fill="#FFFFFF" \
font-family="{FONT}" font-size="30" font-weight="800" letter-spacing="5">{chip_text}</text>
  <text x="{MARGIN}" y="{chip_y + 124}" fill="#FFFFFF" fill-opacity="0.62" \
font-family="{FONT}" font-size="32" font-weight="500">{escape(subline)}</text>
  <text x="{WIDTH - MARGIN}" y="{chip_y + 43}" text-anchor="end" fill="#FFFFFF" \
fill-opacity="0.42" font-family="{FONT}" font-size="26" font-weight="900" \
letter-spacing="5">{BRAND_MARK}</text>
  {figures}
  <g fill="#FFFFFF" fill-opacity="0.4" font-family="{FONT}" font-size="24" font-weight="700" \
letter-spacing="5" text-anchor="middle">
    <text x="{front_x:.0f}" y="{label_y}">FRONT</text>
    <text x="{back_x:.0f}" y="{label_y}">BACK</text>
  </g>
{legend}
</svg>
"""


def render_exercise_svg(ex: dict) -> str:
    group = tx.MUSCLE_GROUPS[primary_group_key(ex)]
    muscles = ex.get("muscles") or {}
    return render_svg(
        group["label"],
        group["color"],
        ex["slug"],
        f"{ex['name'].strip()}: muscles worked",
        primary=muscles.get("primary") or [],
        secondary=muscles.get("secondary") or [],
    )


def render_generic_svg() -> str:
    """Fallback for exercises with no image and no generated placeholder."""
    return render_svg(
        "Exercise", "#3A3A34", "generic", "Exercise image coming soon", subline="Image coming soon"
    )
