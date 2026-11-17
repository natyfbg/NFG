"""Qiltsim launch: email (Resend), password reset, canonical domain, rename, SEO files."""

import datetime
import json
import urllib.error

import mailer
import pytest
from werkzeug.security import check_password_hash

# --------------------------------------------------------------------------
# mailer.py
# --------------------------------------------------------------------------


def test_mailer_without_key_logs_instead_of_sending(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    calls = []
    assert mailer.configured() is False
    assert mailer.send("a@example.com", "Hi", "Body", post=calls.append) == "logged"
    assert calls == []


def test_mailer_posts_to_resend_with_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.delenv("MAIL_FROM", raising=False)
    seen = {}

    def fake_post(req):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return {"id": "msg_123"}

    assert mailer.send("a@example.com", "Hi", "Body", "<p>Body</p>", post=fake_post) == "msg_123"
    assert seen["url"] == mailer.RESEND_URL
    assert seen["auth"] == "Bearer re_test_key"
    assert seen["body"] == {
        "from": "Qiltsim <hello@qiltsim.com>",
        "to": ["a@example.com"],
        "subject": "Hi",
        "text": "Body",
        "html": "<p>Body</p>",
    }


def test_mailer_sender_can_be_overridden(monkeypatch):
    monkeypatch.setenv("MAIL_FROM", "Qiltsim <noreply@qiltsim.com>")
    assert mailer.sender() == "Qiltsim <noreply@qiltsim.com>"


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError(mailer.RESEND_URL, 403, "Forbidden", {}, None),
        urllib.error.URLError("offline"),
        TimeoutError(),
    ],
)
def test_mailer_errors_never_include_the_key(monkeypatch, error):
    monkeypatch.setenv("RESEND_API_KEY", "re_secret_value")

    def failing_post(req):
        raise error

    with pytest.raises(mailer.MailError) as exc:
        mailer.send("a@example.com", "Hi", "Body", post=failing_post)
    assert "re_secret_value" not in str(exc.value)


# --------------------------------------------------------------------------
# Canonical domain
# --------------------------------------------------------------------------


@pytest.fixture
def canonical(nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "CANONICAL_HOST", "qiltsim.com")
    return nfg_app


@pytest.mark.parametrize("host", ["www.qiltsim.com", "nfg-app.onrender.com"])
def test_other_hosts_redirect_to_the_canonical_domain(client, canonical, host):
    resp = client.get("/workouts?group=chest", base_url=f"https://{host}")
    assert resp.status_code == 301
    assert resp.headers["Location"] == "https://qiltsim.com/workouts?group=chest"


def test_canonical_host_health_check_and_posts_are_not_redirected(client, canonical):
    assert client.get("/about", base_url="https://qiltsim.com").status_code == 200
    assert client.get("/healthz", base_url="https://nfg-app.onrender.com").status_code == 200
    resp = client.post("/account/login", base_url="https://nfg-app.onrender.com", data={})
    assert resp.status_code != 301


def test_no_redirect_until_canonical_host_is_set(client, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "CANONICAL_HOST", "")
    assert client.get("/about", base_url="https://nfg-app.onrender.com").status_code == 200


def test_canonical_link_only_with_canonical_host(client, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "CANONICAL_HOST", "")
    assert 'rel="canonical"' not in client.get("/about").get_data(as_text=True)
    monkeypatch.setattr(nfg_app, "CANONICAL_HOST", "qiltsim.com")
    html = client.get("/about", base_url="https://qiltsim.com").get_data(as_text=True)
    assert '<link rel="canonical" href="https://qiltsim.com/about">' in html
    assert '<meta property="og:url" content="https://qiltsim.com/about">' in html


def test_hosted_links_ignore_a_forged_host_header(nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "CANONICAL_HOST", "")
    monkeypatch.setattr(nfg_app, "IS_HOSTED", True)
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "nfg-app.onrender.com")
    with nfg_app.app.test_request_context("/", base_url="https://evil.example"):
        url = nfg_app._public_url("about_page")
    assert url == "https://nfg-app.onrender.com/about"


# --------------------------------------------------------------------------
# Rename and meta tags
# --------------------------------------------------------------------------


def test_pages_use_the_new_name(client):
    html = client.get("/").get_data(as_text=True)
    assert "<title>Qiltsim – Train today</title>" in html
    assert 'class="nfg-brand-word">QILTSIM<' in html
    assert "NFG" not in html


def test_meta_description_and_open_graph(client, nfg_app):
    html = client.get("/").get_data(as_text=True)
    assert '<meta name="description" content="Qiltsim helps you decide' in html
    assert '<meta property="og:site_name" content="Qiltsim">' in html
    assert '<meta property="og:title" content="Qiltsim – Train today">' in html


def test_exercise_page_has_its_own_description(client, nfg_app):
    nfg_app.db.workouts.insert_one(
        {"slug": "meta-squat", "name": "Meta Squat", "publish_status": "published"}
    )
    html = client.get("/workouts/meta-squat").get_data(as_text=True)
    assert 'content="How to do the Meta Squat: step-by-step form' in html


# --------------------------------------------------------------------------
# robots.txt and sitemap.xml
# --------------------------------------------------------------------------


