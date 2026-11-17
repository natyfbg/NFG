"""The exercise muscle card: front and back figures with the worked muscles lit up.

Dark background, primary muscles red, secondary muscles yellow (Nate's reference), the
exercise's muscle-group colour on the label and the glow behind the figures. Upper-body
exercises use the male figure, lower-body exercises the female figure (Nate, 2026-11-16).

The figures are the licensed react-native-body-highlighter art in bodymap_paths.py. Art
regions map to taxonomy muscles through bodymap.REGION_MUSCLES plus CARD_EXTRA below
(muscles the art has no region of their own for borrow the nearest one).

No Flask or Mongo imports, on purpose (like taxonomy.py): content_sync.py uses it to
write static/img/exercises/<slug>.svg.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import bodymap
import bodymap_paths as art

PRIMARY = "#F0392B"  # red, as in Nate's reference
PRIMARY_DEEP = "#B81D17"
SECONDARY = "#F5B731"  # yellow
SECONDARY_DEEP = "#D18D12"
MUSCLE_IDLE = "#3C413E"  # muscles the exercise doesn't train
BODY_BASE = "#272B29"  # silhouette under the muscles
DECOR = "#303432"  # head, hands, feet, joints
SEAM = "#101211"  # thin lines between muscles
BACKGROUND = "#0D0F0E"

# Muscles with no art region of their own light the nearest region.
CARD_EXTRA: Dict[Tuple[str, str], List[str]] = {
    ("back", "deltoids"): ["rotator_cuff"],
    ("back", "gluteal"): ["abductors"],
    ("front", "adductors"): ["hip_flexors"],
}

LOWER_BODY = {"quads", "hamstrings", "glutes", "adductors", "abductors", "hip_flexors", "calves"}

_FIGURES = {
    ("male", "front"): art.FRONT,
    ("male", "back"): art.BACK,
    ("female", "front"): art.FEMALE_FRONT,
    ("female", "back"): art.FEMALE_BACK,
}
_OUTLINES = {
    ("male", "front"): art.OUTLINE_FRONT,
    ("male", "back"): art.OUTLINE_BACK,
    ("female", "front"): art.FEMALE_OUTLINE_FRONT,
    ("female", "back"): art.FEMALE_OUTLINE_BACK,
}


def region_muscles(view: str, slug: str) -> List[str]:
    return list(bodymap.REGION_MUSCLES.get((view, slug), [])) + CARD_EXTRA.get((view, slug), [])


def figure_for(primary: Iterable[str]) -> str:
    """'female' when most primary muscles are lower body, otherwise 'male'."""
    primary = list(primary)
    lower = sum(1 for m in primary if m in LOWER_BODY)
    return "female" if lower and lower > len(primary) - lower else "male"


def region_role(view: str, slug: str, primary: set, secondary: set) -> Optional[str]:
    muscles = region_muscles(view, slug)
    if not muscles:
        return None if slug in bodymap.DECORATIVE else "idle"
    if any(m in primary for m in muscles):
        return "primary"
    if any(m in secondary for m in muscles):
        return "secondary"
    return "idle"


def _viewbox(sex: str, view: str) -> Tuple[float, float, float, float]:
    x, y, w, h = (float(v) for v in art.TIGHT_VIEWBOX[f"{sex}_{view}"].split())
    return x, y, w, h


def figure_width(sex: str, view: str, height: float) -> float:
    _, _, w, h = _viewbox(sex, view)
    return height * w / h


def _figure(
    sex: str, view: str, primary: set, secondary: set, x: float, y: float, height: float, uid: str
) -> str:
    vx, vy, vw, vh = _viewbox(sex, view)
    width = height * vw / vh
    fills = {"idle": MUSCLE_IDLE, "primary": f"url(#{uid}p)", "secondary": f"url(#{uid}s)"}
    lit: Dict[str, List[str]] = {"primary": [], "secondary": []}
    body: List[str] = []
    for region in _FIGURES[(sex, view)]:
        role = region_role(view, region["slug"], primary, secondary)
        fill = fills.get(role, DECOR)
        paths = "".join(f'<path d="{d}"/>' for d in region["paths"])
        attrs = f' data-role="{role}"' if role in lit else ""
        body.append(f'<g fill="{fill}"{attrs}>{paths}</g>')
        if role in lit:
            lit[role].append(paths)
    glow = ""
    if lit["primary"] or lit["secondary"]:
        glow = (
            f'<g filter="url(#{uid}g)" opacity="0.7">'
            f'<g fill="{PRIMARY}">{"".join(lit["primary"])}</g>'
            f'<g fill="{SECONDARY}" opacity="0.6">{"".join(lit["secondary"])}</g></g>'
        )
    outline = _OUTLINES[(sex, view)]
    return (
        f'<svg x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
        f'viewBox="{vx:g} {vy:g} {vw:g} {vh:g}" overflow="visible">'
        f"<defs>"
        f'<linearGradient id="{uid}p" x1="0" y1="0" x2="0.35" y2="1">'
        f'<stop offset="0" stop-color="{PRIMARY}"/><stop offset="1" stop-color="{PRIMARY_DEEP}"/>'
        f"</linearGradient>"
        f'<linearGradient id="{uid}s" x1="0" y1="0" x2="0.35" y2="1">'
        f'<stop offset="0" stop-color="{SECONDARY}"/>'
        f'<stop offset="1" stop-color="{SECONDARY_DEEP}"/></linearGradient>'
        f'<linearGradient id="{uid}h" x1="0" y1="0" x2="0.25" y2="1">'
        f'<stop offset="0" stop-color="#FFFFFF" stop-opacity="0.10"/>'
        f'<stop offset="0.55" stop-color="#FFFFFF" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="#000000" stop-opacity="0.28"/></linearGradient>'
        f'<filter id="{uid}g" x="-20%" y="-20%" width="140%" height="140%">'
        f'<feGaussianBlur stdDeviation="14"/></filter>'
        f'<path id="{uid}o" d="{outline}"/>'
        f'<clipPath id="{uid}c"><use href="#{uid}o"/></clipPath>'
        f"</defs>"
        f"{glow}"
        f'<use href="#{uid}o" fill="{BODY_BASE}"/>'
        f'<g stroke="{SEAM}" stroke-width="2.2" stroke-linejoin="round">{"".join(body)}</g>'
        f'<rect x="{vx:g}" y="{vy:g}" width="{vw:g}" height="{vh:g}" fill="url(#{uid}h)" '
        f'clip-path="url(#{uid}c)"/>'
        f"</svg>"
    )


def figures(
    primary: Iterable[str],
    secondary: Iterable[str],
    *,
    x: float,
    y: float,
    height: float,
    gap: float,
    sex: Optional[str] = None,
) -> Tuple[str, float, str]:
    """Front and back figures side by side. Returns (markup, total width, sex used)."""
    primary, secondary = set(primary), set(secondary) - set(primary)
    sex = sex or figure_for(primary)
    w_front = figure_width(sex, "front", height)
    w_back = figure_width(sex, "back", height)
    front = _figure(sex, "front", primary, secondary, x, y, height, "mf")
    back = _figure(sex, "back", primary, secondary, x + w_front + gap, y, height, "mb")
    return front + back, w_front + gap + w_back, sex
