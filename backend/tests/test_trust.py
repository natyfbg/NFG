"""Your data: privacy and terms pages, export, delete account, forget this device."""

import json

import pytest

PROFILE = {
    "units": "metric",
    "sex": "female",
    "age": "30",
    "height_cm": "168",
    "weight_kg": "64",
    "activity": "light",
    "goal": "cut",
}


@pytest.fixture
def member(client, make_user, nfg_app):
    user_id = make_user()
    client.post("/account/login", data={"identity": "tester", "password": "password123"})
    key = f"user:{user_id}"
    client.post("/macros/profile", data=PROFILE)
    client.post("/macros/log", data={"name": "Eggs", "kcal": "300", "protein": "25"})
    nfg_app.db.favorites.insert_one(
        {"viewer_id": key, "item_type": "exercise", "item_slug": "push-up"}
    )
    nfg_app.db.program_day_progress.insert_one(
        {
            "viewer_id": key,
            "track_slug": "8-week-challenge-beginner-gym",
            "week_number": 1,
            "day_key": "full-body-a",
        }
    )
    return client, key, user_id


def test_privacy_and_terms_pages(client):
    privacy = client.get("/privacy").get_data(as_text=True)
    assert "What we keep" in privacy and "nfg_vid" in privacy and "Resend" in privacy
    assert "Forget this device" in privacy
    terms = client.get("/terms").get_data(as_text=True)
    assert "not medical advice" in terms
    home = client.get("/").get_data(as_text=True)
    assert 'href="/privacy"' in home and 'href="/terms"' in home
    assert "/privacy" in client.get("/sitemap.xml").get_data(as_text=True)
    assert "agree to the" in client.get("/account/register").get_data(as_text=True)


def test_export_has_everything_and_nothing_internal(member):
    client, key, _ = member
    resp = client.get("/account/export")
    assert resp.status_code == 200 and "attachment" in resp.headers["Content-Disposition"]
    assert resp.headers["Cache-Control"] == "no-store"
    data = json.loads(resp.get_data(as_text=True))
    assert data["account"]["email"] == "tester@example.com"
    assert data["food_log"][0]["name"] == "Eggs" and data["macro_profiles"][0]["goal"] == "cut"
    assert data["favorites"][0]["item_slug"] == "push-up"
    assert data["program_day_progress"][0]["day_key"] == "full-body-a"
    text = resp.get_data(as_text=True)
    assert "password_hash" not in text and "viewer_id" not in text and key not in text


def test_export_needs_sign_in(client):
    assert client.get("/account/export").status_code == 302


def test_delete_account_needs_password_then_removes_everything(member, nfg_app):
    client, key, user_id = member
    db = nfg_app.db
    client.post("/account/delete", data={"password": "wrong"})
    assert db.users.find_one({"_id": user_id}) is not None
    assert db.food_log.count_documents({"viewer_id": key}) == 1
    resp = client.post("/account/delete", data={"password": "password123"})
    assert resp.status_code == 302
    assert db.users.find_one({"_id": user_id}) is None
    for name in nfg_app.VIEWER_COLLECTIONS:
        assert db[name].count_documents({"viewer_id": key}) == 0, name
    assert client.get("/account").status_code == 302  # signed out


def test_forget_this_device_for_guests(client, nfg_app):
    db = nfg_app.db
    client.set_cookie("nfg_vid", "guest-forget-1")
    client.post("/macros/profile", data=PROFILE)
    client.post("/macros/log", data={"name": "Toast", "kcal": "150"})
    assert db.food_log.count_documents({"viewer_id": "guest-forget-1"}) == 1
    resp = client.post("/privacy/forget-device")
    assert resp.status_code == 302 and "nfg_vid=;" in " ".join(resp.headers.getlist("Set-Cookie"))
    for name in nfg_app.VIEWER_COLLECTIONS:
        assert db[name].count_documents({"viewer_id": "guest-forget-1"}) == 0, name


def test_every_per_viewer_collection_is_covered(nfg_app):
    """New per-viewer data must be added to VIEWER_COLLECTIONS (export + delete)."""
    import pathlib
    import re

    sources = "".join(p.read_text() for p in pathlib.Path(".").glob("*.py"))
    keyed = set(
        re.findall(
            r'db\.(\w+)\.(?:insert_one|update_one|update_many)\(\s*\{?\s*"viewer_id"', sources
        )
    )
    keyed |= set(re.findall(r"db\.(\w+)\.insert_one\(\s*dict\([^)]*viewer_id=", sources))
    assert keyed, "pattern found nothing"
    assert keyed <= set(nfg_app.VIEWER_COLLECTIONS) | {"content_sync_runs"}, keyed - set(
        nfg_app.VIEWER_COLLECTIONS
    )
