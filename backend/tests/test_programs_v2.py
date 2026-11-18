"""Programs v2: program content files, sync, the program page and the day page."""

import copy
import re

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


def _program(programs, slug=HUB):
    return next(p for p in programs if p["slug"] == slug)


def _week_url(week=1, level="beginner", env="gym", day=None):
    url = f"/programs/{HUB}/week/{week}?level={level}&env={env}"
    return url + (f"&day={day}" if day else "")


# --------------------------------------------------------------------------
# Content files and expansion
# --------------------------------------------------------------------------


def test_eight_week_challenge_shape(content):
    exercises, programs = content
    expanded = pc.expand_program(_program(programs), exercises)
    days = {t["level"]: t["days_per_week"] for t in expanded["tracks"]}
    assert days == {"beginner": 3, "intermediate": 4, "advanced": 5}
    assert len(expanded["tracks"]) == 9
    assert all(len(t["weeks"]) == 8 for t in expanded["tracks"])


def test_home_tracks_only_use_home_equipment(content):
    exercises, programs = content
    by_slug = {e["slug"]: e for e in exercises}
    for track in pc.expand_program(_program(programs), exercises)["tracks"]:
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
        for t in pc.expand_program(_program(programs), exercises)["tracks"]
        if t["slug"] == f"{HUB}-beginner-gym"
    )
    first = beginner_gym["weeks"][0]["days"][0]["items"][0]
    build = beginner_gym["weeks"][3]["days"][0]["items"][0]
    assert (first["sets"], first["reps"]) == (3, "10–12")
    assert (build["sets"], build["reps"]) == (4, "8–10")


def test_sessions_are_about_45_minutes(content):
    exercises, programs = content
    for track in pc.expand_program(_program(programs), exercises)["tracks"]:
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
    raw = copy.deepcopy(_program(programs))
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
    assert "Start here" in html and "Start week 1" in html
    assert "Full body" in html and "Full Body A" not in html  # day names say the type
    assert "3 days a week" in html and "~45 min" in html
    assert "How this plan works" in html and "Foundation" in html
    assert 'aria-label="Set up your plan"' in html  # first visit: the two pickers


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
    assert html.index("Day 2</span>") < html.index("All weeks")
    assert 'aria-label="Set up your plan"' not in html  # return visit: slim toggles


def test_hub_slug_page_is_the_program_page(synced, client):
    assert "Set up your plan" in client.get(f"/programs/{HUB}").get_data(as_text=True)


# --------------------------------------------------------------------------
# Day page
# --------------------------------------------------------------------------


def test_day_page_lists_sets_reps_and_rest(synced, client):
    html = client.get(_week_url(day="full-body-a")).get_data(as_text=True)
    assert "Goblet Squat" in html and "3 × 10–12" in html and "Rest 75s" in html
    assert "Plank" in html and "20–40 s" in html
    assert 'href="/workouts/plank"' in html  # core lines are real exercise pages now
    assert "Mark complete" in html and "Next: Day 2 · Full body" in html


def test_home_day_uses_home_swaps(synced, client):
    gym = client.get(_week_url(day="full-body-c")).get_data(as_text=True)
    home = client.get(_week_url(env="home", day="full-body-c")).get_data(as_text=True)
    assert 'href="/workouts/leg-press"' in gym and 'href="/workouts/leg-press"' not in home
    assert "Split Squat" in home and "Home swap for Leg Press" in home
    assert "Home swap for" not in gym


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
    assert "Undo" in html and "Mark complete" not in html and "Completed" in html


def test_continue_training_on_home_points_at_the_next_day(synced, client):
    client.post(
        f"/programs/{HUB}/week/1/day-status",
        data={"level": "beginner", "env": "gym", "day_key": "full-body-a"},
    )
    html = client.get("/").get_data(as_text=True)
    assert "Continue training" in html and "day=full-body-b" in html


