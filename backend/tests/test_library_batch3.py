"""Legs and glutes batch, muscle-map placeholders and session ordering."""

from pathlib import Path

import content_sync as cs
import musclecard
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
        assert 'data-role="primary"' in svg, ex["slug"]  # primary muscles are lit up


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


def test_muscle_card_regions_use_taxonomy_keys():
    for (view, slug), muscles in musclecard.CARD_EXTRA.items():
        assert view in ("front", "back") and set(muscles) <= set(tx.MUSCLES)
    # Every muscle in the taxonomy lights at least one region on the card.
    lit = set()
    for view, slug in list(musclecard.bodymap.REGION_MUSCLES) + list(musclecard.CARD_EXTRA):
        lit.update(musclecard.region_muscles(view, slug))
    assert set(tx.MUSCLES) <= lit


def test_muscle_card_colours_primary_red_and_secondary_yellow():
    markup, width, sex = musclecard.figures(
        ["chest"], ["front_delts", "chest"], x=0, y=0, height=640, gap=40
    )
    assert width > 0 and sex == "male"
    assert 'fill="url(#mfp)" data-role="primary"' in markup  # chest, red gradient
    assert 'fill="url(#mfs)" data-role="secondary"' in markup  # front delts, yellow
    assert musclecard.region_role("front", "chest", {"chest"}, {"chest"}) == "primary"
    assert musclecard.region_role("front", "hands", {"chest"}, set()) is None  # decor


def test_upper_body_uses_the_male_figure_and_lower_body_the_female():
    assert musclecard.figure_for(["chest", "triceps"]) == "male"
    assert musclecard.figure_for(["quads", "glutes"]) == "female"
    assert musclecard.figure_for(["glutes", "hamstrings", "lower_back"]) == "female"
    assert musclecard.figure_for(["quads", "chest"]) == "male"  # a tie stays male
    assert musclecard.figure_for([]) == "male"


def test_placeholder_keeps_label_and_corner_free():
    svg = placeholders.render_exercise_svg(
        {
            "slug": "x",
            "name": "X",
            "category": "strength",
            "muscles": {"primary": ["quads"], "secondary": ["glutes"]},
        }
    )
    assert "QUADS" in svg and placeholders.BRAND_MARK in svg and "muscles worked" in svg
    # Figures sit inside the centre 4:5 band that cards crop to, above the brand mark.
    assert placeholders.MAP_TOP + placeholders.MAP_HEIGHT < placeholders.SAFE_BOTTOM - 100


def test_generic_placeholder_lights_nothing():
    svg = placeholders.render_generic_svg()
    assert "data-role=" not in svg and "Primary" not in svg  # no lit muscles, no legend


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


def _lower(app, key):
    import session_builder as sb

    with app.app.test_request_context("/"):
        return sb.build_session(
            app._library_for_env("hybrid"), tx.SESSION_PRESETS[key], "intermediate"
        )


def test_lower_day_leads_with_bilateral_lifts_in_movement_order(lower_library):
    built = _lower(lower_library, "lower")
    main = [r["slug"] for r in built["main"]]
    assert main[:3] == ["squat", "hinge", "lateral-lunge"]


def test_holds_come_after_the_lifts(lower_library):
    built = _lower(lower_library, "lower-quad")
    slugs = [r["slug"] for r in built["main"] + built["secondary"]]
    assert slugs.index("wall-sit") > slugs.index("squat")
    wall = next(r for r in built["main"] + built["secondary"] if r["slug"] == "wall-sit")
    assert wall["scheme"].endswith(" s")  # holds are timed
