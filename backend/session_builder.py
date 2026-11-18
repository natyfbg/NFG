"""Builds the sessions (Push / Pull / Lower / Core / Stretching / Full body) and the
Muscle Mixer's workout from the exercise library. One builder, two entry points.

Nate's rules (2026-11-16):
- Main lifts by level: Beginner 6–7, Intermediate 7–9, Advanced 8–9 (sessions);
  the Mixer: Beginner 6–7, Intermediate 7–8, Advanced 8–9.
- "Secondary lifts": 2–3 complements; anything picked under Add joins them.
- Add is contextual: Lower → calves, hips, core; Pull → rear delts, traps, forearms;
  Push → forearms, core, rotator cuff.
- Mixer results come in tiers: exercises that hit every picked muscle first, then fewer.

Variety: one version of each exercise per session (Barbell / Dumbbell / Smith "Bench
Press" are the same exercise for this purpose), at most two of the same movement on the
same muscle group, and every target group gets at least one lift.

No Flask or Mongo imports, on purpose (like taxonomy.py): works on plain dicts.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import taxonomy as tx

LEVELS = ("beginner", "intermediate", "advanced")
DEFAULT_LEVEL = "beginner"
SESSION_COUNTS = {"beginner": (6, 7), "intermediate": (7, 9), "advanced": (8, 9)}
MIXER_COUNTS = {"beginner": (6, 7), "intermediate": (7, 8), "advanced": (8, 9)}
SECONDARY_COUNTS = {"beginner": 2, "intermediate": 3, "advanced": 3}
MORE_LIMIT = 12

# How well an exercise's level suits the session's level (lower is better; None = never).
LEVEL_FIT = {
    "beginner": {"Beginner": 0, "Intermediate": 1.5, "Advanced": None},
    "intermediate": {"Intermediate": 0, "Beginner": 0.6, "Advanced": 2},
    "advanced": {"Advanced": 0, "Intermediate": 0.3, "Beginner": 1.2},
}

# Sets x reps by role and level.
SCHEMES = {
    "compound": {"beginner": "3 × 8–10", "intermediate": "4 × 6–10", "advanced": "4 × 5–8"},
    "isolation": {"beginner": "3 × 10–12", "intermediate": "3 × 10–15", "advanced": "4 × 10–15"},
    "secondary": {"beginner": "2 × 12–15", "intermediate": "3 × 12–15", "advanced": "3 × 12–15"},
    "hold": {"beginner": "3 × 20–30 s", "intermediate": "3 × 30–45 s", "advanced": "3 × 45–60 s"},
    "core": {"beginner": "3 × 10–12", "intermediate": "3 × 12–15", "advanced": "4 × 12–15"},
    "stretch": {"beginner": "2 × 30 s", "intermediate": "2 × 30–45 s", "advanced": "2 × 45 s"},
    "cardio": {"beginner": "10–15 min", "intermediate": "15–20 min", "advanced": "20–30 min"},
}

# Add options (muscle level, so "rear delts" and "traps" can be added on a pull day).
ADD_OPTIONS: Dict[str, dict] = {
    "calves": {"label": "Calves", "muscles": ["calves"]},
    "hips": {"label": "Hips", "muscles": ["abductors", "adductors", "hip_flexors"]},
    "core": {"label": "Core", "muscles": ["abs", "obliques"]},
    "forearms": {"label": "Forearms", "muscles": ["forearms"]},
    "rear_delts": {"label": "Rear delts", "muscles": ["rear_delts"]},
    "traps": {"label": "Traps", "muscles": ["traps"]},
    "rotator_cuff": {"label": "Rotator cuff", "muscles": ["rotator_cuff"]},
    "lower_back": {"label": "Lower back", "muscles": ["lower_back"]},
}
SPLIT_ADDS = {
    "push": ["forearms", "core", "rotator_cuff"],
    "pull": ["rear_delts", "traps", "forearms"],
    "lower": ["calves", "hips", "core"],
    "core": ["lower_back", "hips"],
    "full-body": ["core", "calves", "forearms"],
    "stretch": [],
}
# Old ?g= add-on links (calves/core/forearms groups) map onto add options.
LEGACY_ADD = {"calves": "calves", "core": "core", "forearms": "forearms"}

# Words that only name the equipment or the grip: "Smith Machine Bench Press" and
# "Dumbbell Bench Press" are both "bench press" for variety purposes. Position words
# (seated, one-arm) stay, so a seated row and a bent-over row can share a pull day.
STEM_WORDS = {
    "barbell",
    "dumbbell",
    "dumbbells",
    "smith",
    "machine",
    "cable",
    "cables",
    "band",
    "banded",
    "bands",
    "ez",
    "bar",
    "kettlebell",
    "weighted",
    "assisted",
    "bodyweight",
    "plate",
    "trx",
    "suspension",
    "close",
    "wide",
    "narrow",
    "neutral",
    "grip",
    "alternating",
}
STEM_ALIASES = {
    "chest dip": "dip",
    "triceps dip": "dip",
    "triceps pushdown": "pushdown",
    "rope face pull": "face pull",
    "low face pull": "face pull",
}

# The classics: when two exercises fit equally, these lead a session (exact stems).
STAPLES = {
    "bench press",
    "push up",
    "incline press",
    "incline bench press",
    "shoulder press",
    "overhead press",
    "dip",
    "lateral raise",
    "fly",
    "pec deck fly",
    "pushdown",
    "skull crusher",
    "pull up",
    "chin up",
    "lat pulldown",
    "row",
    "seated row",
    "one arm row",
    "t bar row",
    "rear delt fly",
    "curl",
    "hammer curl",
    "back squat",
    "front squat",
    "goblet squat",
    "squat",
    "romanian deadlift",
    "deadlift",
    "hip thrust",
    "bulgarian split squat",
    "split squat",
    "walking lunge",
    "reverse lunge",
    "leg press",
    "lying leg curl",
    "seated leg curl",
    "leg extension",
    "standing calf raise",
    "calf raise",
    "plank",
    "dead bug",
    "hanging leg raise",
    "crunch",
    "pallof press",
    "side plank",
    "glute bridge",
}

# Nate's planner lists (content files' `lists:`) per session preset.
PRESET_LISTS = {
    "push": "full_push",
    "push-chest": "chest_focus",
    "push-shoulders": "shoulder_focus",
    "pull": "pull",
    "pull-vertical": "pull",
    "pull-horizontal": "pull",
    "lower": "lower",
    "lower-quad": "lower",
    "lower-hip": "lower",
}

# Full body: one lift per slot, in this order (the first N for the level).
FULL_BODY_SLOTS = [
    ["squat", "lunge"],
    ["horizontal_push", "vertical_push"],
    ["horizontal_pull", "vertical_pull"],
    ["hinge"],
    ["core"],
    ["lunge", "squat"],
    ["vertical_push", "horizontal_push"],
    ["vertical_pull", "horizontal_pull"],
    ["carry", "core"],
]


def stem(name: str) -> str:
    text = (name or "").lower().replace("iso-lateral", " ").replace("-", " ")
    words = re.findall(r"[a-z]+", text)
    core = [w for w in words if w not in STEM_WORDS]
    out = " ".join(core or words)
    return STEM_ALIASES.get(out, out)


def is_staple(doc: dict) -> bool:
    return stem(doc.get("name")) in STAPLES


def norm_level(value: Optional[str]) -> str:
    value = (value or "").strip().lower()
    return value if value in LEVELS else DEFAULT_LEVEL


def add_options_for(split_key: str) -> List[str]:
    return list(SPLIT_ADDS.get(split_key, []))


def add_option_color(key: str) -> str:
    muscles = ADD_OPTIONS[key]["muscles"]
    groups = tx.groups_for_muscles(muscles)
    return tx.MUSCLE_GROUPS[groups[0]]["color"] if groups else "#3A3A34"


# -----------------------------------------------------------------------------
# Shared helpers
# -----------------------------------------------------------------------------
def _primary(doc: dict) -> set:
    return set((doc.get("muscles") or {}).get("primary") or [])


def _secondary(doc: dict) -> set:
    return set((doc.get("muscles") or {}).get("secondary") or [])


def is_hold(doc: dict) -> bool:
    return "isometric" in (doc.get("tags") or [])


def kind(doc: dict) -> str:
    """compound / isolation / hold / core / stretch / cardio, for the sets x reps."""
    cat = doc.get("category")
    if cat == "mobility":
        return "stretch"
    if cat == "cardio":
        return "cardio"
    if is_hold(doc):
        return "hold"
    if doc.get("movement") == "core":
        return "core"
    if doc.get("mechanics") == "isolation" or doc.get("movement") == "isolation":
        return "isolation"
    return "compound"


def scheme_for(doc: dict, level: str, role: str) -> str:
    k = kind(doc)
    if role == "secondary" and k in ("compound", "isolation"):
        k = "secondary"
    text = SCHEMES[k][level]
    if doc.get("laterality") == "unilateral" and k not in ("cardio",):
        text += " each side"
    return text


def _level_fit(doc: dict, level: str) -> Optional[float]:
    return LEVEL_FIT[level].get(doc.get("level") or "Beginner", 1.0)


def _pick_count(counts: Tuple[int, int], available: int) -> int:
    low, high = counts
    return high if available >= high + 3 else low


class _Picker:
    """Keeps a session varied: one version per exercise; per movement + muscle group at
    most `cap` (two for the session's main group, one for the others); one isolation
    exercise per muscle unless the session is about a single group."""

    def __init__(self, cap: int = 2, lead: Optional[str] = None, single: bool = False):
        self.cap = cap
        self.lead = lead
        self.single = single
        self.stems: set = set()
        self.slots: Dict[Tuple[str, str], int] = {}
        self.slugs: set = set()

    def _slot(self, doc: dict) -> Tuple[str, str]:
        if not self.single and kind(doc) == "isolation":
            muscles = (doc.get("muscles") or {}).get("primary") or ["?"]
            return ("isolation", muscles[0])
        groups = doc.get("primary_groups") or ["?"]
        return (doc.get("movement") or "?", groups[0])

    def _cap(self, slot: Tuple[str, str]) -> int:
        if self.single:
            return self.cap
        if slot[0] == "isolation":
            return 1
        return self.cap if (self.lead is None or slot[1] == self.lead) else max(1, self.cap - 1)

    def fresh(self, doc: dict) -> bool:
        """Not in the session yet, in any version."""
        return doc.get("slug") not in self.slugs and stem(doc.get("name")) not in self.stems

    def ok(self, doc: dict) -> bool:
        if not self.fresh(doc):
            return False
        slot = self._slot(doc)
        return self.slots.get(slot, 0) < self._cap(slot)

    def take(self, doc: dict) -> None:
        self.slugs.add(doc.get("slug"))
        self.stems.add(stem(doc.get("name")))
        slot = self._slot(doc)
        self.slots[slot] = self.slots.get(slot, 0) + 1


def _item(doc: dict, level: str, role: str, **extra) -> dict:
    out = dict(doc)
    out["session_role"] = role
    out["scheme"] = scheme_for(doc, level, role)
    out.update(extra)
    return out


# -----------------------------------------------------------------------------
# Sessions (presets from taxonomy.SESSION_SPLITS)
# -----------------------------------------------------------------------------
def preset_wants(doc: dict, preset: dict) -> bool:
    if preset.get("key") == "full-body":
        return doc.get("category") not in ("cardio", "mobility") and (
            doc.get("movement") in {m for slot in FULL_BODY_SLOTS for m in slot}
            or "full_body" in (doc.get("primary_groups") or [])
        )
    categories = set(preset.get("categories") or [])
    if categories:
        return doc.get("category") in categories
    if doc.get("category") in ("cardio", "mobility"):
        return False
    if doc.get("movement") in set(preset.get("exclude_movements") or []):
        return False
    muscles = set(preset.get("muscles") or [])
    groups = set(preset.get("groups") or [])
    return (
        doc.get("movement") in set(preset.get("movements") or [])
        or bool(_primary(doc) & muscles)
        or bool(set(doc.get("primary_groups") or []) & groups)
    )


def _preset_score(doc: dict, preset: dict) -> float:
    movements = set(preset.get("movements") or [])
    muscles = set(preset.get("muscles") or [])
    groups = set(preset.get("groups") or [])
    main_pattern = doc.get("movement") in movements
    in_list = PRESET_LISTS.get(preset.get("key")) in (doc.get("lists") or [])
    return (
        (3 if main_pattern else 0)
        + 1.5 * min(2, len(_primary(doc) & muscles))
        + 0.5 * min(2, len(_secondary(doc) & muscles))
        + (2 if groups & set(doc.get("primary_groups") or []) else 0)
        + (3 if in_list else 0)
        + (4 if is_staple(doc) else 0)
        - (0.5 if doc.get("laterality") == "unilateral" else 0)
    )


def _target_groups(preset: dict) -> List[str]:
    out = tx.groups_for_muscles(list(preset.get("muscles") or []))
    for g in preset.get("groups") or []:
        if g not in out:
            out.append(g)
    return out


def build_session(
    pool: Iterable[dict], preset: dict, level: str = DEFAULT_LEVEL, adds: Sequence[str] = ()
) -> dict:
    """{"main": [...], "secondary": [...], "more": [...], "count": n} for a session preset.

    `pool` is the library already filtered to the viewer's setting (Home/Gym/Hybrid).
    Items are copies of the docs with `session_role` and `scheme` added.
    """
    level = norm_level(level)
    allowed = SPLIT_ADDS.get(preset.get("split") or preset.get("key"), list(ADD_OPTIONS))
    adds = [a for a in adds if a in ADD_OPTIONS and a in allowed]
    order = {m: i for i, m in enumerate(preset.get("movements") or [])}
    if preset.get("key") == "full-body":
        order = {}
    cands = []
    for doc in pool:
        if not preset_wants(doc, preset):
            continue
        fit = _level_fit(doc, level)
        if fit is None:
            continue
        cands.append((doc, fit))
    if not cands:
        return {"main": [], "secondary": [], "more": [], "count": 0}

    movements = set(preset.get("movements") or [])

    def tier(doc: dict) -> float:
        k = kind(doc)
        if k == "compound":
            return 0 if (not movements or doc.get("movement") in movements) else 1.5
        return {"isolation": 1, "core": 1, "stretch": 1, "hold": 2}.get(k, 1)

    def rank(entry):
        doc, fit = entry
        k = kind(doc)
        return (
            tier(doc),
            doc.get("laterality") == "unilateral" and k == "compound",
            order.get(doc.get("movement"), len(order)) if k == "compound" else 0,
            -_preset_score(doc, preset) + fit * 1.5,
            doc.get("name") or "",
        )

    cands.sort(key=rank)
    ranked = [d for d, _ in cands]
    target = _pick_count(SESSION_COUNTS[level], len(ranked))
    targets = _target_groups(preset)
    single = bool(len(targets) <= 1 or preset.get("categories"))
    picker = _Picker(
        cap=99 if single else 2,
        lead=targets[0] if targets else None,
        single=single or preset.get("key") == "full-body",
    )
    main: List[dict] = []
    if preset.get("key") == "full-body":
        for slot in FULL_BODY_SLOTS:
            if len(main) >= target:
                break
            for doc in sorted(
                (
                    d
                    for d in ranked
                    if d.get("movement") in slot
                    and (kind(d) == "compound" or "core" in slot and kind(d) in ("core", "hold"))
                ),
                key=lambda d: (slot.index(d.get("movement")), ranked.index(d)),
            ):
                if picker.ok(doc):
                    picker.take(doc)
                    main.append(doc)
                    break
    # Each of the preset's movements gets a lift (a lower day: a squat, a hinge and a
    # lunge), then every target group, then the best of the rest. Compounds fill about
    # 60% of a split day so isolation work (raises, flies, curls) still fits.
    max_compound = target if single or preset.get("key") == "full-body" else -(-target * 3 // 5)

    def compounds() -> int:
        return sum(1 for d in main if kind(d) == "compound")

    def add(doc: dict) -> bool:
        if not picker.ok(doc) or len(main) >= target:
            return False
        if kind(doc) == "compound" and compounds() >= max_compound:
            return False
        picker.take(doc)
        main.append(doc)
        return True

    for mv in preset.get("movements") or []:
        if any(d.get("movement") == mv for d in main):
            continue
        for doc in ranked:
            if doc.get("movement") == mv and kind(doc) == "compound" and add(doc):
                break
    for g in _target_groups(preset):
        if any(g in (d.get("primary_groups") or []) for d in main):
            continue
        for doc in ranked:
            if g in (doc.get("primary_groups") or []) and add(doc):
                break
    for doc in ranked:
        if len(main) >= target:
            break
        add(doc)
    if len(main) < target:  # not enough isolation work: let compounds fill the rest
        max_compound = target
        for doc in ranked:
            if len(main) >= target:
                break
            add(doc)
    if preset.get("key") != "full-body":
        main.sort(key=lambda d: ranked.index(d))

    # Secondary lifts: complements that reach muscles the main lifts touch least, plus
    # one per Add option.
    covered = set()
    for d in main:
        covered |= _primary(d)
    want = set(preset.get("muscles") or [])
    secondary: List[dict] = []
    rest = [d for d in ranked if picker.ok(d)]
    rest.sort(
        key=lambda d: (
            -(len(_primary(d) & (want - covered)) * 2 + len(_primary(d) & want)),
            kind(d) == "compound",
            ranked.index(d),
        )
    )
    for doc in rest:
        if len(secondary) >= SECONDARY_COUNTS[level]:
            break
        if picker.ok(doc):
            picker.take(doc)
            secondary.append(doc)
            covered |= _primary(doc)
    added: List[dict] = []
    for key in adds:
        muscles = set(ADD_OPTIONS[key]["muscles"])
        best = None
        for doc in pool:
            if doc.get("category") in ("cardio", "mobility") or not (_primary(doc) & muscles):
                continue
            fit = _level_fit(doc, level)
            if fit is None or not picker.fresh(doc):
                continue
            lead = ((doc.get("muscles") or {}).get("primary") or [""])[0] in muscles
            score = (not lead, fit, doc.get("laterality") == "unilateral", doc.get("name") or "")
            if best is None or score < best[0]:
                best = (score, doc)
        if best:
            picker.take(best[1])
            added.append(_item(best[1], level, "secondary", added_by=key))

    more = [d for d in ranked if d.get("slug") not in picker.slugs][:MORE_LIMIT]
    return {
        "main": [_item(d, level, "main") for d in main],
        "secondary": [_item(d, level, "secondary") for d in secondary] + added,
        "more": [_item(d, level, "secondary") for d in more],
        "count": len(main) + len(secondary) + len(added),
    }


# -----------------------------------------------------------------------------
# Muscle Mixer
# -----------------------------------------------------------------------------
def _groups_hit(doc: dict, groups: Sequence[str]) -> Tuple[List[str], List[str]]:
    pg = set(doc.get("primary_groups") or [])
    mg = set(doc.get("muscle_groups") or []) | pg
    primary = [g for g in groups if g in pg]
    secondary = [g for g in groups if g in mg and g not in pg]
    return primary, secondary


def build_mix(pool: Iterable[dict], groups: Sequence[str], level: str = DEFAULT_LEVEL) -> dict:
    """{"tiers": [{"hits", "label", "items"}], "more": [...], "count": n} for picked groups.

    Tier 1 hits every picked group, then fewer. Within a tier: more main targets first,
    compounds before isolation, the level's best fit, then name.
    """
    level = norm_level(level)
    groups = [g for g in groups if g in tx.MUSCLE_GROUPS]
    if not groups:
        return {"tiers": [], "more": [], "count": 0}
    special = {"cardio", "mobility"} & set(groups)
    cands = []
    for doc in pool:
        cat = doc.get("category")
        if cat in ("cardio", "mobility") and cat not in special:
            continue
        primary, secondary = _groups_hit(doc, groups)
        if cat in special:
            primary = primary or [cat]
        if not primary:  # must train at least one picked group as a main target
            continue
        fit = _level_fit(doc, level)
        if fit is None:
            continue
        hits = len(set(primary) | set(secondary))
        cands.append((doc, primary, secondary, hits, fit))
    if not cands:
        return {"tiers": [], "more": [], "count": 0}

    cands.sort(
        key=lambda c: (
            -c[3],
            -(len(c[1]) + (1.5 if is_staple(c[0]) else 0) - c[4]),
            kind(c[0]) != "compound",
            c[0].get("laterality") == "unilateral",
            c[0].get("name") or "",
        )
    )
    target = _pick_count(MIXER_COUNTS[level], len(cands))
    picker = _Picker(single=len(groups) == 1)
    chosen = []
    max_compound = target if len(groups) == 1 else -(-target * 3 // 5)
    for limit in (max_compound, target):  # isolation work first gets a share, then anything
        for c in cands:
            if len(chosen) >= target:
                break
            if c in chosen or not picker.ok(c[0]):
                continue
            if (
                kind(c[0]) == "compound"
                and sum(1 for x in chosen if kind(x[0]) == "compound") >= limit
            ):
                continue
            picker.take(c[0])
            chosen.append(c)
    # Every picked group gets at least one exercise where it's a main target.
    for g in groups:
        if any(g in c[1] for c in chosen):
            continue
        for c in cands:
            if g in c[1] and picker.ok(c[0]):
                if len(chosen) >= target and chosen:
                    # Replace the weakest pick whose groups are all covered elsewhere.
                    for i in range(len(chosen) - 1, -1, -1):
                        others = [x for j, x in enumerate(chosen) if j != i]
                        if all(any(h in x[1] for x in others) for h in chosen[i][1]):
                            picker.slugs.discard(chosen[i][0].get("slug"))
                            picker.stems.discard(stem(chosen[i][0].get("name")))
                            chosen.pop(i)
                            break
                picker.take(c[0])
                chosen.append(c)
                break
    chosen.sort(key=lambda c: cands.index(c))

    n = len(groups)
    tiers: List[dict] = []
    for c in chosen:
        doc, primary, secondary, hits, _ = c
        item = _item(doc, level, "main", mix_primary=primary, mix_secondary=secondary)
        if not tiers or tiers[-1]["hits"] != hits:
            if n == 1:
                label = ""
            elif hits == n:
                label = "Hits all of them" if n > 2 else "Hits both"
            elif hits > 1:
                label = f"Hits {hits} of {n}"
            else:
                label = "One at a time"
            tiers.append({"hits": hits, "label": label, "items": []})
        tiers[-1]["items"].append(item)
    more = []
    for c in cands:
        if c[0].get("slug") in picker.slugs:
            continue
        more.append(_item(c[0], level, "secondary", mix_primary=c[1], mix_secondary=c[2]))
        if len(more) >= MORE_LIMIT:
            break
    return {"tiers": tiers, "more": more, "count": len(chosen)}
