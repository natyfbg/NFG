# app.py
from __future__ import annotations

import datetime
import logging
import os
import re
import time
import uuid
from collections import deque
from logging.handlers import RotatingFileHandler
from time import perf_counter
from typing import Dict, List, Optional, Any

from bson.objectid import ObjectId
from bson.regex import Regex
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    render_template_string,
    request,
    send_from_directory,
    session,
    url_for,
    jsonify,
)
from flask_login import LoginManager, UserMixin, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from jinja2 import TemplateNotFound
from pymongo import ASCENDING, MongoClient
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

# -----------------------------------------------------------------------------
# Config / DB
# -----------------------------------------------------------------------------
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATHS = [
    os.path.join(APP_ROOT, ".env"),
    os.path.join(os.path.dirname(APP_ROOT), ".env"),
]

for env_path in ENV_PATHS:
    if os.path.exists(env_path):
        load_dotenv(env_path, override=False)

MONGO_URI = os.environ.get("MONGO_URI") or "mongodb://localhost:27017/NFG"
MONGO_DB = os.environ.get("MONGO_DB")  # optional override
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")

# Upload/media configuration (Render Disk ready)
MEDIA_ROOT = os.getenv("MEDIA_ROOT", "").strip()
MEDIA_URL = (os.getenv("MEDIA_URL") or "/media/").strip() or "/media/"
UPLOAD_FOLDER_LEGACY = os.getenv("UPLOAD_FOLDER", "static/uploads").strip()

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Session progress key (cookie)
PROGRESS_SESSION_KEY = "program_progress_v1"

# Safer cookies in hosted envs (Render sets RENDER=true)
if os.getenv("RENDER"):
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

client = MongoClient(MONGO_URI)


def _resolve_db():
    """Pick DB name from override, URI default, or fallback to NFG."""
    if MONGO_DB:
        return client[MONGO_DB]

    uri_tail = MONGO_URI.split("://", 1)[-1]
    if "/" in uri_tail:
        try:
            return client.get_default_database()
        except Exception:
            return client["NFG"]
    return client["NFG"]


db = _resolve_db()

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG_DIR = os.path.join(app.root_path, "instance", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_path = os.path.join(LOG_DIR, "app.log")

file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3)
file_handler.setFormatter(
    logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
)
file_handler.setLevel(logging.INFO)

app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info("App startup")
app.logger.info("Using Mongo at: %s (db=%s)", MONGO_URI, db.name)

# -----------------------------------------------------------------------------
# Helpers (general)
# -----------------------------------------------------------------------------
def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _split_list(text: str) -> List[str]:
    if not text:
        return []
    return [p.strip() for p in re.split(r"[\n,]+", text) if p.strip()]


_YT_PAT = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/))?([A-Za-z0-9_-]{11})"
)


def _extract_youtube_id(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    m = _YT_PAT.search(val.strip())
    return m.group(1) if m else val.strip()


# -----------------------------------------------------------------------------
# Upload helpers
# -----------------------------------------------------------------------------
def _abs_upload_root() -> str:
    """
    Resolve the absolute folder where we write files.
    - If MEDIA_ROOT is set and absolute, use it directly.
    - If MEDIA_ROOT is set and relative, place it under app.root_path.
    - Else fall back to legacy UPLOAD_FOLDER_LEGACY (relative to app).
    """
    base = MEDIA_ROOT if MEDIA_ROOT else UPLOAD_FOLDER_LEGACY
    return base if os.path.isabs(base) else os.path.join(app.root_path, base)


def _public_base_url() -> str:
    """
    Base URL prefix for serving files.
    - If MEDIA_ROOT is used, serve at MEDIA_URL (defaults to /media/).
    - Else serve under /static/uploads/...
    """
    if MEDIA_ROOT:
        return MEDIA_URL if MEDIA_URL.endswith("/") else MEDIA_URL + "/"
    path = "/" + UPLOAD_FOLDER_LEGACY.strip("/")
    return path if path.endswith("/") else path + "/"


UPLOAD_ROOT_ABS = _abs_upload_root()
PUBLIC_BASE = _public_base_url()
os.makedirs(UPLOAD_ROOT_ABS, exist_ok=True)

# keep for backwards compat / easy debugging
app.config["UPLOAD_ROOT_ABS"] = UPLOAD_ROOT_ABS
app.config["PUBLIC_UPLOAD_BASE"] = PUBLIC_BASE

app.logger.info("Uploads: saving to %s ; public at %s", UPLOAD_ROOT_ABS, PUBLIC_BASE)

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB
ALLOWED_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def _allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTS


def _save_one_file(file_storage) -> Optional[str]:
    """Save a single FileStorage and return its public URL path."""
    if not file_storage or not getattr(file_storage, "filename", ""):
        return None
    if not _allowed_image(file_storage.filename):
        return None

    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    day = datetime.datetime.utcnow().strftime("%Y%m%d")
    folder_abs = os.path.join(UPLOAD_ROOT_ABS, day)
    os.makedirs(folder_abs, exist_ok=True)

    fname = f"{uuid.uuid4().hex}.{ext}"
    abs_path = os.path.join(folder_abs, secure_filename(fname))
    file_storage.save(abs_path)

    # Public URL (normalize double slashes)
    url = f"{PUBLIC_BASE}{day}/{fname}"
    while "//" in url:
        url = url.replace("//", "/")
    return url


def _collect_ordered_images_from_form(req) -> List[str]:
    ordered: List[str] = []
    for i in range(1, 9):
        up = req.files.get(f"img{i}_file") or req.files.get(f"image_file_{i}")
        if up and getattr(up, "filename", ""):
            saved = _save_one_file(up)
            if saved:
                ordered.append(saved)
                continue

        url = (req.form.get(f"img{i}_url") or req.form.get(f"image_url_{i}") or "").strip()
        if url:
            ordered.append(url)

    if not ordered:
        legacy = _split_list(req.form.get("images", ""))
        ordered = legacy
    return ordered


def _collect_muscle_image_from_form(req) -> Optional[str]:
    up = req.files.get("muscle_image_file")
    if up and getattr(up, "filename", ""):
        saved = _save_one_file(up)
        if saved:
            return saved
    url = (req.form.get("muscle_image_url") or req.form.get("muscle_image") or "").strip()
    return url or None


# -----------------------------------------------------------------------------
# Request timing / caching logs
# -----------------------------------------------------------------------------
@app.before_request
def _start_timer():
    g._t0 = perf_counter()


@app.after_request
def _log_request(resp):
    try:
        dt = (perf_counter() - getattr(g, "_t0", perf_counter())) * 1000.0

        # Long caching for media routes
        if MEDIA_ROOT and request.path.startswith(MEDIA_URL.rstrip("/") + "/"):
            resp.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")

        app.logger.info(
            "REQ %s %s %s %s %.1fms UA=%s",
            request.method,
            request.path,
            resp.status_code,
            request.headers.get("X-Forwarded-For", request.remote_addr),
            dt,
            request.headers.get("User-Agent", "")[:120],
        )
    except Exception:
        pass
    return resp


csrf = CSRFProtect(app)

# -----------------------------------------------------------------------------
# Auth (single admin)
# -----------------------------------------------------------------------------
ADMIN_USERNAME = (os.getenv("ADMIN_USERNAME") or "Admin").strip() or "Admin"
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "").strip()
ADMIN_PASSWORD_HASH = (os.getenv("ADMIN_PASSWORD_HASH") or "").strip()
ADMIN_USER_ID = "admin"

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to access Admin."
login_manager.login_message_category = "warning"


class User(UserMixin):
    def __init__(self, user_id: str):
        self.id = user_id


@login_manager.user_loader
def load_user(user_id):
    if user_id == ADMIN_USER_ID:
        return User(ADMIN_USER_ID)
    return None


def _check_admin_credentials(username: str, password: str) -> bool:
    normalized_username = (username or "").strip().casefold()
    configured_username = ADMIN_USERNAME.casefold()
    if normalized_username != configured_username:
        return False
    if ADMIN_PASSWORD_HASH:
        try:
            return check_password_hash(ADMIN_PASSWORD_HASH, password or "")
        except (TypeError, ValueError) as exc:
            app.logger.warning("ADMIN_PASSWORD_HASH is invalid: %s", exc)
            return False
    if ADMIN_PASSWORD:
        return password == ADMIN_PASSWORD
    app.logger.warning("Admin login attempted without ADMIN_PASSWORD_HASH or ADMIN_PASSWORD configured.")
    return False


FAILED_LOGINS: Dict[str, deque] = {}


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "unknown")


def _allowed_login_attempt(ip: str, limit: int = 5, window_sec: int = 900) -> bool:
    now = time.time()
    dq = FAILED_LOGINS.get(ip)
    if dq is None:
        return True
    while dq and now - dq[0] > window_sec:
        dq.popleft()
    return len(dq) < limit


def _record_failed_login(ip: str) -> None:
    FAILED_LOGINS.setdefault(ip, deque()).append(time.time())


def _clear_failed_logins(ip: str) -> None:
    FAILED_LOGINS.pop(ip, None)


# -----------------------------------------------------------------------------
# Canonical lists (static)
# -----------------------------------------------------------------------------
WORKOUT_LEVELS = ["Beginner", "Intermediate", "Advanced"]

DEFAULT_WORKOUT_STYLES = [
    "BodyWeight",
    "Barbell",
    "Dumbbell",
    "Kettlebell",
    "Resistance Bands",
    "Machines",
    "Calisthenics",
    "Cardio/Endurance",
    "Plyometric/Explosive",
    "CrossFit/Functional",
    "Yoga/Mobility",
]

