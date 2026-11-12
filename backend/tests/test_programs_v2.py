"""Programs v2: program content files, sync, the program page and the day page."""

import copy

import content_sync as cs
import program_content as pc
import pytest
import yaml

HUB = "8-week-challenge"


@pytest.fixture(scope="module")
def content():
    exercises, _ = cs.load_exercises()
    programs, _ = pc.load_programs(exercises)
    return exercises, programs


@pytest.fixture
def synced(nfg_app, content):
    exercises, programs = content
    cs.apply_sync(nfg_app.db, cs.plan_sync(nfg_app.db, exercises))
    pc.apply_program_sync(nfg_app.db, pc.plan_program_sync(nfg_app.db, programs, exercises))
    return nfg_app


def _week_url(week=1, level="beginner", env="gym", day=None):
    url = f"/programs/{HUB}/week/{week}?level={level}&env={env}"
    return url + (f"&day={day}" if day else "")


# --------------------------------------------------------------------------
# Content files and expansion
# --------------------------------------------------------------------------


def test_eight_week_challenge_shape(content):
    exercises, programs = content
    expanded = pc.expand_program(programs[0], exercises)
    days = {t["level"]: t["days_per_week"] for t in expanded["tracks"]}
    assert days == {"beginner": 3, "intermediate": 4, "advanced": 5}
    assert len(expanded["tracks"]) == 9
    assert all(len(t["weeks"]) == 8 for t in expanded["tracks"])


def test_home_tracks_only_use_home_equipment(content):
    exercises, programs = content
    by_slug = {e["slug"]: e for e in exercises}
    for track in pc.expand_program(programs[0], exercises)["tracks"]:
        if track["env"] != "home":
            continue
        for week in track["weeks"]:
            for day in week["days"]:
                for it in day["items"]:
                    if it["workout_slug"]:
                        gear = set(by_slug[it["workout_slug"]]["equipment"])
                        assert gear <= pc.HOME_EQUIPMENT, (track["slug"], it["workout_slug"])


def test_phases_change_sets_and_reps(content):
    exercises, programs = content
    beginner_gym = next(
        t
        for t in pc.expand_program(programs[0], exercises)["tracks"]
        if t["slug"] == f"{HUB}-beginner-gym"
    )
    first = beginner_gym["weeks"][0]["days"][0]["items"][0]
    build = beginner_gym["weeks"][3]["days"][0]["items"][0]
    assert (first["sets"], first["reps"]) == (3, "10–12")
    assert (build["sets"], build["reps"]) == (4, "8–10")


def test_sessions_are_about_45_minutes(content):
    exercises, programs = content
    for track in pc.expand_program(programs[0], exercises)["tracks"]:
        for week in track["weeks"]:
            for day in week["days"]:
                assert 25 <= day["minutes"] <= 55, (track["slug"], week["week_number"], day)


@pytest.mark.parametrize(
    "change, message",
    [
        (
            lambda p: p["levels"]["beginner"]["days"][0]["items"][0].update(exercise="deadlift"),
            "add a home: alternative",
        ),
        (
            lambda p: p["levels"]["beginner"]["days"][2]["items"][0].update(home="leg-press"),
            "needs gym equipment",
        ),
        (lambda p: p["phases"].pop(), "cover weeks 1–8"),
        (
            lambda p: p["levels"]["beginner"]["days"][0]["items"].append({"exercise": "nope"}),
            "no exercise file",
        ),
    ],
)
def test_validation_catches_mistakes(content, change, message):
    exercises, programs = content
    raw = copy.deepcopy(programs[0])
    change(raw)
    errors = pc.validate_program(raw, f"{HUB}.yaml", {e["slug"]: e for e in exercises})
    assert any(message in e for e in errors), errors


def test_rest_parsing_and_estimate():
    assert pc.rest_seconds("90s") == 90
    assert pc.rest_seconds("2 min") == 120
    assert pc.rest_seconds("1:30") == 90
    assert pc.estimate_minutes([{"sets": 3, "rest": "60s"}]) == 5 + 5


# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------


def test_sync_writes_weeks_and_items(synced):
    db = synced.db
    track = db.programs.find_one({"slug": f"{HUB}-intermediate-home"})
    weeks = list(db.program_weeks.find({"program_id": track["_id"]}))
    assert len(weeks) == 8 and all(w.get("content_managed") for w in weeks)
    items = list(db.program_items.find({"week_id": weeks[0]["_id"]}))
    assert {i["day"] for i in items} == {"Upper A", "Lower A", "Upper B", "Lower B"}
    assert all(i.get("workout_id") for i in items if i.get("workout_slug"))
    hub = db.programs.find_one({"slug": HUB})
    assert hub["goal"] == "Lose fat, keep muscle" and hub["days_per_week"] == [3, 4, 5]


