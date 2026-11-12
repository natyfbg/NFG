"""Core batch: holds, crunches, leg raises, anti-rotation and carries."""

import content_sync as cs
import program_content as pc
import taxonomy as tx

CORE = {
    "plank",
    "side-plank",
    "hollow-body-hold",
    "dead-bug",
    "bird-dog",
    "crunch",
    "reverse-crunch",
    "bicycle-crunch",
    "cable-crunch",
    "lying-leg-raise",
    "hanging-knee-raise",
    "mountain-climber",
    "pallof-press",
    "cable-woodchop",
    "suitcase-carry",
}


def _exercises():
    exercises, _ = cs.load_exercises()
    return {e["slug"]: e for e in exercises}


def test_core_batch_is_in_the_library_and_lands_in_the_core_group():
    by_slug = _exercises()
    assert CORE <= set(by_slug)
    for slug in CORE:
        assert "core" in tx.groups_for_muscles(by_slug[slug]["muscles"]["primary"]), slug


def test_core_works_at_home():
    by_slug = _exercises()
    home = [s for s in CORE if "home" in tx.environments_for_equipment(by_slug[s]["equipment"])]
    assert len(home) >= 11


def test_holds_are_accessories():
    by_slug = _exercises()
    for slug in ("plank", "side-plank", "hollow-body-hold"):
        assert "isometric" in by_slug[slug]["tags"], slug
        assert pc._default_role(by_slug[slug]) == "accessory", slug


def test_variation_links_point_at_real_exercises():
    by_slug = _exercises()
    for slug in CORE:
        for other in by_slug[slug].get("regressions", []) + by_slug[slug].get("progressions", []):
            assert other in by_slug, f"{slug} -> {other}"


def test_coachs_picks_resolve_now_that_plank_exists():
    exercises, _ = cs.load_exercises()
    assert cs.collection_warnings(exercises) == []


def test_eight_week_challenge_has_no_free_text_lines():
    exercises, _ = cs.load_exercises()
    programs, _ = pc.load_programs(exercises)
    program = next(p for p in programs if p["slug"] == "8-week-challenge")
    expanded = pc.expand_program(program, exercises)
    rows = [
        item
        for track in expanded["tracks"]
        for week in track["weeks"]
        for day in week["days"]
        for item in day["items"]
    ]
    assert rows and all(r["workout_slug"] for r in rows)
    planks = [r for r in rows if r["workout_slug"] in ("plank", "side-plank")]
    assert planks and all(r["role"] == "accessory" for r in planks)