BODY_PARTS_MASTER = [
    "Chest",
    "Back",
    "Lats",
    "Shoulders",
    "Arms",
    "Biceps",
    "Triceps",
    "Forearms",
    "Core",
    "Abs",
    "Obliques",
    "Lower Back",
    "Upper Back",
    "Legs",
    "Quads",
    "Hamstrings",
    "Glutes",
    "Calves",
    "Hips",
    "Full Body",
    "Neck",
]

FEATURED_BODY_PARTS = ["Chest", "Back", "Legs"]
FEATURED_STYLES = ["BodyWeight", "Barbell", "Machines"]

# -----------------------------------------------------------------------------
# Program helpers (dynamic Hub -> Tracks)
# -----------------------------------------------------------------------------
DEFAULT_LEVELS = ["beginner", "intermediate", "advanced"]
DEFAULT_ENVS = ["home", "gym", "hybrid"]
PROGRAM_SPLIT_TYPES = ["push", "pull", "legs", "upper", "lower", "core", "cardio"]


def _norm_choice(val: Optional[str]) -> str:
    return (val or "").strip().lower()


def _infer_env_from_slug(slug: str) -> Optional[str]:
    s = (slug or "").lower()
    for env in DEFAULT_ENVS:
        if s.endswith(f"-{env}") or f"-{env}-" in s:
            return env
    return None


def _week_count_from_duration_label(duration_label: Optional[str]) -> int:
    if not duration_label:
        return 8
    m = re.search(r"(\d+)", duration_label)
    if not m:
        return 8
    n = int(m.group(1))
    return max(1, min(n, 52))


def _get_hub_or_404(hub_slug: str) -> dict:
    hub = db.programs.find_one({"slug": hub_slug, "active": {"$ne": False}})
    if not hub:
        abort(404)
    if hub.get("kind") and hub.get("kind") != "hub":
        abort(404)
    return hub


def _tracks_for_hub(hub_slug: str) -> List[dict]:
    return list(
        db.programs.find({"kind": "track", "hub_slug": hub_slug, "active": {"$ne": False}}).sort(
            [("order", 1), ("created_at", -1)]
        )
    )


def _levels_for_hub(hub_slug: str) -> List[str]:
    tracks = _tracks_for_hub(hub_slug)
    lvls: List[str] = []
    seen = set()
    for t in tracks:
        lvl = _norm_choice(t.get("track_level"))
        if lvl and lvl not in seen:
            seen.add(lvl)
            lvls.append(lvl)
    return lvls or DEFAULT_LEVELS


def _envs_for_hub_level(hub_slug: str, level: str) -> List[str]:
    tracks = _tracks_for_hub(hub_slug)
    envs: List[str] = []
    seen = set()
    for t in tracks:
        lvl = _norm_choice(t.get("track_level"))
        if lvl and lvl != level:
            continue
        env = _infer_env_from_slug(t.get("slug", "")) or _infer_env_from_slug(t.get("category", ""))
        if env and env not in seen:
            seen.add(env)
            envs.append(env)
    return envs or DEFAULT_ENVS


def _pick_track_for(hub_slug: str, level: str, env: str) -> Optional[dict]:
    tracks = _tracks_for_hub(hub_slug)
    level = _norm_choice(level)
    env = _norm_choice(env)

    for t in tracks:
        if (
            _norm_choice(t.get("track_level")) == level
            and _infer_env_from_slug(t.get("slug", "")) == env
        ):
            return t

    for t in tracks:
        if _norm_choice(t.get("track_level")) == level:
            return t

    return None


