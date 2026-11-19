"""Session mode phase 2: saved workouts, the exercise picker, add, swap, Saved tab."""

import content_sync as cs
import pytest
from bson import ObjectId


@pytest.fixture(scope="module")
def exercises():
    loaded, _ = cs.load_exercises()
    return loaded


@pytest.fixture
def synced(nfg_app, exercises):
    cs.apply_sync(nfg_app.db, cs.plan_sync(nfg_app.db, exercises))
    return nfg_app


PUSH = {"from": "session", "p": "push", "level": "beginner", "env": "gym"}


def _active(db):
    return db.training_sessions.find_one({"status": "active"})


def _saved(db):
    return db.saved_workouts.find_one({})


def _log(client, item, row=0, **values):
    data = {"key": item["key"], "row": str(row), "action": "done"}
    data.update({k: str(v) for k, v in values.items()})
    return client.post("/train/set", data=data, headers={"Accept": "application/json"})


def test_save_a_session_build_and_edit_it(synced, client):
    page = client.get("/workouts/mix?p=push&level=beginner&env=gym").get_data(as_text=True)
    assert 'name="what" value="source"' in page and "Save" in page
    resp = client.post("/train/save", data=dict(PUSH, what="source"))
    saved = _saved(synced.db)
    assert resp.status_code == 302 and resp.headers["Location"].endswith(
        f"/train/workouts/{saved['_id']}"
    )
    assert saved["title"] == "Push" and len(saved["items"]) >= 6
    url = f"/train/workouts/{saved['_id']}"
    html = client.get(url).get_data(as_text=True)
    assert saved["items"][0]["name"] in html and "Start workout" in html and "Add exercise" in html

    first, second = saved["items"][0], saved["items"][1]
    client.post(url + "/edit", data={"action": "rename", "title": "  Monday   push "})
    client.post(
        url + "/edit",
        data={"action": "item", "key": first["key"], "sets": "5", "reps": "5", "rest": "3 min"},
    )
    client.post(
        url + "/edit",
        data={"action": "item", "key": second["key"], "sets": "99", "reps": "10", "rest": "a week"},
    )
    client.post(url + "/edit", data={"action": "down", "key": first["key"]})
    after = _saved(synced.db)
    assert after["title"] == "Monday push"
    assert [i["key"] for i in after["items"][:2]] == [second["key"], first["key"]]
    edited = after["items"][1]
    assert (edited["sets"], edited["reps"], edited["rest"]) == (5, "5", "3 min")
    assert after["items"][0]["sets"] == 12 and after["items"][0]["rest"] == ""
    client.post(url + "/edit", data={"action": "remove", "key": first["key"]})
    assert first["key"] not in [i["key"] for i in _saved(synced.db)["items"]]


def test_saved_workouts_are_private(synced, client, nfg_app):
    client.post("/train/save", data=dict(PUSH, what="source"))
    url = f"/train/workouts/{_saved(synced.db)['_id']}"
    other = nfg_app.app.test_client()
    assert other.get(url).status_code == 404
    assert other.post(url + "/edit", data={"action": "rename", "title": "x"}).status_code == 404
    assert other.post(url + "/delete").status_code == 404
    assert (
        other.post(
            "/train/start", data={"from": "saved", "id": str(_saved(synced.db)["_id"])}
        ).status_code
        == 302
    )
    assert synced.db.training_sessions.count_documents({}) == 0
    assert client.get("/train/workouts/not-an-id").status_code == 404
    client.post(url + "/delete")
    assert synced.db.saved_workouts.count_documents({}) == 0


def test_start_a_saved_workout_and_finish_it(synced, client):
    client.post("/train/save", data=dict(PUSH, what="source"))
    saved = _saved(synced.db)
    wid = str(saved["_id"])
    client.post("/train/start", data={"from": "saved", "id": wid})
    doc = _active(synced.db)
    assert doc["source"]["kind"] == "saved" and doc["title"] == "Push"
    assert [i["slug"] for i in doc["items"]] == [i["slug"] for i in saved["items"]]
    assert "Resume workout" in client.get(f"/train/workouts/{wid}").get_data(as_text=True)
    weighted = next(i for i in doc["items"] if i["kind"] == "weight_reps")
    _log(client, weighted, 0, weight=60, reps=8)
    client.post("/train/finish")
    assert _saved(synced.db).get("last_done_at")
    listing = client.get("/train").get_data(as_text=True)
    assert "last done" in listing and "Push" in listing