def test_program_files_are_valid_yaml_with_matching_slugs():
    for path in sorted(pc.PROGRAMS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["slug"] == path.stem
    assert yaml.safe_load((pc.PROGRAMS_DIR / f"{HUB}.yaml").read_text())["weeks"] == 8


# --------------------------------------------------------------------------
# Revamp phase 4: new programs, day names, Home/Gym only, today's day
# --------------------------------------------------------------------------
RECOMP = "12-week-body-recomposition"
BUILD = "16-week-muscle-building"


def test_recomp_is_three_days_push_pull_legs(content):
    exercises, programs = content
    expanded = pc.expand_program(_program(programs, RECOMP), exercises)
    levels = {t["level"] for t in expanded["tracks"]}
    assert levels == {"intermediate"}
    gym = next(t for t in expanded["tracks"] if t["env"] == "gym")
    assert len(gym["weeks"]) == 12
    assert [d["label"] for d in gym["weeks"][0]["days"]] == ["Push", "Pull", "Legs"]
    first = gym["weeks"][0]["days"][0]["items"][0]
    peak = gym["weeks"][9]["days"][0]["items"][0]
    assert first["sets"] < peak["sets"]  # progressive overload across phases


def test_muscle_building_is_five_days_for_two_levels(content):
    exercises, programs = content
    expanded = pc.expand_program(_program(programs, BUILD), exercises)
    days = {t["level"]: t["days_per_week"] for t in expanded["tracks"]}
    assert days == {"intermediate": 5, "advanced": 5}
    gym = next(t for t in expanded["tracks"] if t["slug"] == f"{BUILD}-advanced-gym")
    labels = [d["label"] for d in gym["weeks"][0]["days"]]
    assert labels == ["Push", "Pull", "Legs", "Upper", "Lower"]
    assert len(gym["weeks"]) == 16


@pytest.mark.parametrize("slug", [HUB, RECOMP, BUILD])
def test_every_program_home_track_uses_home_equipment(content, slug):
    exercises, programs = content
    by_slug = {e["slug"]: e for e in exercises}
    for track in pc.expand_program(_program(programs, slug), exercises)["tracks"]:
        if track["env"] != "home":
            continue
        for week in track["weeks"]:
            for day in week["days"]:
                for it in day["items"]:
                    if it["workout_slug"]:
                        gear = set(by_slug[it["workout_slug"]]["equipment"])
                        assert gear <= pc.HOME_EQUIPMENT, (track["slug"], it["workout_slug"])


@pytest.mark.parametrize("slug", [RECOMP, BUILD])
def test_new_programs_keep_sessions_reasonable(content, slug):
    """Two main lifts a day (the rest are accessories) keeps every day under ~80 min."""
    exercises, programs = content
    published = {e["slug"] for e in exercises if e.get("status") == "published"}
    for track in pc.expand_program(_program(programs, slug), exercises)["tracks"]:
        for week in track["weeks"]:
            for day in week["days"]:
                assert 25 <= day["minutes"] <= 80, (
                    track["slug"],
                    week["week_number"],
                    day["label"],
                )
                mains = [i for i in day["items"] if i["role"] == "main"]
                assert len(mains) <= 2, (track["slug"], day["label"], [i["name"] for i in mains])
                for it in day["items"]:
                    assert it["workout_slug"] in published, it["workout_slug"]


def test_accent_and_order_are_validated(content):
    exercises, programs = content
    raw = copy.deepcopy(_program(programs))
    raw.update(accent="orange", order="first")
    errors = pc.validate_program(raw, f"{HUB}.yaml", {e["slug"]: e for e in exercises})
    assert any("accent" in e for e in errors) and any("order" in e for e in errors)


def test_day_style_names_the_workout_type():
    import app as nfg

    assert nfg._day_style("Push") == ("Push", "#D63A2F")
    assert nfg._day_style("Upper B")[0] == "Upper"
    assert nfg._day_style("Lower A")[0] == "Lower"
    names = [nfg._day_style("Full Body A", i) for i in range(3)]
    assert {n for n, _ in names} == {"Full body"}
    assert len({c for _, c in names}) == 3  # each full-body day has its own colour


def test_day_page_has_day_pills_and_week_pills(synced, client):
    html = client.get(_week_url(day="full-body-b")).get_data(as_text=True)
    assert 'aria-label="Days this week"' in html
    assert len(re.findall(r'class="nfg-day-pill[ "]', html)) == 3
    assert 'aria-label="Day 2: Full body"' in html and 'aria-current="page">2<' in html
    assert len(re.findall(r'class="nfg-week-pill[ "]', html)) == 8
    assert "Week 1 · Foundation · Day 2" in html


def test_programs_are_home_or_gym_only(synced, client):
    html = client.get(f"/programs/{HUB}?level=beginner").get_data(as_text=True)
    assert ">Home<" in html and ">Gym<" in html and ">Hybrid<" not in html
    old_link = client.get(_week_url(env="hybrid", day="full-body-c"))  # old links still work
    assert old_link.status_code == 200
    assert 'href="/workouts/leg-press"' in old_link.get_data(as_text=True)  # the gym version


def test_hybrid_progress_carries_over_to_gym(synced, client):
    client.get("/")  # get a viewer cookie
    client.post(
        f"/programs/{HUB}/week/1/day-status",
        data={"level": "beginner", "env": "gym", "day_key": "full-body-a"},
    )
    db = synced.db
    # Move that progress row onto the old Hybrid track, as if it was done before the change.
    db.program_day_progress.update_many({}, {"$set": {"track_slug": f"{HUB}-beginner-hybrid"}})
    html = client.get(f"/programs/{HUB}?level=beginner&env=gym").get_data(as_text=True)
    assert "1 of 24 days" in html


def test_program_cards_go_to_todays_day_once_started(synced, client):
    before = client.get("/programs").get_data(as_text=True)
    assert f'href="/programs/{HUB}"' in before and "Open plan" in before
    client.post(
        f"/programs/{HUB}/week/1/day-status",
        data={"level": "beginner", "env": "gym", "day_key": "full-body-a"},
    )
    after = client.get("/programs").get_data(as_text=True)
    assert f"/programs/{HUB}/week/1?" in after and "day=full-body-b" in after
    assert "Week 1 · Full body" in after and "Continue" in after
    home = client.get("/").get_data(as_text=True)
    assert "day=full-body-b" in home


def _publish_new_programs(db):
    """The 12- and 16-week files start as drafts; this stands in for Nate's approval."""
    db.programs.update_many(
        {"$or": [{"slug": {"$regex": f"^{RECOMP}"}}, {"slug": {"$regex": f"^{BUILD}"}}]},
        {"$set": {"publish_status": "published"}},
    )


def test_new_programs_start_as_drafts_only_admin_sees(synced, client, monkeypatch):
    from werkzeug.security import generate_password_hash

    assert synced.db.programs.find_one({"slug": RECOMP})["publish_status"] == "draft"
    listing = client.get("/programs").get_data(as_text=True)
    assert "8 Week Challenge" in listing and "12-Week Body" not in listing
    assert client.get(f"/programs/{RECOMP}").status_code == 404
    monkeypatch.setattr(synced, "ADMIN_PASSWORD_HASH", generate_password_hash("pw"))
    client.post("/login", data={"username": "admin", "password": "pw"})
    assert client.get(f"/programs/{RECOMP}").status_code == 200
    assert "12-Week Body" in client.get("/programs").get_data(as_text=True)


def test_programs_list_in_file_order_with_accents(synced, client):
    _publish_new_programs(synced.db)
    html = client.get("/programs").get_data(as_text=True)
    assert (
        html.index("8 Week Challenge") < html.index("12-Week Body") < html.index("16-Week Muscle")
    )
    assert "--accent: #E07A2E" in html


def test_single_level_program_shows_level_as_text(synced, client):
    _publish_new_programs(synced.db)
    html = client.get(f"/programs/{RECOMP}").get_data(as_text=True)
    assert "Written for intermediate lifters" in html
    assert "Push" in html and "Day 1" in html


@pytest.mark.parametrize("slug", [HUB, RECOMP, BUILD])
def test_no_day_repeats_an_exercise(content, slug):
    exercises, programs = content
    for track in pc.expand_program(_program(programs, slug), exercises)["tracks"]:
        for day in track["weeks"][0]["days"]:
            names = [i["name"] for i in day["items"]]
            assert len(names) == len(set(names)), (track["slug"], day["label"], names)
            if "Bulgarian Split Squat" in names:  # same movement, twice in a day
                assert "Split Squat" not in names, (track["slug"], day["label"])