def _object_id_or_404(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception:
        abort(404)


def _object_id_or_none(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except Exception:
        return None


def _get_program_and_week_or_404(program_id: str, week_id: str):
    program_oid = _object_id_or_404(program_id)
    week_oid = _object_id_or_404(week_id)
    program = db.programs.find_one({"_id": program_oid})
    if not program:
        abort(404)
    week = db.program_weeks.find_one({"_id": week_oid, "program_id": program["_id"]})
    if not week:
        abort(404)
    return program, week


def _form_int(name: str, default: int = 0, min_value: Optional[int] = None) -> int:
    try:
        value = int(request.form.get(name) or default)
    except (TypeError, ValueError):
        value = default
    if min_value is not None:
        value = max(min_value, value)
    return value


# -----------------------------------------------------------------------------
# Progress foundation (cookie/session-based)
# -----------------------------------------------------------------------------
def _get_progress() -> dict:
    prog = session.get(PROGRESS_SESSION_KEY)
    return prog if isinstance(prog, dict) else {}


def _set_progress(prog: dict) -> None:
    session[PROGRESS_SESSION_KEY] = prog
    session.modified = True


def _progress_key(hub_slug: str) -> str:
    return f"hub:{hub_slug}"


def _get_active_selection(hub_slug: str) -> dict:
    prog = _get_progress()
    return prog.get(_progress_key(hub_slug), {}) if prog else {}


def _set_active_selection(hub_slug: str, level: str, env: str) -> None:
    prog = _get_progress()
    key = _progress_key(hub_slug)

    prev = prog.get(key, {})
    completed_weeks = prev.get("completed_weeks", [])
    if not isinstance(completed_weeks, list):
        completed_weeks = []

    prog[key] = {
        "level": _norm_choice(level) or "beginner",
        "env": _norm_choice(env) or "home",
        "completed_weeks": completed_weeks,
        "updated_at": datetime.datetime.utcnow().isoformat(),
    }
    _set_progress(prog)


def _mark_week_done(hub_slug: str, week_number: int) -> None:
    prog = _get_progress()
    key = _progress_key(hub_slug)

    st = prog.get(key, {})
    done = st.get("completed_weeks", [])
    if not isinstance(done, list):
        done = []

    if week_number not in done:
        done.append(week_number)

    st["completed_weeks"] = sorted(done)
    st["updated_at"] = datetime.datetime.utcnow().isoformat()
    prog[key] = st
    _set_progress(prog)


def _max_week_for_hub(hub: dict, track: Optional[dict]) -> int:
    """
    Best-effort max week:
    - if track/hub has duration_label like '8 weeks' -> use that
    - else fallback to 8
    """
    n = _week_count_from_duration_label((track or {}).get("duration_label") or hub.get("duration_label"))
    return n or 8


def _next_unlocked_week(saved: dict, max_week: int) -> int:
    """
    Unlock rule: week 1 is unlocked by default.
    Each completed week unlocks the next.
    """
    done = saved.get("completed_weeks", []) if isinstance(saved, dict) else []
    if not isinstance(done, list) or not done:
        return 1
    completed = {int(w) for w in done if isinstance(w, int) or str(w).isdigit()}
    for week in range(1, max_week + 1):
        if week not in completed:
            return week
    return max_week


def _progress_view(saved: dict, max_week: int) -> dict:
    """
    Return a template-friendly progress object while preserving the stored shape.
    Templates use:
    - week: current unlocked week
    - max_week: total weeks in the track
    - completed: completed week numbers
    """
    state = dict(saved or {})
    completed = state.get("completed_weeks", [])
    if not isinstance(completed, list):
        completed = []
    completed = sorted({int(w) for w in completed if isinstance(w, int) or str(w).isdigit()})
    current_week = _next_unlocked_week({"completed_weeks": completed}, max_week)
    state.update(
        {
            "completed": completed,
            "completed_weeks": completed,
            "week": current_week,
            "unlocked_week": current_week,
            "max_week": max_week,
        }
    )
    return state


# -----------------------------------------------------------------------------
# Indexes (safe to call repeatedly)
# -----------------------------------------------------------------------------
db.workouts.create_index([("slug", 1)], unique=True, sparse=True)
db.workouts.create_index([("name", 1)])
db.workouts.create_index([("level", 1)])
db.workouts.create_index([("body_part", 1)])
db.workouts.create_index([("style", 1)])
db.workouts.create_index([("created_at", -1)])
db.workouts.create_index([("rating", -1)])

db.styles.create_index([("slug", 1)], unique=True, sparse=True)

db.home_plans.create_index([("slug", 1)], unique=True, sparse=True)
db.home_plans.create_index([("order", 1)])
db.home_plans.create_index([("created_at", -1)])
db.home_plans.create_index([("active", 1)])

db.programs.create_index([("slug", 1)], unique=True, sparse=True)
db.programs.create_index([("active", 1)])
db.programs.create_index([("order", 1)])
db.programs.create_index([("created_at", -1)])
db.programs.create_index([("show_on_home", 1)])
db.programs.create_index([("kind", 1)])
db.programs.create_index([("hub_slug", 1)])
db.programs.create_index([("track_level", 1)])

db.program_weeks.create_index([("program_id", 1)])
db.program_weeks.create_index([("week_number", 1)])
db.program_weeks.create_index([("order", 1)])

db.program_items.create_index([("week_id", 1)])
db.program_items.create_index([("order", 1)])
db.program_items.create_index([("created_at", 1)])
db.program_items.create_index([("workout_id", 1)])
db.program_items.create_index([("program_id", 1)])
db.program_items.create_index([("day_number", 1)])


def get_styles() -> List[str]:
    styles = list(db.styles.find({"active": {"$ne": False}}).sort([("order", 1), ("name", 1)]))
    if styles:
        return [s["name"] for s in styles]
    return DEFAULT_WORKOUT_STYLES


def _ensure_style_seed_once() -> None:
    try:
        if db.styles.count_documents({}) == 0:
            docs = [
                {"name": n, "slug": slugify(n), "order": i, "active": True}
                for i, n in enumerate(DEFAULT_WORKOUT_STYLES)
            ]
            if docs:
                db.styles.insert_many(docs)
    except Exception as e:
        app.logger.warning("Styles seed skipped: %s", e)


_ensure_style_seed_once()

# -----------------------------------------------------------------------------
# Seed the 8-week hub + track programs (only if missing)
# -----------------------------------------------------------------------------
EIGHT_WEEK_HUB_SLUG = "8-week-challenge"

EIGHT_WEEK_TRACK_SLUGS = [
    "8-week-challenge-beginner-home",
    "8-week-challenge-beginner-gym",
    "8-week-challenge-beginner-hybrid",
    "8-week-challenge-intermediate-home",
    "8-week-challenge-intermediate-gym",
    "8-week-challenge-intermediate-hybrid",
    "8-week-challenge-advanced-home",
    "8-week-challenge-advanced-gym",
    "8-week-challenge-advanced-hybrid",
]

DEFAULT_8W_RULES = [
    "Diet: low/zero added sugar • 3–6L water • prioritize protein + whole foods.",
    "Cardio: 20–30 min steady pace • conversational breathing • optional light intervals if you feel good.",
    "Rule: scale reps/weight to keep form clean — consistency beats intensity.",
]


def _ensure_8_week_programs_seed_once() -> None:
    try:
        now = datetime.datetime.utcnow()

        hub = db.programs.find_one({"slug": EIGHT_WEEK_HUB_SLUG})
        if not hub:
            db.programs.insert_one(
                {
                    "title": "8 Week Challenge",
                    "slug": EIGHT_WEEK_HUB_SLUG,
                    "kind": "hub",
                    "category": "Challenge",
                    "duration_label": "8 weeks",
                    "summary": "Pick your level and training environment. Follow the weekly plan and build momentum.",
                    "cover_image": None,
                    "order": 0,
                    "active": True,
                    "show_on_home": True,
                    "rules": DEFAULT_8W_RULES,
                    "created_at": now,
                }
            )
        else:
            if hub.get("kind") != "hub":
                db.programs.update_one({"_id": hub["_id"]}, {"$set": {"kind": "hub"}})

        defaults = [
            ("Beginner • Home", "Beginner", "Home", 10),
            ("Beginner • Gym", "Beginner", "Gym", 11),
            ("Beginner • Hybrid", "Beginner", "Hybrid", 12),
            ("Intermediate • Home", "Intermediate", "Home", 20),
            ("Intermediate • Gym", "Intermediate", "Gym", 21),
            ("Intermediate • Hybrid", "Intermediate", "Hybrid", 22),
            ("Advanced • Home", "Advanced", "Home", 30),
            ("Advanced • Gym", "Advanced", "Gym", 31),
            ("Advanced • Hybrid", "Advanced", "Hybrid", 32),
        ]

        for slug, meta in zip(EIGHT_WEEK_TRACK_SLUGS, defaults):
            title, level, env, order = meta
            existing = db.programs.find_one({"slug": slug})
            if existing:
                updates = {}
                if existing.get("kind") != "track":
                    updates["kind"] = "track"
                if existing.get("hub_slug") != EIGHT_WEEK_HUB_SLUG:
                    updates["hub_slug"] = EIGHT_WEEK_HUB_SLUG
                if not existing.get("track_level"):
                    updates["track_level"] = level
                if updates:
                    db.programs.update_one({"_id": existing["_id"]}, {"$set": updates})
                continue

            db.programs.insert_one(
                {
                    "title": f"8 Week Challenge — {title}",
                    "slug": slug,
                    "kind": "track",
                    "hub_slug": EIGHT_WEEK_HUB_SLUG,
                    "track_level": level,
                    "category": f"{level} • {env}",
                    "duration_label": "8 weeks",
                    "summary": "Week-by-week plan with exercise options. Add weeks/workouts next in Admin.",
                    "cover_image": None,
                    "order": order,
                    "active": True,
                    "show_on_home": False,
                    "rules": DEFAULT_8W_RULES,
                    "created_at": now,
                }
            )

    except Exception as e:
        app.logger.warning("8-week seed skipped/failed: %s", e)


_ensure_8_week_programs_seed_once()

# -----------------------------------------------------------------------------
# Quick menu (sidebar)
# -----------------------------------------------------------------------------
QUICK_OPTIONS = [
    {"label": "Favorites", "url": "/workouts/browse?sort=favorites"},
    {"label": "Recently Added", "url": "/workouts/browse?sort=recent"},
    {"label": "Top Rated", "url": "/workouts/browse?sort=rating"},
]

# -----------------------------------------------------------------------------
# Static/media serving for Render Disk
# -----------------------------------------------------------------------------
if MEDIA_ROOT:
    if not MEDIA_URL.startswith("/"):
        MEDIA_URL = "/" + MEDIA_URL
    if not MEDIA_URL.endswith("/"):
        MEDIA_URL = MEDIA_URL + "/"

    @app.route(f"{MEDIA_URL}<path:fp>")
    def _serve_media(fp):
        return send_from_directory(UPLOAD_ROOT_ABS, fp, conditional=True)


# -----------------------------------------------------------------------------
# Context processors
# -----------------------------------------------------------------------------
@app.context_processor
def inject_globals():
    return {"quick_options": QUICK_OPTIONS, "csrf_token": generate_csrf}


# -----------------------------------------------------------------------------
# Helper (template fallback)
# -----------------------------------------------------------------------------
def render_or_fallback(template_name: str, **ctx):
    """
    Render a template, but if it's missing (or broken), return a readable fallback HTML page
    instead of crashing into a generic 500.
    """
    try:
        return render_template(template_name, **ctx)
    except TemplateNotFound:
        app.logger.exception("Template missing: %s", template_name)
        html = f"""
        <div style="max-width:820px;margin:40px auto;font-family:Arial,sans-serif;">
          <h1>Template missing</h1>
          <p><b>{template_name}</b> was not found in your templates folder.</p>
          <p>Fix: create <code>backend/templates/{template_name}</code> (or update the filename in app.py).</p>
          <hr/>
          <p><a href="/programs">Back to programs</a></p>
        </div>
        """
        return render_template_string(html), 500
    except Exception:
        app.logger.exception("Template error while rendering: %s", template_name)
        html = f"""
        <div style="max-width:820px;margin:40px auto;font-family:Arial,sans-serif;">
          <h1>Template error</h1>
          <p>There is a rendering error in <b>{template_name}</b>.</p>
          <p>Check <code>instance/logs/app.log</code> for the exact traceback.</p>
          <hr/>
          <p><a href="/programs">Back to programs</a></p>
        </div>
        """
        return render_template_string(html), 500


# -----------------------------------------------------------------------------
# Public pages
# -----------------------------------------------------------------------------
@app.route("/")
def home():
    featured_programs = list(
        db.programs.find({"active": {"$ne": False}, "show_on_home": True})
        .sort([("order", 1), ("created_at", -1)])
        .limit(6)
    )
    return render_template("home.html", name="NFG", featured_programs=featured_programs)


# -----------------------------------------------------------------------------
# Public: Programs
# -----------------------------------------------------------------------------
@app.route("/programs")
def programs_index():
    programs = list(
        db.programs.find({"active": {"$ne": False}, "kind": "hub"})
        .sort([("order", 1), ("created_at", -1)])
        .limit(50)
    )
    return render_template("programs.html", programs=programs)


@app.route("/programs/<slug>")
def program_detail(slug):
    program = db.programs.find_one({"slug": slug, "active": {"$ne": False}})
    if not program:
        abort(404)

    # Hub routes should go to selection flow
    if program.get("kind") == "hub":
        return redirect(url_for("program_hub_level", hub_slug=program["slug"]))

    weeks = list(
        db.program_weeks.find({"program_id": program["_id"]}).sort(
            [("week_number", 1), ("order", 1)]
        )
    )

    week_ids = [w["_id"] for w in weeks]
    rows = []
    if week_ids:
        rows = list(
            db.program_items.find({"week_id": {"$in": week_ids}}).sort(
                [("order", 1), ("created_at", 1)]
            )
        )

    rows_by_week = {wid: [] for wid in week_ids}
    for r in rows:
        rows_by_week.setdefault(r["week_id"], []).append(r)

    workout_ids = [r.get("workout_id") for r in rows if r.get("workout_id")]
    workout_map = {}
    if workout_ids:
        ws = list(db.workouts.find({"_id": {"$in": workout_ids}}, {"name": 1, "slug": 1}))
        workout_map = {w["_id"]: w for w in ws}

    return render_template(
        "program_detail.html",
        program=program,
        weeks=weeks,
        rows_by_week=rows_by_week,
        workout_map=workout_map,
    )


# -----------------------------------------------------------------------------
# Public: Dynamic Hub -> Tracks flow
# -----------------------------------------------------------------------------
@app.route("/programs/<hub_slug>/level")
def program_hub_level(hub_slug):
    hub = _get_hub_or_404(hub_slug)
    levels = _levels_for_hub(hub_slug)

    saved = _get_active_selection(hub_slug)
    return render_or_fallback("program_level.html", hub=hub, levels=levels, saved=saved)


@app.route("/programs/<hub_slug>/environment")
def program_hub_environment(hub_slug):
    hub = _get_hub_or_404(hub_slug)

    level = _norm_choice(request.args.get("level"))
    levels = _levels_for_hub(hub_slug)
    if level not in levels:
        return redirect(url_for("program_hub_level", hub_slug=hub_slug))

    envs = _envs_for_hub_level(hub_slug, level)
    saved = _get_active_selection(hub_slug)
    return render_or_fallback(
        "program_environment.html",
        hub=hub,
        level=level,
        envs=envs,
        saved=saved,
    )


@app.route("/programs/<hub_slug>/weeks")
def program_hub_weeks(hub_slug):
    hub = _get_hub_or_404(hub_slug)

    level = _norm_choice(request.args.get("level"))
    env = _norm_choice(request.args.get("env"))

    levels = _levels_for_hub(hub_slug)
    if level not in levels:
        return redirect(url_for("program_hub_level", hub_slug=hub_slug))

    envs = _envs_for_hub_level(hub_slug, level)
    if env not in envs:
        return redirect(url_for("program_hub_environment", hub_slug=hub_slug, level=level))

    # Save selection for resume
    _set_active_selection(hub_slug, level, env)

    track = _pick_track_for(hub_slug, level, env)
    if not track:
        flash("That track isn't set up yet. Create a Track program in Admin for this Hub.", "warning")
        return redirect(url_for("program_hub_environment", hub_slug=hub_slug, level=level))

    weeks = list(
        db.program_weeks.find({"program_id": track["_id"]}).sort([("week_number", 1), ("order", 1)])
    )
    if not weeks:
        n = _week_count_from_duration_label(track.get("duration_label") or hub.get("duration_label"))
        weeks = [{"week_number": i, "title": None} for i in range(1, n + 1)]

    max_week = _max_week_for_hub(hub, track)
    saved = _progress_view(_get_active_selection(hub_slug), max_week)
    completed_weeks = saved["completed"]
    unlocked_week = saved["week"]

    return render_or_fallback(
        "program_weeks.html",
        track=track,
        hub=hub,
        level=level,
        env=env,
        weeks=weeks,
        completed_weeks=completed_weeks,
        saved=saved,
        max_week=max_week,
        unlocked_week=unlocked_week,
    )


@app.route("/programs/<hub_slug>/week/<int:week_number>")
def program_hub_week_detail(hub_slug, week_number: int):
    hub = _get_hub_or_404(hub_slug)

    # prefer query params, else session selection
    level = _norm_choice(request.args.get("level"))
    env = _norm_choice(request.args.get("env"))
    if not level or not env:
        saved = _get_active_selection(hub_slug)
        level = level or _norm_choice(saved.get("level")) or "beginner"
        env = env or _norm_choice(saved.get("env")) or "home"

    levels = _levels_for_hub(hub_slug)
    if level not in levels:
        level = levels[0] if levels else "beginner"

    envs = _envs_for_hub_level(hub_slug, level)
    if env not in envs:
        env = envs[0] if envs else "home"

    track = _pick_track_for(hub_slug, level, env)
    if not track:
        abort(404)

    week = db.program_weeks.find_one({"program_id": track["_id"], "week_number": week_number})

    items = []
    workout_map = {}
    if week:
        items = list(
            db.program_items.find({"week_id": week["_id"]}).sort(
                [("day_number", 1), ("order", 1), ("created_at", 1)]
            )
        )
        workout_ids = []
        for it in items:
            raw_workout_id = it.get("workout_id")
            if isinstance(raw_workout_id, ObjectId):
                workout_ids.append(raw_workout_id)
            elif isinstance(raw_workout_id, str) and ObjectId.is_valid(raw_workout_id):
                workout_ids.append(ObjectId(raw_workout_id))
        if workout_ids:
            ws = list(db.workouts.find({"_id": {"$in": workout_ids}}, {"name": 1, "slug": 1}))
            workout_map = {w["_id"]: w for w in ws}
            workout_map.update({str(w["_id"]): w for w in ws})

    items_by_day: Dict[int, List[dict]] = {}
    for item in items:
        day_number = item.get("day_number") or 1
        try:
            day_number = int(day_number)
        except (TypeError, ValueError):
            day_number = 1
        items_by_day.setdefault(day_number, []).append(item)

    max_week = _max_week_for_hub(hub, track)
    saved = _progress_view(_get_active_selection(hub_slug), max_week)
    completed_weeks = saved["completed"]
    unlocked_week = saved["week"]
    is_locked = week_number > unlocked_week

    return render_or_fallback(
        "program_week_detail.html",
        track=track,
        hub=hub,
        level=level,
        env=env,
        week_number=week_number,
        week=week,
        items=items,
        items_by_day=items_by_day,
        workout_map=workout_map,
        levels=levels or DEFAULT_LEVELS,
        envs=envs or DEFAULT_ENVS,
        completed_weeks=completed_weeks,
        saved=saved,
        max_week=max_week,
        unlocked_week=unlocked_week,
        is_locked=is_locked,
    )


# -----------------------------------------------------------------------------
# Progress endpoints
# -----------------------------------------------------------------------------
@app.route("/programs/<hub_slug>/start")
def program_start(hub_slug):
    """
    Start / resume a hub selection.
    If level/env provided, save them then go to weeks.
    Else if saved exists, go to weeks with saved.
    Else go to level page.
    """
    _get_hub_or_404(hub_slug)  # ensure exists

    level = _norm_choice(request.args.get("level"))
    env = _norm_choice(request.args.get("env"))

    saved = _get_active_selection(hub_slug)

    if level and env:
        _set_active_selection(hub_slug, level, env)
        return redirect(url_for("program_hub_weeks", hub_slug=hub_slug, level=level, env=env))

    if saved.get("level") and saved.get("env"):
        return redirect(
            url_for(
                "program_hub_weeks",
                hub_slug=hub_slug,
                level=_norm_choice(saved.get("level")),
                env=_norm_choice(saved.get("env")),
            )
        )

    return redirect(url_for("program_hub_level", hub_slug=hub_slug))


@app.route("/programs/<hub_slug>/week/<int:week_number>/complete", methods=["POST"])
def program_mark_week_complete(hub_slug, week_number: int):
    """
    Marks a week as completed in cookie/session.
    Later we’ll move this into a real User model.
    """
    hub = _get_hub_or_404(hub_slug)

    # keep selection if present
    level = _norm_choice(request.args.get("level"))
    env = _norm_choice(request.args.get("env"))
    if level and env:
        _set_active_selection(hub_slug, level, env)

    saved = _get_active_selection(hub_slug)

    # Guard: only allow completing the currently unlocked week
    sel_level = _norm_choice(saved.get("level")) or level or "beginner"
    sel_env = _norm_choice(saved.get("env")) or env or "home"
    track = _pick_track_for(hub_slug, sel_level, sel_env)

    max_week = _max_week_for_hub(hub, track)
    unlocked_week = _next_unlocked_week(saved, max_week)

    if week_number != unlocked_week:
        flash("You can only complete the currently active week.", "warning")
        return redirect(
            url_for(
                "program_hub_week_detail",
                hub_slug=hub_slug,
                week_number=week_number,
                level=sel_level,
                env=sel_env,
            )
        )

    _mark_week_done(hub_slug, week_number)

    flash(f"Week {week_number} marked complete.", "success")
    return redirect(url_for("program_hub_weeks", hub_slug=hub_slug, level=sel_level, env=sel_env))


@app.route("/programs/<hub_slug>/progress.json")
def program_progress_json(hub_slug):
    _get_hub_or_404(hub_slug)
    return jsonify(_get_active_selection(hub_slug))


# -----------------------------------------------------------------------------
# Backwards-compatible endpoint aliases (prevents template BuildError)
# -----------------------------------------------------------------------------
@app.route("/programs/<slug>/level-legacy")
def program_level(slug):
    # Old templates: url_for('program_level', slug=...)
    return redirect(url_for("program_hub_level", hub_slug=slug))


@app.route("/programs/<slug>/environment-legacy")
def program_environment(slug):
    # Old templates: url_for('program_environment', slug=..., level=...)
    level = _norm_choice(request.args.get("level"))
    return redirect(url_for("program_hub_environment", hub_slug=slug, level=level))


@app.route("/programs/<slug>/weeks-legacy")
def program_weeks(slug):
    # Old templates: url_for('program_weeks', slug=..., level=..., env=...)
    level = _norm_choice(request.args.get("level"))
    env = _norm_choice(request.args.get("env"))
    return redirect(url_for("program_hub_weeks", hub_slug=slug, level=level, env=env))


# -----------------------------------------------------------------------------
# Legacy: KEEP ONLY the hub root redirect
# -----------------------------------------------------------------------------
@app.route("/programs/8-week-challenge")
def eight_week_hub_redirect():
    return redirect(url_for("program_hub_level", hub_slug=EIGHT_WEEK_HUB_SLUG))


# -----------------------------------------------------------------------------
# Workouts
# -----------------------------------------------------------------------------
@app.route("/workouts")
def workouts():
    parts_single = set(db.workouts.distinct("body_part"))
    parts_multi = set(db.workouts.distinct("body_parts"))
    parts_in_db = parts_single | parts_multi
    body_parts_featured = [p for p in FEATURED_BODY_PARTS if p in parts_in_db] or FEATURED_BODY_PARTS[:]

    all_ws = list(db.workouts.find({}).sort([("name", ASCENDING)]).limit(3))

    return render_template(
        "workouts.html",
        workout_levels=WORKOUT_LEVELS,
        body_parts_featured=body_parts_featured,
        workout_styles=get_styles(),
        workout_styles_featured=FEATURED_STYLES,
        all_workouts=all_ws,
    )


@app.route("/workouts/all")
def workouts_all():
    items = list(db.workouts.find({}).sort([("name", ASCENDING)]))
    return render_template("all_workouts_index.html", items=items)


@app.route("/workouts/styles")
def styles_index():
    styles = get_styles()
    counts = {st: db.workouts.count_documents({"style": st}) for st in styles}
    return render_template("styles_index.html", styles=styles, counts=counts)


@app.route("/workouts/body-parts")
def body_parts_index():
    counts = {
        bp: db.workouts.count_documents({"$or": [{"body_part": bp}, {"body_parts": bp}]})
        for bp in BODY_PARTS_MASTER
    }
    return render_template("body_parts_index.html", body_parts=BODY_PARTS_MASTER, counts=counts)


@app.route("/workouts/browse")
def workouts_browse():
    level = request.args.get("level") or ""
    body = request.args.get("body") or ""
    style = request.args.get("style") or ""
    q = (request.args.get("q") or "").strip()
    sort_key = request.args.get("sort", "name")
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 6)), 1), 100)

    and_clauses = []
    if level:
        and_clauses.append({"level": level})
    if style:
        and_clauses.append({"style": style})
    if body:
        and_clauses.append({"$or": [{"body_part": body}, {"body_parts": body}]})
    if sort_key == "favorites":
        and_clauses.append({"is_favorite": True})
    if q:
        rx = Regex(q, "i")
        and_clauses.append(
            {
                "$or": [
                    {"name": rx},
                    {"level": rx},
                    {"body_part": rx},
                    {"body_parts": rx},
                    {"style": rx},
                    {"tags": rx},
                ]
            }
        )

    query = {"$and": and_clauses} if and_clauses else {}

    sort = [("name", ASCENDING)]
    if sort_key == "recent":
        sort = [("created_at", -1)]
    elif sort_key == "rating":
        sort = [("rating", -1), ("name", ASCENDING)]

    total = db.workouts.count_documents(query)
    items = list(db.workouts.find(query).sort(sort).skip((page - 1) * per_page).limit(per_page))

    return render_template(
        "browse_workouts.html",
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        sort=sort_key,
        level=level,
        body=body,
        style=style,
        q=q,
        workout_levels=WORKOUT_LEVELS,
        body_parts=BODY_PARTS_MASTER,
        workout_styles=get_styles(),
    )


