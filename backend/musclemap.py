"""Front and back body outline with the worked muscles lit up.

Used inside generated exercise placeholders (and anywhere else a quick "what does
this work?" picture helps). It shows anatomy, never technique, so it can't be
mistaken for form guidance.

Each region is drawn for the right half of the body in a 0..640 tall figure with
x = 0 on the body's midline, then mirrored for the left half. Region keys are the
muscle keys in taxonomy.MUSCLES.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

# Silhouette pieces (right half). The same outline serves front and back.
SILHOUETTE: List[str] = [
    # neck
    "M0,72 L17,74 C18,82 19,90 21,100 L0,102 Z",
    # torso
    "M0,98 L22,99 C40,101 55,105 63,113 C70,121 66,141 57,161 "
    "C51,191 47,229 47,261 C48,281 56,295 58,309 L58,322 C40,330 18,334 0,336 Z",
    # upper arm
    "M58,110 C76,113 88,128 90,150 L95,218 C91,227 77,229 72,221 L63,160 Z",
    # forearm
    "M72,220 L95,217 C101,248 105,278 105,300 L87,305 C82,280 76,250 72,220 Z",
    # hand
    "M87,303 L105,299 C110,310 110,326 104,334 C98,338 90,336 88,328 C86,320 86,310 87,303 Z",
    # thigh
    "M6,330 L58,317 C65,360 63,420 57,470 L22,473 C15,430 9,380 6,330 Z",
    # lower leg
    "M22,471 L57,469 C61,510 59,560 53,600 L30,603 C24,560 20,510 22,471 Z",
    # foot
    "M30,600 L53,598 C57,610 62,622 56,630 L27,631 C25,620 26,610 30,600 Z",
]
HEAD = (0, 40, 29, 35)  # cx, cy, rx, ry (drawn whole, not mirrored)

FRONT: Dict[str, List[str]] = {
    "traps": ["M17,90 C30,96 44,102 56,109 L40,112 C30,108 22,104 18,99 Z"],
    "front_delts": ["M44,108 C54,103 62,107 64,114 C66,126 64,142 60,156 C54,146 48,128 44,108 Z"],
    "side_delts": ["M65,112 C73,116 79,126 79,140 C79,150 75,158 69,162 C67,150 67,128 65,112 Z"],
    # One fan-shaped pec per side, split into the upper (clavicular) and lower heads.
    "chest": [
        "M3,134 C20,132 38,129 56,126 C58,134 58,142 56,150 "
        "C50,162 40,170 28,171 C17,172 9,169 3,166 Z"
    ],
    "upper_chest": ["M3,112 C18,110 34,109 46,110 C51,114 54,119 55,124 C38,127 20,130 3,132 Z"],
    "biceps": [
        "M66,163 C74,160 82,164 86,172 C90,186 90,200 87,213 "
        "C81,218 75,216 71,210 C67,194 65,177 66,163 Z"
    ],
    "forearms": [
        "M75,226 C84,222 92,224 96,232 C100,252 102,276 101,295 L90,298 C84,276 79,250 75,226 Z"
    ],
    "abs": [
        "M3,180 L25,178 L25,203 L3,205 Z",
        "M3,209 L25,207 L25,232 L3,233 Z",
        "M3,237 L25,236 L25,261 L3,262 Z",
        "M3,266 L24,265 C24,280 22,292 16,300 L3,302 Z",
    ],
    "obliques": [
        "M30,184 C38,182 44,188 45,200 C45,230 45,258 43,279 C37,284 32,282 30,276 "
        "C30,246 30,214 30,184 Z"
    ],
    "hip_flexors": ["M26,302 C34,300 42,304 47,312 L41,330 C35,322 29,313 26,302 Z"],
    "abductors": ["M50,300 C56,304 60,314 60,326 L57,346 C53,336 51,320 50,300 Z"],
    "quads": [
        "M22,338 C34,331 48,331 58,342 C62,372 60,410 56,452 "
        "C50,466 40,470 32,466 C24,440 20,390 22,338 Z"
    ],
    "adductors": ["M8,336 L19,336 C19,372 21,402 25,432 C17,422 11,392 9,366 Z"],
    "calves": [
        "M49,492 C56,502 58,522 56,550 C54,556 51,556 49,552 C49,530 49,512 49,492 Z",
        "M24,494 C24,512 24,532 26,552 C30,550 30,532 30,502 Z",
    ],
}

BACK: Dict[str, List[str]] = {
    "traps": ["M0,90 L18,91 C30,99 44,105 58,111 C46,119 34,129 27,148 " "C21,168 12,188 0,204 Z"],
    "rear_delts": ["M44,110 C54,104 62,107 64,114 C66,126 64,142 60,156 C53,146 47,128 44,110 Z"],
    "side_delts": ["M65,112 C73,116 79,126 79,140 C79,150 75,158 69,162 C67,150 67,128 65,112 Z"],
    "upper_back": ["M29,150 C37,142 48,142 56,148 C58,161 54,176 46,188 C38,190 32,186 29,178 Z"],
    "lats": [
        "M30,192 C40,193 50,185 57,172 C59,200 55,236 46,263 "
        "C36,266 26,262 20,254 C22,232 26,210 30,192 Z"
    ],
    "lower_back": ["M3,210 L16,208 C18,236 20,262 22,288 L3,290 Z"],
    "triceps": [
        "M66,160 C76,160 84,166 88,176 C90,192 88,206 85,216 "
        "C79,218 73,214 70,208 C66,192 64,176 66,160 Z"
    ],
    "forearms": [
        "M75,226 C84,222 92,224 96,232 C100,252 102,276 101,295 L90,298 C84,276 79,250 75,226 Z"
    ],
    "abductors": ["M30,285 C42,283 54,289 59,301 C52,297 42,294 30,292 Z"],
    # Two rounded lobes with a cleft between them and a curved lower fold.
    "glutes": [
        "M6,314 C14,302 28,295 42,296 C54,300 60,314 59,330 C58,344 52,356 40,362 "
        "C28,366 14,362 7,354 C5,342 5,326 6,314 Z"
    ],
    "hamstrings": [
        "M14,369 C26,363 44,363 56,371 C58,401 56,431 52,461 "
        "C44,469 34,469 26,463 C18,433 14,401 14,369 Z"
    ],
    "adductors": ["M6,367 L12,367 C14,391 16,411 20,433 C12,421 8,399 6,381 Z"],
    "calves": [
        "M24,495 C34,487 48,489 56,501 C60,523 56,549 50,567 "
        "C44,575 34,575 28,567 C22,547 20,519 24,495 Z"
    ],
}

FIGURE_HEIGHT = 640
FIGURE_HALF_WIDTH = 112  # widest point (hand) plus a little air


def _paths(d_list: Iterable[str], fill: str) -> str:
    out = []
    for d in d_list:
        out.append(f'<path d="{d}" fill="{fill}"/>')
        out.append(f'<path d="{d}" fill="{fill}" transform="scale(-1,1)"/>')
    return "".join(out)


def _layer(d_lists: Iterable[List[str]], ink: str, opacity: float) -> str:
    """One translucent layer. Opacity is set on the group, so shapes that overlap
    (the two halves, arm on shoulder) don't show darker seams."""
    body = "".join(_paths(d, ink) for d in d_lists)
    return f'<g opacity="{opacity}">{body}</g>' if body else ""


