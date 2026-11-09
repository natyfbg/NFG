"""Regression tests for the Phase 0 security fixes."""

import io
import re

import pytest
from PIL import Image
from pymongo.errors import ServerSelectionTimeoutError
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash

# --------------------------------------------------------------------------
# Admin credentials
# --------------------------------------------------------------------------


@pytest.fixture
def admin_config(nfg_app, monkeypatch):
    def _set(hash_value="", plaintext="", hosted=False):
        monkeypatch.setattr(nfg_app, "ADMIN_PASSWORD_HASH", hash_value)
        monkeypatch.setattr(nfg_app, "ADMIN_PASSWORD", plaintext)
        monkeypatch.setattr(nfg_app, "IS_HOSTED", hosted)
        return nfg_app._check_admin_credentials

    return _set


def test_hash_is_the_only_credential_when_configured(admin_config):
    check = admin_config(
        hash_value=generate_password_hash("real-strong-password"), plaintext="changeme"
    )
    assert check("admin", "real-strong-password") is True
    # The old behaviour fell through to the plaintext default after a hash mismatch.
    assert check("admin", "changeme") is False


def test_malformed_hash_disables_login(admin_config):
    check = admin_config(hash_value="not-a-real-hash", plaintext="changeme")
    assert check("admin", "changeme") is False


def test_plaintext_never_accepted_when_hosted(admin_config):
    check = admin_config(plaintext="changeme", hosted=True)
    assert check("admin", "changeme") is False


def test_plaintext_works_for_local_development(admin_config):
    check = admin_config(plaintext="local-pass")
    assert check("admin", "local-pass") is True
    assert check("admin", "wrong") is False
    assert check("someone-else", "local-pass") is False


def test_hosted_config_guard(nfg_app):
    problems = nfg_app._hosted_config_problems
    assert problems(False, "dev-secret", "") == []
    assert len(problems(True, "dev-secret", "")) == 2
    assert len(problems(True, "", "scrypt:hash")) == 1
    assert problems(True, "a-real-secret", "scrypt:hash") == []


# --------------------------------------------------------------------------
# Login throttling (shared via Mongo, checked before credentials)
# --------------------------------------------------------------------------


def test_member_login_locks_out_after_repeated_failures(client, make_user):
    make_user(password="password123")
    for _ in range(5):
        resp = client.post("/account/login", data={"identity": "tester", "password": "nope"})
        assert resp.status_code == 200

    # Even the correct password is refused while locked out.
    resp = client.post("/account/login", data={"identity": "tester", "password": "password123"})
    assert resp.status_code == 429


def test_successful_member_login_clears_failures(client, make_user, nfg_app):
    make_user(password="password123")
    client.post("/account/login", data={"identity": "tester", "password": "nope"})
    resp = client.post("/account/login", data={"identity": "tester", "password": "password123"})
    assert resp.status_code == 302
    assert nfg_app.db.login_failures.count_documents({}) == 0


def test_admin_login_locks_out_before_checking_password(client, nfg_app, monkeypatch):
    monkeypatch.setattr(nfg_app, "ADMIN_PASSWORD_HASH", generate_password_hash("right-one"))
    for _ in range(5):
        client.post("/login", data={"username": "admin", "password": "wrong"})
    resp = client.post("/login", data={"username": "admin", "password": "right-one"})
    assert resp.status_code == 429


# --------------------------------------------------------------------------
# Open redirects
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target, safe",
    [
        ("/programs", True),
        ("/workouts/browse?q=chest", True),
        ("//evil.example", False),
        ("/\\evil.example", False),
        ("/\t/evil.example", False),
        ("https://evil.example", False),
        ("javascript:alert(1)", False),
        ("", False),
    ],
)
def test_is_safe_local_path(nfg_app, target, safe):
    assert nfg_app._is_safe_local_path(target) is safe


