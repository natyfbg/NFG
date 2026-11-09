"""Discovery Home, training environment, Muscle Mixer, favorites and curation."""

import datetime

import content_sync as cs
import curation
import pytest

BASE = {
    "status": "published",
    "level": "beginner",
    "category": "strength",
    "movement": "horizontal_push",
}


def ex(slug, name, primary, secondary=(), equipment=(), **extra):
    return {
        **BASE,
        "slug": slug,
        "name": name,
        "muscles": {"primary": list(primary), "secondary": list(secondary)},
        "equipment": list(equipment),
        **extra,
    }


LIBRARY = [
    ex("bw-press", "BW Press", ["chest"], ["triceps", "front_delts"]),
    ex(
        "bar-press", "Bar Press", ["chest"], ["triceps"], ["barbell", "bench"], level="intermediate"
    ),
    ex("db-extension", "DB Extension", ["triceps"], [], ["dumbbell"], movement="isolation"),
    ex("cable-row", "Cable Row", ["lats"], ["biceps"], ["cable"], movement="horizontal_pull"),
    ex("hidden-press", "Hidden Press", ["chest"], status="draft"),
]


@pytest.fixture
def library(nfg_app):
    plan = cs.plan_sync(nfg_app.db, LIBRARY)
    cs.apply_sync(nfg_app.db, plan)
    return nfg_app.db


# --------------------------------------------------------------------------
# Home
# --------------------------------------------------------------------------


def test_home_shows_discovery_sections(client, library):
    html = client.get("/").get_data(as_text=True)
    for text in ("What are you training today?", "Where are you training?", "Muscle Mixer"):
        assert text in html
    # Group tiles carry counts of published exercises only (draft hidden).
    assert "Chest" in html and "2 exercises" in html


def test_env_choice_is_remembered_in_cookie(client, library):
    resp = client.get("/?env=home")
    assert any(c.startswith("nfg_env=home") for c in resp.headers.getlist("Set-Cookie"))
    # Next visit without ?env= keeps "home": barbell press no longer counted.
    html = client.get("/").get_data(as_text=True)
    assert 'aria-current="true">Home<' in html
    assert "1 exercise<" in html or "1 exercise\n" in html


def test_invalid_env_falls_back_to_hybrid(client, library):
    html = client.get("/?env=moon").get_data(as_text=True)
    assert 'aria-current="true">Hybrid<' in html


def test_group_counts_respect_environment(nfg_app, library):
    with nfg_app.app.test_request_context("/"):
        all_counts = nfg_app._group_counts("hybrid")
        home_counts = nfg_app._group_counts("home")
    assert all_counts["chest"] == 2 and home_counts["chest"] == 1
    assert all_counts.get("back") == 1 and "back" not in home_counts


def test_coach_picks_render_and_skip_missing(client, library, nfg_app, monkeypatch):
    monkeypatch.setattr(
        nfg_app,
        "CURATED",
        {
            "coach-picks": {
                "title": "Coach's Picks",
                "subtitle": "",
                "items": ["cable-row", "nope", "hidden-press", "bw-press"],
            }
        },
    )
    html = client.get("/").get_data(as_text=True)
    assert "Coach&#39;s Picks" in html or "Coach's Picks" in html
    assert html.index("Cable Row") < html.index("BW Press")  # file order kept
    assert "Hidden Press" not in html


# --------------------------------------------------------------------------
# Muscle Mixer
# --------------------------------------------------------------------------


def test_mixer_ranks_by_coverage(nfg_app, library):
    with nfg_app.app.test_request_context("/"):
        results = nfg_app._mix_results(["chest", "triceps"], "hybrid")
    names = [r["name"] for r in results]
    # Presses hit chest (primary) + triceps (secondary) = 3; extension hits triceps only = 2.
    assert names[:2] == ["BW Press", "Bar Press"]  # beginner before intermediate on ties
    assert names[2] == "DB Extension"
    assert "Cable Row" not in names and "Hidden Press" not in names


def test_mixer_page_filters_by_environment(client, library):
    html = client.get("/workouts/mix?g=chest&env=home").get_data(as_text=True)
    assert "BW Press" in html and "Bar Press" not in html