@app.route("/workouts/<slug>")
def workout_detail(slug):
    w = db.workouts.find_one({"slug": slug})
    if not w:
        abort(404)

    parts = w.get("body_parts") or ([w.get("body_part")] if w.get("body_part") else [])
    rel_or = []
    if parts:
        rel_or.append({"body_parts": {"$in": parts}})
    if w.get("style"):
        rel_or.append({"style": w.get("style")})

    if rel_or:
        rel_q = {"$and": [{"slug": {"$ne": w["slug"]}}, {"$or": rel_or}]}
    else:
        rel_q = {"slug": {"$ne": w["slug"]}}

    related = list(
        db.workouts.find(rel_q).sort([("rating", -1), ("created_at", -1), ("name", 1)]).limit(6)
    )
    return render_template("workout_detail.html", w=w, related=related)


# -----------------------------------------------------------------------------
# Recipes + Search
# -----------------------------------------------------------------------------
@app.route("/recipes")
def recipes():
    recs = list(db.recipes.find().sort([("name", ASCENDING)]))
    return render_template("recipes.html", recipes=recs)


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return render_template("home.html", name="NFG", featured_programs=[])

    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 24)), 1), 100)

    rx = Regex(q, "i")
    w_query = {
        "$or": [
            {"name": rx},
            {"level": rx},
            {"body_part": rx},
            {"body_parts": rx},
            {"style": rx},
            {"tags": rx},
        ]
    }

    total = db.workouts.count_documents(w_query)
    items = list(
        db.workouts.find(w_query)
        .sort([("name", ASCENDING)])
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    rs = list(db.recipes.find({"name": rx}).sort([("name", ASCENDING)]))

    return render_template(
        "search_results.html",
        q=q,
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        recipes=rs,
    )


# -----------------------------------------------------------------------------
# Auth routes
# -----------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = _client_ip()
        if not _allowed_login_attempt(ip):
            flash("Too many failed login attempts. Try again in ~15 minutes.", "danger")
            return render_template("login.html")

        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if _check_admin_credentials(username, password):
            _clear_failed_logins(ip)
            login_user(User(ADMIN_USER_ID))
            flash("Logged in.", "success")
            return redirect(request.args.get("next") or url_for("admin_index"))

        _record_failed_login(ip)
        flash("Invalid credentials.", "danger")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("home"))