def test_save_a_finished_workout(synced, client):
    client.post("/train/start", data=PUSH)
    doc = _active(synced.db)
    item = doc["items"][0]
    client.post("/train/rows", data={"key": item["key"], "action": "add"})
    _log(client, _active(synced.db)["items"][0], 0, weight=50, reps=8, minutes=5, seconds=30)
    resp = client.post("/train/finish")
    summary = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Save as workout" in summary
    client.post("/train/save", data={"what": str(doc["_id"])})
    saved = _saved(synced.db)
    assert saved["title"] == doc["title"] and len(saved["items"]) == len(doc["items"])
    assert saved["items"][0]["sets"] == item["sets_target"] + 1
    # Someone else's workout can't be saved.
    assert client.post("/train/save", data={"what": str(ObjectId())}).status_code == 302
    assert synced.db.saved_workouts.count_documents({}) == 1


def test_new_workout_pick_and_add(synced, client):
    resp = client.post("/train/workouts/new")
    wid = str(_saved(synced.db)["_id"])
    assert resp.headers["Location"].endswith(f"/train/workouts/{wid}")
    html = client.get(f"/train/workouts/{wid}").get_data(as_text=True)
    assert "Start workout" not in html and "Add exercise" in html
    pick = client.get(f"/train/pick?to={wid}&g=chest&env=gym").get_data(as_text=True)
    assert "Barbell Bench Press" in pick and 'name="slug" value="barbell-bench-press"' in pick
    search = client.get(f"/train/pick?to={wid}&q=goblet&env=gym").get_data(as_text=True)
    assert "Goblet Squat" in search and "Barbell Bench Press" not in search
    resp = client.post(
        "/train/add",
        data={"to": wid, "slug": "barbell-bench-press", "next": f"/train/workouts/{wid}"},
    )
    assert resp.headers["Location"].endswith(f"/train/workouts/{wid}")
    saved = _saved(synced.db)
    assert [i["slug"] for i in saved["items"]] == ["barbell-bench-press"] and saved["items"][0][
        "reps"
    ]
    again = client.get(f"/train/pick?to={wid}&g=chest&env=gym").get_data(as_text=True)
    assert "In it" in again
    assert client.get(f"/train/pick?to={ObjectId()}").status_code == 404


def test_add_to_workout_from_an_exercise_page(synced, client):
    page = client.get("/workouts/barbell-bench-press").get_data(as_text=True)
    assert "Add to workout" in page and "/train/add/barbell-bench-press" in page
    menu = client.get("/train/add/barbell-bench-press").get_data(as_text=True)
    assert "New workout" in menu and "Workout in progress" not in menu
    client.post("/train/add", data={"to": "new", "slug": "barbell-bench-press"})
    wid = str(_saved(synced.db)["_id"])
    client.post("/train/start", data=PUSH)
    menu = client.get("/train/add/goblet-squat").get_data(as_text=True)
    assert "Workout in progress" in menu and "New workout" in menu and "/train/workouts" not in menu
    resp = client.post("/train/add", data={"to": "session", "slug": "goblet-squat"})
    items = _active(synced.db)["items"]
    assert items[-1]["slug"] == "goblet-squat" and resp.headers["Location"].endswith(
        f"#ex-{items[-1]['key']}"
    )
    resp = client.post(
        "/train/add", data={"to": wid, "slug": "goblet-squat", "next": "/workouts/goblet-squat"}
    )
    assert resp.headers["Location"].endswith("/workouts/goblet-squat")
    assert [i["slug"] for i in _saved(synced.db)["items"]] == [
        "barbell-bench-press",
        "goblet-squat",
    ]
    assert client.get("/train/add/no-such-exercise").status_code == 404