def test_mixer_without_groups_shows_picker(client, library):
    html = client.get("/workouts/mix").get_data(as_text=True)
    assert "Build your mix" in html and "nfg-group-card" in html


def test_mixer_ignores_unknown_groups_and_caps_selection(client, library):
    resp = client.get("/workouts/mix?g=chest&g=wings&g=chest")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert '<h1 class="nfg-hero-title">Chest</h1>' in html


def test_mixer_chip_links_toggle_groups(client, library):
    html = client.get("/workouts/mix?g=chest").get_data(as_text=True)
    assert "/workouts/mix?g=chest&amp;g=triceps" in html  # add triceps
    assert 'href="/workouts/mix"' in html  # removing the only group clears the mix


# --------------------------------------------------------------------------
# Favorites (generic, guest-friendly)
# --------------------------------------------------------------------------


def test_guest_can_save_and_unsave_exercise(client, library):
    client.set_cookie("nfg_vid", "guest-1")
    resp = client.post("/favorites/exercise/bw-press/toggle", data={"next": "/"})
    assert resp.status_code == 302
    assert library.favorites.count_documents({"viewer_id": "guest-1"}) == 1
    assert "BW Press" in client.get("/").get_data(as_text=True).split("Saved")[-1]
    assert "♥ Saved" in client.get("/workouts/bw-press").get_data(as_text=True)

    client.post("/favorites/exercise/bw-press/toggle")
    assert library.favorites.count_documents({"viewer_id": "guest-1"}) == 0


def test_cannot_save_drafts_or_unknown_types(client, library):
    assert client.post("/favorites/exercise/hidden-press/toggle").status_code == 404
    assert client.post("/favorites/recipe/bw-press/toggle").status_code == 404


def test_saved_exercises_move_to_member_on_register(client, library):
    client.set_cookie("nfg_vid", "guest-2")
    client.post("/favorites/exercise/cable-row/toggle")
    client.post(
        "/account/register",
        data={
            "username": "saver",
            "email": "saver@example.com",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    user = library.users.find_one({"username_lower": "saver"})
    assert library.favorites.count_documents({"viewer_id": f"user:{user['_id']}"}) == 1
    assert library.favorites.count_documents({"viewer_id": "guest-2"}) == 0


# --------------------------------------------------------------------------
# Continue training progress
# --------------------------------------------------------------------------


def test_continue_plan_reports_percent(nfg_app):
    db = nfg_app.db
    track = db.programs.find_one({"slug": "8-week-challenge-beginner-home"})
    week = db.program_weeks.insert_one({"program_id": track["_id"], "week_number": 1, "order": 1})
    for day in ("push", "pull", "legs", "core"):
        db.program_items.insert_one({"week_id": week.inserted_id, "day": day, "order": 1})
    db.program_day_progress.insert_one(
        {
            "viewer_id": "guest-3",
            "track_slug": track["slug"],
            "week_number": 1,
            "day_key": "push",
            "completed_at": datetime.datetime.utcnow(),
        }
    )
    with nfg_app.app.test_request_context("/"):
        plan = nfg_app._continue_plan_for_owner("guest-3")
    assert plan["total_days"] == 4 and plan["done_days"] == 1 and plan["percent"] == 25


# --------------------------------------------------------------------------
# Curation files and content guards
# --------------------------------------------------------------------------


def test_repo_collections_are_valid():
    collections = curation.load_collections()
    assert "coach-picks" in collections
    assert collections["coach-picks"]["items"]


def test_bad_collection_rejected(tmp_path):
    (tmp_path / "bad.yaml").write_text("title: ''\nitems: oops\n", encoding="utf-8")
    with pytest.raises(curation.CurationError):
        curation.load_collections(tmp_path)


def test_reserved_slug_rejected(tmp_path):
    import yaml

    data = ex("mix", "Mix Exercise", ["chest"])
    (tmp_path / "mix.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(cs.ContentError, match="reserved"):
        cs.load_exercises(tmp_path)


def test_nav_says_exercises(client):
    html = client.get("/").get_data(as_text=True)
    assert '<span class="label">Exercises</span>' in html
