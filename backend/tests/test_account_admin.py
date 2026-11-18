"""Account page (plans, macros, saved, preferences, password) and Admin → Coach's Picks."""

import pytest
from werkzeug.security import check_password_hash, generate_password_hash


@pytest.fixture
def member(client, make_user, nfg_app):
    user_id = make_user()
    client.post("/account/login", data={"identity": "tester", "password": "password123"})
    yield client, f"user:{user_id}", user_id
    nfg_app.db.curated_collections.delete_many({})


def _program_sync(nfg_app):
    import content_sync as cs
    import program_content as pc

    exercises, _ = cs.load_exercises()
    cs.apply_sync(nfg_app.db, cs.plan_sync(nfg_app.db, exercises))
    programs, _ = pc.load_programs(exercises)
    pc.apply_program_sync(nfg_app.db, pc.plan_program_sync(nfg_app.db, programs, exercises))


def test_account_page_needs_sign_in(client):
    resp = client.get("/account")
    assert resp.status_code == 302 and "/account/login" in resp.headers["Location"]


def test_account_shows_profile_and_empty_states(member):
    client, _, _ = member
    html = client.get("/account").get_data(as_text=True)
    assert "tester" in html and "tester@example.com" in html
    assert "Pick a Coach&#39;s Plan" in html or "Pick a Coach's Plan" in html
    assert "Work out your daily calories" in html
    assert "Preferences" in html and "Change password" in html and "Sign out" in html


def test_account_lists_plans_and_macros(member, nfg_app):
    client, key, _ = member
    _program_sync(nfg_app)
    client.post(
        "/programs/12-week-body-recomposition/week/1/day-status",
        data={"level": "intermediate", "env": "gym", "day_key": "push"},
    )
    client.post(
        "/macros/profile",
        data={
            "units": "metric",
            "sex": "female",
            "age": "30",
            "height_cm": "168",
            "weight_kg": "64",
            "activity": "light",
            "goal": "cut",
        },
    )
    html = client.get("/account").get_data(as_text=True)
    assert "12-Week Body Recomposition" in html and "Week 1 · Pull" in html
    assert "day=pull" in html  # the plan card goes straight to today's day
    assert "Cut · " in html and "kcal today" in html
    assert "Push</b> · 12-Week Body Recomposition" in html  # recently trained
    for coll in ("macro_profiles", "weight_log"):
        nfg_app.db[coll].delete_many({"viewer_id": key})


def test_account_preferences_set_the_cookies(member):
    client, _, _ = member
    resp = client.get("/account?level=advanced")
    assert any(c.startswith("nfg_level=advanced") for c in resp.headers.getlist("Set-Cookie"))
    resp = client.get("/account?env=home")
    assert any(c.startswith("nfg_env=home") for c in resp.headers.getlist("Set-Cookie"))
    html = client.get("/account").get_data(as_text=True)
    assert 'aria-current="true">Advanced<' in html and 'aria-current="true">Home<' in html
    resp = client.post("/theme", data={"theme": "dark", "next": "/account"})
    assert resp.headers["Location"].endswith("/account")


def test_change_password(member, nfg_app):
    client, _, user_id = member
    wrong = client.post(
        "/account/password",
        data={
            "current_password": "nope",
            "password": "newpassword1",
            "confirm_password": "newpassword1",
        },
    )
    assert wrong.status_code == 302
    doc = nfg_app.db.users.find_one({"_id": user_id})
    assert check_password_hash(doc["password_hash"], "password123")
    ok = client.post(
        "/account/password",
        data={
            "current_password": "password123",
            "password": "newpassword1",
            "confirm_password": "newpassword1",
        },
    )
    assert ok.status_code == 302
    doc = nfg_app.db.users.find_one({"_id": user_id})
    assert check_password_hash(doc["password_hash"], "newpassword1")
    assert int(doc.get("auth_gen") or 0) == 1  # other sessions end
    assert client.get("/account").status_code == 200  # this one stays signed in


# --------------------------------------------------------------------------
# Admin → Coach's Picks
# --------------------------------------------------------------------------
@pytest.fixture
def admin(client, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "ADMIN_PASSWORD_HASH", generate_password_hash("pw"))
    client.post("/login", data={"username": "admin", "password": "pw"})
    _program_sync(nfg_app)
    yield client
    nfg_app.db.curated_collections.delete_many({})


def test_picks_screen_is_admin_only(client):
    assert client.get("/admin/coach-picks").status_code in (302, 401, 403)


def test_admin_edits_coach_picks(admin, nfg_app):
    db = nfg_app.db
    html = admin.get("/admin/coach-picks").get_data(as_text=True)
    assert "Push-Up" in html and "content/collections/coach-picks.yaml" in html
    admin.post(
        "/admin/coach-picks", data={"action": "add", "q": "Goblet squat"}
    )  # case-insensitive
    admin.post("/admin/coach-picks", data={"action": "add", "q": "Barbell Back Squat"})
    admin.post("/admin/coach-picks", data={"action": "up", "slug": "barbell-back-squat"})
    admin.post("/admin/coach-picks", data={"action": "remove", "slug": "push-up"})
    doc = db.curated_collections.find_one({"key": "coach-picks"})
    items = doc["items"]
    assert "push-up" not in items and items.count("goblet-squat") == 1
    assert items[-2] == "barbell-back-squat"  # added last, then moved up one
    admin.post(
        "/admin/coach-picks",
        data={"action": "text", "title": "Nate's staples", "subtitle": "Start here"},
    )
    # Home shows the override (in order) for visitors.
    admin.post("/logout")
    nfg_app._invalidate_library_cache()
    home = admin.get("/").get_data(as_text=True)
    assert "Nate&#39;s staples" in home or "Nate's staples" in home
    assert "Barbell Back Squat" in home


def test_admin_can_reset_picks_to_the_file(admin, nfg_app):
    admin.post("/admin/coach-picks", data={"action": "remove", "slug": "push-up"})
    assert nfg_app.db.curated_collections.count_documents({}) == 1
    admin.post("/admin/coach-picks", data={"action": "reset"})
    assert nfg_app.db.curated_collections.count_documents({}) == 0
    html = admin.get("/admin/coach-picks").get_data(as_text=True)
    assert "Push-Up" in html


def test_unknown_exercise_is_refused(admin, nfg_app):
    admin.post("/admin/coach-picks", data={"action": "add", "q": "Not A Real Lift"})
    assert nfg_app.db.curated_collections.count_documents({}) == 0