def test_empty_workout_then_add_exercises(synced, client):
    page = client.get("/train").get_data(as_text=True)
    assert "Start an empty workout" in page and "New workout" in page
    client.post("/train/start", data={"from": "empty"})
    doc = _active(synced.db)
    assert doc["items"] == [] and doc["title"] == "Workout"
    html = client.get("/train/session").get_data(as_text=True)
    assert "Add the exercises you" in html and "/train/pick?to=session" in html
    pick = client.get("/train/pick?to=session&q=bench&env=gym").get_data(as_text=True)
    assert "Barbell Bench Press" in pick
    client.post("/train/add", data={"to": "session", "slug": "barbell-bench-press"})
    item = _active(synced.db)["items"][0]
    assert item["slug"] == "barbell-bench-press" and len(item["rows"]) >= 3
    assert _log(client, item, 0, weight=60, reps=5).get_json()["ok"]


def test_swap_mid_workout(synced, client):
    client.post("/train/start", data=PUSH)
    doc = _active(synced.db)
    first = doc["items"][0]
    pick = client.get(f"/train/pick?to=session&swap={first['key']}").get_data(as_text=True)
    assert "Good swaps" in pick and f"Instead of {first['name']}" in pick
    import re

    slugs = re.findall(r'name="slug" value="([^"]+)"', pick)
    in_workout = {i["slug"] for i in doc["items"]}
    choice = next(s for s in slugs if s not in in_workout)
    client.post("/train/add", data={"to": "session", "slug": choice, "swap": first["key"]})
    items = _active(synced.db)["items"]
    assert items[0]["slug"] == choice and items[0]["swapped_from"] == first["name"]
    assert len(items[0]["rows"]) == len(first["rows"]) and len(items) == len(doc["items"])
    assert f"Instead of {first['name']}" in client.get("/train/session").get_data(as_text=True)

    # With a set already logged, the logged set stays and the new exercise follows.
    second = items[1]
    _log(client, second, 0, weight=40, reps=10, seconds=30, minutes=5)
    other = next(s for s in slugs if s not in {i["slug"] for i in items})
    client.post("/train/add", data={"to": "session", "slug": other, "swap": second["key"]})
    items = _active(synced.db)["items"]
    assert items[1]["slug"] == second["slug"] and len(items[1]["rows"]) == 1
    assert items[2]["slug"] == other and len(items[2]["rows"]) == len(second["rows"]) - 1


def test_remove_an_exercise(synced, client):
    client.post("/train/start", data=PUSH)
    items = _active(synced.db)["items"]
    client.post("/train/remove-item", data={"key": items[0]["key"]})
    after = _active(synced.db)["items"]
    assert len(after) == len(items) - 1 and after[0]["key"] == items[1]["key"]
    _log(client, after[0], 0, weight=40, reps=10, seconds=30, minutes=5)
    client.post("/train/remove-item", data={"key": after[0]["key"]})  # has a logged set: stays
    assert len(_active(synced.db)["items"]) == len(after)


def test_saved_tab(synced, client):
    assert "Tap <b>Save</b> on an exercise" in client.get("/train?tab=saved").get_data(as_text=True)
    client.post("/favorites/exercise/barbell-bench-press/toggle")
    html = client.get("/train?tab=saved").get_data(as_text=True)
    assert "Barbell Bench Press" in html
    history = client.get("/train?tab=history").get_data(as_text=True)
    assert "Workouts you log show up here" in history


def test_saved_workouts_follow_the_account(synced, client, make_user):
    client.post("/train/save", data=dict(PUSH, what="source"))
    make_user("lifter")
    client.post("/account/login", data={"identity": "lifter", "password": "password123"})
    saved = _saved(synced.db)
    assert saved["viewer_id"].startswith("user:")
    export = client.get("/account/export").get_json()
    assert export["saved_workouts"] and export["saved_workouts"][0]["title"] == "Push"
    client.post("/account/delete", data={"password": "password123"})
    assert synced.db.saved_workouts.count_documents({}) == 0
