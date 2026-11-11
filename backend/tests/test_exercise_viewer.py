"""Exercise page viewer (9:16, photos then muscle map), clearing images and demo videos."""

import re

import content_sync as cs
import placeholders
import pytest

BASE = {
    "slug": "push-up",
    "name": "Push-Up",
    "status": "published",
    "level": "beginner",
    "category": "strength",
    "movement": "horizontal_push",
    "muscles": {"primary": ["chest"], "secondary": ["triceps"]},
    "equipment": [],
    "summary": "A push-up.",
    "steps": ["Lower.", "Press."],
    "cues": ["Straight line."],
}


@pytest.fixture
def db(nfg_app):
    return nfg_app.db


def _sync(db, *exercises):
    cs.apply_sync(db, cs.plan_sync(db, list(exercises)))


def _page(client, slug="push-up"):
    resp = client.get(f"/workouts/{slug}")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _slides(html):
    return re.findall(r'<img class="nfg-slide is-(photo|map)" src="([^"]+)"', html)


# --------------------------------------------------------------------------
# images: [] clears photos back to the muscle map
# --------------------------------------------------------------------------


def test_empty_images_list_means_the_generated_placeholder():
    existing = {"images": ["/static/uploads/ez-bar-1.jpg", "/static/uploads/ez-bar-2.jpg"]}
    out = cs.image_fields({**BASE, "images": []}, existing)
    assert out["images"] == ["/static/img/exercises/push-up.svg"]


def test_listed_images_still_win():
    out = cs.image_fields({**BASE, "images": ["/static/img/x.jpg"]}, None)
    assert out["images"] == ["/static/img/x.jpg"]


def test_sync_clears_admin_photos_when_file_says_none(db):
    _sync(db, BASE)
    db.workouts.update_one({"slug": "push-up"}, {"$set": {"images": ["/static/uploads/a.jpg"]}})
    db.workouts.update_one({"slug": "push-up"}, {"$unset": {"updated_at": ""}})
    plan = cs.plan_sync(db, [{**BASE, "images": []}])
    assert [u["slug"] for u in plan["updates"]] == ["push-up"]
    cs.apply_sync(db, plan)
    assert db.workouts.find_one({"slug": "push-up"})["images"] == [
        "/static/img/exercises/push-up.svg"
    ]


def test_push_up_file_clears_the_stand_in_photos():
    exercises, _ = cs.load_exercises()
    push_up = next(e for e in exercises if e["slug"] == "push-up")
    assert push_up["images"] == []


# --------------------------------------------------------------------------
# The viewer
# --------------------------------------------------------------------------


def test_photos_come_first_and_the_muscle_map_last(client, db):
    _sync(db, BASE)
    db.workouts.update_one(
        {"slug": "push-up"},
        {
            "$set": {
                "images": [
                    "/static/uploads/a.jpg",
                    "/static/img/exercises/push-up.svg",
                    "/static/uploads/b.jpg",
                ]
            }
        },
    )
    html = _page(client)
    assert _slides(html) == [
        ("photo", "/static/uploads/a.jpg"),
        ("photo", "/static/uploads/b.jpg"),
        ("map", "/static/img/exercises/push-up.svg"),
    ]
    assert len(re.findall(r'class="nfg-gallery-step[ "]', html)) == 3
    assert "has-steps" in html


def test_single_image_has_no_steps(client, db):
    _sync(db, BASE)
    html = _page(client)
    assert _slides(html) == [("map", "/static/img/exercises/push-up.svg")]
    assert "nfg-gallery-step" not in html and "has-steps" not in html


def test_dead_placeholder_links_are_not_slides(client, db):
    _sync(db, BASE)
    db.workouts.update_one(
        {"slug": "push-up"}, {"$set": {"images": ["https://via.placeholder.com/300"]}}
    )
    assert _slides(_page(client)) == [("map", "/static/img/exercises/push-up.svg")]


def test_exercise_without_a_map_file_gets_the_generic_image(client, db):
    _sync(db, {**BASE, "slug": "no-map-here", "name": "No Map Here"})
    db.workouts.update_one({"slug": "no-map-here"}, {"$set": {"images": []}})
    assert _slides(_page(client, "no-map-here")) == [
        ("map", "/static/img/exercise-placeholder.svg")
    ]


def test_question_mark_is_the_only_how_to_button(client, db):
    _sync(db, BASE)
    html = _page(client)
    assert "nfg-help-btn" in html and 'id="howToSheet"' in html
    assert "nfg-howto-btn" not in html


def test_title_comes_before_the_viewer(client, db):
    _sync(db, BASE)
    html = _page(client)
    assert html.index('class="nfg-ex-title"') < html.index('class="nfg-gallery"')


# --------------------------------------------------------------------------
# Demo videos
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=IODxDxX7oi4",
        "https://youtube.com/watch?pp=abc&v=IODxDxX7oi4",
        "https://m.youtube.com/watch?v=IODxDxX7oi4&t=10s",
        "https://youtu.be/IODxDxX7oi4",
        "https://www.youtube.com/shorts/IODxDxX7oi4",
        "https://www.youtube.com/embed/IODxDxX7oi4",
    ],
)
def test_youtube_id_from_any_link_shape(nfg_app, url):
    assert nfg_app._youtube_id_from_url(url) == "IODxDxX7oi4"


@pytest.mark.parametrize(
    "url",
    ["", "https://www.youtube.com/@channel", "https://vimeo.com/123", "https://youtu.be/short"],
)
def test_youtube_id_rejects_other_links(nfg_app, url):
    assert nfg_app._youtube_id_from_url(url) == ""


def test_youtube_link_feeds_watch_button_and_video_tab(client, db):
    _sync(db, {**BASE, "videos": {"youtube": "https://www.youtube.com/watch?v=IODxDxX7oi4"}})
    html = _page(client)
    assert "Watch on " in html and 'href="https://www.youtube.com/watch?v=IODxDxX7oi4"' in html
    assert 'id="tab-video"' in html
    assert "youtube-nocookie.com/embed/IODxDxX7oi4" in html


def test_stored_youtube_id_wins(nfg_app):
    w = {"youtube_id": "AAAAAAAAAAA", "videos": {"youtube": "https://youtu.be/IODxDxX7oi4"}}
    assert nfg_app._youtube_id(w) == "AAAAAAAAAAA"


# --------------------------------------------------------------------------
# Placeholder art: 9:16 with a 4:5 safe zone
# --------------------------------------------------------------------------


def test_placeholder_is_9_16_and_says_muscles_worked():
    svg = placeholders.render_exercise_svg(BASE)
    assert 'viewBox="0 0 1080 1920"' in svg
    assert "Muscles worked" in svg and "Demo coming soon" not in svg


def test_placeholder_content_sits_in_the_card_crop():
    # Cards crop the centre 4:5 (1080 x 1350) of the 9:16 image.
    assert placeholders.SAFE_BOTTOM - placeholders.SAFE_TOP == 1350
    assert placeholders.MAP_TOP > placeholders.SAFE_TOP
    assert placeholders.MAP_TOP + placeholders.MAP_HEIGHT < placeholders.SAFE_BOTTOM - 100