@app.route("/health")
def health():
    return {"status": "ok"}, 200


# -----------------------------------------------------------------------------
# Admin: Workouts
# -----------------------------------------------------------------------------
@app.route("/admin")
@login_required
def admin_index():
    items = list(db.workouts.find().sort([("created_at", -1)]))
    return render_template("admin_index.html", items=items)


@app.route("/admin/workouts/new", methods=["GET", "POST"])
@login_required
def admin_workout_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        level = request.form.get("level", "").strip()
        style = request.form.get("style", "").strip()
        body_parts = _split_list(request.form.get("body_parts", ""))
        body_part = (
            body_parts[0] if body_parts else (request.form.get("body_part", "").strip() or "")
        )
        tags = _split_list(request.form.get("tags", ""))
        images = _collect_ordered_images_from_form(request)
        muscle_image = _collect_muscle_image_from_form(request)
        info = (request.form.get("info") or "").strip() or None
        tips = _split_list(request.form.get("tips", ""))
        youtube_id = _extract_youtube_id(request.form.get("youtube_id"))
        is_favorite = request.form.get("is_favorite") == "on"
        rating = float(request.form.get("rating") or 0)
        slug = (request.form.get("slug") or slugify(name)).strip()

        if not name:
            flash("Name is required.", "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS,
                parts=BODY_PARTS_MASTER,
                styles=get_styles(),
                data=request.form,
            )

        if not slug:
            slug = slugify(name)

        if db.workouts.find_one({"slug": slug}):
            flash(f"Slug '{slug}' is already used by another workout.", "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS,
                parts=BODY_PARTS_MASTER,
                styles=get_styles(),
                data=request.form,
            )

        doc = {
            "name": name,
            "slug": slug,
            "level": level,
            "body_part": body_part,
            "body_parts": body_parts,
            "style": style,
            "tags": tags,
            "images": images,
            "muscle_image": muscle_image,
            "info": info,
            "tips": tips,
            "youtube_id": youtube_id,
            "is_favorite": is_favorite,
            "rating": rating,
            "created_at": datetime.datetime.utcnow(),
        }

        try:
            db.workouts.insert_one(doc)
            flash("Workout added.", "success")
            return redirect(url_for("admin_index"))
        except Exception as exc:
            flash(f"Error: {exc}", "danger")

    return render_template(
        "admin_workout_form.html",
        levels=WORKOUT_LEVELS,
        parts=BODY_PARTS_MASTER,
        styles=get_styles(),
        data={},
    )


@app.route("/admin/workouts/<id>/edit", methods=["GET", "POST"])
@login_required
def admin_workout_edit(id):
    w = db.workouts.find_one({"_id": ObjectId(id)})
    if not w:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        level = request.form.get("level", "").strip()
        style = request.form.get("style", "").strip()
        body_parts = _split_list(request.form.get("body_parts", ""))
        body_part = (
            body_parts[0] if body_parts else (request.form.get("body_part", "").strip() or "")
        )
        tags = _split_list(request.form.get("tags", ""))
        images = _collect_ordered_images_from_form(request)
        muscle_image = _collect_muscle_image_from_form(request)
        info = (request.form.get("info") or "").strip() or None
        tips = _split_list(request.form.get("tips", ""))
        youtube_id = _extract_youtube_id(request.form.get("youtube_id"))
        is_favorite = request.form.get("is_favorite") == "on"
        rating = float(request.form.get("rating") or 0)
        slug = (request.form.get("slug") or slugify(name)).strip()

        if not name:
            flash("Name is required.", "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS,
                parts=BODY_PARTS_MASTER,
                styles=get_styles(),
                data=request.form,
                edit=True,
                _id=id,
            )

        if not slug:
            slug = slugify(name)

        existing = db.workouts.find_one({"slug": slug, "_id": {"$ne": ObjectId(id)}})
        if existing:
            flash(f"Slug '{slug}' is already used by another workout.", "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS,
                parts=BODY_PARTS_MASTER,
                styles=get_styles(),
                data=request.form,
                edit=True,
                _id=id,
            )

        update = {
            "name": name,
            "slug": slug,
            "level": level,
            "body_part": body_part,
            "body_parts": body_parts,
            "style": style,
            "tags": tags,
            "images": images,
            "muscle_image": muscle_image,
            "info": info,
            "tips": tips,
            "youtube_id": youtube_id,
            "is_favorite": is_favorite,
            "rating": rating,
        }

        try:
            db.workouts.update_one({"_id": ObjectId(id)}, {"$set": update})
            flash("Workout updated.", "success")
            return redirect(url_for("admin_index"))
        except Exception as exc:
            flash(f"Error: {exc}", "danger")

    data = dict(w)
    data["tags"] = ", ".join(data.get("tags", []))
    data["tips"] = "\n".join(data.get("tips", []))
    if isinstance(data.get("body_parts"), list):
        data["body_parts"] = ", ".join(data["body_parts"])
    else:
        data["body_parts"] = data.get("body_parts") or data.get("body_part", "")

    return render_template(
        "admin_workout_form.html",
        levels=WORKOUT_LEVELS,
        parts=BODY_PARTS_MASTER,
        styles=get_styles(),
        data=data,
        edit=True,
        _id=id,
    )