def test_robots_txt(client, canonical):
    resp = client.get("/robots.txt", base_url="https://qiltsim.com")
    text = resp.get_data(as_text=True)
    assert resp.status_code == 200 and resp.mimetype == "text/plain"
    for path in ("/admin", "/account", "/search"):
        assert f"Disallow: {path}\n" in text
    assert "Sitemap: https://qiltsim.com/sitemap.xml" in text


def test_sitemap_lists_public_pages_only(client, canonical):
    canonical.db.workouts.insert_many(
        [
            {"slug": "map-squat", "name": "Map Squat", "publish_status": "published"},
            {"slug": "map-draft", "name": "Map Draft", "publish_status": "draft"},
        ]
    )
    resp = client.get("/sitemap.xml", base_url="https://qiltsim.com")
    xml = resp.get_data(as_text=True)
    assert resp.status_code == 200 and resp.mimetype == "application/xml"
    assert "<loc>https://qiltsim.com/</loc>" in xml
    assert "<loc>https://qiltsim.com/about</loc>" in xml
    assert "<loc>https://qiltsim.com/workouts/map-squat</loc>" in xml
    assert "map-draft" not in xml
    assert "https://qiltsim.com/programs/" in xml
    assert "/account" not in xml and "/admin" not in xml


# --------------------------------------------------------------------------
# Password reset
# --------------------------------------------------------------------------


@pytest.fixture
def outbox(nfg_app, monkeypatch):
    sent = []
    monkeypatch.setattr(
        nfg_app,
        "_deliver_email",
        lambda to, subject, text, html=None: sent.append(
            {"to": to, "subject": subject, "text": text, "html": html}
        ),
    )
    return sent


def _reset_path(email):
    line = next(ln for ln in email["text"].splitlines() if "/account/reset/" in ln)
    return line[line.index("/account/reset/") :].strip()


def _sign_in(client, identity, password):
    return client.post("/account/login", data={"identity": identity, "password": password})


def test_login_page_links_to_reset(client):
    html = client.get("/account/login").get_data(as_text=True)
    assert 'href="/account/forgot"' in html
    assert 'autocomplete="current-password"' in html


def test_reset_is_off_on_a_hosted_site_without_a_key(client, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "MAIL_ENABLED", False)
    assert client.get("/account/forgot").status_code == 404
    assert client.get("/account/reset/anything").status_code == 404
    assert "/account/forgot" not in client.get("/account/login").get_data(as_text=True)


