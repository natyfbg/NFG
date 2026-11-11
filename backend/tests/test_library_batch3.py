"""Legs and glutes batch, muscle-map placeholders and session ordering."""

from pathlib import Path

import content_sync as cs
import musclemap
import placeholders
import pytest
import taxonomy as tx

EXERCISE_IMG_DIR = Path(cs.BACKEND_DIR) / "static" / "img" / "exercises"


# --------------------------------------------------------------------------
# Repository content
# --------------------------------------------------------------------------


def test_every_published_exercise_has_instructions():
    _, warnings = cs.load_exercises()
    missing = [w for w in warnings if "published without" in w]
    assert missing == []


def test_every_exercise_has_a_placeholder_with_a_muscle_map():
    exercises, _ = cs.load_exercises()
    for ex in exercises:
        svg = (EXERCISE_IMG_DIR / f"{ex['slug']}.svg").read_text(encoding="utf-8")
        assert 'opacity="0.96"' in svg, ex["slug"]  # primary muscles are lit up


def test_lower_body_is_covered_at_home_and_in_the_gym():
    exercises, _ = cs.load_exercises()
    slugs = {e["slug"] for e in exercises}
    for slug in (
        "barbell-back-squat",
        "romanian-deadlift",
        "bulgarian-split-squat",
        "glute-bridge",
    ):
        assert slug in slugs
    assert len(exercises) >= 65


# --------------------------------------------------------------------------
# Muscle map
# --------------------------------------------------------------------------


def test_muscle_map_regions_use_taxonomy_keys():
    for regions in (musclemap.FRONT, musclemap.BACK):
        assert set(regions) <= set(tx.MUSCLES)


def test_muscle_map_lights_primary_brighter_than_secondary():
    markup, width = musclemap.muscle_map_group(
        ["chest"], ["front_delts"], x=0, y=0, height=640, gap=40
    )
    assert width > 0
    front = markup.split("</g><g transform")[0]  # the front figure
    assert front.index('opacity="0.5"') < front.index('opacity="0.96"')  # primary on top


def test_placeholder_keeps_label_and_corner_free():
    svg = placeholders.render_exercise_svg(
        {
            "slug": "x",
            "name": "X",
            "category": "strength",
            "muscles": {"primary": ["quads"], "secondary": ["glutes"]},
        }
    )
    assert "QUADS" in svg and "NFG" in svg and "muscles worked" in svg
    # Figures end above the bottom margin, leaving the "?" corner clear.
    assert placeholders.MAP_TOP + placeholders.MAP_HEIGHT < placeholders.HEIGHT - 150


def test_generic_placeholder_has_no_body():
    assert 'opacity="0.96"' not in placeholders.render_generic_svg()


# --------------------------------------------------------------------------
# Session ordering
# --------------------------------------------------------------------------


@pytest.fixture
def lower_library(nfg_app):
    base = {"status": "published", "level": "beginner", "category": "strength"}

    def ex(slug, movement, laterality="bilateral", primary=("quads", "glutes"), **extra):
        return {
            **base,
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "movement": movement,
            "mechanics": "compound",
            "laterality": laterality,
            "muscles": {"primary": list(primary), "secondary": []},
            "equipment": [],
            **extra,
        }

    content = [
        ex("lateral-lunge", "lunge", "unilateral", ["adductors", "glutes", "quads"]),
        ex("squat", "squat"),
        ex("hinge", "hinge", primary=["hamstrings", "glutes"]),
        ex("wall-sit", "squat", primary=["quads"], tags=["isometric"]),
    ]
    cs.apply_sync(nfg_app.db, cs.plan_sync(nfg_app.db, content))
    return nfg_app


def test_lower_day_leads_with_bilateral_lifts_in_movement_order(lower_library):
    with lower_library.app.test_request_context("/"):
        results = lower_library._session_results(tx.SESSION_PRESETS["lower"], [], "hybrid")
    main = [r["slug"] for r in results if r["session_role"] == "main"]
    assert main == ["squat", "hinge", "lateral-lunge"]


def test_holds_are_accessories(lower_library):
    with lower_library.app.test_request_context("/"):
        results = lower_library._session_results(tx.SESSION_PRESETS["lower-quad"], [], "hybrid")
    roles = {r["slug"]: r["session_role"] for r in results}
    assert roles["wall-sit"] == "accessory"