def _figure(regions: Dict[str, List[str]], primary: set, secondary: set, ink: str) -> str:
    cx, cy, rx, ry = HEAD
    head = f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{ink}"/>'
    silhouette = f'<g opacity="0.16">{head}{"".join(_paths(SILHOUETTE, ink))}</g>'
    rest = [d for k, d in regions.items() if k not in primary and k not in secondary]
    second = [d for k, d in regions.items() if k in secondary]
    first = [d for k, d in regions.items() if k in primary]
    return (
        silhouette + _layer(rest, ink, 0.12) + _layer(second, ink, 0.5) + _layer(first, ink, 0.96)
    )


def muscle_map_group(
    primary: Iterable[str],
    secondary: Iterable[str] = (),
    *,
    x: float,
    y: float,
    height: float,
    gap: float,
    ink: str = "#FFFFFF",
) -> Tuple[str, float]:
    """SVG <g> with the front and back figures side by side, top-left at (x, y).

    Returns (markup, total_width). `height` is the figures' drawn height.
    """
    p = set(primary)
    s = set(secondary) - p
    scale = height / FIGURE_HEIGHT
    fig_w = 2 * FIGURE_HALF_WIDTH * scale
    front_cx = x + fig_w / 2
    back_cx = front_cx + fig_w + gap
    front = _figure(FRONT, p, s, ink)
    back = _figure(BACK, p, s, ink)
    markup = (
        f'<g transform="translate({front_cx:.1f},{y:.1f}) scale({scale:.4f})">{front}</g>'
        f'<g transform="translate({back_cx:.1f},{y:.1f}) scale({scale:.4f})">{back}</g>'
    )
    return markup, 2 * fig_w + gap


def figure_width(height: float) -> float:
    return 2 * FIGURE_HALF_WIDTH * height / FIGURE_HEIGHT
