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
    "chest": {"label": "Chest", "color": "#B4532A", "muscles": ["chest", "upper_chest"]},
    "back": {
        "label": "Back",
        "color": "#2F5D8A",
        "muscles": ["lats", "upper_back", "traps", "lower_back", "rear_delts"],
    },
    "shoulders": {
        "label": "Shoulders",
        "color": "#6D4C9F",
        "muscles": ["front_delts", "side_delts", "rear_delts"],
    },
    "biceps": {"label": "Biceps", "color": "#B83B5E", "muscles": ["biceps"]},
    "triceps": {"label": "Triceps", "color": "#4B4FA0", "muscles": ["triceps"]},
    "forearms": {"label": "Forearms", "color": "#7A5C3E", "muscles": ["forearms"]},
    "core": {"label": "Core", "color": "#B58A1E", "muscles": ["abs", "obliques"]},
    "glutes": {"label": "Glutes", "color": "#9C3F8C", "muscles": ["glutes", "abductors"]},
    "quads": {"label": "Quads", "color": "#2E7D5B", "muscles": ["quads", "adductors"]},
    "hamstrings": {"label": "Hamstrings", "color": "#6B7A2A", "muscles": ["hamstrings"]},
    "calves": {"label": "Calves", "color": "#2F7F7A", "muscles": ["calves"]},
    "full_body": {"label": "Full Body", "color": "#2B2B27", "muscles": [], "category": None},
    "cardio": {"label": "Cardio", "color": "#C0392B", "muscles": [], "category": "cardio"},
    "mobility": {"label": "Mobility", "color": "#3B8EA5", "muscles": [], "category": "mobility"},
}

# An exercise counts as full body when its primary muscles span this many groups,
# or when it is explicitly tagged full_body.
FULL_BODY_MIN_GROUPS = 3

# -----------------------------------------------------------------------------
# Equipment and training environments
# -----------------------------------------------------------------------------
# An exercise lists the equipment it REQUIRES. An empty list means bodyweight.
EQUIPMENT: Dict[str, dict] = {
    "dumbbell": {"label": "Dumbbells", "legacy": "Dumbbell", "style": "Dumbbell"},
    "barbell": {"label": "Barbell", "legacy": "Barbell", "style": "Barbell"},
    "kettlebell": {"label": "Kettlebell", "legacy": "Kettlebell", "style": "Kettlebell"},
    "cable": {"label": "Cables", "legacy": "Cable", "style": "Machines"},
    "machine": {"label": "Machines", "legacy": "Machines", "style": "Machines"},
    "bands": {"label": "Bands", "legacy": "Bands", "style": "Resistance Bands"},
    "suspension": {"label": "Suspension Trainer", "legacy": "TRX", "style": "BodyWeight"},
    "bench": {"label": "Bench", "legacy": None, "style": None},
    "pullup_bar": {"label": "Pull-Up Bar", "legacy": None, "style": "BodyWeight"},
    "box": {"label": "Box / Step", "legacy": None, "style": None},
    "cardio_machine": {
        "label": "Cardio Machine",
        "legacy": "Cardio Machine",
        "style": "Cardio/Endurance",
    },
}

BODYWEIGHT_LABEL = "Bodyweight"

# Environment presets: which equipment a user in that setting is assumed to have.
# "hybrid" means the user trains in both places, so nothing is filtered out.
ENVIRONMENTS: Dict[str, dict] = {
    "home": {
        "label": "Home",
        "equipment": [
            "dumbbell",
            "kettlebell",
            "bands",
            "suspension",
            "pullup_bar",
            "bench",
            "box",
        ],
    },
    "gym": {"label": "Gym", "equipment": list(EQUIPMENT)},
    "hybrid": {"label": "Hybrid", "equipment": list(EQUIPMENT)},
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
    "beginner": {"label": "Beginner", "tier": "Easy"},
    "intermediate": {"label": "Intermediate", "tier": "Moderate"},
    "advanced": {"label": "Advanced", "tier": "Hard"},
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
        for gk, group in MUSCLE_GROUPS.items():
            if mk in group["muscles"] and gk not in out:
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
    """Environments where every required piece of equipment is available."""
    needed = set(required)
    return [ek for ek, env in ENVIRONMENTS.items() if needed.issubset(env["equipment"])]


def equipment_text(required: List[str]) -> str:
    """Human label for what an exercise needs, e.g. "Dumbbells + Bench" or "Pull-Up Bar"."""
    if not required:
        return BODYWEIGHT_LABEL
    return " + ".join(EQUIPMENT[k]["label"] for k in required)


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
