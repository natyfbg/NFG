"""Admin → Content sync: the dry run, Apply (inline in tests), one run at a time."""

import datetime as dt

import content_admin
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def admin(client, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "ADMIN_PASSWORD_HASH", generate_password_hash("pw"))
    nfg_app.app.config["CONTENT_SYNC_INLINE"] = True
    client.post("/login", data={"username": "admin", "password": "pw"})
    yield client
    nfg_app.app.config.pop("CONTENT_SYNC_INLINE", None)
    nfg_app.db.content_sync_runs.delete_many({})


def test_sync_screen_is_admin_only(client):
    assert client.get("/admin/content-sync").status_code in (302, 401, 403)
    assert client.post("/admin/content-sync/apply").status_code in (302, 401, 403)


def test_dry_run_lists_what_would_change(admin, nfg_app):
    html = admin.get("/admin/content-sync").get_data(as_text=True)
    plan = content_admin.build_plan(nfg_app.db)
    assert plan["exercises"]["creates"] and plan["changes"] > 0
    assert f"Apply {plan['changes']} change" in html
    assert "12-Week Body Recomposition" in html and "Draft: only you see it" in html
    assert "never deletes anything" in html
    assert nfg_app.db.content_sync_runs.count_documents({}) == 0  # looking writes nothing


def test_apply_writes_and_then_everything_is_up_to_date(admin, nfg_app):
    db = nfg_app.db
    before = db.workouts.count_documents({})
    resp = admin.post("/admin/content-sync/apply")
    assert resp.status_code == 302
    run = db.content_sync_runs.find_one({})
    assert run["status"] == "done" and run["counts"]["created"] > 0
    assert db.workouts.count_documents({}) > before
    assert db.programs.find_one({"slug": "16-week-muscle-building", "kind": "hub"})
    html = admin.get("/admin/content-sync").get_data(as_text=True)
    assert "Last sync finished" in html and "Everything is up to date" in html
    again = admin.post("/admin/content-sync/apply", follow_redirects=True)
    assert "already up to date" in again.get_data(as_text=True)
    assert db.content_sync_runs.count_documents({}) == 1
    assert admin.get("/admin/content-sync/status").get_json()["status"] == "done"


def test_one_run_at_a_time_and_stale_runs_release(admin, nfg_app):
    db = nfg_app.db
    db.content_sync_runs.insert_one({"status": "running", "started_at": dt.datetime.utcnow()})
    html = admin.post("/admin/content-sync/apply", follow_redirects=True).get_data(as_text=True)
    assert "already running" in html and db.content_sync_runs.count_documents({}) == 1
    db.content_sync_runs.update_many(
        {}, {"$set": {"started_at": dt.datetime.utcnow() - dt.timedelta(hours=1)}}
    )
    admin.post("/admin/content-sync/apply")
    assert db.content_sync_runs.count_documents({"status": "done"}) == 1


def test_failures_are_recorded_without_details(admin, nfg_app, monkeypatch):
    def boom(db):
        raise RuntimeError("mongodb+srv://user:secret@cluster.example.net")

    monkeypatch.setattr(content_admin, "apply_content", boom)
    admin.post("/admin/content-sync/apply")
    run = nfg_app.db.content_sync_runs.find_one({})
    assert run["status"] == "failed" and run["error"] == "RuntimeError"
    html = admin.get("/admin/content-sync").get_data(as_text=True)
    assert "The last sync stopped" in html and "secret" not in html
