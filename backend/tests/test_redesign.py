"""Exercises landing, browse filters, global search, exercise page and app shell."""

import content_sync as cs
import pytest

BASE = {"status": "published", "level": "beginner", "category": "strength"}


def ex(slug, name, primary, secondary=(), equipment=(), movement="horizontal_push", **extra):
    return {
        **BASE,
        "slug": slug,
        "name": name,
        "movement": movement,
        "muscles": {"primary": list(primary), "secondary": list(secondary)},
        "equipment": list(equipment),
        **extra,
    }


LIBRARY = [
    ex(
        "floor-press",
        "Floor Press",
        ["chest"],
        ["triceps"],
        summary="Press from the floor.",
        setup=["Lie on the floor."],
        steps=["Press up.", "Lower down."],
        cues=["Elbows at 45."],
        mistakes=["Bouncing."],
        safety=["Go light."],
        regressions=["wall-press"],
        progressions=["bench-press"],
        videos={
            "youtube": "https://www.youtube.com/watch?v=abcdefghijk",
            "tiktok": "https://www.tiktok.com/@nfg/video/123",
        },
    ),
    ex("wall-press", "Wall Press", ["chest"]),
    ex(
        "bench-press",
        "Bench Press",
        ["chest"],
        ["triceps"],
        ["barbell", "bench"],
        level="intermediate",
    ),
    ex("db-row", "DB Row", ["lats"], ["biceps"], ["dumbbell"], movement="horizontal_pull"),
    ex("cable-row", "Cable Row", ["lats"], [], ["cable"], movement="horizontal_pull"),
    ex("draft-press", "Draft Press", ["chest"], status="draft"),
]


@pytest.fixture
def library(nfg_app):
    cs.apply_sync(nfg_app.db, cs.plan_sync(nfg_app.db, LIBRARY))
    return nfg_app.db


# --------------------------------------------------------------------------
# Exercises landing
# --------------------------------------------------------------------------


def test_landing_shows_tiles_without_bloat(client, library):
    html = client.get("/workouts").get_data(as_text=True)
    assert "Muscle groups" in html and "Equipment" in html and "Level" in html
    assert "Bodyweight" in html and "Barbell" in html and "Cables" in html
    assert "Use the library to learn movements" not in html  # old intro copy removed
    assert 'placeholder="Search 5 exercises"' in html


def test_landing_equipment_counts_follow_environment(client, library):
    html = client.get("/workouts?env=home").get_data(as_text=True)
    assert "Barbell" not in html and "Cables" not in html
    assert "Dumbbells" in html


# --------------------------------------------------------------------------
# Browse
# --------------------------------------------------------------------------


def _names(html):
    return [
        n for n in ("Floor Press", "Wall Press", "Bench Press", "DB Row", "Cable Row") if n in html
    ]


@pytest.mark.parametrize(
    "query, expected",
    [
        ("", ["Floor Press", "Wall Press", "Bench Press", "DB Row", "Cable Row"]),
        ("?group=back", ["DB Row", "Cable Row"]),
        ("?eq=bodyweight", ["Floor Press", "Wall Press"]),
        ("?eq=barbell", ["Bench Press"]),
        ("?level=Intermediate", ["Bench Press"]),
        ("?env=home", ["Floor Press", "Wall Press", "DB Row"]),
        ("?q=row", ["DB Row", "Cable Row"]),
        ("?body=Back", ["DB Row", "Cable Row"]),  # legacy label filter still works
        ("?group=wings", ["Floor Press", "Wall Press", "Bench Press", "DB Row", "Cable Row"]),
    ],
)
def test_browse_filters(client, library, query, expected):
    html = client.get("/workouts/browse" + query).get_data(as_text=True)
    assert _names(html) == [n for n in _names(" ".join(expected))]
    assert "Draft Press" not in html


def test_browse_active_filter_chips_remove_one_filter(client, library):
    html = client.get("/workouts/browse?group=chest&eq=bodyweight").get_data(as_text=True)
    assert "Remove filter Chest" in html and "Remove filter Bodyweight" in html
    assert 'href="/workouts/browse?eq=bodyweight"' in html  # removing Chest keeps equipment
    assert '<span class="nfg-count">2</span>' in html


def test_browse_paginates_with_numbers(client, library):
    html = client.get("/workouts/browse?per_page=2").get_data(as_text=True)
    assert 'aria-current="page">1<' in html
    assert "/workouts/browse?page=2&amp;per_page=2" in html
    assert "/workouts/browse?page=3&amp;per_page=2" in html
    last = client.get("/workouts/browse?per_page=2&page=99").get_data(as_text=True)
    assert 'aria-current="page">3<' in last  # out-of-range page clamps to the last page


