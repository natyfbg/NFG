"""SVG placeholder art for exercises that don't have real images yet.

Placeholders are deliberately abstract: colour-coded by muscle group with the
exercise name and equipment. They never depict a body or technique, so nobody
mistakes them for form guidance. Real photos/illustrations replace them by
listing `images:` in the exercise file.
"""

from __future__ import annotations

from typing import List
from xml.sax.saxutils import escape

import taxonomy as tx

WIDTH, HEIGHT = 900, 1600  # 9:16, matches the portrait workout cards
MARGIN = 80
FONT = "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif"


def _shade(hex_color: str, factor: float) -> str:
    """Darken (factor < 1) a #RRGGBB colour."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, int(c * factor))) for c in (r, g, b)))


def _wrap(text: str, max_chars: int) -> List[str]:
    lines: List[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def primary_group_key(ex: dict) -> str:
    groups = tx.groups_for_muscles(ex["muscles"]["primary"])
    for gk, group in tx.MUSCLE_GROUPS.items():
        if group.get("category") == ex.get("category"):
            return gk
    if ex.get("full_body") or len(groups) >= tx.FULL_BODY_MIN_GROUPS:
        return "full_body"
    return groups[0] if groups else "full_body"


def equipment_text(equipment: List[str]) -> str:
    if not equipment:
        return tx.BODYWEIGHT_LABEL
    return " + ".join(tx.EQUIPMENT[k]["label"] for k in equipment)


def render_exercise_svg(ex: dict) -> str:
    group = tx.MUSCLE_GROUPS[primary_group_key(ex)]
    base = group["color"]
    deep = _shade(base, 0.55)

    name = ex["name"].strip()
    font_size, max_chars = (108, 13)
    lines = _wrap(name, max_chars)
    if len(lines) > 3:
        font_size, max_chars = (84, 17)
        lines = _wrap(name, max_chars)
    line_h = int(font_size * 1.08)
    name_bottom = 1240
    name_top = name_bottom - line_h * (len(lines) - 1)

    name_tspans = "".join(
        f'<tspan x="{MARGIN}" y="{name_top + i * line_h}">{escape(line)}</tspan>'
        for i, line in enumerate(lines)
    )
    muscles = " · ".join(tx.MUSCLES[m]["label"] for m in ex["muscles"]["primary"])
    meta = f"{equipment_text(ex['equipment'])} · {tx.LEVELS[ex['level']]['label']}"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" \
width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="{escape(name)} (placeholder image)">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.35" y2="1">
      <stop offset="0" stop-color="{base}"/>
      <stop offset="1" stop-color="{deep}"/>
    </linearGradient>
  </defs>
  <rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>
  <circle cx="{WIDTH - 40}" cy="260" r="360" fill="#FFFFFF" fill-opacity="0.07"/>
  <circle cx="{WIDTH - 140}" cy="420" r="210" fill="#FFFFFF" fill-opacity="0.06"/>
  <circle cx="120" cy="{HEIGHT - 120}" r="260" fill="#000000" fill-opacity="0.10"/>
  <text x="{MARGIN}" y="170" fill="#FFFFFF" fill-opacity="0.78" font-family="{FONT}" \
font-size="40" font-weight="700" letter-spacing="6">{escape(group["label"].upper())}</text>
  <text x="{MARGIN}" y="226" fill="#FFFFFF" fill-opacity="0.62" font-family="{FONT}" \
font-size="32" font-weight="500">{escape(muscles)}</text>
  <text fill="#FFFFFF" font-family="{FONT}" font-size="{font_size}" font-weight="800" \
letter-spacing="-1">{name_tspans}</text>
  <text x="{MARGIN}" y="{name_bottom + 90}" fill="#FFFFFF" fill-opacity="0.85" \
font-family="{FONT}" font-size="40" font-weight="600">{escape(meta)}</text>
  <line x1="{MARGIN}" y1="{HEIGHT - 170}" x2="{WIDTH - MARGIN}" y2="{HEIGHT - 170}" \
stroke="#FFFFFF" stroke-opacity="0.25" stroke-width="2"/>
  <text x="{MARGIN}" y="{HEIGHT - 100}" fill="#FFFFFF" font-family="{FONT}" font-size="44" \
font-weight="900" letter-spacing="4">NFG</text>
  <text x="{WIDTH - MARGIN}" y="{HEIGHT - 100}" text-anchor="end" fill="#FFFFFF" \
fill-opacity="0.6" font-family="{FONT}" font-size="30" font-weight="500">Demo image coming soon</text>
</svg>
"""