@app.route("/admin/workouts/<id>/delete", methods=["POST"])
@login_required
def admin_workout_delete(id):
    db.workouts.delete_one({"_id": ObjectId(id)})
    flash("Workout deleted.", "success")
    return redirect(url_for("admin_index"))


# -----------------------------------------------------------------------------
# Admin: Styles
# -----------------------------------------------------------------------------
@app.route("/admin/styles", methods=["GET", "POST"])
@login_required
def admin_styles():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        order = int(request.form.get("order") or 0)

        if not name:
            flash("Style name is required.", "danger")
            return redirect(url_for("admin_styles"))

        slug = slugify(name)
        if db.styles.find_one({"slug": slug}):
            flash(f"Style '{name}' already exists.", "warning")
            return redirect(url_for("admin_styles"))

        db.styles.insert_one({"name": name, "slug": slug, "order": order, "active": True})
        flash("Style added.", "success")
        return redirect(url_for("admin_styles"))

    styles = list(db.styles.find().sort([("order", 1), ("name", 1)]))
    return render_template("admin_style.html", styles=styles)


@app.route("/admin/styles/<id>/toggle", methods=["POST"])
@login_required
def admin_style_toggle(id):
    s = db.styles.find_one({"_id": ObjectId(id)})
    if not s:
        abort(404)
    db.styles.update_one({"_id": s["_id"]}, {"$set": {"active": not s.get("active", True)}})
    flash(f"Style {'activated' if not s.get('active', True) else 'deactivated'}.", "success")
    return redirect(url_for("admin_styles"))


@app.route("/admin/styles/<id>/delete", methods=["POST"])
@login_required
def admin_style_delete(id):
    db.styles.delete_one({"_id": ObjectId(id)})
    flash("Style deleted.", "success")
    return redirect(url_for("admin_styles"))


# -----------------------------------------------------------------------------
# Admin: Home Plans (legacy)
# -----------------------------------------------------------------------------
@app.route("/admin/home-plans")
@login_required
def admin_home_plans():
    plans = list(db.home_plans.find().sort([("active", -1), ("order", 1), ("created_at", -1)]))
    return render_template("admin_home_plans.html", plans=plans)


@app.route("/admin/home-plans/new", methods=["GET", "POST"])
@login_required
def admin_home_plan_new():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        slug = (request.form.get("slug") or "").strip() or slugify(title)
        category = (request.form.get("category") or "").strip() or None
        duration_label = (request.form.get("duration_label") or "").strip() or None
        summary = (request.form.get("summary") or "").strip() or None
        cover_image = (request.form.get("cover_image") or "").strip() or None
        cta_label = (request.form.get("cta_label") or "").strip() or "View Plan"
        cta_url = (request.form.get("cta_url") or "").strip()
        order = int(request.form.get("order") or 0)
        active = request.form.get("active") == "on"

        if not title:
            flash("Title is required.", "danger")
            return render_template("admin_home_plan_form.html", data=request.form, edit=False)

        if not cta_url:
            flash("Primary button URL is required.", "danger")
            return render_template("admin_home_plan_form.html", data=request.form, edit=False)

        if db.home_plans.find_one({"slug": slug}):
            flash(f"Slug '{slug}' already exists.", "danger")
            return render_template("admin_home_plan_form.html", data=request.form, edit=False)

        doc = {
            "title": title,
            "slug": slug,
            "category": category,
            "duration_label": duration_label,
            "summary": summary,
            "cover_image": cover_image,
            "cta_label": cta_label,
            "cta_url": cta_url,
            "order": order,
            "active": active,
            "created_at": datetime.datetime.utcnow(),
        }

        try:
            db.home_plans.insert_one(doc)
            flash("Home plan created.", "success")
            return redirect(url_for("admin_home_plans"))
        except Exception as exc:
            flash(f"Error: {exc}", "danger")

    return render_template("admin_home_plan_form.html", data={}, edit=False)


@app.route("/admin/home-plans/<id>/edit", methods=["GET", "POST"])
@login_required
def admin_home_plan_edit(id):
    p = db.home_plans.find_one({"_id": ObjectId(id)})
    if not p:
        abort(404)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        slug = (request.form.get("slug") or "").strip() or slugify(title)
        category = (request.form.get("category") or "").strip() or None
        duration_label = (request.form.get("duration_label") or "").strip() or None
        summary = (request.form.get("summary") or "").strip() or None
        cover_image = (request.form.get("cover_image") or "").strip() or None
        cta_label = (request.form.get("cta_label") or "").strip() or "View Plan"
        cta_url = (request.form.get("cta_url") or "").strip()
        order = int(request.form.get("order") or 0)
        active = request.form.get("active") == "on"

        if not title:
            flash("Title is required.", "danger")
            return render_template(
                "admin_home_plan_form.html",
                data=request.form,
                edit=True,
                _id=id,
            )

        if not cta_url:
            flash("Primary button URL is required.", "danger")
            return render_template(
                "admin_home_plan_form.html",
                data=request.form,
                edit=True,
                _id=id,
            )

        existing = db.home_plans.find_one({"slug": slug, "_id": {"$ne": ObjectId(id)}})
        if existing:
            flash(f"Slug '{slug}' already exists.", "danger")
            return render_template(
                "admin_home_plan_form.html",
                data=request.form,
                edit=True,
                _id=id,
            )

        update = {
            "title": title,
            "slug": slug,
            "category": category,
            "duration_label": duration_label,
            "summary": summary,
            "cover_image": cover_image,
            "cta_label": cta_label,
            "cta_url": cta_url,
            "order": order,
            "active": active,
        }

        try:
            db.home_plans.update_one({"_id": ObjectId(id)}, {"$set": update})
            flash("Home plan updated.", "success")
            return redirect(url_for("admin_home_plans"))
        except Exception as exc:
            flash(f"Error: {exc}", "danger")

    return render_template("admin_home_plan_form.html", data=dict(p), edit=True, _id=id)


@app.route("/admin/home-plans/<id>/toggle", methods=["POST"])
@login_required
def admin_home_plan_toggle(id):
    p = db.home_plans.find_one({"_id": ObjectId(id)})
    if not p:
        abort(404)
    db.home_plans.update_one({"_id": p["_id"]}, {"$set": {"active": not p.get("active", True)}})
    flash("Home plan updated.", "success")
    return redirect(url_for("admin_home_plans"))


@app.route("/admin/home-plans/<id>/delete", methods=["POST"])
@login_required
def admin_home_plan_delete(id):
    db.home_plans.delete_one({"_id": ObjectId(id)})
    flash("Home plan deleted.", "success")
    return redirect(url_for("admin_home_plans"))


# -----------------------------------------------------------------------------
# Admin: Programs
# -----------------------------------------------------------------------------
@app.route("/admin/programs")
@login_required
def admin_programs():
    programs = list(
        db.programs.find().sort(
            [("active", -1), ("show_on_home", -1), ("order", 1), ("created_at", -1)]
        )
    )
    return render_template("admin_programs.html", programs=programs)


@app.route("/admin/programs/new", methods=["GET", "POST"])
@login_required
def admin_program_new():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        slug = (request.form.get("slug") or "").strip() or slugify(title)
        category = (request.form.get("category") or "").strip() or None
        duration_label = (request.form.get("duration_label") or "").strip() or None
        summary = (request.form.get("summary") or "").strip() or None
        cover_image = (request.form.get("cover_image") or "").strip() or None
        order = int(request.form.get("order") or 0)
        active = request.form.get("active") == "on"
        show_on_home = request.form.get("show_on_home") == "on"
        kind = (request.form.get("kind") or "").strip().lower() or "hub"
        if kind not in ("hub", "track"):
            kind = "hub"
        hub_slug = (request.form.get("hub_slug") or "").strip() or None
        track_level = (request.form.get("track_level") or "").strip() or None

        if kind != "track":
            hub_slug = None
            track_level = None

        if not title:
            flash("Title is required.", "danger")
            return render_template("admin_program_form.html", data=request.form, edit=False)

        if db.programs.find_one({"slug": slug}):
            flash(f"Slug '{slug}' already exists.", "danger")
            return render_template("admin_program_form.html", data=request.form, edit=False)

        doc = {
            "title": title,
            "slug": slug,
            "kind": kind,
            "hub_slug": hub_slug,
            "track_level": track_level,
            "category": category,
            "duration_label": duration_label,
            "summary": summary,
            "cover_image": cover_image,
            "order": order,
            "active": active,
            "show_on_home": show_on_home,
            "created_at": datetime.datetime.utcnow(),
        }

        try:
            db.programs.insert_one(doc)
            flash("Program created.", "success")
            return redirect(url_for("admin_programs"))
        except Exception as exc:
            flash(f"Error: {exc}", "danger")

    return render_template("admin_program_form.html", data={}, edit=False)