def test_resync_keeps_progress(synced, content, client):
    exercises, programs = content
    client.post(
        f"/programs/{HUB}/week/1/day-status",
        data={"level": "beginner", "env": "gym", "day_key": "full-body-a"},
    )
    db = synced.db
    pc.apply_program_sync(db, pc.plan_program_sync(db, programs, exercises))
    html = client.get(f"/programs/{HUB}?level=beginner&env=gym").get_data(as_text=True)
    assert "1 of 24 days" in html


def test_sync_skips_weeks_built_in_admin(nfg_app, content):
    exercises, programs = content
    db = nfg_app.db
    track = db.programs.find_one({"slug": f"{HUB}-advanced-gym"})
    db.program_weeks.insert_one({"program_id": track["_id"], "week_number": 1})
    plan = pc.plan_program_sync(db, programs, exercises)
    row = next(r for r in plan if r["track"]["slug"] == f"{HUB}-advanced-gym")
    assert row["action"] == "skip"
    pc.apply_program_sync(db, plan)
    assert db.program_weeks.count_documents({"program_id": track["_id"]}) == 1
    assert any("--force" in line for line in pc.describe_program_plan(plan))


# --------------------------------------------------------------------------
# Program page
# --------------------------------------------------------------------------


def test_program_page_starts_at_week_one(synced, client):
    html = client.get(f"/programs/{HUB}?level=beginner&env=home").get_data(as_text=True)
    assert "Start here" in html and "Full Body A" in html
    assert "3 days a week" in html and "~45 min" in html
    assert "How this program works" in html and "Foundation" in html


def test_program_page_remembers_level_and_place(synced, client):
    resp = client.get(f"/programs/{HUB}?level=advanced&env=home")
    cookies = resp.headers.getlist("Set-Cookie")
    assert any(c.startswith("nfg_level=advanced") for c in cookies)
    assert any(c.startswith("nfg_env=home") for c in cookies)
    html = client.get(f"/programs/{HUB}").get_data(as_text=True)
    assert 'aria-current="true">Advanced<' in html and 'aria-current="true">Home<' in html
    assert "5 days a week" in html


def test_marking_a_day_moves_next_up(synced, client):
    resp = client.post(
        f"/programs/{HUB}/week/1/day-status",
        data={
            "level": "beginner",
            "env": "gym",
            "day_key": "full-body-a",
            "next_day_key": "full-body-b",
        },
    )
    assert resp.status_code == 302 and "day=full-body-b" in resp.headers["Location"]
    html = client.get(f"/programs/{HUB}").get_data(as_text=True)
    assert "Next up" in html and "1 of 24 days" in html
    assert html.index("Full Body B") < html.index("Weeks")


def test_hub_slug_page_is_the_program_page(synced, client):
    assert "Your level" in client.get(f"/programs/{HUB}").get_data(as_text=True)


# --------------------------------------------------------------------------
# Day page
# --------------------------------------------------------------------------


def test_day_page_lists_sets_reps_and_rest(synced, client):
    html = client.get(_week_url(day="full-body-a")).get_data(as_text=True)
    assert "Goblet Squat" in html and "3 × 10–12" in html and "rest 75s" in html
    assert "Plank" in html and "20–40 s" in html
    assert 'href="/workouts/plank"' in html  # core lines are real exercise pages now
    assert "Mark day done" in html and "Next: Full Body B" in html


def test_home_day_uses_home_swaps(synced, client):
    gym = client.get(_week_url(day="full-body-c")).get_data(as_text=True)
    home = client.get(_week_url(env="home", day="full-body-c")).get_data(as_text=True)
    assert "Leg Press" in gym and "Leg Press" not in home and "Split Squat" in home


def test_later_weeks_open_after_earlier_ones(synced, client):
    resp = client.get(_week_url(week=2))
    assert resp.status_code == 302
    for day in ("full-body-a", "full-body-b", "full-body-c"):
        client.post(
            f"/programs/{HUB}/week/1/day-status",
            data={"level": "beginner", "env": "gym", "day_key": day},
        )
    assert client.get(_week_url(week=2)).status_code == 200


def test_done_day_offers_undo(synced, client):
    client.post(
        f"/programs/{HUB}/week/1/day-status",
        data={"level": "beginner", "env": "gym", "day_key": "full-body-a"},
    )
    html = client.get(_week_url(day="full-body-a")).get_data(as_text=True)
    assert "Undo" in html and "Mark day done" not in html


def test_continue_training_on_home_points_at_the_next_day(synced, client):
    client.post(
        f"/programs/{HUB}/week/1/day-status",
        data={"level": "beginner", "env": "gym", "day_key": "full-body-a"},
    )
    html = client.get("/").get_data(as_text=True)
    assert "Continue training" in html and "day=full-body-b" in html


def test_program_file_is_valid_yaml_with_matching_slug():
    raw = yaml.safe_load((pc.PROGRAMS_DIR / f"{HUB}.yaml").read_text(encoding="utf-8"))
    assert raw["slug"] == HUB and raw["weeks"] == 8