def test_member_login_ignores_offsite_next(client, make_user):
    make_user()
    resp = client.post(
        "/account/login?next=//evil.example",
        data={"identity": "tester", "password": "password123"},
    )
    assert resp.status_code == 302
    assert "evil.example" not in resp.headers["Location"]


def test_member_login_keeps_local_next(client, make_user):
    make_user()
    resp = client.post(
        "/account/login?next=/programs",
        data={"identity": "tester", "password": "password123"},
    )
    assert resp.headers["Location"].endswith("/programs")


def test_program_favorite_ignores_offsite_next(client):
    resp = client.post("/programs/8-week-challenge/favorite", data={"next": "//evil.example"})
    assert resp.status_code == 302
    assert "evil.example" not in resp.headers["Location"]


# --------------------------------------------------------------------------
# Search and query parsing
# --------------------------------------------------------------------------


def test_search_input_is_literal_not_regex(nfg_app):
    assert nfg_app._search_regex("a(b").pattern == re.escape("a(b")


def test_search_query_is_length_capped(nfg_app):
    assert len(nfg_app._clean_search_query("x" * 500)) == nfg_app.SEARCH_MAX_LEN


@pytest.mark.parametrize("q", ["(", "[", "a+++", ".*" * 50])
def test_regex_metacharacters_do_not_error(client, q):
    assert client.get("/search", query_string={"q": q}).status_code == 200
    assert client.get("/workouts/browse", query_string={"q": q}).status_code == 200


def test_search_matches_literal_punctuation(client, nfg_app):
    nfg_app.db.workouts.insert_one({"name": "Push (Modified)", "slug": "push-modified"})
    resp = client.get("/search", query_string={"q": "Push (Mod"})
    assert b"Push (Modified)" in resp.data


@pytest.mark.parametrize(
    "path", ["/workouts/browse?page=abc", "/workouts/browse?per_page=x", "/search?q=a&page=-5"]
)
def test_bad_pagination_params_do_not_500(client, path):
    assert client.get(path).status_code == 200


# --------------------------------------------------------------------------
# Health, headers, CSRF
# --------------------------------------------------------------------------


def test_healthz_ok_when_mongo_up(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["mongo"] == "up"


def test_healthz_503_when_mongo_down(client, nfg_app, monkeypatch):
    class _DownAdmin:
        def command(self, *_args, **_kwargs):
            raise ServerSelectionTimeoutError("simulated outage")

    class _DownClient:
        admin = _DownAdmin()

    monkeypatch.setattr(nfg_app, "client", _DownClient())
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.get_json()["mongo"] == "down"


def test_security_headers_present(client):
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_bootstrap_cdn_has_sri(client):
    html = client.get("/").get_data(as_text=True)
    assert html.count('integrity="sha384-') == 2


def test_csrf_still_enforced(client, nfg_app):
    nfg_app.app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post("/account/login", data={"identity": "x", "password": "y"})
        assert resp.status_code == 400
    finally:
        nfg_app.app.config["WTF_CSRF_ENABLED"] = False


# --------------------------------------------------------------------------
# Upload validation
# --------------------------------------------------------------------------


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_real_image_is_saved_with_detected_extension(nfg_app):
    # A real PNG uploaded with a .jpg name is saved as .png.
    upload = FileStorage(stream=io.BytesIO(_png_bytes()), filename="photo.jpg")
    with nfg_app.app.test_request_context():
        url = nfg_app._save_one_file(upload)
    assert url and url.endswith(".png")


def test_non_image_with_image_extension_is_rejected(nfg_app):
    upload = FileStorage(stream=io.BytesIO(b"<?php echo 'hi'; ?>"), filename="evil.png")
    with nfg_app.app.test_request_context():
        assert nfg_app._save_one_file(upload) is None


def test_disallowed_extension_is_rejected(nfg_app):
    upload = FileStorage(stream=io.BytesIO(_png_bytes()), filename="image.svg")
    with nfg_app.app.test_request_context():
        assert nfg_app._save_one_file(upload) is None
