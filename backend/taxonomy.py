"""NFG exercise taxonomy: the single source of truth for muscles, groups,
equipment, environments and movement patterns.

Everything here uses stable lowercase keys. Display labels can change freely;
keys are stored in the database and in content files, so never rename a key
without a migration.

This module has no Flask or Mongo dependencies so the content tooling and the
app can both import it without side effects.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

# -----------------------------------------------------------------------------
# Muscles
# -----------------------------------------------------------------------------
# key -> label, broad region (used for the legacy single `body_part` field) and
# the most specific legacy BODY_PARTS_MASTER label (used for legacy filters).
MUSCLES: Dict[str, dict] = {
    "chest": {"label": "Chest", "region": "Chest", "legacy": "Chest"},
    "upper_chest": {"label": "Upper Chest", "region": "Chest", "legacy": "Chest"},
    "front_delts": {"label": "Front Delts", "region": "Shoulders", "legacy": "Shoulders"},
    "side_delts": {"label": "Side Delts", "region": "Shoulders", "legacy": "Shoulders"},
    "rear_delts": {"label": "Rear Delts", "region": "Shoulders", "legacy": "Shoulders"},
    "rotator_cuff": {"label": "Rotator Cuff", "region": "Shoulders", "legacy": "Shoulders"},
    "lats": {"label": "Lats", "region": "Back", "legacy": "Lats"},
    "upper_back": {"label": "Upper Back", "region": "Back", "legacy": "Upper Back"},
    "traps": {"label": "Traps", "region": "Back", "legacy": "Upper Back"},
    "lower_back": {"label": "Lower Back", "region": "Back", "legacy": "Lower Back"},
    "biceps": {"label": "Biceps", "region": "Arms", "legacy": "Biceps"},
    "triceps": {"label": "Triceps", "region": "Arms", "legacy": "Triceps"},
    "forearms": {"label": "Forearms", "region": "Arms", "legacy": "Forearms"},
    "abs": {"label": "Abs", "region": "Core", "legacy": "Abs"},
    "obliques": {"label": "Obliques", "region": "Core", "legacy": "Obliques"},
    "quads": {"label": "Quads", "region": "Legs", "legacy": "Quads"},
    "hamstrings": {"label": "Hamstrings", "region": "Legs", "legacy": "Hamstrings"},
    "glutes": {"label": "Glutes", "region": "Legs", "legacy": "Glutes"},
    "adductors": {"label": "Adductors", "region": "Legs", "legacy": "Hips"},
    "abductors": {"label": "Abductors", "region": "Legs", "legacy": "Hips"},
    "hip_flexors": {"label": "Hip Flexors", "region": "Legs", "legacy": "Hips"},
    "calves": {"label": "Calves", "region": "Legs", "legacy": "Calves"},
}

# Legacy umbrella labels from BODY_PARTS_MASTER that the old browse filter uses.
REGION_LEGACY = {
    "Chest": "Chest",
    "Shoulders": "Shoulders",
    "Back": "Back",
    "Arms": "Arms",
    "Core": "Core",
    "Legs": "Legs",
}

# -----------------------------------------------------------------------------
# Muscle groups: what users pick on Home and in the Muscle Mixer.
# A muscle may belong to several groups (rear delts are both Shoulders and Back).
# Groups without muscles match on exercise `category` instead.
# -----------------------------------------------------------------------------
MUSCLE_GROUPS: Dict[str, dict] = {
    "chest": {
        "label": "Chest",
        "color": "#D63A2F",  # true red: chest is red everywhere (cards, chips, maps)
        "muscles": ["chest", "upper_chest"],
        "blurb": "Pecs and upper chest",
    },
    "back": {
        "label": "Back",
        "color": "#2F5D8A",
        "muscles": ["lats", "upper_back", "traps", "lower_back", "rear_delts"],
        "blurb": "Lats, upper back, traps, lower back",
    },
    "shoulders": {
        "label": "Shoulders",
        "color": "#6D4C9F",
        "muscles": ["front_delts", "side_delts", "rear_delts", "rotator_cuff"],
        "blurb": "Front, side and rear delts, rotator cuff",
    },
    "biceps": {
        "label": "Biceps",
        "color": "#B83B5E",
        "muscles": ["biceps"],
        "blurb": "Front of the upper arm",
    },
    "triceps": {
        "label": "Triceps",
        "color": "#4B4FA0",
        "muscles": ["triceps"],
        "blurb": "Back of the upper arm",
    },
    "forearms": {
        "label": "Forearms",
        "color": "#7A5C3E",
        "muscles": ["forearms"],
        "blurb": "Grip and wrists",
    },
    "core": {
        "label": "Core",
        "color": "#B58A1E",
        "muscles": ["abs", "obliques"],
        "blurb": "Abs and obliques",
    },
    "glutes": {
        "label": "Glutes",
        "color": "#9C3F8C",
        "muscles": ["glutes", "abductors"],
        "blurb": "Glutes and outer hips",
    },
    "quads": {
        "label": "Quads",
        "color": "#2E7D5B",
        "muscles": ["quads", "adductors"],
        "blurb": "Front and inner thighs",
    },
    "hamstrings": {
        "label": "Hamstrings",
        "color": "#6B7A2A",
        "muscles": ["hamstrings"],
        "blurb": "Back of the thighs",
    },
    "calves": {
        "label": "Calves",
        "color": "#2F7F7A",
        "muscles": ["calves"],
        "blurb": "Lower legs",
    },
    "full_body": {
        "label": "Full Body",
        "color": "#2B2B27",
        "muscles": [],
        "category": None,
        "blurb": "Whole-body movements",
    },
    "cardio": {
        "label": "Cardio",
        "color": "#C0392B",
        "muscles": [],
        "category": "cardio",
        "blurb": "Heart rate and conditioning",
    },
    "mobility": {
        "label": "Mobility",
        "color": "#3B8EA5",
        "muscles": [],
        "category": "mobility",
        "blurb": "Stretch, open up, move better",
    },
}

# Where a muscle belongs to several groups, the group it should headline under.
MUSCLE_HOME_GROUP = {"rear_delts": "shoulders"}

# Session splits on Home. Each split has a "full" preset plus focus variants
# (`short` is the variant's name on the Home card: "Focus: Chest · Shoulders").
# A preset selects exercises by movement pattern (the main lifts) and by target
# muscles (accessories), e.g. Pull > Vertical pull = pull-ups/pulldowns first, then
# biceps work. `exclude_movements` keeps a focus honest (no rows on a vertical day).
SESSION_SPLITS = [
    {
        "key": "push",
        "label": "Push",
        "color": "#D63A2F",
        "presets": [
            {
                "key": "push",
                "label": "Full push",
                "covers": "Chest · shoulders · triceps",
                "movements": ["horizontal_push", "vertical_push"],
                "muscles": ["chest", "upper_chest", "front_delts", "side_delts", "triceps"],
            },
            {
                "key": "push-chest",
                "short": "Chest",
                "label": "Chest focus",
                "covers": "Flat & incline pressing · triceps",
                "movements": ["horizontal_push"],
                "muscles": ["chest", "upper_chest", "triceps"],
                "exclude_movements": ["vertical_push"],
            },
            {
                "key": "push-shoulders",
                "short": "Shoulders",
                "label": "Shoulder focus",
                "covers": "Overhead pressing · delts · upper chest",
                "movements": ["vertical_push"],
                "muscles": ["front_delts", "side_delts", "rear_delts", "upper_chest", "triceps"],
            },
        ],
    },
    {
        "key": "pull",
        "label": "Pull",
        "color": "#2F64A0",
        "presets": [
            {
                "key": "pull",
                "label": "Full pull",
                "covers": "Back · biceps · rear delts",
                "movements": ["vertical_pull", "horizontal_pull"],
                "muscles": ["lats", "upper_back", "traps", "rear_delts", "biceps", "forearms"],
            },
            {
                "key": "pull-vertical",
                "short": "Vertical",
                "label": "Vertical pull",
                "covers": "Pull-ups & pulldowns · lats · biceps",
                "movements": ["vertical_pull"],
                "muscles": ["lats", "biceps"],
                "exclude_movements": ["horizontal_pull"],
            },
            {
                "key": "pull-horizontal",
                "short": "Horizontal",
                "label": "Horizontal pull",
                "covers": "Rows · upper back · rear delts",
                "movements": ["horizontal_pull"],
                "muscles": ["upper_back", "traps", "rear_delts", "biceps"],
                "exclude_movements": ["vertical_pull"],
            },
        ],
    },
    {
        "key": "lower",
        "label": "Lower",
        "color": "#2E8A5F",
        "presets": [
            {
                "key": "lower",
                "label": "Full lower",
                "covers": "Quads · glutes · hamstrings · calves",
                "movements": ["squat", "hinge", "lunge"],
                "muscles": ["quads", "glutes", "hamstrings", "adductors", "abductors", "calves"],
            },
            {
                "key": "lower-quad",
                "short": "Quads",
                "label": "Quad dominant",
                "covers": "Squats & lunges · quads",
                "movements": ["squat", "lunge"],
                "muscles": ["quads", "adductors", "calves"],
                "exclude_movements": ["hinge"],
            },
            {
                "key": "lower-hip",
                "short": "Glutes & hams",
                "label": "Glute & ham dominant",
                "covers": "Hinges & bridges · glutes · hamstrings",
                "movements": ["hinge"],
                "muscles": ["glutes", "hamstrings", "abductors", "calves"],
                "exclude_movements": ["squat", "lunge"],
            },
        ],
    },
    {
        "key": "core",
        "label": "Core",
        "color": "#C79A1E",
        "presets": [
            {
                "key": "core",
                "label": "Core",
                "covers": "Abs · obliques · bracing",
                "movements": ["core"],
                "muscles": ["abs", "obliques"],
            },
        ],
    },
    {
        "key": "stretch",
        "label": "Stretching",
        "color": "#2A8C8C",
        "presets": [
            {
                "key": "stretch",
                "label": "Stretching",
                "covers": "Mobility · stretches · warm-ups",
                "movements": [],
                "muscles": [],
                "categories": ["mobility"],
            },
        ],
    },
    {
        "key": "full-body",
        "label": "Full body",
        "color": "#6A44A8",
        "home": False,  # reachable from the Mixer and programs, not a Home card
        "presets": [
            {
                "key": "full-body",
                "label": "Full body",
                "covers": "Big movements that train everything",
                "movements": ["full_body"],
                "muscles": [],
                "groups": ["full_body"],
            },
        ],
    },
]

SESSION_PRESETS: Dict[str, dict] = {
    p["key"]: dict(p, split=split["key"], split_label=split["label"], split_color=split["color"])
    for split in SESSION_SPLITS
    for p in split["presets"]
}

# Quick add-ons on a session page ("calves can go with any day").
SESSION_ADDONS = ["calves", "core", "forearms"]


def group_blurb(group_key: str) -> str:
    """Short description of what a muscle group covers, e.g. "Lats, Upper Back, Traps"."""
    group = MUSCLE_GROUPS[group_key]
    if group.get("blurb"):
        return group["blurb"]
    return ", ".join(MUSCLES[m]["label"] for m in group["muscles"])


# An exercise counts as full body when its primary muscles span this many groups,
# or when it is explicitly tagged full_body.
FULL_BODY_MIN_GROUPS = 3

# -----------------------------------------------------------------------------
# Equipment and training environments
# -----------------------------------------------------------------------------
# An exercise lists the equipment it REQUIRES. An empty list means bodyweight.
# `color` is the tile accent on the Exercises page.
EQUIPMENT: Dict[str, dict] = {
    # `tier` is the lowest setting that has it (home < hybrid < gym); `tile` says
    # whether it gets one of the 12 tiles on the Exercises page (the rest are still
    # filterable). `optional` equipment (a mat) never makes an exercise need it.
    "dumbbell": {
        "label": "Dumbbells",
        "legacy": "Dumbbell",
        "style": "Dumbbell",
        "color": "#B4532A",
        "tier": "hybrid",
        "tile": True,
    },
    "barbell": {
        "label": "Barbell",
        "legacy": "Barbell",
        "style": "Barbell",
        "color": "#3A3A34",
        "tier": "gym",
        "tile": True,
        "note": "EZ bar, landmine and plate work count as barbell",
    },
    "kettlebell": {
        "label": "Kettlebell",
        "legacy": "Kettlebell",
        "style": "Kettlebell",
        "color": "#7A5C3E",
        "tier": "hybrid",
        "tile": True,
    },
    "cable": {
        "label": "Cables",
        "legacy": "Cable",
        "style": "Machines",
        "color": "#2F5D8A",
        "tier": "gym",
        "tile": True,
    },
    "machine": {
        "label": "Machines",
        "legacy": "Machines",
        "style": "Machines",
        "color": "#4B4FA0",
        "tier": "gym",
        "tile": True,
        "note": "Sled, battle ropes and wrist roller count as machines",
    },
    "smith": {
        "label": "Smith Machine",
        "legacy": "Machines",
        "style": "Machines",
        "color": "#5B5F9E",
        "tier": "gym",
        "tile": True,
    },
    "bands": {
        "label": "Bands",
        "legacy": "Bands",
        "style": "Resistance Bands",
        "color": "#B83B5E",
        "tier": "hybrid",
        "tile": True,
    },
    "yoga_mat": {
        "label": "Yoga Mat",
        "legacy": None,
        "style": None,
        "color": "#2F7F7A",
        "tier": "home",
        "tile": True,
        "optional": True,  # floor work: nice to have, never required
    },
    "pullup_bar": {
        "label": "Pull-Up Bar",
        "legacy": None,
        "style": "BodyWeight",
        "color": "#6D4C9F",
        "tier": "home",
        "tile": True,
    },
    "bench": {
        "label": "Bench",
        "legacy": None,
        "style": None,
        "color": "#6B6B60",
        "tier": "hybrid",
        "tile": True,
    },
    "cardio_machine": {
        "label": "Cardio Machines",
        "legacy": "Cardio Machine",
        "style": "Cardio/Endurance",
        "color": "#C0392B",
        "tier": "gym",
        "tile": True,
    },
    "suspension": {
        "label": "Suspension Trainer",
        "legacy": "TRX",
        "style": "BodyWeight",
        "color": "#2F7F7A",
        "tier": "hybrid",
        "tile": False,
    },
    "box": {
        "label": "Box / Step",
        "legacy": None,
        "style": None,
        "color": "#6B6B60",
        "tier": "home",
        "tile": False,
        "note": "A sturdy chair or step works",
    },
    "dip_bars": {
        "label": "Dip Bars",
        "legacy": None,
        "style": "BodyWeight",
        "color": "#6D4C9F",
        "tier": "gym",
        "tile": False,
    },
    "ab_wheel": {
        "label": "Ab Wheel",
        "legacy": None,
        "style": None,
        "color": "#B58A1E",
        "tier": "hybrid",
        "tile": False,
    },
    "jump_rope": {
        "label": "Jump Rope",
        "legacy": None,
        "style": "Cardio/Endurance",
        "color": "#C0392B",
        "tier": "home",
        "tile": False,
    },
    "hand_gripper": {
        "label": "Hand Gripper",
        "legacy": None,
        "style": None,
        "color": "#7A5C3E",
        "tier": "hybrid",
        "tile": False,
    },
}

# The 12 tiles on the Exercises page, in this order (Bodyweight is drawn first by the page).
EQUIPMENT_TILES: List[str] = [
    "dumbbell",
    "barbell",
    "cable",
    "machine",
    "bands",
    "yoga_mat",
    "pullup_bar",
    "smith",
    "bench",
    "kettlebell",
    "cardio_machine",
]

BODYWEIGHT_LABEL = "Bodyweight"
BODYWEIGHT_COLOR = "#2E7D5B"

# Training settings (Nate's planner, Key tab):
#   Home   = bodyweight, household items (chair, towel, doorway count as nothing)
#            and a pull-up bar
#   Hybrid = basic kit you could own: dumbbells, an adjustable bench, bands …
#   Gym    = everything: barbells, cables, machines, Smith machine, dip bars
# Each setting includes the ones below it, so a Gym session can use Home moves and a
# Hybrid session can use Home moves, but a Home session never shows dumbbell work.
ENV_ORDER: List[str] = ["home", "hybrid", "gym"]
ENVIRONMENTS: Dict[str, dict] = {
    "home": {
        "label": "Home",
        "blurb": "Bodyweight, household items and a pull-up bar",
        "equipment": [k for k, e in EQUIPMENT.items() if e["tier"] == "home"],
    },
    "hybrid": {
        "label": "Hybrid",
        "blurb": "Home moves plus dumbbells, a bench and bands",
        "equipment": [k for k, e in EQUIPMENT.items() if e["tier"] in ("home", "hybrid")],
    },
    "gym": {
        "label": "Gym",
        "blurb": "Everything: barbells, cables and machines",
        "equipment": list(EQUIPMENT),
    },
}

# -----------------------------------------------------------------------------
# Movement patterns, categories, levels
# -----------------------------------------------------------------------------
MOVEMENTS: Dict[str, dict] = {
    "horizontal_push": {"label": "Horizontal Push", "legacy": "Push"},
    "vertical_push": {"label": "Vertical Push", "legacy": "Push"},
    "horizontal_pull": {"label": "Horizontal Pull", "legacy": "Pull"},
    "vertical_pull": {"label": "Vertical Pull", "legacy": "Pull"},
    "squat": {"label": "Squat", "legacy": "Squat"},
    "hinge": {"label": "Hinge", "legacy": "Hinge"},
    "lunge": {"label": "Lunge", "legacy": "Lunge"},
    "carry": {"label": "Carry", "legacy": "Carry"},
    "core": {"label": "Core", "legacy": "Core"},
    "isolation": {"label": "Isolation", "legacy": "Accessory"},
    "cardio": {"label": "Cardio", "legacy": "Cardio"},
    "mobility": {"label": "Mobility", "legacy": "Mobility"},
    "full_body": {"label": "Full Body", "legacy": "Full Body"},
}

CATEGORIES: Dict[str, dict] = {
    "strength": {"label": "Strength", "style": None},
    "plyometric": {"label": "Plyometric", "style": "Plyometric/Explosive"},
    "cardio": {"label": "Cardio", "style": "Cardio/Endurance"},
    "mobility": {"label": "Mobility", "style": "Yoga/Mobility"},
}

LEVELS: Dict[str, dict] = {
    "beginner": {
        "label": "Beginner",
        "tier": "Easy",
        "rank": 1,
        "blurb": "Simple, stable moves to learn the pattern",
    },
    "intermediate": {
        "label": "Intermediate",
        "tier": "Moderate",
        "rank": 2,
        "blurb": "More load, balance and coordination",
    },
    "advanced": {
        "label": "Advanced",
        "tier": "Hard",
        "rank": 3,
        "blurb": "Heavy, technical or explosive lifts",
    },
}

MECHANICS = ("compound", "isolation")
LATERALITY = ("bilateral", "unilateral")
PUBLISH_STATUSES = ("draft", "published")


# -----------------------------------------------------------------------------
# Derivations
# -----------------------------------------------------------------------------
def groups_for_muscles(muscle_keys: Iterable[str]) -> List[str]:
    """Group keys containing the given muscles, ordered by the muscles' order.

    The first muscle's group comes first, so an exercise's lead muscle decides
    its headline group (e.g. Goblet Squat [quads, glutes] -> quads first).
    """
    out: List[str] = []
    for mk in muscle_keys:
        candidates = [gk for gk, group in MUSCLE_GROUPS.items() if mk in group["muscles"]]
        home = MUSCLE_HOME_GROUP.get(mk)
        if home in candidates:
            candidates.remove(home)
            candidates.insert(0, home)
        for gk in candidates:
            if gk not in out:
                out.append(gk)
    return out


def muscles_for_groups(group_keys: Iterable[str]) -> List[str]:
    out: List[str] = []
    for gk in group_keys:
        for mk in MUSCLE_GROUPS.get(gk, {}).get("muscles", []):
            if mk not in out:
                out.append(mk)
    return out


def environments_for_equipment(required: Iterable[str]) -> List[str]:
    """Settings where every required piece of equipment is available, lowest first.

    Equipment marked optional (a mat) is ignored: it never raises the setting.
    """
    needed = {k for k in required if not EQUIPMENT[k].get("optional")}
    return [ek for ek in ENV_ORDER if needed.issubset(ENVIRONMENTS[ek]["equipment"])]


def equipment_tier(required: Iterable[str]) -> str:
    """The lowest setting an exercise fits: home, hybrid or gym."""
    envs = environments_for_equipment(required)
    return envs[0] if envs else "gym"


def equipment_text(required: List[str]) -> str:
    """Human label for what an exercise needs, e.g. "Dumbbells + Bench" or "Pull-Up Bar"."""
    needed = [k for k in required if not EQUIPMENT[k].get("optional")]
    if not needed:
        return BODYWEIGHT_LABEL
    return " + ".join(EQUIPMENT[k]["label"] for k in needed)


def legacy_equipment_label(required: List[str]) -> str:
    """Single legacy `equipment` value for the old browse filter and admin form."""
    if not required:
        return BODYWEIGHT_LABEL
    labelled = [EQUIPMENT[k]["legacy"] for k in required if EQUIPMENT[k]["legacy"]]
    if not labelled:
        return BODYWEIGHT_LABEL
    return labelled[0] if len(set(labelled)) == 1 else "Mixed"


def legacy_style_label(required: List[str], category: str) -> str:
    """Legacy `style` value (matches the styles collection / DEFAULT_WORKOUT_STYLES)."""
    category_style = CATEGORIES.get(category, {}).get("style")
    if category_style:
        return category_style
    for key in required:
        style = EQUIPMENT[key]["style"]
        if style:
            return style
    return "BodyWeight"


def legacy_body_parts(primary: List[str]) -> List[str]:
    """Legacy `body_parts` labels for PRIMARY muscles: region first, then specifics.

    Region first keeps `body_part == body_parts[0]`, which the admin form enforces.
    Secondary muscles are left out so the old single-muscle browse stays precise.
    """
    out: List[str] = []
    for mk in primary:
        for label in (REGION_LEGACY[MUSCLES[mk]["region"]], MUSCLES[mk]["legacy"]):
            if label not in out:
                out.append(label)
    return out
