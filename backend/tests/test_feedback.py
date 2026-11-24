"""Feedback: the form, what's saved, the email, spam guards, Admin → Feedback, privacy."""

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def sent(nfg_app, monkeypatch):
    """Capture feedback emails (sent inline, as if Resend were set up)."""
    import feedback
    import mailer

    calls = []
    monkeypatch.setenv("FEEDBACK_EMAIL", "owner@example.com")
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(
        mailer,
        "send",
        lambda to, subject, text, html=None, reply_to=None: calls.append(
            (to, subject, text, reply_to)
        ),
    )
    monkeypatch.setattr(feedback.mailer, "send", mailer.send)
    nfg_app.app.config["FEEDBACK_INLINE"] = True
    yield calls
    nfg_app.app.config.pop("FEEDBACK_INLINE", None)


def _post(client, **data):
    form = {
        "message": "Love the rest timer. Wish it beeped on iPhone.",
        "back": "/train",
        "app": "installed",
    }
    form.update(data)
    return client.post(
        "/feedback",
        data=form,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
            )
        },
    )


def test_form_and_menu_link(client):
    html = client.get("/feedback?back=/programs").get_data(as_text=True)
    assert "How&#39;s Qiltsim working for you?" in html or "How's Qiltsim working for you?" in html
    assert 'name="rating"' in html and 'name="topic"' in html and 'name="message"' in html
    assert 'name="website"' in html  # the honeypot
    assert 'name="back" value="/programs"' in html
    home = client.get("/").get_data(as_text=True)
    assert "Send feedback" in home and 'href="/feedback?back=/"' in home
    assert '<a href="/feedback">Feedback</a>' in home  # footer


def test_feedback_is_saved_and_emailed(client, nfg_app, sent):
    resp = _post(
        client, rating="4", topic="workouts", name="Sam", email="sam@example.com", may_share="1"
    )
    assert resp.status_code == 302 and "sent=1" in resp.headers["Location"]
    doc = nfg_app.db.feedback.find_one({})
    assert doc["rating"] == 4 and doc["topic"] == "workouts" and doc["may_share"] is True
    assert doc["name"] == "Sam" and doc["email"] == "sam@example.com"
    assert doc["page"] == "/train" and doc["app"] == "installed"
    assert doc["device"] == "iPhone · Safari"
    assert doc["viewer_id"] and doc["user_id"] is None and doc["handled"] is False
    assert "ip_key" in doc and "ip" not in doc  # only a salted hash of the address
    assert len(sent) == 1
    to, subject, text, reply_to = sent[0]
    assert to == "owner@example.com" and reply_to == "sam@example.com"
    assert "★★★★☆" in subject and "Workouts" in subject
    assert "Love the rest timer" in text and "/admin/feedback" in text
    thanks = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Thanks for the feedback" in thanks and 'href="/train"' in thanks


def test_only_a_message_is_needed(client, nfg_app, sent):
    assert _post(client, message="Great app", back="", app="").status_code == 302
    doc = nfg_app.db.feedback.find_one({})
    assert doc["rating"] is None and doc["topic"] == "" and doc["email"] == ""
    assert sent[0][3] is None  # no reply-to without an email


def test_bad_input_is_explained_and_kept(client, nfg_app):
    resp = _post(client, message="", email="not-an-email", name="Sam")
    assert resp.status_code == 400
    html = resp.get_data(as_text=True)
    assert "Write a few words" in html and "email doesn" in html
    assert 'value="Sam"' in html  # what they typed comes back
    assert nfg_app.db.feedback.count_documents({}) == 0


def test_bot_filled_honeypot_keeps_nothing(client, nfg_app, sent):
    resp = _post(client, website="http://spam.example")
    assert resp.status_code == 302 and "sent=1" in resp.headers["Location"]
    assert nfg_app.db.feedback.count_documents({}) == 0 and not sent


def test_five_an_hour_from_one_place(client, nfg_app, sent):
    for i in range(5):
        assert _post(client, message=f"note {i}").status_code == 302
    resp = _post(client, message="one more")
    assert resp.status_code == 400 and "Try again in an hour" in resp.get_data(as_text=True)
    assert nfg_app.db.feedback.count_documents({}) == 5


def test_unsafe_back_link_is_dropped(client, nfg_app):
    _post(client, back="//evil.example/x")
    assert nfg_app.db.feedback.find_one({})["page"] == ""


def test_member_prefill_and_account_link(client, make_user, nfg_app, sent):
    user_id = make_user()
    client.post("/account/login", data={"identity": "tester", "password": "password123"})
    html = client.get("/feedback").get_data(as_text=True)
    assert 'value="tester"' in html and 'value="tester@example.com"' in html
    _post(client, name="tester", email="tester@example.com")
    doc = nfg_app.db.feedback.find_one({})
    assert doc["user_id"] == user_id and doc["viewer_id"] == f"user:{user_id}"


def test_guest_feedback_moves_to_the_account_and_is_in_export(client, make_user, nfg_app, sent):
    _post(client)
    user_id = make_user()
    client.post("/account/login", data={"identity": "tester", "password": "password123"})
    assert nfg_app.db.feedback.find_one({})["viewer_id"] == f"user:{user_id}"
    export = client.get("/account/export").get_json()
    assert export["feedback"] and export["feedback"][0]["message"].startswith("Love the rest timer")


def test_admin_list_and_mark_handled(client, nfg_app, monkeypatch, sent):
    _post(client, rating="5", topic="idea", message="Add a progress page")
    _post(client, rating="3", message="Filters took me a second")
    monkeypatch.setattr(nfg_app, "ADMIN_PASSWORD_HASH", generate_password_hash("pw"))
    client.post("/login", data={"username": "admin", "password": "pw"})
    html = client.get("/admin/feedback").get_data(as_text=True)
    assert "Add a progress page" in html and "Filters took me a second" in html
    assert "average <b>4.0</b>" in html and "New · 2" in html
    assert "/feedback" in html  # the link to send people
    fid = str(nfg_app.db.feedback.find_one({"rating": 5})["_id"])
    client.post("/admin/feedback", data={"id": fid, "action": "handled"})
    html = client.get("/admin/feedback").get_data(as_text=True)
    assert "Add a progress page" not in html and "New · 1" in html
    assert "Add a progress page" in client.get("/admin/feedback?show=all").get_data(as_text=True)


def test_admin_feedback_is_admin_only(client):
    resp = client.get("/admin/feedback")
    assert resp.status_code in (302, 401)


def test_device_labels():
    import feedback

    assert (
        feedback.device_label("Mozilla/5.0 (Linux; Android 14) Chrome/120.0 Mobile Safari/537.36")
        == "Android · Chrome"
    )
    assert (
        feedback.device_label("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36 Edg/120")
        == "Windows · Edge"
    )
    assert feedback.device_label("") == "Other · Browser"
