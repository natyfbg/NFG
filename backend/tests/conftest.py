"""Test harness: runs the real Flask app against an in-memory MongoDB (mongomock).

app.py connects to Mongo and seeds at import time, so the environment and the
MongoClient patch must be in place before `import app` happens. Nothing in the
test suite touches Docker, Render or Atlas.
"""

import os
import sys
import tempfile

import mongomock
import pymongo
import pytest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND_DIR)

for _var in ("RENDER", "MEDIA_ROOT", "ADMIN_PASSWORD", "ADMIN_PASSWORD_HASH", "MONGO_URI_LOCAL"):
    os.environ.pop(_var, None)
os.environ["MONGO_URI"] = "mongodb://localhost:27017/NFG"
os.environ["MONGO_DB"] = "NFG_TEST"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["UPLOAD_FOLDER"] = tempfile.mkdtemp(prefix="nfg-test-uploads-")
# Tests write to the DB between requests; the library snapshot is tested on its own
# (test_library_cache.py) with caching switched back on.
os.environ["LIBRARY_CACHE_SECONDS"] = "0"

pymongo.MongoClient = mongomock.MongoClient

import app as nfg  # noqa: E402

# Collections that tests write to. Seeded catalogue collections (styles,
# programs) are left alone so they match what the app seeds at startup.
_USER_STATE_COLLECTIONS = (
    "users",
    "workouts",
    "program_weeks",
    "program_items",
    "program_favorites",
    "program_day_progress",
    "program_week_progress",
    "login_failures",
    "favorites",
)


@pytest.fixture
def nfg_app():
    nfg.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    nfg._invalidate_library_cache()
    yield nfg
    for name in _USER_STATE_COLLECTIONS:
        nfg.db[name].delete_many({})
    nfg._invalidate_library_cache()


@pytest.fixture
def client(nfg_app):
    return nfg_app.app.test_client()


@pytest.fixture
def make_user(nfg_app):
    from werkzeug.security import generate_password_hash

    def _make(username="tester", password="password123", email=None):
        email = email or f"{username}@example.com"
        res = nfg_app.db.users.insert_one(
            {
                "username": username,
                "username_lower": username.lower(),
                "email": email,
                "email_lower": email.lower(),
                "password_hash": generate_password_hash(password),
                "active": True,
            }
        )
        return res.inserted_id

    return _make