def test_browse_filter_sheet_present(client, library):
    html = client.get("/workouts/browse").get_data(as_text=True)
    assert 'id="filterSheet"' in html
    for legend in ("Training at", "Muscle group", "Equipment", "Level", "Sort"):
        assert f"<legend>{legend}</legend>" in html


# --------------------------------------------------------------------------
# Global search
# --------------------------------------------------------------------------


def test_search_without_query_shows_search_page(client, library):
    resp = client.get("/search")
    assert resp.status_code == 200
    assert "autofocus" in resp.get_data(as_text=True)


def test_search_finds_exercises_and_programs(client, library):
    html = client.get("/search?q=challenge").get_data(as_text=True)
    assert "8 Week Challenge" in html
    html = client.get("/search?q=press").get_data(as_text=True)
    assert "Floor Press" in html and "Draft Press" not in html


def test_search_programs_skip_internal_tracks(client, library):
    html = client.get("/search?q=beginner").get_data(as_text=True)
    assert "8 Week Challenge — Beginner" not in html


# --------------------------------------------------------------------------
# Exercise page
# --------------------------------------------------------------------------


def test_exercise_page_layout(client, library):
    html = client.get("/workouts/floor-press").get_data(as_text=True)
    assert (
        'nfg-mchip is-primary" style="--group-color: #B4532A;" href="/workouts/mix?g=chest">Chest<'
        in html
    )
    assert "nfg-mchip is-primary" in html and ">Triceps<" in html
    assert 'id="howToSheet"' in html and "nfg-help-btn" in html
    assert "Easier" in html and "Wall Press" in html
    assert "Harder" in html and "Bench Press" in html
    assert "Overview" not in html and "Difficulty tier" not in html


def test_video_links_only_for_safe_platform_urls(client, library):
    html = client.get("/workouts/floor-press").get_data(as_text=True)
    assert 'href="https://www.youtube.com/watch?v=abcdefghijk"' in html
    assert 'href="https://www.tiktok.com/@nfg/video/123"' in html
    assert "is-instagram" not in html
    assert 'rel="noopener noreferrer"' in html


def test_no_howto_sheet_without_instructions(client, library):
    html = client.get("/workouts/wall-press").get_data(as_text=True)
    assert 'id="howToSheet"' not in html and "nfg-help-btn" not in html


def test_more_like_this_excludes_self_and_variations(nfg_app, library):
    with nfg_app.app.test_request_context("/"):
        w = library.workouts.find_one({"slug": "floor-press"})
        related = nfg_app._related_exercises(w, ["floor-press", "wall-press", "bench-press"])
    slugs = [r["slug"] for r in related]
    assert "floor-press" not in slugs and "wall-press" not in slugs and "bench-press" not in slugs


@pytest.mark.parametrize(
    "url, ok",
    [
        ("https://www.youtube.com/watch?v=x", True),
        ("https://youtu.be/x", True),
        ("https://www.instagram.com/reel/x", True),
        ("http://www.youtube.com/watch?v=x", False),
        ("https://evil.example/youtube.com", False),
        ("https://youtube.com.evil.example/x", False),
        ("javascript:alert(1)", False),
    ],
)
def test_safe_video_url(nfg_app, url, ok):
    assert nfg_app._is_safe_video_url(url) is ok


