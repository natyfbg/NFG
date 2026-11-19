"""Install to home screen + offline: manifest, service worker, offline page, head tags,
staying signed in, and the logger's offline journal hooks."""

import datetime
import json

import pytest


def test_manifest(client):
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.mimetype == "application/manifest+json"
    data = json.loads(resp.get_data(as_text=True))
    assert data["name"] == "Qiltsim" and data["display"] == "standalone"
    assert data["start_url"] == "/" and data["scope"] == "/"
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in data["icons"]}
    assert (
        ("192x192", "any") in sizes
        and ("512x512", "any") in sizes
        and ("512x512", "maskable") in sizes
    )
    for icon in data["icons"]:  # every icon exists
        assert client.get(icon["src"]).status_code == 200
    assert {s["url"] for s in data["shortcuts"]} == {"/train", "/workouts", "/macros"}


def test_service_worker_is_served_fresh_and_versioned(client, nfg_app):
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert resp.mimetype == "application/javascript"
    assert "no-cache" in resp.headers["Cache-Control"]
    assert resp.headers["Service-Worker-Allowed"] == "/"
    js = resp.get_data(as_text=True)
    assert f'var VERSION = "{nfg_app.ASSET_VERSION}"' in js or "var VERSION = " in js
    assert '"/offline"' in js  # precached
    assert "/account" in js and "/admin" in js  # never kept
    assert "/account/logout" in js  # signing out clears kept pages
    assert "unregister" not in js


def test_service_worker_switch_off(client, monkeypatch):
    import pwa

    monkeypatch.setattr(pwa, "ENABLED", False)
    js = client.get("/sw.js").get_data(as_text=True)
    assert "unregister" in js and "caches.delete" in js
    html = client.get("/").get_data(as_text=True)
    assert 'rel="manifest"' not in html and "serviceWorker" not in html


def test_offline_page(client):
    resp = client.get("/offline")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "You&#39;re offline" in html or "You're offline" in html
    assert 'name="robots" content="noindex"' in html
    assert 'href="/train"' in html


def test_head_tags_and_install_entry(client):
    html = client.get("/").get_data(as_text=True)
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert "navigator.serviceWorker.register('/sw.js'" in html
    assert "data-install hidden" in html and "Add to home screen" in html
    assert "data-install-tip hidden" in html


def test_members_stay_signed_in(client, make_user, nfg_app):
    make_user()
    resp = client.post("/account/login", data={"identity": "tester", "password": "password123"})
    cookie = next(c for c in resp.headers.getlist("Set-Cookie") if c.startswith("session="))
    assert "Expires=" in cookie  # not a browser-session cookie
    assert nfg_app.app.config["PERMANENT_SESSION_LIFETIME"] == datetime.timedelta(days=30)
    assert nfg_app.app.config["WTF_CSRF_TIME_LIMIT"] is None


def test_backup_admin_session_ends_with_the_browser(client, nfg_app, monkeypatch):
    from werkzeug.security import generate_password_hash

    monkeypatch.setattr(nfg_app, "ADMIN_PASSWORD_HASH", generate_password_hash("pw"))
    resp = client.post("/login", data={"username": "admin", "password": "pw"})
    cookie = next(c for c in resp.headers.getlist("Set-Cookie") if c.startswith("session="))
    assert "Expires=" not in cookie


@pytest.fixture
def workout(client, nfg_app):
    import content_sync as cs

    exercises, _ = cs.load_exercises()
    cs.apply_sync(nfg_app.db, cs.plan_sync(nfg_app.db, exercises))
    client.post(
        "/train/start", data={"from": "session", "p": "push", "level": "beginner", "env": "gym"}
    )
    doc = nfg_app.db.training_sessions.find_one({"status": "active"})
    assert doc
    return doc


def test_logger_page_carries_what_the_offline_journal_needs(client, workout):
    html = client.get("/train/session").get_data(as_text=True)
    wid = str(workout["_id"])
    assert f'data-workout="{wid}"' in html and 'data-unit="imperial"' in html
    key = workout["items"][0]["key"]
    assert f'data-key="{key}" data-row="0"' in html
    assert f'name="wid" value="{wid}"' in html
    assert "data-rest-seconds=" in html and "data-offline-note hidden" in html
    assert "nfg_train_journal" in html


def test_a_set_kept_for_another_workout_is_refused(client, workout, nfg_app):
    key = workout["items"][0]["key"]
    resp = client.post(
        "/train/set",
        data={
            "wid": "0" * 24,
            "key": key,
            "row": "0",
            "action": "done",
            "weight": "100",
            "reps": "8",
        },
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 409 and resp.get_json()["ok"] is False
    doc = nfg_app.db.training_sessions.find_one({"_id": workout["_id"]})
    assert not doc["items"][0]["rows"][0].get("done")
    ok = client.post(
        "/train/set",
        data={
            "wid": str(workout["_id"]),
            "key": key,
            "row": "0",
            "action": "done",
            "weight": "100",
            "reps": "8",
        },
        headers={"Accept": "application/json"},
    )
    assert ok.status_code == 200 and ok.get_json()["done"] is True
    # Sending the same set again (a retry after a timeout) changes nothing.
    again = client.post(
        "/train/set",
        data={
            "wid": str(workout["_id"]),
            "key": key,
            "row": "0",
            "action": "done",
            "weight": "100",
            "reps": "8",
        },
        headers={"Accept": "application/json"},
    )
    assert again.get_json()["sets"] == ok.get_json()["sets"]