@app.route("/admin/programs/<id>/edit", methods=["GET", "POST"])
@login_required
def admin_program_edit(id):
    p = db.programs.find_one({"_id": ObjectId(id)})
    if not p:
        abort(404)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        slug = (request.form.get("slug") or "").strip() or slugify(title)
        category = (request.form.get("category") or "").strip() or None
        duration_label = (request.form.get("duration_label") or "").strip() or None
        summary = (request.form.get("summary") or "").strip() or None
        cover_image = (request.form.get("cover_image") or "").strip() or None
        order = int(request.form.get("order") or 0)
        active = request.form.get("active") == "on"
        show_on_home = request.form.get("show_on_home") == "on"
        kind = (request.form.get("kind") or p.get("kind") or "hub").strip().lower()
        if kind not in ("hub", "track"):
            kind = "hub"
        hub_slug = (request.form.get("hub_slug") or "").strip() or None
        track_level = (request.form.get("track_level") or "").strip() or None

        if kind != "track":
            hub_slug = None
            track_level = None

        if not title:
            flash("Title is required.", "danger")
            return render_template("admin_program_form.html", data=request.form, edit=True, _id=id)

        existing = db.programs.find_one({"slug": slug, "_id": {"$ne": ObjectId(id)}})
        if existing:
            flash(f"Slug '{slug}' already exists.", "danger")
            return render_template("admin_program_form.html", data=request.form, edit=True, _id=id)

        update = {
            "title": title,
            "slug": slug,
            "kind": kind,
            "hub_slug": hub_slug,
            "track_level": track_level,
            "category": category,
            "duration_label": duration_label,
            "summary": summary,
            "cover_image": cover_image,
            "order": order,
            "active": active,
            "show_on_home": show_on_home,
        }

        try:
            db.programs.update_one({"_id": ObjectId(id)}, {"$set": update})
            flash("Program updated.", "success")
            return redirect(url_for("admin_programs"))
        except Exception as exc:
            flash(f"Error: {exc}", "danger")

    return render_template("admin_program_form.html", data=dict(p), edit=True, _id=id)


@app.route("/admin/programs/<id>/copy-structure")
@login_required
def admin_program_copy_structure(id):
    source_program = db.programs.find_one({"_id": _object_id_or_404(id)})
    if not source_program:
        abort(404)

    programs = list(
        db.programs.find({"_id": {"$ne": source_program["_id"]}}, {"title": 1, "slug": 1, "kind": 1, "track_level": 1})
        .sort([("title", ASCENDING)])
    )
    source_weeks = list(
        db.program_weeks.find({"program_id": source_program["_id"]}).sort(
            [("week_number", 1), ("order", 1)]
        )
    )
    source_week_ids = [week["_id"] for week in source_weeks]
    item_count = db.program_items.count_documents({"week_id": {"$in": source_week_ids}}) if source_week_ids else 0

    return render_template(
        "admin_program_copy_structure.html",
        source_program=source_program,
        programs=programs,
        source_weeks=source_weeks,
        item_count=item_count,
    )