def test_content_rejects_bad_video_links(tmp_path):
    import yaml

    data = ex("bad-video", "Bad Video", ["chest"], videos={"youtube": "https://evil.example/v"})
    (tmp_path / "bad-video.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(cs.ContentError, match="videos.youtube"):
        cs.load_exercises(tmp_path)


# --------------------------------------------------------------------------
# App shell
# --------------------------------------------------------------------------


def test_top_bar_has_global_search_and_icons(client):
    html = client.get("/").get_data(as_text=True)
    assert 'class="nfg-topsearch" action="/search"' in html
    assert 'aria-label="Search"' in html
    assert "bi-house-door-fill" in html  # active Home icon in the bottom nav
    assert "About NFG" in html


def test_brand_logo_only_when_file_exists(client, nfg_app, monkeypatch, tmp_path):
    monkeypatch.setattr(nfg_app, "BRAND_DIR", str(tmp_path))
    assert "nfg-brand-logo" not in client.get("/").get_data(as_text=True)
    (tmp_path / "logo-128.png").write_bytes(b"png")
    (tmp_path / "favicon-32.png").write_bytes(b"png")
    html = client.get("/").get_data(as_text=True)
    assert "nfg-brand-logo" in html and 'rel="icon"' in html


# --------------------------------------------------------------------------
# Session presets (Push / Pull / Lower / Core with focus variants)
# --------------------------------------------------------------------------

SESSION_LIBRARY = [
    ex(
        "pull-up",
        "Pull Up",
        ["lats"],
        ["biceps"],
        ["pullup_bar"],
        movement="vertical_pull",
        mechanics="compound",
    ),
    ex(
        "cable-row-2",
        "Seated Row",
        ["upper_back", "lats"],
        ["biceps"],
        ["cable"],
        movement="horizontal_pull",
        mechanics="compound",
    ),
    ex("curl", "Curl", ["biceps"], [], ["dumbbell"], movement="isolation", mechanics="isolation"),
    ex(
        "chin-up",
        "Chin Up",
        ["biceps", "lats"],
        [],
        ["pullup_bar"],
        movement="vertical_pull",
        mechanics="compound",
    ),
    ex("raise", "Calf Raise", ["calves"], [], [], movement="isolation", mechanics="isolation"),
    ex(
        "squat",
        "Squat",
        ["quads", "glutes"],
        [],
        ["barbell"],
        movement="squat",
        mechanics="compound",
    ),
    ex(
        "rdl",
        "RDL",
        ["hamstrings", "glutes"],
        [],
        ["dumbbell"],
        movement="hinge",
        mechanics="compound",
    ),
]


@pytest.fixture
def sessions(nfg_app):
    cs.apply_sync(nfg_app.db, cs.plan_sync(nfg_app.db, SESSION_LIBRARY))
    return nfg_app


def _session(nfg_app, key, extra=(), env="hybrid"):
    import taxonomy as tx

    with nfg_app.app.test_request_context("/"):
        return nfg_app._session_results(tx.SESSION_PRESETS[key], list(extra), env)


def test_presets_reference_real_taxonomy_keys():
    import taxonomy as tx

    for key, p in tx.SESSION_PRESETS.items():
        assert set(p["muscles"]) <= set(tx.MUSCLES), key
        assert set(p["movements"]) | set(p.get("exclude_movements", [])) <= set(tx.MOVEMENTS), key
        assert set(p.get("groups", [])) <= set(tx.MUSCLE_GROUPS), key


def test_vertical_pull_puts_main_lifts_first_and_skips_rows(sessions):
    results = _session(sessions, "pull-vertical")
    names = [r["name"] for r in results]
    assert "Seated Row" not in names  # horizontal pattern excluded
    mains = [r["name"] for r in results if r["session_role"] == "main"]
    assert set(mains) == {"Pull Up", "Chin Up"}
    assert names.index("Curl") > max(names.index("Pull Up"), names.index("Chin Up"))


def test_horizontal_pull_keeps_rows_and_curls(sessions):
    names = [r["name"] for r in _session(sessions, "pull-horizontal")]
    assert "Seated Row" in names and "Curl" in names
    assert "Pull Up" not in names and "Chin Up" not in names


def test_lower_focus_splits_quad_and_hip_dominant(sessions):
    quad = [r["name"] for r in _session(sessions, "lower-quad")]
    hip = [r["name"] for r in _session(sessions, "lower-hip")]
    assert "Squat" in quad and "RDL" not in quad
    assert "RDL" in hip and "Squat" not in hip
    assert "Calf Raise" in quad and "Calf Raise" in hip  # calves go with any lower day


def test_addon_groups_extend_a_session(sessions):
    plain = [r["name"] for r in _session(sessions, "pull")]
    with_calves = [r["name"] for r in _session(sessions, "pull", ["calves"])]
    assert "Calf Raise" not in plain and "Calf Raise" in with_calves


def test_session_page_shows_focus_options_and_sections(client, sessions):
    html = client.get("/workouts/mix?p=pull-vertical").get_data(as_text=True)
    assert '<h1 class="nfg-page-title">Pull · Vertical pull</h1>' in html
    assert "Full pull" in html and "Horizontal pull" in html
    assert "Main lifts" in html and "Accessories" in html
    assert "/workouts/mix?p=pull-vertical&amp;g=calves" in html  # add-on chip


def test_unknown_preset_falls_back_to_mixer(client, sessions):
    html = client.get("/workouts/mix?p=nope").get_data(as_text=True)
    assert "Build your mix" in html
