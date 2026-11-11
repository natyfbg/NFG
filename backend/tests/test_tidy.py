"""Sprint 1 (ship and tidy): library snapshot, stylesheet versioning, visitor-facing copy."""

import content_sync as cs
import mongomock
import pytest

BASE = {"status": "published", "level": "beginner", "category": "strength"}


def ex(slug, name, primary, equipment=(), movement="horizontal_push", **extra):
    return {
        **BASE,
        "slug": slug,
        "name": name,
        "movement": movement,
        "muscles": {"primary": list(primary), "secondary": []},
        "equipment": list(equipment),
        **extra,
    }


LIBRARY = [
    ex("bw-press", "BW Press", ["chest"], mechanics="compound"),
    ex("bb-press", "BB Press", ["chest"], ["barbell", "bench"], mechanics="compound"),
    ex("cable-row", "Cable Row", ["upper_back"], ["cable"], movement="horizontal_pull"),
]


@pytest.fixture
def library(nfg_app):
    cs.apply_sync(nfg_app.db, cs.plan_sync(nfg_app.db, LIBRARY))
    return nfg_app.db


@pytest.fixture
def cached(nfg_app, monkeypatch):
    """Switch the library snapshot on, as in production."""
    monkeypatch.setattr(nfg_app, "LIBRARY_CACHE_SECONDS", 60)
    nfg_app._invalidate_library_cache()
    yield nfg_app
    nfg_app._invalidate_library_cache()


@pytest.fixture
def query_log(monkeypatch):
    """Counts Mongo calls per collection while a test runs."""
    calls = []
    for name in ("find", "find_one", "count_documents", "aggregate"):
        original = getattr(mongomock.collection.Collection, name)

        def wrapped(self, *args, _original=original, _name=name, **kwargs):
            calls.append((self.name, _name))
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(mongomock.collection.Collection, name, wrapped)
    return calls


# --------------------------------------------------------------------------
# Library snapshot
# --------------------------------------------------------------------------


def test_home_reads_the_library_once_then_from_cache(client, library, cached, query_log):
    client.get("/")
    first = [c for c in query_log if c[0] == "workouts"]
    query_log.clear()
    assert client.get("/").status_code == 200
    again = [c for c in query_log if c[0] == "workouts"]
    assert len(first) == 1 and again == []
    assert len(query_log) <= 5  # whole page, warm cache


def test_exercises_page_uses_the_snapshot(client, library, cached, query_log):
    client.get("/")
    query_log.clear()
    html = client.get("/workouts").get_data(as_text=True)
    assert [c for c in query_log if c[0] == "workouts"] == []
    assert "Search 3 exercises" in html and "Barbell" in html and "Cables" in html


def test_cached_docs_are_not_changed_by_pages(client, library, cached):
    client.get("/")
    client.get("/workouts/mix?p=push")
    for doc in cached._library_snapshot["docs"]:
        assert "session_role" not in doc and "mix_score" not in doc


def test_snapshot_refreshes_after_an_admin_write(client, library, cached):
    client.get("/workouts")
    cs.apply_sync(
        library,
        cs.plan_sync(library, LIBRARY + [ex("new-press", "New Press", ["chest"])]),
    )
    assert "Search 3 exercises" in client.get("/workouts").get_data(as_text=True)
    client.post("/admin/workouts/backfill-metadata")  # any admin POST clears it
    assert "Search 4 exercises" in client.get("/workouts").get_data(as_text=True)


def test_admin_reads_fresh_and_sees_drafts(client, library, cached, nfg_app, monkeypatch):
    from werkzeug.security import generate_password_hash

    client.get("/workouts")  # visitor primes the cache
    draft = {**ex("draft-press", "Draft Press", ["chest"]), "status": "draft"}
    cs.apply_sync(library, cs.plan_sync(library, LIBRARY + [draft]))
    monkeypatch.setattr(nfg_app, "ADMIN_PASSWORD_HASH", generate_password_hash("pw"))
    client.post("/login", data={"username": "admin", "password": "pw"})
    assert "Search 4 exercises" in client.get("/workouts").get_data(as_text=True)


# --------------------------------------------------------------------------
# Stylesheet versioning
# --------------------------------------------------------------------------


def test_stylesheet_link_is_versioned_and_long_cached(client, nfg_app, monkeypatch):
    html = client.get("/about").get_data(as_text=True)
    assert "/static/css/app.css?v=" in html
    resp = client.get(f"/static/css/app.css?v={nfg_app.ASSET_VERSION}")
    assert "immutable" in resp.headers.get("Cache-Control", "")
    assert "immutable" not in client.get("/static/css/app.css").headers.get("Cache-Control", "")


def test_asset_version_prefers_the_deployed_commit(nfg_app, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abcdef1234567890")
    assert nfg_app._asset_version() == "abcdef12"


# --------------------------------------------------------------------------
# Visitor-facing copy
# --------------------------------------------------------------------------


def test_about_is_real_copy(client):
    html = client.get("/about").get_data(as_text=True)
    assert "starter template" not in html and "Suggested sections" not in html
    assert "Train safely" in html


def test_programs_page_has_no_internal_notes(client):
    html = client.get("/programs").get_data(as_text=True)
    assert "exist in the database" not in html


def test_unbuilt_week_says_coming_soon_to_visitors(client):
    html = client.get("/programs/8-week-challenge/week/1?level=beginner&env=home").get_data(
        as_text=True
    )
    assert "is on its way" in html
    assert "Sample week" not in html and "Admin" not in html


def test_admin_only_sentences_are_hidden(nfg_app):
    text = "Week-by-week plan with exercise options. Add weeks/workouts next in Admin."
    assert nfg_app.visitor_text(text) == "Week-by-week plan with exercise options."
    assert nfg_app.visitor_text(None) == ""


def test_recipes_empty_state_has_no_admin_talk(client, nfg_app):
    nfg_app.db.recipes.delete_many({})
    html = client.get("/recipes").get_data(as_text=True)
    assert "admin tool" not in html