@app.route("/admin/programs/<id>/copy-structure", methods=["POST"])
@login_required
def admin_program_copy_structure_submit(id):
    source_program = db.programs.find_one({"_id": _object_id_or_404(id)})
    if not source_program:
        abort(404)

    target_program_id = (request.form.get("target_program_id") or "").strip()
    target_program_oid = _object_id_or_none(target_program_id)
    target_program = db.programs.find_one({"_id": target_program_oid}) if target_program_oid else None
    if not target_program:
        flash("Choose a valid target program.", "danger")
        return redirect(url_for("admin_program_copy_structure", id=id))

    if target_program["_id"] == source_program["_id"]:
        flash("A program cannot be copied into itself.", "danger")
        return redirect(url_for("admin_program_copy_structure", id=id))

    source_weeks = list(
        db.program_weeks.find({"program_id": source_program["_id"]}).sort(
            [("week_number", 1), ("order", 1)]
        )
    )
    if not source_weeks:
        flash("Source program has no weeks to copy.", "warning")
        return redirect(url_for("admin_program_copy_structure", id=id))

    source_week_numbers = [week.get("week_number") for week in source_weeks]
    conflicts = list(
        db.program_weeks.find(
            {"program_id": target_program["_id"], "week_number": {"$in": source_week_numbers}},
            {"week_number": 1},
        ).sort([("week_number", 1)])
    )
    if conflicts:
        conflict_numbers = ", ".join(str(week.get("week_number")) for week in conflicts)
        flash(
            f"Copy blocked. Target program already has week number(s): {conflict_numbers}.",
            "danger",
        )
        return redirect(url_for("admin_program_copy_structure", id=id))

    copy_items = request.form.get("copy_items") == "on"
    now = datetime.datetime.utcnow()
    week_id_map = {}
    copied_weeks = []

    for week in source_weeks:
        doc = {
            "program_id": target_program["_id"],
            "week_number": week.get("week_number"),
            "title": week.get("title"),
            "description": week.get("description"),
            "order": week.get("order", week.get("week_number") or 0),
            "created_at": now,
            "updated_at": now,
        }
        inserted = db.program_weeks.insert_one(doc)
        week_id_map[week["_id"]] = inserted.inserted_id
        copied_weeks.append(doc)

    copied_item_count = 0
    if copy_items and week_id_map:
        source_items = list(
            db.program_items.find({"week_id": {"$in": list(week_id_map.keys())}}).sort(
                [("week_id", 1), ("day_number", 1), ("order", 1), ("created_at", 1)]
            )
        )
        copy_fields = [
            "day_number",
            "day_label",
            "split_type",
            "workout_id",
            "workout_name",
            "sets",
            "reps",
            "duration",
            "notes",
            "order",
        ]
        copied_items = []
        for item in source_items:
            new_week_id = week_id_map.get(item.get("week_id"))
            if not new_week_id:
                continue
            copied = {field: item.get(field) for field in copy_fields if field in item}
            copied.update(
                {
                    "program_id": target_program["_id"],
                    "week_id": new_week_id,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            copied_items.append(copied)

        if copied_items:
            db.program_items.insert_many(copied_items)
            copied_item_count = len(copied_items)

    flash(
        f"Copied {len(copied_weeks)} week{'s' if len(copied_weeks) != 1 else ''}"
        f"{' and ' + str(copied_item_count) + ' item' + ('s' if copied_item_count != 1 else '') if copy_items else ''}"
        f" into {target_program.get('title')}.",
        "success",
    )
    return redirect(url_for("admin_program_weeks", program_id=str(target_program["_id"])))


@app.route("/admin/programs/<id>/toggle-active", methods=["POST"])
@login_required
def admin_program_toggle_active(id):
    p = db.programs.find_one({"_id": ObjectId(id)})
    if not p:
        abort(404)
    db.programs.update_one({"_id": p["_id"]}, {"$set": {"active": not p.get("active", True)}})
    flash("Program updated.", "success")
    return redirect(url_for("admin_programs"))


@app.route("/admin/programs/<id>/toggle-home", methods=["POST"])
@login_required
def admin_program_toggle_home(id):
    p = db.programs.find_one({"_id": ObjectId(id)})
    if not p:
        abort(404)
    db.programs.update_one(
        {"_id": p["_id"]},
        {"$set": {"show_on_home": not p.get("show_on_home", False)}},
    )
    flash("Program updated.", "success")
    return redirect(url_for("admin_programs"))


@app.route("/admin/programs/<id>/delete", methods=["POST"])
@login_required
def admin_program_delete(id):
    prog = db.programs.find_one({"_id": ObjectId(id)})
    if not prog:
        abort(404)

    weeks = list(db.program_weeks.find({"program_id": prog["_id"]}, {"_id": 1}))
    week_ids = [w["_id"] for w in weeks]

    if week_ids:
        db.program_items.delete_many({"week_id": {"$in": week_ids}})
        db.program_weeks.delete_many({"_id": {"$in": week_ids}})

    db.programs.delete_one({"_id": prog["_id"]})

    flash("Program deleted.", "success")
    return redirect(url_for("admin_programs"))


# -----------------------------------------------------------------------------
# Admin: Program Weeks
# -----------------------------------------------------------------------------
@app.route("/admin/programs/<program_id>/weeks")
@login_required
def admin_program_weeks(program_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    weeks = list(
        db.program_weeks.find({"program_id": program["_id"]}).sort(
            [("week_number", 1), ("order", 1)]
        )
    )
    item_counts = {
        week["_id"]: db.program_items.count_documents({"week_id": week["_id"]}) for week in weeks
    }
    return render_template(
        "admin_program_weeks.html",
        program=program,
        weeks=weeks,
        item_counts=item_counts,
    )


@app.route("/admin/programs/<program_id>/weeks/new", methods=["POST"])
@login_required
def admin_program_week_new(program_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week_number = int(request.form.get("week_number") or 0)
    title = (request.form.get("title") or "").strip() or None
    order = int(request.form.get("order") or week_number)

    if week_number < 1:
        flash("Week number must be at least 1.", "danger")
        return redirect(url_for("admin_program_weeks", program_id=program_id))

    existing = db.program_weeks.find_one({"program_id": program["_id"], "week_number": week_number})
    if existing:
        flash(f"Week {week_number} already exists.", "danger")
        return redirect(url_for("admin_program_weeks", program_id=program_id))

    db.program_weeks.insert_one(
        {
            "program_id": program["_id"],
            "week_number": week_number,
            "title": title,
            "order": order,
            "created_at": datetime.datetime.utcnow(),
        }
    )

    flash(f"Week {week_number} created.", "success")
    return redirect(url_for("admin_program_weeks", program_id=program_id))


@app.route("/admin/programs/<program_id>/weeks/<week_id>/delete", methods=["POST"])
@login_required
def admin_program_week_delete(program_id, week_id):
    program, week = _get_program_and_week_or_404(program_id, week_id)

    db.program_items.delete_many({"week_id": week["_id"]})
    db.program_weeks.delete_one({"_id": week["_id"]})

    flash("Week deleted.", "success")
    return redirect(url_for("admin_program_weeks", program_id=program_id))


@app.route("/admin/programs/<program_id>/weeks/<week_id>/duplicate")
@login_required
def admin_program_week_duplicate(program_id, week_id):
    program, week = _get_program_and_week_or_404(program_id, week_id)
    programs = list(
        db.programs.find({}, {"title": 1, "slug": 1, "kind": 1, "track_level": 1}).sort(
            [("title", ASCENDING)]
        )
    )
    item_count = db.program_items.count_documents({"week_id": week["_id"]})
    return render_template(
        "admin_program_week_duplicate.html",
        program=program,
        week=week,
        programs=programs,
        item_count=item_count,
    )


@app.route("/admin/programs/<program_id>/weeks/<week_id>/duplicate", methods=["POST"])
@login_required
def admin_program_week_duplicate_submit(program_id, week_id):
    program, week = _get_program_and_week_or_404(program_id, week_id)

    target_program_id = (request.form.get("target_program_id") or "").strip()
    target_program_oid = _object_id_or_none(target_program_id)
    target_program = db.programs.find_one({"_id": target_program_oid}) if target_program_oid else None
    if not target_program:
        flash("Choose a valid target program.", "danger")
        return redirect(url_for("admin_program_week_duplicate", program_id=program_id, week_id=week_id))

    target_week_number = _form_int("target_week_number", default=0, min_value=0)
    if target_week_number < 1:
        flash("Target week number must be at least 1.", "danger")
        return redirect(url_for("admin_program_week_duplicate", program_id=program_id, week_id=week_id))

    existing = db.program_weeks.find_one(
        {"program_id": target_program["_id"], "week_number": target_week_number}
    )
    if existing:
        flash(
            f"Week {target_week_number} already exists in {target_program.get('title')}. "
            "Duplicate was blocked to avoid overwriting data.",
            "danger",
        )
        return redirect(url_for("admin_program_week_duplicate", program_id=program_id, week_id=week_id))

    now = datetime.datetime.utcnow()
    new_week = {
        "program_id": target_program["_id"],
        "week_number": target_week_number,
        "title": week.get("title"),
        "description": week.get("description"),
        "order": _form_int("target_order", default=target_week_number, min_value=0),
        "created_at": now,
        "updated_at": now,
    }
    inserted = db.program_weeks.insert_one(new_week)
    new_week_id = inserted.inserted_id

    source_items = list(
        db.program_items.find({"week_id": week["_id"]}).sort(
            [("day_number", 1), ("order", 1), ("created_at", 1)]
        )
    )
    copied_items = []
    copy_fields = [
        "day_number",
        "day_label",
        "split_type",
        "workout_id",
        "workout_name",
        "sets",
        "reps",
        "duration",
        "notes",
        "order",
    ]
    for item in source_items:
        copied = {field: item.get(field) for field in copy_fields if field in item}
        copied.update(
            {
                "program_id": target_program["_id"],
                "week_id": new_week_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        copied_items.append(copied)

    if copied_items:
        db.program_items.insert_many(copied_items)

    flash(
        f"Duplicated Week {week.get('week_number')} to Week {target_week_number} "
        f"with {len(copied_items)} item{'s' if len(copied_items) != 1 else ''}.",
        "success",
    )
    return redirect(url_for("admin_program_weeks", program_id=str(target_program["_id"])))


# -----------------------------------------------------------------------------
# Admin: Program Week Items
# -----------------------------------------------------------------------------
@app.route("/admin/programs/<program_id>/weeks/<week_id>/items")
@login_required
def admin_program_week_items(program_id, week_id):
    program, week = _get_program_and_week_or_404(program_id, week_id)
    items = list(
        db.program_items.find({"program_id": program["_id"], "week_id": week["_id"]}).sort(
            [("day_number", 1), ("order", 1), ("created_at", 1)]
        )
    )
    workouts = list(db.workouts.find({}, {"name": 1, "slug": 1, "level": 1, "style": 1}).sort([("name", ASCENDING)]))
    return render_template(
        "admin_program_week_items.html",
        program=program,
        week=week,
        items=items,
        workouts=workouts,
        split_types=PROGRAM_SPLIT_TYPES,
        edit_item=None,
    )


@app.route("/admin/programs/<program_id>/weeks/<week_id>/items/new", methods=["POST"])
@login_required
def admin_program_week_item_new(program_id, week_id):
    program, week = _get_program_and_week_or_404(program_id, week_id)
    workout_id = (request.form.get("workout_id") or "").strip()
    workout_oid = _object_id_or_none(workout_id)
    workout = db.workouts.find_one({"_id": workout_oid}) if workout_oid else None
    if not workout:
        flash("Choose a workout from the workout database.", "danger")
        return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))

    split_type = (request.form.get("split_type") or "").strip().lower()
    if split_type not in PROGRAM_SPLIT_TYPES:
        split_type = None

    now = datetime.datetime.utcnow()
    doc = {
        "program_id": program["_id"],
        "week_id": week["_id"],
        "day_number": _form_int("day_number", default=1, min_value=1),
        "day_label": (request.form.get("day_label") or "").strip() or None,
        "split_type": split_type,
        "workout_id": workout["_id"],
        "workout_name": workout.get("name"),
        "sets": (request.form.get("sets") or "").strip() or None,
        "reps": (request.form.get("reps") or "").strip() or None,
        "duration": (request.form.get("duration") or "").strip() or None,
        "notes": (request.form.get("notes") or "").strip() or None,
        "order": _form_int("order", default=0, min_value=0),
        "created_at": now,
        "updated_at": now,
    }
    db.program_items.insert_one(doc)
    flash("Workout item added.", "success")
    return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))


@app.route("/admin/programs/<program_id>/weeks/<week_id>/items/<item_id>/edit", methods=["GET", "POST"])
@login_required
def admin_program_week_item_edit(program_id, week_id, item_id):
    program, week = _get_program_and_week_or_404(program_id, week_id)
    item_oid = _object_id_or_404(item_id)
    item = db.program_items.find_one({"_id": item_oid, "program_id": program["_id"], "week_id": week["_id"]})
    if not item:
        abort(404)

    if request.method == "POST":
        workout_id = (request.form.get("workout_id") or "").strip()
        workout_oid = _object_id_or_none(workout_id)
        workout = db.workouts.find_one({"_id": workout_oid}) if workout_oid else None
        if not workout:
            flash("Choose a workout from the workout database.", "danger")
            return redirect(
                url_for(
                    "admin_program_week_item_edit",
                    program_id=program_id,
                    week_id=week_id,
                    item_id=item_id,
                )
            )

        split_type = (request.form.get("split_type") or "").strip().lower()
        if split_type not in PROGRAM_SPLIT_TYPES:
            split_type = None

        update = {
            "day_number": _form_int("day_number", default=1, min_value=1),
            "day_label": (request.form.get("day_label") or "").strip() or None,
            "split_type": split_type,
            "workout_id": workout["_id"],
            "workout_name": workout.get("name"),
            "sets": (request.form.get("sets") or "").strip() or None,
            "reps": (request.form.get("reps") or "").strip() or None,
            "duration": (request.form.get("duration") or "").strip() or None,
            "notes": (request.form.get("notes") or "").strip() or None,
            "order": _form_int("order", default=0, min_value=0),
            "updated_at": datetime.datetime.utcnow(),
        }
        db.program_items.update_one({"_id": item["_id"]}, {"$set": update})
        flash("Workout item updated.", "success")
        return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))

    items = list(
        db.program_items.find({"program_id": program["_id"], "week_id": week["_id"]}).sort(
            [("day_number", 1), ("order", 1), ("created_at", 1)]
        )
    )
    workouts = list(db.workouts.find({}, {"name": 1, "slug": 1, "level": 1, "style": 1}).sort([("name", ASCENDING)]))
    return render_template(
        "admin_program_week_items.html",
        program=program,
        week=week,
        items=items,
        workouts=workouts,
        split_types=PROGRAM_SPLIT_TYPES,
        edit_item=item,
    )


@app.route("/admin/programs/<program_id>/weeks/<week_id>/items/<item_id>/delete", methods=["POST"])
@login_required
def admin_program_week_item_delete(program_id, week_id, item_id):
    program, week = _get_program_and_week_or_404(program_id, week_id)
    result = db.program_items.delete_one(
        {
            "_id": _object_id_or_404(item_id),
            "program_id": program["_id"],
            "week_id": week["_id"],
        }
    )
    flash("Workout item deleted." if result.deleted_count else "Workout item not found.", "success")
    return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))


# -----------------------------------------------------------------------------
# Errors & health
# -----------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    app.logger.warning("404: %s %s", request.method, request.path)
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    app.logger.exception("500 on %s %s", request.method, request.path)
    return render_template("500.html"), 500


@app.route("/healthz")
def healthz():
    try:
        client.admin.command("ping")
        return {"status": "ok", "mongo": "up"}, 200
    except Exception as e:
        app.logger.warning("Healthz DB ping failed: %s", e)
        return {"status": "ok", "mongo": "down"}, 200


# -----------------------------------------------------------------------------
# Main (dev only)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
