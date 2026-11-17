"""Form steps: illustrated figures for Start · Movement · Finish, captions and tips."""

import xml.etree.ElementTree as ET

import content_sync as cs
import formfigures as ff
import pytest

BASE = {
    "slug": "push-up",
    "name": "Push-Up",
    "status": "published",
    "level": "beginner",
    "category": "strength",
    "movement": "horizontal_push",
    "muscles": {"primary": ["chest"], "secondary": ["triceps"]},
    "equipment": [],
    "summary": "A push-up.",
    "setup": ["Hands under shoulders, body straight."],
    "steps": ["Brace your core.", "Lower your chest to the floor.", "Press back up."],
    "cues": ["Straight line from head to heels.", "Elbows at about 45 degrees."],
    "mistakes": ["Letting the hips sag."],
}


@pytest.fixture
def db(nfg_app):
    return nfg_app.db


def _sync(db, *exercises):
    cs.apply_sync(db, cs.plan_sync(db, list(exercises)))


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------


def test_every_exercise_gets_three_valid_figures():
    exercises, _ = cs.load_exercises()
    for ex in exercises:
        figs = ff.figures_for(ex)
        assert len(figs) == 3, ex["slug"]
        for svg in figs:
            ET.fromstring(svg)  # well-formed markup
            assert 'viewBox="0 0 240 300"' in svg


@pytest.mark.parametrize("family", sorted(ff.F))
def test_every_family_draws_for_both_figures_and_any_implement(family):
    for sex in ("male", "female"):
        for imp in (None, "barbell", "dumbbell", "cable", "bands", "kettlebell", "smith"):
            for step in range(3):
                ET.fromstring(ff.frame_svg(family, sex, imp, step, "#F5B731", "t"))


def test_movement_and_finish_show_a_ghost_and_an_arrow():
    start, move, finish = ff.figures_for(
        {**BASE, "slug": "dumbbell-curl", "movement": "isolation", "equipment": ["dumbbell"]}
    )
    assert 'opacity="0.16"' not in start
    assert 'opacity="0.16"' in move and "stroke-dasharray" in move
    assert 'opacity="0.16"' in finish


def test_holds_show_the_hold_twice():
    figs = ff.figures_for({**BASE, "slug": "plank", "movement": "core"})
    assert 'opacity="0.16"' in figs[1] and 'opacity="0.16"' not in figs[2]


def test_family_rules_prefer_the_most_specific_keyword():
    fam = ff.family_for
    assert fam({"slug": "bulgarian-split-squat"}) == "split_squat_rear"
    assert fam({"slug": "crunch"}) == "crunch"  # "run" must not match inside a word
    assert fam({"slug": "seated-calf-raise"}) == "seated_calf"
    assert fam({"slug": "incline-barbell-bench-press"}) == "incline_press"
    assert fam({"slug": "something-new", "movement": "hinge"}) == "rdl"
    assert fam({"slug": "push-up", "form_pose": "plank"}) == "plank"


def test_women_demonstrate_lower_body_and_men_upper_body():
    assert ff.sex_for({"muscles": {"primary": ["quads", "glutes"]}}) == "female"
    assert ff.sex_for({"muscles": {"primary": ["chest"]}}) == "male"


def test_implement_comes_from_the_equipment_list_or_db_doc():
    assert ff.implement_for({"equipment": ["barbell", "bench"]}) == "barbell"
    assert ff.implement_for({"equipment": "Barbell", "equipment_all": ["dumbbell"]}) == "dumbbell"
    assert ff.implement_for({"equipment": []}) is None


# --------------------------------------------------------------------------
# Step text
# --------------------------------------------------------------------------


def test_step_text_comes_from_the_instructions():
    start, move, finish = ff.step_texts(BASE)
    assert start == {
        "title": "Start",
        "caption": "Hands under shoulders, body straight.",
        "tip": "Straight line from head to heels.",
    }
    assert move["caption"] == "Lower your chest to the floor."  # skips "Brace your core."
    assert move["tip"] == "Elbows at about 45 degrees."
    assert finish["caption"] == "Press back up."
    assert finish["tip"] == "Avoid letting the hips sag."


def test_form_overrides_win():
    ex = {**BASE, "form": [{"caption": "Custom start."}, {}, {"tip": "Custom tip."}]}
    texts = ff.step_texts(ex)
    assert texts[0]["caption"] == "Custom start." and texts[2]["tip"] == "Custom tip."


def test_every_exercise_has_captions_and_tips():
    exercises, _ = cs.load_exercises()
    for ex in exercises:
        for t in ff.step_texts(ex):
            assert t["caption"] and t["tip"], (ex["slug"], t["title"])


# --------------------------------------------------------------------------
# Content files and the exercise page
# --------------------------------------------------------------------------


def test_content_validation_checks_form_fields_and_lists():
    errs, _ = cs.validate_exercise({**BASE, "form_pose": "nope"}, "push-up.yaml")
    assert any("form_pose" in e for e in errs)
    errs, _ = cs.validate_exercise({**BASE, "form": [{"caption": ""}]}, "push-up.yaml")
    assert any("form must be" in e for e in errs)
    errs, _ = cs.validate_exercise({**BASE, "lists": ["legs_day"]}, "push-up.yaml")
    assert any("unknown list" in e for e in errs)
    ok, _ = cs.validate_exercise(
        {**BASE, "form_pose": "push_up", "form": [{"tip": "x"}], "lists": ["full_push"]},
        "push-up.yaml",
    )
    assert ok == []


def test_sync_stores_lists_and_form_fields(db):
    _sync(
        db,
        {**BASE, "lists": ["full_push"], "form_pose": "push_up", "form": [{"caption": "Set up."}]},
    )
    doc = db.workouts.find_one({"slug": "push-up"})
    assert doc["lists"] == ["full_push"]
    assert doc["form_pose"] == "push_up"
    assert doc["form_steps"] == [{"caption": "Set up."}]


def test_exercise_page_shows_three_steps(client, db):
    _sync(db, BASE)
    html = client.get("/workouts/push-up").get_data(as_text=True)
    assert "How to do it" in html
    assert html.count('class="nfg-form-step"') == 3
    assert html.count('class="nfg-form-figure"') == 3
    assert "Lower your chest to the floor." in html and "Avoid letting the hips sag." in html


def test_exercise_page_uses_step_photos_when_they_exist(client, db, monkeypatch):
    import app as nfg

    _sync(db, BASE)
    monkeypatch.setattr(
        nfg,
        "_form_image",
        lambda slug, n: f"/static/img/exercises/{slug}/step-{n}.jpg" if n == 2 else None,
    )
    html = client.get("/workouts/push-up").get_data(as_text=True)
    assert html.count('class="nfg-form-figure"') == 2
    assert 'src="/static/img/exercises/push-up/step-2.jpg"' in html
