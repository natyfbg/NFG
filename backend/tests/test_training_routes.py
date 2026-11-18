"""Session mode routes: start, log, finish, history, one workout at a time."""

import datetime as dt
import re

import content_sync as cs
import program_content as pc
import pytest

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


def _start_push(client, level="beginner", env="gym", **extra):
    data = {"from": "session", "p": "push", "level": level, "env": env}
    data.update(extra)
    return client.post("/train/start", data=data)


def _active(db):
    return db.training_sessions.find_one({"status": "active"})


def _log(client, item, row=0, **values):
    data = {"key": item["key"], "row": str(row), "action": "done"}
    data.update({k: str(v) for k, v in values.items()})
    return client.post("/train/set", data=data, headers={"Accept": "application/json"})


def _first_weighted(doc):
    return next(i for i in doc["items"] if i["kind"] == "weight_reps")


def test_start_a_session_and_see_the_logger(synced, client):
    page = client.get("/workouts/mix?p=push&level=beginner&env=gym").get_data(as_text=True)
    assert "Start workout" in page and 'action="/train/start"' in page
    resp = _start_push(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/train/session")
    doc = _active(synced.db)
    assert doc["title"] and doc["source"]["kind"] == "session" and len(doc["items"]) >= 6
    assert all(len(i["rows"]) == i["sets_target"] for i in doc["items"])
    html = client.get("/train/session").get_data(as_text=True)
    assert doc["items"][0]["name"] in html and "Finish workout" in html and "data-rest" in html
    # The page that started it now says Resume, and pressing Start again resumes.
    page = client.get("/workouts/mix?p=push&level=beginner&env=gym").get_data(as_text=True)
    assert "Resume workout" in page
    assert _start_push(client).headers["Location"].endswith("/train/session")
    assert synced.db.training_sessions.count_documents({}) == 1


def test_log_undo_and_validation(synced, client):
    _start_push(client)
    doc = _active(synced.db)
    item = _first_weighted(doc)
    resp = _log(client, item, 0, weight=135, reps=8)
    data = resp.get_json()
    assert (
        data["ok"]
        and data["done"]
        and data["shown"] == {"weight": "135", "reps": 8, "seconds": "", "minutes": ""}
    )
    assert data["rest"] == item["rest_seconds"] > 0 and data["item"] == {
        "done": 1,
        "total": item["sets_target"],
    }
    row = _active(synced.db)["items"][doc["items"].index(item)]["rows"][0]
    assert row["done"] and abs(row["weight_kg"] - 61.235) < 0.01 and row["reps"] == 8

    bad = _log(client, item, 1, weight=135)
    assert bad.status_code == 400 and bad.get_json()["error"] == "Enter your reps first."
    assert _log(client, item, 99, reps=5).status_code == 400
    assert (
        client.post(
            "/train/set", data={"key": "nope", "row": "0"}, headers={"Accept": "application/json"}
        ).status_code
        == 400
    )

    undo = client.post(
        "/train/set",
        data={"key": item["key"], "row": "0", "action": "undo"},
        headers={"Accept": "application/json"},
    ).get_json()
    assert undo["ok"] and not undo["done"] and undo["rest"] == 0
    # Without the page's script: a plain form post redirects back to the exercise.
    plain = client.post("/train/set", data={"key": item["key"], "row": "0", "reps": "6"})
    assert plain.status_code == 302 and f"#ex-{item['key']}" in plain.headers["Location"]


def test_add_and_remove_rows(synced, client):
    _start_push(client)
    item = _active(synced.db)["items"][0]
    n = len(item["rows"])
    client.post("/train/rows", data={"key": item["key"], "action": "add"})
    assert len(_active(synced.db)["items"][0]["rows"]) == n + 1
    client.post("/train/rows", data={"key": item["key"], "action": "remove"})
    client.post("/train/rows", data={"key": item["key"], "action": "remove"})
    assert len(_active(synced.db)["items"][0]["rows"]) == n - 1


def test_finish_saves_history_and_last_time_shows_next_time(synced, client):
    _start_push(client)
    doc = _active(synced.db)
    item = _first_weighted(doc)
    _log(client, item, 0, weight=100, reps=10)
    _log(client, item, 1, weight=100, reps=9)
    resp = client.post("/train/finish")
    assert resp.status_code == 302 and "done=1" in resp.headers["Location"]
    done = synced.db.training_sessions.find_one({"_id": doc["_id"]})
    assert done["status"] == "done" and done["finished_at"]
    summary = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Workout complete" in summary and "100 lb × 10" in summary and "Skipped:" in summary
    index = client.get("/train").get_data(as_text=True)
    assert doc["title"] in index and "2 sets" in index

    _start_push(client)
    html = client.get("/train/session").get_data(as_text=True)
    assert "Last time: 100 lb × 10, 9" in html
    assert 'name="ph_weight" value="100"' in html
    # A blank tick uses last time's numbers.
    again = _active(synced.db)
    item2 = next(i for i in again["items"] if i["slug"] == item["slug"])
    data = _log(client, item2, 0, ph_weight=100, ph_reps=10).get_json()
    assert data["ok"] and data["shown"]["reps"] == 10


def test_finish_with_nothing_logged_saves_nothing(synced, client):
    _start_push(client)
    resp = client.post("/train/finish", follow_redirects=True)
    assert "Nothing was logged" in resp.get_data(as_text=True)
    assert synced.db.training_sessions.count_documents({}) == 0


def test_one_workout_at_a_time(synced, client):
    _start_push(client)
    first = _active(synced.db)
    _log(client, _first_weighted(first), 0, weight=50, reps=5)
    page = client.post(
        "/train/start", data={"from": "session", "p": "pull", "level": "beginner", "env": "gym"}
    )
    html = page.get_data(as_text=True)
    assert (
        page.status_code == 200
        and "You're in the middle of" in html
        and "Save it and start" in html
    )
    assert 'name="replace" value="1"' in html and 'name="p" value="pull"' in html
    client.post(
        "/train/start",
        data={"from": "session", "p": "pull", "level": "beginner", "env": "gym", "replace": "1"},
    )
    assert synced.db.training_sessions.find_one({"_id": first["_id"]})["status"] == "done"
    assert "Pull" in _active(synced.db)["title"]


def test_discard(synced, client):
    _start_push(client)
    resp = client.post("/train/discard")
    assert resp.status_code == 302 and "/workouts/mix" in resp.headers["Location"]
    assert synced.db.training_sessions.count_documents({}) == 0
    assert client.get("/train/session").headers["Location"].endswith("/train")


def test_stale_workouts_close_themselves(synced, client):
    _start_push(client)
    doc = _active(synced.db)
    old = dt.datetime.utcnow() - dt.timedelta(hours=7)
    synced.db.training_sessions.update_one(
        {"_id": doc["_id"]}, {"$set": {"started_at": old, "updated_at": old}}
    )
    assert client.get("/train/session").status_code == 302  # nothing logged: dropped
    assert synced.db.training_sessions.count_documents({}) == 0

    _start_push(client)
    doc = _active(synced.db)
    item = _first_weighted(doc)
    _log(client, item, 0, weight=60, reps=8)
    idx = doc["items"].index(item)
    synced.db.training_sessions.update_one(
        {"_id": doc["_id"]},
        {"$set": {"started_at": old, "updated_at": old, f"items.{idx}.rows.0.at": old}},
    )
    client.get("/train")
    kept = synced.db.training_sessions.find_one({"_id": doc["_id"]})
    assert kept["status"] == "done" and abs(kept["finished_at"] - old) < dt.timedelta(seconds=1)


def test_program_day_workout_marks_the_day_done(synced, client):
    page = client.get(f"/programs/{HUB}/week/1?level=beginner&env=gym").get_data(as_text=True)
    assert "Start workout" in page
    day = re.search(r'name="day" value="([^"]+)"', page).group(1)
    client.post(
        "/train/start",
        data={
            "from": "program",
            "hub": HUB,
            "week": "1",
            "day": day,
            "level": "beginner",
            "env": "gym",
        },
    )
    doc = _active(synced.db)
    assert doc["source"]["kind"] == "program" and "Week 1" in doc["title"]
    assert doc["items"][0]["reps"]  # the program's own sets and reps
    first = doc["items"][0]
    _log(client, first, 0, weight=20, reps=10, seconds=30, minutes=10)
    client.post("/train/finish")
    progress = synced.db.program_day_progress.find_one({"day_key": day, "week_number": 1})
    assert progress and progress.get("via") == "workout"
    page = client.get(f"/programs/{HUB}/week/1?level=beginner&env=gym&day={day}").get_data(
        as_text=True
    )
    assert "Completed" in page


def test_start_from_mix_and_exercise(synced, client):
    page = client.get("/workouts/mix?g=chest&g=back").get_data(as_text=True)
    assert "Start workout" in page
    client.post("/train/start", data={"from": "mix", "g": ["chest", "back"]})
    doc = _active(synced.db)
    assert doc["title"] == "Chest + Back" and doc["source"]["kind"] == "mix" and doc["items"]
    client.post("/train/discard")

    page = client.get("/workouts/push-up").get_data(as_text=True)
    assert "Log your sets" in page
    client.post("/train/start", data={"from": "exercise", "slug": "push-up"})
    doc = _active(synced.db)
    assert [i["slug"] for i in doc["items"]] == ["push-up"] and doc["items"][0]["kind"] == "reps"
    html = client.get("/train/session").get_data(as_text=True)
    assert "<span>Reps</span>" in html and "<span>lb</span>" not in html


def test_bad_start_forms_are_refused_politely(synced, client):
    for data in (
        {"from": "session", "p": "nope"},
        {"from": "mix"},
        {"from": "exercise", "slug": "nope"},
        {"from": "program", "hub": HUB, "week": "99", "day": "x"},
        {"from": "zzz"},
    ):
        resp = client.post("/train/start", data=data)
        assert resp.status_code == 302
    assert synced.db.training_sessions.count_documents({}) == 0


def test_units_switch(synced, client):
    _start_push(client)
    item = _first_weighted(_active(synced.db))
    _log(client, item, 0, weight=135, reps=8)
    client.post("/train/units", data={"units": "metric", "next": "/train/session"})
    html = client.get("/train/session").get_data(as_text=True)
    assert 'value="61.2"' in html and "<span>kg</span>" in html


def test_home_card_and_menu(synced, client):
    assert "Workout in progress" not in client.get("/").get_data(as_text=True)
    _start_push(client)
    home = client.get("/").get_data(as_text=True)
    assert "Workout in progress" in home and 'href="/train/session"' in home
    assert 'href="/train"' in home  # menu: My workouts


def test_history_is_private_and_deletable(synced, client, nfg_app):
    _start_push(client)
    doc = _active(synced.db)
    _log(client, _first_weighted(doc), 0, weight=50, reps=5)
    client.post("/train/finish")
    url = f"/train/history/{doc['_id']}"
    other = nfg_app.app.test_client()
    assert other.get(url).status_code == 404
    assert other.post(url + "/delete").status_code == 404
    assert client.get("/train/history/not-an-id").status_code == 404
    client.post(url + "/delete")
    assert synced.db.training_sessions.count_documents({}) == 0


def test_guest_workouts_move_to_the_account(synced, client, make_user):
    _start_push(client)
    doc = _active(synced.db)
    _log(client, _first_weighted(doc), 0, weight=50, reps=5)
    client.post("/train/finish")
    guest = synced.db.training_sessions.find_one({})["viewer_id"]
    make_user("lifter")
    client.post("/account/login", data={"identity": "lifter", "password": "password123"})
    moved = synced.db.training_sessions.find_one({})
    assert moved["viewer_id"] != guest and moved["viewer_id"].startswith("user:")
    assert doc["title"] in client.get("/train").get_data(as_text=True)


def test_export_and_delete_cover_workouts(synced, client, make_user):
    make_user("lifter")
    client.post("/account/login", data={"identity": "lifter", "password": "password123"})
    _start_push(client)
    _log(client, _first_weighted(_active(synced.db)), 0, weight=50, reps=5)
    client.post("/train/finish")
    export = client.get("/account/export").get_json()
    assert export["training_sessions"] and "viewer_id" not in export["training_sessions"][0]
    client.post("/account/delete", data={"password": "password123"})
    assert synced.db.training_sessions.count_documents({}) == 0