def test_forgot_sends_one_link_and_says_the_same_for_unknown_emails(client, make_user, outbox):
    make_user("lifter", email="Lifter@Example.com")
    known = client.post("/account/forgot", data={"email": "lifter@example.com"})
    unknown = client.post("/account/forgot", data={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert "Check your email" in known.get_data(as_text=True)
    assert "Check your email" in unknown.get_data(as_text=True)
    assert len(outbox) == 1
    email = outbox[0]
    assert email["to"] == "Lifter@Example.com"
    assert email["subject"] == "Reset your Qiltsim password"
    assert "Hi lifter," in email["text"] and "http://localhost/account/reset/" in email["text"]
    assert "Choose a new password" in email["html"]


def test_forgot_rejects_a_blank_email(client, outbox):
    resp = client.post("/account/forgot", data={"email": ""})
    assert "Enter the email you signed up with." in resp.get_data(as_text=True)
    assert outbox == []


def test_only_the_token_hash_is_stored(client, nfg_app, make_user, outbox):
    make_user("lifter")
    client.post("/account/forgot", data={"email": "lifter@example.com"})
    token = _reset_path(outbox[0]).rsplit("/", 1)[1]
    rec = nfg_app.db.password_resets.find_one()
    assert rec["token_hash"] != token and token not in json.dumps(rec, default=str)
    assert rec["expires_at"] - rec["created_at"] == datetime.timedelta(hours=1)


def test_reset_emails_are_limited_per_account(client, make_user, outbox, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "_allowed_login_attempt", lambda key: True)
    make_user("lifter")
    for _ in range(5):
        client.post("/account/forgot", data={"email": "lifter@example.com"})
    assert len(outbox) == nfg_app.RESET_PER_ACCOUNT_PER_HOUR


def test_forgot_is_throttled_per_ip(client, nfg_app, outbox):
    for _ in range(nfg_app.LOGIN_FAIL_LIMIT):
        client.post("/account/forgot", data={"email": "nobody@example.com"})
    assert client.post("/account/forgot", data={"email": "x@example.com"}).status_code == 429


def test_reset_changes_the_password_and_signs_in(client, nfg_app, make_user, outbox):
    user_id = make_user("lifter", password="old-password")
    client.post("/account/forgot", data={"email": "lifter@example.com"})
    path = _reset_path(outbox[0])

    page = client.get(path)
    assert page.status_code == 200
    assert "Choose a new password" in page.get_data(as_text=True)
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["Referrer-Policy"] == "no-referrer"

    resp = client.post(path, data={"password": "new-password", "confirm_password": "new-password"})
    assert resp.status_code == 302
    doc = nfg_app.db.users.find_one({"_id": user_id})
    assert check_password_hash(doc["password_hash"], "new-password")
    assert client.get("/account").status_code == 200  # signed in

    # The link works once.
    assert "This link has expired" in client.get(path).get_data(as_text=True)


def test_reset_validates_the_new_password(client, make_user, outbox):
    make_user("lifter")
    client.post("/account/forgot", data={"email": "lifter@example.com"})
    path = _reset_path(outbox[0])
    short = client.post(path, data={"password": "short", "confirm_password": "short"})
    assert "Use at least 8 characters." in short.get_data(as_text=True)
    mismatch = client.post(path, data={"password": "long-enough", "confirm_password": "different"})
    assert "The two passwords don&#39;t match." in mismatch.get_data(as_text=True)


def test_expired_and_unknown_links_say_so(client, nfg_app, make_user, outbox):
    make_user("lifter")
    client.post("/account/forgot", data={"email": "lifter@example.com"})
    path = _reset_path(outbox[0])
    nfg_app.db.password_resets.update_many(
        {}, {"$set": {"expires_at": datetime.datetime.utcnow() - datetime.timedelta(seconds=1)}}
    )
    assert "This link has expired" in client.get(path).get_data(as_text=True)
    assert "This link has expired" in client.get("/account/reset/not-a-token").get_data(
        as_text=True
    )


def test_using_one_link_cancels_the_others(client, nfg_app, make_user, outbox, monkeypatch):
    monkeypatch.setattr(nfg_app, "_allowed_login_attempt", lambda key: True)
    make_user("lifter")
    client.post("/account/forgot", data={"email": "lifter@example.com"})
    client.post("/account/forgot", data={"email": "lifter@example.com"})
    first, second = _reset_path(outbox[0]), _reset_path(outbox[1])
    client.post(second, data={"password": "new-password", "confirm_password": "new-password"})
    assert "This link has expired" in client.get(first).get_data(as_text=True)


def test_reset_signs_out_other_sessions(nfg_app, make_user, outbox):
    make_user("lifter", password="old-password")
    other_device = nfg_app.app.test_client()
    assert _sign_in(other_device, "lifter", "old-password").status_code == 302
    assert other_device.get("/account").status_code == 200

    me = nfg_app.app.test_client()
    me.post("/account/forgot", data={"email": "lifter@example.com"})
    me.post(
        _reset_path(outbox[0]),
        data={"password": "new-password", "confirm_password": "new-password"},
    )

    resp = other_device.get("/account")
    assert resp.status_code == 302 and "/account/login" in resp.headers["Location"]
    # Signing in again with the new password works and sticks.
    assert _sign_in(other_device, "lifter", "new-password").status_code == 302
    assert other_device.get("/account").status_code == 200


# --------------------------------------------------------------------------
# Phase 1 foundation: theme, Home copy, swipe-back targets
# --------------------------------------------------------------------------


def test_theme_follows_the_cookie(client):
    html = client.get("/").get_data(as_text=True)
    assert '<html lang="en">' in html  # no cookie: the inline script decides
    client.set_cookie("nfg_theme", "dark")
    html = client.get("/").get_data(as_text=True)
    assert 'data-theme="dark" data-bs-theme="dark"' in html
    assert 'name="theme" value="dark" class="is-on"' in html


def test_theme_route_sets_and_clears_the_cookie(client):
    resp = client.post("/theme", data={"theme": "dark", "next": "/about"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/about")
    assert "nfg_theme=dark" in resp.headers.get("Set-Cookie", "")
    resp = client.post("/theme", data={"theme": "system"})
    assert "nfg_theme=;" in resp.headers.get("Set-Cookie", "")
    assert client.post("/theme", data={"theme": "neon"}).status_code == 400


def test_home_greeting_and_sessions(client, make_user):
    html = client.get("/").get_data(as_text=True)
    assert "Hi there" in html and "What are we training today?" in html
    assert "Coach&#39;s Plans" in html
    assert "Full body" not in html.split("Or mix muscles")[0]  # no Full body session card
    make_user("nate", password="password123")
    client.post("/account/login", data={"identity": "nate", "password": "password123"})
    assert "Hi, nate" in client.get("/").get_data(as_text=True)


def test_sub_pages_declare_a_back_target(client, nfg_app):
    nfg_app.db.workouts.insert_one(
        {"slug": "back-squat-x", "name": "X", "publish_status": "published"}
    )
    assert 'data-back-url="/workouts"' in client.get("/workouts/back-squat-x").get_data(
        as_text=True
    )
    assert 'data-back-url="/programs"' in client.get("/programs/8-week-challenge").get_data(
        as_text=True
    )
    assert 'data-back-url=""' in client.get("/").get_data(as_text=True)
    assert "a.nfg-back" in client.get("/about").get_data(as_text=True)  # swipe-back script present


def test_text_responses_are_compressed_and_images_cached(client, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "DEV_RELOAD", False)
    resp = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("Content-Encoding") == "gzip"
    resp = client.get("/static/img/exercise-placeholder.svg", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"].startswith("public, max-age=86400")
    assert resp.headers.get("Content-Encoding") == "gzip"
