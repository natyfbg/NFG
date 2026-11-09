"""Guest progress, favorites and the guest -> member migration."""

import datetime

GUEST_ID = "guest-viewer-123"
TRACK = "8-week-challenge-beginner-home"


def _seed_guest_state(db):
    now = datetime.datetime.utcnow()
    db.program_favorites.insert_one(
        {"viewer_id": GUEST_ID, "program_slug": "8-week-challenge", "created_at": now}
    )
    db.program_day_progress.insert_one(
        {
            "viewer_id": GUEST_ID,
            "track_slug": TRACK,
            "week_number": 1,
            "day_key": "push",
            "completed_at": now,
        }
    )
    db.program_week_progress.insert_one(
        {"viewer_id": GUEST_ID, "track_slug": TRACK, "week_number": 1, "completed_at": now}
    )


def test_new_visitor_gets_guest_cookie(client):
    resp = client.get("/")
    cookie = resp.headers.get("Set-Cookie", "")
    assert "nfg_vid=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_guest_can_favorite_a_program(client, nfg_app):
    client.set_cookie("nfg_vid", GUEST_ID)
    client.post("/programs/8-week-challenge/favorite", data={"next": "/programs"})
    assert nfg_app.db.program_favorites.count_documents({"viewer_id": GUEST_ID}) == 1

    # Posting again toggles it off.
    client.post("/programs/8-week-challenge/favorite", data={"next": "/programs"})
    assert nfg_app.db.program_favorites.count_documents({"viewer_id": GUEST_ID}) == 0


def test_register_migrates_guest_state(client, nfg_app):
    db = nfg_app.db
    _seed_guest_state(db)
    client.set_cookie("nfg_vid", GUEST_ID)

    resp = client.post(
        "/account/register",
        data={
            "username": "newmember",
            "email": "new@example.com",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    assert resp.status_code == 302

    user = db.users.find_one({"username_lower": "newmember"})
    member_key = f"user:{user['_id']}"
    for coll in ("program_favorites", "program_day_progress", "program_week_progress"):
        assert db[coll].count_documents({"viewer_id": GUEST_ID}) == 0, coll
        assert db[coll].count_documents({"viewer_id": member_key}) == 1, coll


def test_login_migrates_guest_state(client, nfg_app, make_user):
    db = nfg_app.db
    user_id = make_user()
    _seed_guest_state(db)
    client.set_cookie("nfg_vid", GUEST_ID)

    client.post("/account/login", data={"identity": "tester", "password": "password123"})

    member_key = f"user:{user_id}"
    assert db.program_day_progress.count_documents({"viewer_id": member_key}) == 1
    assert db.program_day_progress.count_documents({"viewer_id": GUEST_ID}) == 0


def test_continue_plan_points_at_resume_target(nfg_app):
    db = nfg_app.db
    _seed_guest_state(db)
    with nfg_app.app.test_request_context():
        plan = nfg_app._continue_plan_for_owner(GUEST_ID)
    assert plan is not None
    assert plan["week_number"] == 1
    assert "/programs/8-week-challenge/week/1" in plan["url"]
