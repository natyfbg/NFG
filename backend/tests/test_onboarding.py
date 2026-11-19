"""The training profile (/start) and the redesigned sign-in pages."""

import content_sync as cs
import program_content as pc
import pytest

REG = {
    "username": "lifter",
    "email": "lifter@example.com",
    "password": "password123",
    "confirm_password": "password123",
}


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


def _cookie(client, name):
    c = client.get_cookie(name)
    return c.value if c else None


def test_sign_up_goes_to_setup_and_saves_the_profile(client, nfg_app):
    resp = client.post("/account/register", data=REG)
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/start")
    page = client.get("/start").get_data(as_text=True)
    assert "Make" in page and "How long have you been training?" in page
    assert (
        "Where do you train?" in page
        and "What's your main goal?" in page
        and "Skip for now" in page
    )
    resp = client.post(
        "/start", data={"level": "intermediate", "env": "hybrid", "goal": "build_muscle"}
    )
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    assert _cookie(client, "nfg_level") == "intermediate" and _cookie(client, "nfg_env") == "hybrid"
    assert _cookie(client, "nfg_goal") == "build_muscle" and _cookie(client, "nfg_onboarded") == "1"
    user = nfg_app.db.users.find_one({"username_lower": "lifter"})
    prof = user["training_profile"]
    assert (prof["level"], prof["env"], prof["goal"]) == ("intermediate", "hybrid", "build_muscle")


def test_bad_answers_are_ignored_and_next_is_kept_safe(client):
    resp = client.post(
        "/start",
        data={"level": "olympian", "env": "moon", "goal": "fly", "next": "//evil.example.com"},
    )
    assert resp.headers["Location"].endswith("/") and "evil" not in resp.headers["Location"]
    assert _cookie(client, "nfg_level") is None and _cookie(client, "nfg_goal") is None
    resp = client.post("/start", data={"goal": "recomp", "next": "/macros"})
    assert resp.headers["Location"].endswith("/macros") and _cookie(client, "nfg_goal") == "recomp"


def test_home_offers_setup_once(client):
    home = client.get("/").get_data(as_text=True)
    assert "Make Qiltsim yours" in home and "Three taps" in home
    client.post("/start", data={"action": "skip"})
    assert _cookie(client, "nfg_onboarded") == "skip"
    assert "Make Qiltsim yours" not in client.get("/").get_data(as_text=True)
    other = client.application.test_client()
    other.set_cookie("nfg_level", "beginner")  # has used the app before: don't ask
    assert "Make Qiltsim yours" not in other.get("/").get_data(as_text=True)


def test_home_recommends_a_program_for_the_goal(synced, client):
    client.set_cookie("nfg_goal", "lose_fat")
    home = client.get("/").get_data(as_text=True)
    assert "For your goal" in home
    i = home.index("For your goal")
    assert home.rfind("8-week-challenge", 0, i) > home.rfind("nfg-program-card", 0, i) - 400


def test_profile_follows_the_account(client, nfg_app, make_user):
    make_user("tester")
    nfg_app.db.users.update_one(
        {"username_lower": "tester"},
        {"$set": {"training_profile": {"level": "advanced", "env": "home", "goal": "stay_fit"}}},
    )
    client.post("/account/login", data={"identity": "tester", "password": "password123"})
    assert _cookie(client, "nfg_level") == "advanced" and _cookie(client, "nfg_env") == "home"
    assert _cookie(client, "nfg_goal") == "stay_fit" and _cookie(client, "nfg_onboarded") == "1"
    # Changing a preference anywhere saves it on the account.
    client.get("/account?level=beginner")
    assert (
        nfg_app.db.users.find_one({"username_lower": "tester"})["training_profile"]["level"]
        == "beginner"
    )
    page = client.get("/account").get_data(as_text=True)
    assert "Goal" in page and "/start/goal?goal=lose_fat" in page
    client.get("/start/goal?goal=lose_fat&next=/account")
    assert (
        nfg_app.db.users.find_one({"username_lower": "tester"})["training_profile"]["goal"]
        == "lose_fat"
    )
    export = client.get("/account/export").get_json()
    assert export["account"]["training_profile"]["goal"] == "lose_fat"


def test_guests_profile_stays_in_cookies(client, nfg_app):
    client.post("/start", data={"level": "advanced"})
    assert nfg_app.db.users.count_documents({"training_profile": {"$exists": True}}) == 0


def test_macros_calculator_starts_from_the_goal(client):
    client.set_cookie("nfg_goal", "build_muscle")
    html = client.get("/macros").get_data(as_text=True)
    assert 'name="goal" value="bulk" checked' in html


def test_sign_in_page_keeps_the_name_and_hides_passwords(client, make_user):
    make_user("tester")
    page = client.get("/account/login").get_data(as_text=True)
    assert "Welcome back" in page and "data-pass-toggle" in page and 'type="password"' in page
    bad = client.post("/account/login", data={"identity": "tester", "password": "nope"})
    html = bad.get_data(as_text=True)
    assert "Invalid login" in html and 'value="tester"' in html


def test_sign_up_keeps_what_you_typed(client):
    bad = client.post("/account/register", data=dict(REG, confirm_password="different1"))
    html = bad.get_data(as_text=True)
    assert "Passwords do not match" in html
    assert 'value="lifter"' in html and 'value="lifter@example.com"' in html
    assert "Create your account" in html and "comes with you" in html


def test_admin_and_reset_pages_use_the_new_layout(client):
    for url in ("/login", "/account/forgot"):
        html = client.get(url).get_data(as_text=True)
        assert "nfg-auth-card" in html, url
