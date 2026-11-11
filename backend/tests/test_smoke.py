"""Every public page renders, and admin stays behind login."""

import pytest

PUBLIC_PAGES = [
    "/",
    "/about",
    "/workouts",
    "/workouts/all",
    "/workouts/browse",
    "/workouts/browse?sort=favorites",
    "/recipes",
    "/search?q=push",
    "/programs",
    "/programs/8-week-challenge",
    "/programs/8-week-challenge?level=advanced&env=home",
    "/programs/8-week-challenge/week/1?level=beginner&env=home",
    "/account/login",
    "/account/register",
    "/login",
    "/health",
]


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_page_renders(client, path):
    assert client.get(path).status_code == 200


def test_workout_detail_renders(client, nfg_app):
    nfg_app.db.workouts.insert_one(
        {"name": "Push-Up", "slug": "push-up", "body_part": "Chest", "level": "Beginner"}
    )
    resp = client.get("/workouts/push-up")
    assert resp.status_code == 200
    assert b"Push-Up" in resp.data


def test_unknown_workout_is_404(client):
    assert client.get("/workouts/does-not-exist").status_code == 404


@pytest.mark.parametrize(
    "path, target", [("/workouts/styles", "#equipment"), ("/workouts/body-parts", "#muscles")]
)
def test_old_index_pages_redirect_to_exercises_sections(client, path, target):
    resp = client.get(path)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/workouts" + target)


@pytest.mark.parametrize(
    "path, target",
    [
        ("/programs/8-week-challenge/level", "/programs/8-week-challenge"),
        (
            "/programs/8-week-challenge/environment?level=beginner",
            "/programs/8-week-challenge?level=beginner",
        ),
        (
            "/programs/8-week-challenge/weeks?level=beginner&env=home",
            "/programs/8-week-challenge?level=beginner&env=home",
        ),
        (
            "/programs/8-week-challenge-intermediate-gym",
            "/programs/8-week-challenge?level=intermediate&env=gym",
        ),
    ],
)
def test_old_program_steps_redirect_to_the_program_page(client, path, target):
    resp = client.get(path)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(target)


@pytest.mark.parametrize("path", ["/admin", "/admin/workouts/new", "/admin/programs"])
def test_admin_requires_login(client, path):
    resp = client.get(path)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_member_cannot_reach_admin(client, make_user):
    make_user()
    client.post("/account/login", data={"identity": "tester", "password": "password123"})
    assert client.get("/admin").status_code == 403
