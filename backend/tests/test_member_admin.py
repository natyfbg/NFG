"""Admin rights on a normal account (ADMIN_EMAILS): sign in with your email, keep your data."""

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def member_admin(client, make_user, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "ADMIN_EMAILS", frozenset({"coach@example.com"}))
    user_id = make_user(username="coach", email="Coach@Example.com")
    client.post("/account/login", data={"identity": "coach@example.com", "password": "password123"})
    yield client, user_id


def test_listed_email_opens_admin_and_keeps_the_account(member_admin, nfg_app):
    client, user_id = member_admin
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/content-sync").status_code == 200
    # Still a member: the account page shows, not a bounce to admin.
    account = client.get("/account")
    assert (
        account.status_code == 200 and "coach@example.com" in account.get_data(as_text=True).lower()
    )
    # Their data is keyed to the account, as for any member.
    client.post("/train/start", data={"from": "empty"})
    assert nfg_app.db.training_sessions.count_documents({"viewer_id": f"user:{user_id}"}) == 1


def test_member_admin_menu_has_account_admin_and_sign_out(member_admin):
    client, _ = member_admin
    html = client.get("/").get_data(as_text=True)
    assert 'aria-label="Your account"' in html  # top bar keeps the account icon
    assert 'href="/admin"' in html and "Sign out" in html
    assert "/account/logout" in html


def test_member_admin_sign_out(member_admin):
    client, _ = member_admin
    resp = client.post("/account/logout")
    assert resp.status_code == 302
    assert client.get("/admin").status_code in (302, 401)


def test_unlisted_member_still_gets_403(client, make_user, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "ADMIN_EMAILS", frozenset({"coach@example.com"}))
    make_user(username="someone", email="someone@example.com")
    client.post("/account/login", data={"identity": "someone", "password": "password123"})
    assert client.get("/admin").status_code == 403
    html = client.get("/").get_data(as_text=True)
    assert 'href="/admin"' not in html


def test_rights_follow_the_list(member_admin, nfg_app, monkeypatch):
    client, _ = member_admin
    monkeypatch.setattr(nfg_app, "ADMIN_EMAILS", frozenset())
    assert client.get("/admin").status_code == 403  # removed from the list: no admin next request


def test_backup_admin_login_still_works(client, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "ADMIN_PASSWORD_HASH", generate_password_hash("pw"))
    client.post("/login", data={"username": "admin", "password": "pw"})
    assert client.get("/admin").status_code == 200
    assert client.get("/account").status_code == 302  # no account behind it
    html = client.get("/login").get_data(as_text=True)
    assert "Sign in with your email" in html


def test_admin_emails_parsing(nfg_app, monkeypatch):
    parsed = nfg_app._parse_admin_emails(" Coach@Example.com, ,second@example.com ")
    assert parsed == {"coach@example.com", "second@example.com"}
    assert nfg_app._parse_admin_emails(None) == frozenset()
    monkeypatch.setattr(nfg_app, "ADMIN_EMAILS", parsed)
    assert nfg_app._is_admin_email("COACH@example.com ") is True
    assert nfg_app._is_admin_email("") is False and nfg_app._is_admin_email(None) is False
