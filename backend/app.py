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
from typing import Dict, List, Optional, Tuple

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
    url_for,
)
from flask_login import LoginManager, UserMixin, login_required, login_user, logout_user
from flask_login import current_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from jinja2 import TemplateNotFound
from pymongo import ASCENDING, MongoClient
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# -----------------------------------------------------------------------------
# Config / DB
# -----------------------------------------------------------------------------
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_DIR = os.path.dirname(__file__)

# In local development, prefer values from checked-in/local dotenv files.
# In hosted envs, keep platform-provided environment vars as source of truth.
DOTENV_OVERRIDE = not bool(os.getenv("RENDER"))
for _dotenv_path in (os.path.join(ROOT_DIR, ".env"), os.path.join(BACKEND_DIR, ".env")):
    if os.path.exists(_dotenv_path):
        load_dotenv(_dotenv_path, override=DOTENV_OVERRIDE)

MONGO_URI = os.environ.get("MONGO_URI") or "mongodb://localhost:27017/NFG"
MONGO_DB = os.environ.get("MONGO_DB")  # optional override to align with seed.py
MONGO_URI_LOCAL = os.environ.get("MONGO_URI_LOCAL") or "mongodb://localhost:27017/NFG"
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")

# Upload/media configuration (Render Disk ready)
MEDIA_ROOT = os.getenv("MEDIA_ROOT", "").strip()
MEDIA_URL = (os.getenv("MEDIA_URL") or "/media/").strip() or "/media/"
UPLOAD_FOLDER_LEGACY = os.getenv("UPLOAD_FOLDER", "static/uploads").strip()

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Safer cookies in hosted envs (Render sets RENDER=true)
if os.getenv("RENDER"):
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

def _resolve_db_for_client(mongo_client: MongoClient, mongo_uri: str):
    """Pick DB name from override, URI default, or fallback to NFG."""
    if MONGO_DB:
        return mongo_client[MONGO_DB]

    uri_tail = mongo_uri.split("://", 1)[-1]
    has_db_in_uri = "/" in uri_tail
    if has_db_in_uri:
        try:
            return mongo_client.get_default_database()
        except Exception:
            return mongo_client["NFG"]
    return mongo_client["NFG"]


def _resolve_client_and_db() -> Tuple[MongoClient, object, str, Optional[Exception]]:
    candidates = [MONGO_URI]
    if "://mongo" in MONGO_URI and MONGO_URI_LOCAL not in candidates:
        candidates.append(MONGO_URI_LOCAL)

    last_error: Optional[Exception] = None
    for uri in candidates:
        c = MongoClient(uri, serverSelectionTimeoutMS=1500)
        try:
            c.admin.command("ping")
            return c, _resolve_db_for_client(c, uri), uri, None
        except Exception as e:
            last_error = e

    # Keep the primary URI as final client even if ping failed; routes can still
    # return useful errors and health checks will surface DB status.
    fallback_client = MongoClient(MONGO_URI)
    return (
        fallback_client,
        _resolve_db_for_client(fallback_client, MONGO_URI),
        MONGO_URI,
        last_error,
    )


client, db, ACTIVE_MONGO_URI, MONGO_CONNECT_ERROR = _resolve_client_and_db()

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
app.logger.info("Using Mongo at: %s (db=%s)", ACTIVE_MONGO_URI, db.name)
if MONGO_CONNECT_ERROR:
    app.logger.warning("Mongo ping failed on startup: %s", MONGO_CONNECT_ERROR)

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

    # Public URL
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


@app.before_request
def _ensure_viewer_id():
    viewer_id = (request.cookies.get("nfg_vid") or "").strip()
    if not viewer_id:
        viewer_id = uuid.uuid4().hex
        g._set_viewer_id = viewer_id
    g.viewer_id = viewer_id


@app.before_request
def _guard_admin_pages():
    if not request.path.startswith("/admin"):
        return None

    if not getattr(current_user, "is_authenticated", False):
        return login_manager.unauthorized()

    if not getattr(current_user, "is_admin", False):
        abort(403)
    return None


@app.after_request
def _log_request(resp):
    try:
        dt = (perf_counter() - getattr(g, "_t0", perf_counter())) * 1000.0

        # Long caching for media routes
        if MEDIA_ROOT and request.path.startswith(MEDIA_URL.rstrip("/") + "/"):
            resp.headers.setdefault(
                "Cache-Control",
                "public, max-age=31536000, immutable",
            )

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

    viewer_to_set = getattr(g, "_set_viewer_id", None)
    if viewer_to_set:
        resp.set_cookie(
            "nfg_vid",
            viewer_to_set,
            max_age=60 * 60 * 24 * 365 * 2,  # 2 years
            samesite="Lax",
            secure=bool(os.getenv("RENDER")),
            httponly=True,
        )
    return resp


csrf = CSRFProtect(app)

# -----------------------------------------------------------------------------
# Auth (admin + members)
# -----------------------------------------------------------------------------
ADMIN_USERNAME = (os.getenv("ADMIN_USERNAME", "admin") or "admin").strip()
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD", "changeme") or "").strip()
ADMIN_PASSWORD_HASH = (os.getenv("ADMIN_PASSWORD_HASH", "") or "").strip().strip('"').strip("'")

# Support Docker env_file escaping styles:
# - raw hash: scrypt:...$salt$digest
# - escaped hash for Compose files: scrypt:...$$salt$$digest
if "$$" in ADMIN_PASSWORD_HASH:
    ADMIN_PASSWORD_HASH = ADMIN_PASSWORD_HASH.replace("$$", "$")

if ADMIN_PASSWORD_HASH and ADMIN_PASSWORD_HASH.count("$") < 2:
    app.logger.warning(
        "ADMIN_PASSWORD_HASH appears malformed/truncated (contains %s '$'). "
        "If using Docker env_file, quote the hash value in .env to avoid interpolation issues.",
        ADMIN_PASSWORD_HASH.count("$"),
    )

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to access Admin."
login_manager.login_message_category = "warning"


class User(UserMixin):
    def __init__(
        self,
        user_id: str,
        role: str = "member",
        user_oid: Optional[str] = None,
        username: Optional[str] = None,
    ):
        self.id = user_id
        self.role = role
        self.user_oid = user_oid
        self.username = username

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_member(self) -> bool:
        return self.role == "member"


@login_manager.user_loader
def load_user(user_id):
    if user_id == "admin":
        return User("admin", role="admin")

    if (user_id or "").startswith("member:"):
        oid = user_id.split(":", 1)[1]
        try:
            doc = db.users.find_one({"_id": ObjectId(oid), "active": {"$ne": False}})
        except Exception:
            doc = None
        if doc:
            return User(
                user_id,
                role="member",
                user_oid=str(doc.get("_id")),
                username=doc.get("username"),
            )
    return None


def _check_admin_credentials(username: str, password: str) -> bool:
    submitted_username = (username or "").strip()
    submitted_password = password or ""

    if not submitted_username or not submitted_password:
        return False

    if submitted_username.lower() != ADMIN_USERNAME.lower():
        return False

    if ADMIN_PASSWORD_HASH:
        try:
            if check_password_hash(ADMIN_PASSWORD_HASH, submitted_password):
                return True
        except Exception:
            app.logger.warning("Invalid ADMIN_PASSWORD_HASH format. Falling back to plaintext admin password.")

    if ADMIN_PASSWORD:
        return submitted_password == ADMIN_PASSWORD
    return False


def _member_owner_key() -> Optional[str]:
    if not getattr(current_user, "is_authenticated", False):
        return None
    if getattr(current_user, "is_admin", False):
        return None
    oid = getattr(current_user, "user_oid", None)
    if not oid:
        return None
    return f"user:{oid}"


def _progress_owner_key() -> str:
    return _member_owner_key() or _viewer_id()


def _safe_next_url(default_endpoint: str = "home") -> str:
    nxt = (request.args.get("next") or request.form.get("next") or "").strip()
    if nxt.startswith("/"):
        return nxt
    return url_for(default_endpoint)


def _migrate_guest_state_to_member(guest_viewer_id: str, member_key: str) -> None:
    guest = (guest_viewer_id or "").strip()
    if not guest or guest == member_key:
        return

    guest_favs = list(db.program_favorites.find({"viewer_id": guest}))
    for row in guest_favs:
        slug = (row.get("program_slug") or "").strip()
        if not slug:
            continue
        db.program_favorites.update_one(
            {"viewer_id": member_key, "program_slug": slug},
            {"$setOnInsert": {"created_at": row.get("created_at") or datetime.datetime.utcnow()}},
            upsert=True,
        )
    db.program_favorites.delete_many({"viewer_id": guest})

    guest_progress = list(db.program_day_progress.find({"viewer_id": guest}))
    for row in guest_progress:
        track_slug = (row.get("track_slug") or "").strip()
        week_number = row.get("week_number")
        day_key = (row.get("day_key") or "").strip()
        if not track_slug or not day_key or not week_number:
            continue

        db.program_day_progress.update_one(
            {
                "viewer_id": member_key,
                "track_slug": track_slug,
                "week_number": week_number,
                "day_key": day_key,
            },
            {
                "$set": {
                    "completed_at": row.get("completed_at") or datetime.datetime.utcnow(),
                    "hub_slug": row.get("hub_slug"),
                    "level": row.get("level"),
                    "env": row.get("env"),
                }
            },
            upsert=True,
        )
    db.program_day_progress.delete_many({"viewer_id": guest})

    guest_week_progress = list(db.program_week_progress.find({"viewer_id": guest}))
    for row in guest_week_progress:
        track_slug = (row.get("track_slug") or "").strip()
        week_number = row.get("week_number")
        if not track_slug or not week_number:
            continue

        db.program_week_progress.update_one(
            {
                "viewer_id": member_key,
                "track_slug": track_slug,
                "week_number": week_number,
            },
            {
                "$set": {
                    "completed_at": row.get("completed_at") or datetime.datetime.utcnow(),
                    "hub_slug": row.get("hub_slug"),
                    "level": row.get("level"),
                    "env": row.get("env"),
                }
            },
            upsert=True,
        )
    db.program_week_progress.delete_many({"viewer_id": guest})


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
WORKOUT_MOVEMENT_PATTERNS = [
    "Push",
    "Pull",
    "Squat",
    "Hinge",
    "Lunge",
    "Core",
    "Carry",
    "Cardio",
    "Mobility",
    "Full Body",
    "Accessory",
]
WORKOUT_EQUIPMENT_TYPES = [
    "Bodyweight",
    "Dumbbell",
    "Barbell",
    "Kettlebell",
    "Machines",
    "Cable",
    "Bands",
    "TRX",
    "Cardio Machine",
    "Mixed",
]
WORKOUT_DIFFICULTY_TIERS = ["Easy", "Moderate", "Hard"]

# -----------------------------------------------------------------------------
# Program helpers (dynamic Hub -> Tracks)
# -----------------------------------------------------------------------------
DEFAULT_LEVELS = ["beginner", "intermediate", "advanced"]
DEFAULT_ENVS = ["home", "gym", "hybrid"]
DEFAULT_WEEK_DAY_ORDER = [
    "push",
    "pull",
    "legs",
    "upper",
    "lower",
    "full body",
    "cardio",
    "mobility",
    "rest",
]
DEFAULT_TRACK_DAY_SPLIT = ["Push", "Pull", "Legs", "Upper", "Lower", "Core"]

SAMPLE_DAY_WORKOUT_QUERIES = {
    "push": ["push up", "bench press", "shoulder press"],
    "pull": ["row", "pull up", "lat pulldown"],
    "legs": ["squat", "lunge", "romanian deadlift"],
    "upper": ["incline press", "row", "lateral raise"],
    "lower": ["squat", "deadlift", "leg press"],
    "core": ["plank", "dead bug", "hollow hold"],
    "full body": ["burpee", "thruster", "clean"],
    "cardio": ["run", "bike", "jump rope"],
    "mobility": ["mobility", "stretch", "flow"],
}


def _norm_choice(val: Optional[str]) -> str:
    return (val or "").strip().lower()


def _canonical_choice(raw: Optional[str], options: List[str]) -> str:
    v = _norm_choice(raw)
    if not v:
        return ""
    for opt in options:
        if _norm_choice(opt) == v:
            return opt
    return ""


def _primary_muscle_from_doc(w: dict) -> str:
    primary = _canonical_choice(w.get("primary_muscle"), BODY_PARTS_MASTER)
    if primary:
        return primary
    parts = w.get("body_parts") or ([w.get("body_part")] if w.get("body_part") else [])
    if parts:
        return _canonical_choice(parts[0], BODY_PARTS_MASTER)
    return ""


def _infer_movement_from_primary_muscle(primary_muscle: str) -> str:
    pm = _norm_choice(primary_muscle)
    if not pm:
        return ""
    push = {"chest", "shoulders", "triceps"}
    pull = {"back", "lats", "biceps", "forearms", "upper back"}
    squat = {"legs", "quads", "glutes", "calves", "hips"}
    hinge = {"hamstrings", "lower back"}
    core = {"core", "abs", "obliques"}
    if pm in push:
        return "Push"
    if pm in pull:
        return "Pull"
    if pm in squat:
        return "Squat"
    if pm in hinge:
        return "Hinge"
    if pm in core:
        return "Core"
    if pm == "full body":
        return "Full Body"
    return "Accessory"


def _infer_equipment_from_style(style: str) -> str:
    sty = _norm_choice(style)
    if sty in {"bodyweight", "calisthenics", "yoga/mobility", "plyometric/explosive"}:
        return "Bodyweight"
    if sty == "barbell":
        return "Barbell"
    if sty == "dumbbell":
        return "Dumbbell"
    if sty == "kettlebell":
        return "Kettlebell"
    if sty == "machines":
        return "Machines"
    if sty == "resistance bands":
        return "Bands"
    if sty == "cardio/endurance":
        return "Cardio Machine"
    if sty:
        return "Mixed"
    return ""


def _infer_difficulty_tier_from_level(level: str) -> str:
    lv = _norm_choice(level)
    if lv == "beginner":
        return "Easy"
    if lv == "intermediate":
        return "Moderate"
    if lv == "advanced":
        return "Hard"
    return ""


def _workout_metadata_from_form(form, fallback_doc: Optional[dict] = None) -> tuple:
    fallback_doc = fallback_doc or {}

    fallback_primary = _primary_muscle_from_doc(fallback_doc)
    fallback_movement = _infer_movement_from_primary_muscle(fallback_primary)
    fallback_equipment = _infer_equipment_from_style(fallback_doc.get("style"))
    fallback_tier = _infer_difficulty_tier_from_level(fallback_doc.get("level"))

    primary_muscle = _canonical_choice(
        form.get("primary_muscle") or fallback_primary,
        BODY_PARTS_MASTER,
    )
    movement_pattern = _canonical_choice(
        form.get("movement_pattern") or fallback_movement,
        WORKOUT_MOVEMENT_PATTERNS,
    )
    equipment = _canonical_choice(
        form.get("equipment") or fallback_equipment,
        WORKOUT_EQUIPMENT_TYPES,
    )
    difficulty_tier = _canonical_choice(
        form.get("difficulty_tier") or fallback_tier,
        WORKOUT_DIFFICULTY_TIERS,
    )

    errors = []
    if not primary_muscle:
        errors.append("Primary muscle is required.")
    if not movement_pattern:
        errors.append("Movement pattern is required.")
    if not equipment:
        errors.append("Equipment is required.")
    if not difficulty_tier:
        errors.append("Difficulty tier is required.")

    return (
        {
            "primary_muscle": primary_muscle,
            "movement_pattern": movement_pattern,
            "equipment": equipment,
            "difficulty_tier": difficulty_tier,
        },
        errors,
    )


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
    cursor = db.programs.find(
        {"kind": "track", "hub_slug": hub_slug, "active": {"$ne": False}}
    ).sort([("order", 1), ("created_at", -1)])
    return list(cursor)


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

        env = _track_env_value(t)
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
            and _track_env_value(t) == env
        ):
            return t

    for t in tracks:
        if _norm_choice(t.get("track_level")) == level:
            return t

    return None


def _normalize_week_day_label(val: Optional[str]) -> str:
    day = (val or "").strip()
    if not day:
        return ""

    key = re.sub(r"\s+", " ", day.lower())
    aliases = {
        "upper body": "Upper",
        "lower body": "Lower",
        "full-body": "Full Body",
        "hiit": "Cardio",
    }
    if key in aliases:
        return aliases[key]
    return " ".join(p.capitalize() for p in key.split(" "))


def _safe_int(raw, default: int = 0, min_value: Optional[int] = None, max_value: Optional[int] = None) -> int:
    try:
        val = int(str(raw).strip())
    except Exception:
        val = default
    if min_value is not None and val < min_value:
        val = min_value
    if max_value is not None and val > max_value:
        val = max_value
    return val


def _parse_day_split(raw: Optional[str]) -> List[str]:
    base = (raw or "").strip()
    if not base:
        return list(DEFAULT_TRACK_DAY_SPLIT)

    parts = re.split(r"[,|\n]+", base)
    out: List[str] = []
    seen = set()
    for p in parts:
        label = _normalize_week_day_label(p)
        if not label:
            continue
        key = _norm_choice(label)
        if key in seen:
            continue
        seen.add(key)
        out.append(label)
    return out or list(DEFAULT_TRACK_DAY_SPLIT)


def _track_env_value(track: dict) -> str:
    return (
        _norm_choice(track.get("track_env"))
        or _infer_env_from_slug(track.get("slug", ""))
        or _infer_env_from_slug(track.get("category", ""))
        or ""
    )


def _sample_workout_for_day(day_label: str) -> Optional[dict]:
    key = _norm_choice(day_label)
    queries = SAMPLE_DAY_WORKOUT_QUERIES.get(key, [])

    for q in queries:
        rx = Regex(q, "i")
        doc = db.workouts.find_one(
            {"$or": [{"name": rx}, {"slug": rx}]},
            {"name": 1, "slug": 1, "movement_pattern": 1, "primary_muscle": 1, "equipment": 1},
            sort=[("rating", -1), ("name", 1)],
        )
        if doc:
            return doc

    movement_map = {
        "push": "Push",
        "pull": "Pull",
        "legs": "Squat",
        "lower": "Squat",
        "upper": "Push",
        "core": "Core",
        "full body": "Full Body",
        "cardio": "Cardio",
        "mobility": "Mobility",
    }
    fallback_movement = movement_map.get(key)
    if fallback_movement:
        return db.workouts.find_one(
            {"movement_pattern": fallback_movement},
            {"name": 1, "slug": 1, "movement_pattern": 1, "primary_muscle": 1, "equipment": 1},
            sort=[("rating", -1), ("name", 1)],
        )
    return None


def _placeholder_day_groups_for_track(track: dict) -> tuple:
    split_raw = track.get("default_week_split")
    split = (
        _parse_day_split(", ".join(split_raw))
        if isinstance(split_raw, list)
        else _parse_day_split(split_raw if isinstance(split_raw, str) else "")
    )
    day_groups = []
    workout_map = {}

    for idx, label in enumerate(split):
        key = slugify(label) or f"day-{idx + 1}"
        sample = _sample_workout_for_day(label)

        item = {
            "day": label,
            "order": idx + 1,
            "sets": "3",
            "reps": "8-12",
            "rest": "60-90s",
            "notes": "Sample placeholder. Replace with your exact exercise flow in Admin.",
        }
        if sample:
            item["workout_id"] = sample.get("_id")
            workout_map[sample["_id"]] = sample
        else:
            item["custom_name"] = f"{label} Starter Movement"

        day_groups.append({"key": key, "label": label, "items": [item]})

    return day_groups, workout_map


def _workout_quality_issues(doc: dict) -> List[str]:
    issues: List[str] = []
    if not (doc.get("name") or "").strip():
        issues.append("Missing name")
    if not (doc.get("slug") or "").strip():
        issues.append("Missing slug")
    if not (doc.get("primary_muscle") or "").strip():
        issues.append("Missing primary muscle")
    if not (doc.get("movement_pattern") or "").strip():
        issues.append("Missing movement pattern")
    if not (doc.get("equipment") or "").strip():
        issues.append("Missing equipment")
    if not (doc.get("difficulty_tier") or "").strip():
        issues.append("Missing difficulty tier")
    if not doc.get("images"):
        issues.append("Missing exercise image")
    if not (doc.get("info") or "").strip():
        issues.append("Missing exercise info")
    if not doc.get("tips"):
        issues.append("Missing tips")
    return issues


def _workout_quality_summary(workouts: List[dict]) -> dict:
    total = len(workouts)
    with_issues = 0
    missing_images = 0
    missing_metadata = 0
    for w in workouts:
        issues = _workout_quality_issues(w)
        if issues:
            with_issues += 1
        if "Missing exercise image" in issues:
            missing_images += 1
        if any(
            i in issues
            for i in (
                "Missing primary muscle",
                "Missing movement pattern",
                "Missing equipment",
                "Missing difficulty tier",
            )
        ):
            missing_metadata += 1
    return {
        "total": total,
        "with_issues": with_issues,
        "quality_ok": max(0, total - with_issues),
        "missing_images": missing_images,
        "missing_metadata": missing_metadata,
    }


def _program_link_health_report(include_rows: bool = False) -> dict:
    programs = []
    weeks = []
    program_by_id = {}
    week_by_id = {}
    if include_rows:
        programs = list(db.programs.find({}, {"title": 1, "slug": 1}))
        weeks = list(db.program_weeks.find({}, {"program_id": 1, "week_number": 1, "title": 1}))
        program_by_id = {p["_id"]: p for p in programs if p.get("_id") is not None}
        week_by_id = {w["_id"]: w for w in weeks if w.get("_id") is not None}

    items = list(
        db.program_items.find(
            {},
            {
                "week_id": 1,
                "day": 1,
                "custom_name": 1,
                "workout_id": 1,
                "workout_slug": 1,
                "order": 1,
                "notes": 1,
            },
        )
    )

    workout_ids = [it.get("workout_id") for it in items if it.get("workout_id")]
    workouts = list(db.workouts.find({"_id": {"$in": workout_ids}}, {"_id": 1, "name": 1, "slug": 1}))
    workout_by_id = {w["_id"]: w for w in workouts if w.get("_id") is not None}

    broken_items = []
    unresolved_items = []
    linked_ok = 0
    custom_only = 0
    broken_count = 0
    unresolved_count = 0

    for it in items:
        week = week_by_id.get(it.get("week_id")) if include_rows else None
        program = program_by_id.get((week or {}).get("program_id")) if (include_rows and week) else None

        has_custom_text = bool(
            (it.get("custom_name") or "").strip()
            or (it.get("workout_name") or "").strip()
            or (it.get("notes") or "").strip()
        )
        wid = it.get("workout_id")
        linked_doc = workout_by_id.get(wid) if wid else None

        row = {
            "program": program,
            "week": week,
            "item": it,
            "workout": linked_doc,
        }

        if wid and linked_doc:
            linked_ok += 1
        elif wid and not linked_doc:
            broken_count += 1
            if include_rows:
                broken_items.append(row)
        elif has_custom_text:
            custom_only += 1
        else:
            unresolved_count += 1
            if include_rows:
                unresolved_items.append(row)

    return {
        "total_items": len(items),
        "linked_ok": linked_ok,
        "custom_only": custom_only,
        "broken_count": broken_count,
        "unresolved_count": unresolved_count,
        "broken_items": broken_items,
        "unresolved_items": unresolved_items,
    }


def _viewer_id() -> str:
    return (getattr(g, "viewer_id", "") or "").strip()


def _program_favorite_slugs_for_viewer(viewer_id: str) -> List[str]:
    if not viewer_id:
        return []

    rows = list(
        db.program_favorites.find({"viewer_id": viewer_id}, {"program_slug": 1}).sort(
            [("created_at", -1)]
        )
    )
    return [r.get("program_slug") for r in rows if r.get("program_slug")]


def _favorite_slug_set_for_request() -> set:
    if hasattr(g, "_program_favorite_slugs"):
        return g._program_favorite_slugs

    favs = set(_program_favorite_slugs_for_viewer(_progress_owner_key()))
    g._program_favorite_slugs = favs
    return favs


def _favorite_programs_for_viewer(viewer_id: str, limit: int = 6) -> List[dict]:
    slugs = _program_favorite_slugs_for_viewer(viewer_id)
    if not slugs:
        return []

    # Keep the user's favorite order (latest favorite first).
    want = slugs[: max(1, limit * 3)]
    found = list(
        db.programs.find(
            {"slug": {"$in": want}, "active": {"$ne": False}, "kind": "hub"}
        )
    )
    by_slug = {p.get("slug"): p for p in found if p.get("slug")}
    ordered = [by_slug[s] for s in want if s in by_slug]
    return ordered[:limit]


def _track_level_for_url(track: dict) -> str:
    return _norm_choice(track.get("track_level")) or "beginner"


def _track_env_for_url(track: dict) -> str:
    return _track_env_value(track) or "home"


def _ordered_week_numbers(weeks: List[dict]) -> List[int]:
    def _as_int(val) -> int:
        try:
            return int(val)
        except Exception:
            return 0

    ordered: List[int] = []
    seen = set()
    for w in sorted(
        weeks,
        key=lambda row: (_as_int(row.get("week_number")), _as_int(row.get("order"))),
    ):
        wn = _as_int(w.get("week_number"))
        if wn < 1 or wn in seen:
            continue
        seen.add(wn)
        ordered.append(wn)
    return ordered


def _day_key_from_program_item(item: dict, fallback_num: int) -> str:
    raw_day = (
        item.get("day")
        or item.get("day_label")
        or item.get("label")
        or item.get("title")
        or item.get("custom_name")
        or f"Day {fallback_num}"
    )
    return slugify(_normalize_week_day_label(raw_day) or f"Day {fallback_num}") or f"day-{fallback_num}"


def _week_day_keys_by_week(weeks: List[dict]) -> dict:
    week_numbers = _ordered_week_numbers(weeks)
    day_keys_by_week = {wn: [] for wn in week_numbers}

    week_id_to_number = {
        w.get("_id"): w.get("week_number")
        for w in weeks
        if w.get("_id") is not None and w.get("week_number") is not None
    }
    if not week_id_to_number:
        return day_keys_by_week

    items = list(
        db.program_items.find(
            {"week_id": {"$in": list(week_id_to_number.keys())}},
            {"week_id": 1, "day": 1, "day_label": 1, "label": 1, "title": 1, "custom_name": 1},
        ).sort([("order", 1), ("created_at", 1)])
    )

    seen_by_week = {wn: set() for wn in week_numbers}
    for i, it in enumerate(items):
        wn = week_id_to_number.get(it.get("week_id"))
        if wn is None:
            continue

        day_key = _day_key_from_program_item(it, i + 1)
        if day_key in seen_by_week.setdefault(wn, set()):
            continue
        seen_by_week[wn].add(day_key)
        day_keys_by_week.setdefault(wn, []).append(day_key)

    return day_keys_by_week


def _done_day_keys_by_week(owner_key: str, track_slug: str, week_numbers: List[int]) -> dict:
    done_by_week = {wn: set() for wn in week_numbers}
    if not owner_key or not track_slug or not week_numbers:
        return done_by_week

    done_rows = list(
        db.program_day_progress.find(
            {
                "viewer_id": owner_key,
                "track_slug": track_slug,
                "week_number": {"$in": week_numbers},
            },
            {"week_number": 1, "day_key": 1},
        )
    )
    for row in done_rows:
        wn = row.get("week_number")
        dk = row.get("day_key")
        if wn is None or not dk:
            continue
        done_by_week.setdefault(wn, set()).add(dk)
    return done_by_week


def _week_completion_map(owner_key: str, track_slug: str, week_numbers: List[int]) -> dict:
    completed_map = {wn: None for wn in week_numbers}
    if not owner_key or not track_slug or not week_numbers:
        return completed_map

    rows = list(
        db.program_week_progress.find(
            {
                "viewer_id": owner_key,
                "track_slug": track_slug,
                "week_number": {"$in": week_numbers},
            },
            {"week_number": 1, "completed_at": 1},
        )
    )
    for row in rows:
        wn = row.get("week_number")
        if wn in completed_map:
            completed_map[wn] = row.get("completed_at")
    return completed_map


def _week_progress_for_track(owner_key: str, track: dict, weeks: List[dict]) -> dict:
    week_numbers = _ordered_week_numbers(weeks)
    progress_map = {
        wn: {"done": 0, "total": 0, "all_done": False}
        for wn in week_numbers
        if wn is not None
    }
    if not week_numbers:
        return progress_map

    day_keys_by_week = _week_day_keys_by_week(weeks)
    done_by_week = _done_day_keys_by_week(owner_key, track.get("slug"), week_numbers)

    for wn in week_numbers:
        day_keys = day_keys_by_week.get(wn, [])
        total = len(day_keys)
        done = len(set(day_keys) & done_by_week.get(wn, set()))
        progress_map[wn] = {
            "done": done,
            "total": total,
            "all_done": bool(total) and done >= total,
        }

    return progress_map


def _week_unlock_map(weeks: List[dict], week_progress: dict) -> dict:
    week_numbers = _ordered_week_numbers(weeks)
    unlock_map = {}
    all_previous_complete = True

    for idx, wn in enumerate(week_numbers):
        unlock_map[wn] = idx == 0 or all_previous_complete

        pg = week_progress.get(wn) or {}
        total = int(pg.get("total") or 0)
        done = int(pg.get("done") or 0)
        complete_for_unlock = (total == 0) or (done >= total)
        all_previous_complete = all_previous_complete and complete_for_unlock

    return unlock_map


def _resume_target_for_track(owner_key: str, track: dict, weeks: List[dict], week_progress: dict) -> tuple:
    week_numbers = _ordered_week_numbers(weeks)
    if not owner_key or not track.get("slug") or not week_numbers:
        return None, ""

    unlock_map = _week_unlock_map(weeks, week_progress)
    day_keys_by_week = _week_day_keys_by_week(weeks)
    done_by_week = _done_day_keys_by_week(owner_key, track.get("slug"), week_numbers)

    # Preferred target: first incomplete day in the earliest unlocked week.
    for wn in week_numbers:
        if not unlock_map.get(wn, True):
            continue
        day_keys = day_keys_by_week.get(wn, [])
        if not day_keys:
            continue
        done_for_week = done_by_week.get(wn, set())
        for day_key in day_keys:
            if day_key not in done_for_week:
                return wn, day_key

    # Fallback target: most recent completed day in this track.
    last_row = db.program_day_progress.find_one(
        {"viewer_id": owner_key, "track_slug": track.get("slug")},
        sort=[("completed_at", -1)],
    )
    if last_row:
        wn = last_row.get("week_number")
        day_key = (last_row.get("day_key") or "").strip()
        if wn in week_numbers and unlock_map.get(wn, True):
            return wn, day_key

    # Last resort: first day of first unlocked week (or just first week).
    for wn in week_numbers:
        if not unlock_map.get(wn, True):
            continue
        day_keys = day_keys_by_week.get(wn, [])
        return wn, (day_keys[0] if day_keys else "")

    return week_numbers[0], ""


def _continue_plan_for_owner(owner_key: str) -> Optional[dict]:
    if not owner_key:
        return None

    row = db.program_day_progress.find_one(
        {"viewer_id": owner_key},
        sort=[("completed_at", -1)],
    )
    if not row:
        return None

    track_slug = (row.get("track_slug") or "").strip()
    week_number = row.get("week_number")
    day_key = (row.get("day_key") or "").strip()
    if not track_slug or not week_number:
        return None

    track = db.programs.find_one({"slug": track_slug, "active": {"$ne": False}})
    if not track or track.get("kind") != "track":
        return None

    hub_slug = track.get("hub_slug")
    if not hub_slug:
        return None

    weeks = list(
        db.program_weeks.find({"program_id": track["_id"]}).sort([("week_number", 1), ("order", 1)])
    )
    week_progress = _week_progress_for_track(owner_key, track, weeks)
    resume_week_number, resume_day_key = _resume_target_for_track(
        owner_key, track, weeks, week_progress
    )
    if not resume_week_number:
        resume_week_number = week_number
    if not resume_day_key:
        resume_day_key = day_key

    level = _track_level_for_url(track)
    env = _track_env_for_url(track)
    args = {
        "hub_slug": hub_slug,
        "week_number": resume_week_number,
        "level": level,
        "env": env,
    }
    if resume_day_key:
        args["day"] = resume_day_key

    return {
        "track_title": track.get("title") or "Program",
        "week_number": resume_week_number,
        "day_label": resume_day_key.replace("-", " ").title() if resume_day_key else None,
        "url": url_for("program_hub_week_detail", **args),
        "completed_at": row.get("completed_at"),
    }


# -----------------------------------------------------------------------------
# Indexes (safe to call repeatedly)
# -----------------------------------------------------------------------------
db.workouts.create_index([("slug", 1)], unique=True, sparse=True)
db.workouts.create_index([("name", 1)])
db.workouts.create_index([("level", 1)])
db.workouts.create_index([("body_part", 1)])
db.workouts.create_index([("style", 1)])
db.workouts.create_index([("primary_muscle", 1)])
db.workouts.create_index([("movement_pattern", 1)])
db.workouts.create_index([("equipment", 1)])
db.workouts.create_index([("difficulty_tier", 1)])
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
db.programs.create_index([("track_env", 1)])

db.program_weeks.create_index([("program_id", 1)])
db.program_weeks.create_index([("week_number", 1)])
db.program_weeks.create_index([("order", 1)])

db.program_items.create_index([("week_id", 1)])
db.program_items.create_index([("order", 1)])
db.program_items.create_index([("created_at", 1)])
db.program_items.create_index([("workout_id", 1)])
db.program_items.create_index([("workout_slug", 1)])
db.program_items.create_index([("week_id", 1), ("day", 1), ("order", 1)])

db.program_favorites.create_index([("viewer_id", 1), ("program_slug", 1)], unique=True)
db.program_favorites.create_index([("viewer_id", 1), ("created_at", -1)])

db.program_day_progress.create_index(
    [("viewer_id", 1), ("track_slug", 1), ("week_number", 1), ("day_key", 1)],
    unique=True,
)
db.program_day_progress.create_index([("viewer_id", 1), ("track_slug", 1), ("week_number", 1)])

db.program_week_progress.create_index(
    [("viewer_id", 1), ("track_slug", 1), ("week_number", 1)],
    unique=True,
)
db.program_week_progress.create_index([("viewer_id", 1), ("track_slug", 1), ("completed_at", -1)])

db.users.create_index([("username_lower", 1)], unique=True, sparse=True)
db.users.create_index([("email_lower", 1)], unique=True, sparse=True)
db.users.create_index([("created_at", -1)])


def get_styles() -> List[str]:
    cursor = db.styles.find({"active": {"$ne": False}}).sort([("order", 1), ("name", 1)])
    styles = list(cursor)

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
                    "summary": (
                        "Pick your level and training environment. "
                        "Follow the weekly plan and build momentum."
                    ),
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
                if not existing.get("track_env"):
                    updates["track_env"] = _norm_choice(env)
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
                    "track_env": _norm_choice(env),
                    "category": f"{level} • {env}",
                    "duration_label": "8 weeks",
                    "summary": (
                        "Week-by-week plan with exercise options. "
                        "Add weeks/workouts next in Admin."
                    ),
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

LEGACY_WORKOUT_FILTER_TO_SORT = {
    "favorites": "favorites",
    "recent": "recent",
    "top": "rating",
    "top-rated": "rating",
}

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
    return {
        "quick_options": QUICK_OPTIONS,
        "csrf_token": generate_csrf,
        "program_favorite_slugs": _favorite_slug_set_for_request(),
    }


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

        # Keep lines short for flake8
        html = (
            '<div style="max-width:820px;margin:40px auto;font-family:Arial,sans-serif;">'
            "<h1>Template missing</h1>"
            f"<p><b>{template_name}</b> was not found in your templates folder.</p>"
            "<p>Fix: create <code>backend/templates/"
            f"{template_name}"
            "</code> (or update the filename in app.py).</p>"
            "<hr/>"
            '<p><a href="/programs">Back to programs</a></p>'
            "</div>"
        )
        return render_template_string(html), 500
    except Exception:
        app.logger.exception("Template error while rendering: %s", template_name)
        html = (
            '<div style="max-width:820px;margin:40px auto;font-family:Arial,sans-serif;">'
            "<h1>Template error</h1>"
            f"<p>There is a rendering error in <b>{template_name}</b>.</p>"
            "<p>Check <code>instance/logs/app.log</code> for the exact traceback.</p>"
            "<hr/>"
            '<p><a href="/programs">Back to programs</a></p>'
            "</div>"
        )
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
    owner_key = _progress_owner_key()
    favorite_programs = _favorite_programs_for_viewer(owner_key, limit=6)
    continue_plan = _continue_plan_for_owner(owner_key)
    return render_template(
        "home.html",
        name="NFG",
        featured_programs=featured_programs,
        favorite_programs=favorite_programs,
        continue_plan=continue_plan,
    )


@app.route("/about")
def about_page():
    return render_template("about.html")


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


@app.route("/programs/<slug>/favorite", methods=["POST"])
def program_favorite_toggle(slug):
    program = db.programs.find_one({"slug": slug, "active": {"$ne": False}})
    if not program:
        abort(404)

    owner_key = _progress_owner_key()
    if not owner_key:
        return redirect(url_for("programs_index"))

    existing = db.program_favorites.find_one({"viewer_id": owner_key, "program_slug": slug})
    if existing:
        db.program_favorites.delete_one({"_id": existing["_id"]})
    else:
        db.program_favorites.insert_one(
            {
                "viewer_id": owner_key,
                "program_slug": slug,
                "created_at": datetime.datetime.utcnow(),
            }
        )

    next_url = (request.form.get("next") or "").strip()
    if not next_url.startswith("/"):
        next_url = url_for("programs_index")
    return redirect(next_url)


@app.route("/programs/<slug>")
def program_detail(slug):
    program = db.programs.find_one({"slug": slug, "active": {"$ne": False}})
    if not program:
        abort(404)

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
    return render_or_fallback("program_level.html", hub=hub, levels=levels)


@app.route("/programs/<hub_slug>/environment")
def program_hub_environment(hub_slug):
    hub = _get_hub_or_404(hub_slug)

    level = _norm_choice(request.args.get("level"))
    levels = _levels_for_hub(hub_slug)
    if level not in levels:
        return redirect(url_for("program_hub_level", hub_slug=hub_slug))

    envs = _envs_for_hub_level(hub_slug, level)
    return render_or_fallback("program_environment.html", hub=hub, level=level, envs=envs)


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

    track = _pick_track_for(hub_slug, level, env)
    if not track:
        flash(
            "That track isn't set up yet. Create a Track program in Admin for this Hub.",
            "warning",
        )
        return redirect(url_for("program_hub_environment", hub_slug=hub_slug, level=level))

    weeks = list(
        db.program_weeks.find({"program_id": track["_id"]}).sort([("week_number", 1), ("order", 1)])
    )
    if not weeks:
        n = _week_count_from_duration_label(
            track.get("duration_label") or hub.get("duration_label")
        )
        weeks = [{"week_number": i, "title": None} for i in range(1, n + 1)]

    owner_key = _progress_owner_key()
    week_progress = _week_progress_for_track(owner_key, track, weeks)
    week_unlock_map = _week_unlock_map(weeks, week_progress)
    week_numbers = _ordered_week_numbers(weeks)
    week_completed_at_map = _week_completion_map(owner_key, track.get("slug"), week_numbers)
    resume_week_number, resume_day_key = _resume_target_for_track(
        owner_key, track, weeks, week_progress
    )

    total_days = sum(v.get("total", 0) for v in week_progress.values())
    completed_days = sum(v.get("done", 0) for v in week_progress.values())
    completed_weeks = sum(1 for v in week_progress.values() if v.get("all_done"))

    return render_or_fallback(
        "program_weeks.html",
        track=track,
        level=level,
        env=env,
        weeks=weeks,
        week_progress=week_progress,
        week_unlock_map=week_unlock_map,
        week_completed_at_map=week_completed_at_map,
        resume_week_number=resume_week_number,
        resume_day_key=resume_day_key,
        total_days=total_days,
        completed_days=completed_days,
        completed_weeks=completed_weeks,
    )


@app.route("/programs/<hub_slug>/progress/reset", methods=["POST"])
def program_hub_reset_progress(hub_slug):
    _get_hub_or_404(hub_slug)

    level = _norm_choice(request.form.get("level")) or "beginner"
    env = _norm_choice(request.form.get("env")) or "home"

    levels = _levels_for_hub(hub_slug)
    if level not in levels:
        level = levels[0] if levels else "beginner"

    envs = _envs_for_hub_level(hub_slug, level)
    if env not in envs:
        env = envs[0] if envs else "home"

    track = _pick_track_for(hub_slug, level, env)
    if not track:
        abort(404)

    owner_key = _progress_owner_key()
    track_slug = track.get("slug")
    if owner_key and track_slug:
        db.program_day_progress.delete_many({"viewer_id": owner_key, "track_slug": track_slug})
        db.program_week_progress.delete_many({"viewer_id": owner_key, "track_slug": track_slug})
        flash("Progress reset for this program.", "success")
    else:
        flash("No progress found to reset.", "warning")

    return redirect(url_for("program_hub_weeks", hub_slug=hub_slug, level=level, env=env))


@app.route("/programs/<hub_slug>/week/<int:week_number>")
def program_hub_week_detail(hub_slug, week_number: int):
    hub = _get_hub_or_404(hub_slug)
    requested_day_key = slugify(request.args.get("day") or "")

    level = _norm_choice(request.args.get("level")) or "beginner"
    env = _norm_choice(request.args.get("env")) or "home"

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
    day_groups = []
    is_template_preview = False
    if week:
        items = list(
            db.program_items.find({"week_id": week["_id"]}).sort([("order", 1), ("created_at", 1)])
        )
        workout_ids = [it.get("workout_id") for it in items if it.get("workout_id")]
        workout_slugs = [it.get("workout_slug") for it in items if (it.get("workout_slug") or "").strip()]
        ws = []
        if workout_ids or workout_slugs:
            ws = list(
                db.workouts.find(
                    {
                        "$or": [
                            {"_id": {"$in": workout_ids}},
                            {"slug": {"$in": workout_slugs}},
                        ]
                    },
                    {"name": 1, "slug": 1},
                )
            )
        if ws:
            workout_map = {}
            for w in ws:
                if w.get("_id") is not None:
                    workout_map[w["_id"]] = w
                if w.get("slug"):
                    workout_map[w["slug"]] = w

        by_day = {}
        day_index = {}
        for i, it in enumerate(items):
            raw_day = (
                it.get("day")
                or it.get("day_label")
                or it.get("label")
                or it.get("title")
                or it.get("custom_name")
                or f"Day {i + 1}"
            )
            label = _normalize_week_day_label(raw_day) or f"Day {i + 1}"
            key = slugify(label) or f"day-{i + 1}"

            if key not in by_day:
                by_day[key] = {"key": key, "label": label, "items": []}
                day_index[key] = i
            by_day[key]["items"].append(it)

        # Keep first-seen order, but nudge common day labels into a predictable order.
        def _day_sort_key(k):
            label = (by_day[k]["label"] or "").lower()
            if label in DEFAULT_WEEK_DAY_ORDER:
                return (0, DEFAULT_WEEK_DAY_ORDER.index(label), day_index[k])
            return (1, day_index[k], label)

        day_groups = [by_day[k] for k in sorted(by_day.keys(), key=_day_sort_key)]

    if not day_groups:
        day_groups, preview_workout_map = _placeholder_day_groups_for_track(track)
        workout_map.update(preview_workout_map)
        is_template_preview = True

    weeks_in_track = list(
        db.program_weeks.find({"program_id": track["_id"]}, {"week_number": 1}).sort([("week_number", 1)])
    )
    week_numbers = [w.get("week_number") for w in weeks_in_track if w.get("week_number")]
    owner_key = _progress_owner_key()
    week_progress = _week_progress_for_track(owner_key, track, weeks_in_track)
    week_unlock_map = _week_unlock_map(weeks_in_track, week_progress)
    if week_unlock_map and not week_unlock_map.get(week_number, True):
        flash("Finish previous weeks before opening this week.", "warning")
        return redirect(url_for("program_hub_weeks", hub_slug=hub_slug, level=level, env=env))

    next_week_number = next((n for n in week_numbers if n > week_number), None)
    if not next_week_number:
        max_weeks = _week_count_from_duration_label(track.get("duration_label") or hub.get("duration_label"))
        if week_number < max_weeks:
            next_week_number = week_number + 1

    day_keys = [g.get("key") for g in day_groups if g.get("key")]
    viewer_id = owner_key
    completed_day_keys = set()
    if viewer_id and track.get("slug") and day_keys:
        rows = list(
            db.program_day_progress.find(
                {
                    "viewer_id": viewer_id,
                    "track_slug": track.get("slug"),
                    "week_number": week_number,
                    "day_key": {"$in": day_keys},
                },
                {"day_key": 1},
            )
        )
        completed_day_keys = {r.get("day_key") for r in rows if r.get("day_key")}

    completed_days_count = len([k for k in day_keys if k in completed_day_keys])
    all_days_completed = bool(day_keys) and completed_days_count == len(day_keys)
    initial_day_key = (
        requested_day_key if requested_day_key in day_keys else (day_keys[0] if day_keys else "")
    )

    return render_or_fallback(
        "program_week_detail.html",
        track=track,
        level=level,
        env=env,
        week_number=week_number,
        week=week,
        items=items,
        day_groups=day_groups,
        is_template_preview=is_template_preview,
        total_days=len(day_groups),
        workout_map=workout_map,
        completed_day_keys=completed_day_keys,
        completed_days_count=completed_days_count,
        all_days_completed=all_days_completed,
        initial_day_key=initial_day_key,
        levels=levels or DEFAULT_LEVELS,
        envs=envs or DEFAULT_ENVS,
        next_week_number=next_week_number,
        hub=hub,
    )


@app.route("/programs/<hub_slug>/week/<int:week_number>/day-status", methods=["POST"])
def program_hub_week_day_status(hub_slug, week_number: int):
    hub = _get_hub_or_404(hub_slug)

    level = _norm_choice(request.form.get("level")) or "beginner"
    env = _norm_choice(request.form.get("env")) or "home"

    levels = _levels_for_hub(hub_slug)
    if level not in levels:
        level = levels[0] if levels else "beginner"

    envs = _envs_for_hub_level(hub_slug, level)
    if env not in envs:
        env = envs[0] if envs else "home"

    track = _pick_track_for(hub_slug, level, env)
    if not track:
        abort(404)

    day_key = slugify(request.form.get("day_key") or "")
    action = _norm_choice(request.form.get("action")) or "done"
    next_day_key = slugify(request.form.get("next_day_key") or "")

    owner_key = _progress_owner_key()
    if day_key and owner_key:
        query = {
            "viewer_id": owner_key,
            "track_slug": track.get("slug"),
            "week_number": week_number,
            "day_key": day_key,
        }
        if action == "undo":
            db.program_day_progress.delete_one(query)
        else:
            db.program_day_progress.update_one(
                query,
                {
                    "$set": {
                        "completed_at": datetime.datetime.utcnow(),
                        "hub_slug": hub_slug,
                        "level": level,
                        "env": env,
                    }
                },
                upsert=True,
            )

        week_doc = db.program_weeks.find_one(
            {"program_id": track.get("_id"), "week_number": week_number},
            {"_id": 1},
        )
        if week_doc:
            week_items = list(
                db.program_items.find(
                    {"week_id": week_doc["_id"]},
                    {"day": 1, "day_label": 1, "label": 1, "title": 1, "custom_name": 1},
                ).sort([("order", 1), ("created_at", 1)])
            )
            day_keys = []
            seen_day_keys = set()
            for i, it in enumerate(week_items):
                dk = _day_key_from_program_item(it, i + 1)
                if dk in seen_day_keys:
                    continue
                seen_day_keys.add(dk)
                day_keys.append(dk)

            if day_keys:
                done_count = db.program_day_progress.count_documents(
                    {
                        "viewer_id": owner_key,
                        "track_slug": track.get("slug"),
                        "week_number": week_number,
                        "day_key": {"$in": day_keys},
                    }
                )
                if done_count >= len(day_keys):
                    db.program_week_progress.update_one(
                        {
                            "viewer_id": owner_key,
                            "track_slug": track.get("slug"),
                            "week_number": week_number,
                        },
                        {
                            "$set": {
                                "completed_at": datetime.datetime.utcnow(),
                                "hub_slug": hub_slug,
                                "level": level,
                                "env": env,
                            }
                        },
                        upsert=True,
                    )
                else:
                    db.program_week_progress.delete_one(
                        {
                            "viewer_id": owner_key,
                            "track_slug": track.get("slug"),
                            "week_number": week_number,
                        }
                    )

    target_day = next_day_key or day_key
    args = {
        "hub_slug": hub_slug,
        "week_number": week_number,
        "level": level,
        "env": env,
    }
    if target_day:
        args["day"] = target_day
    return redirect(url_for("program_hub_week_detail", **args))


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
    # Backwards compatibility for old /workouts?filter=... links.
    legacy_filter = _norm_choice(request.args.get("filter"))
    if legacy_filter in LEGACY_WORKOUT_FILTER_TO_SORT:
        return redirect(url_for("workouts_browse", sort=LEGACY_WORKOUT_FILTER_TO_SORT[legacy_filter]))

    parts_single = set(db.workouts.distinct("body_part"))
    parts_multi = set(db.workouts.distinct("body_parts"))
    parts_primary = set(db.workouts.distinct("primary_muscle"))
    parts_in_db = parts_single | parts_multi | parts_primary

    body_parts_featured = [
        p for p in FEATURED_BODY_PARTS if p in parts_in_db
    ] or FEATURED_BODY_PARTS[:]

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
        bp: db.workouts.count_documents(
            {"$or": [{"body_part": bp}, {"body_parts": bp}, {"primary_muscle": bp}]}
        )
        for bp in BODY_PARTS_MASTER
    }
    return render_template("body_parts_index.html", body_parts=BODY_PARTS_MASTER, counts=counts)


@app.route("/workouts/browse")
def workouts_browse():
    level = request.args.get("level") or ""
    body = request.args.get("body") or ""
    style = request.args.get("style") or ""
    movement = request.args.get("movement") or ""
    equipment = request.args.get("equipment") or ""
    difficulty = request.args.get("difficulty") or ""
    q = (request.args.get("q") or "").strip()
    sort_key = request.args.get("sort", "name")
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 12)), 1), 100)

    and_clauses = []
    if level:
        and_clauses.append({"level": level})
    if style:
        and_clauses.append({"style": style})
    if movement:
        and_clauses.append({"movement_pattern": movement})
    if equipment:
        and_clauses.append({"equipment": equipment})
    if difficulty:
        and_clauses.append({"difficulty_tier": difficulty})
    if body:
        and_clauses.append({"$or": [{"body_part": body}, {"body_parts": body}, {"primary_muscle": body}]})
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
                    {"primary_muscle": rx},
                    {"movement_pattern": rx},
                    {"equipment": rx},
                    {"difficulty_tier": rx},
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

    cursor = db.workouts.find(query).sort(sort).skip((page - 1) * per_page).limit(per_page)
    items = list(cursor)
    for w in items:
        w["primary_muscle"] = w.get("primary_muscle") or _primary_muscle_from_doc(w)
        w["movement_pattern"] = w.get("movement_pattern") or _infer_movement_from_primary_muscle(
            w.get("primary_muscle")
        )
        w["equipment"] = w.get("equipment") or _infer_equipment_from_style(w.get("style"))
        w["difficulty_tier"] = w.get("difficulty_tier") or _infer_difficulty_tier_from_level(
            w.get("level")
        )

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
        movement=movement,
        equipment=equipment,
        difficulty=difficulty,
        q=q,
        workout_levels=WORKOUT_LEVELS,
        body_parts=BODY_PARTS_MASTER,
        workout_styles=get_styles(),
        movement_patterns=WORKOUT_MOVEMENT_PATTERNS,
        equipment_types=WORKOUT_EQUIPMENT_TYPES,
        difficulty_tiers=WORKOUT_DIFFICULTY_TIERS,
    )


@app.route("/workouts/<slug>")
def workout_detail(slug):
    w = db.workouts.find_one({"slug": slug})
    if not w:
        abort(404)

    w["primary_muscle"] = w.get("primary_muscle") or _primary_muscle_from_doc(w)
    w["movement_pattern"] = w.get("movement_pattern") or _infer_movement_from_primary_muscle(
        w.get("primary_muscle")
    )
    w["equipment"] = w.get("equipment") or _infer_equipment_from_style(w.get("style"))
    w["difficulty_tier"] = w.get("difficulty_tier") or _infer_difficulty_tier_from_level(w.get("level"))

    parts = w.get("body_parts") or ([w.get("body_part")] if w.get("body_part") else [])
    rel_or = []
    if parts:
        rel_or.append({"body_parts": {"$in": parts}})
    if w.get("style"):
        rel_or.append({"style": w.get("style")})
    if w.get("movement_pattern"):
        rel_or.append({"movement_pattern": w.get("movement_pattern")})

    if rel_or:
        rel_q = {"$and": [{"slug": {"$ne": w["slug"]}}, {"$or": rel_or}]}
    else:
        rel_q = {"slug": {"$ne": w["slug"]}}

    related = list(
        db.workouts.find(rel_q).sort([("rating", -1), ("created_at", -1), ("name", 1)]).limit(6)
    )
    for r in related:
        r["primary_muscle"] = r.get("primary_muscle") or _primary_muscle_from_doc(r)
        r["movement_pattern"] = r.get("movement_pattern") or _infer_movement_from_primary_muscle(
            r.get("primary_muscle")
        )
        r["equipment"] = r.get("equipment") or _infer_equipment_from_style(r.get("style"))
        r["difficulty_tier"] = r.get("difficulty_tier") or _infer_difficulty_tier_from_level(
            r.get("level")
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
        return redirect(url_for("home"))

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
            {"primary_muscle": rx},
            {"movement_pattern": rx},
            {"equipment": rx},
            {"difficulty_tier": rx},
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
    for w in items:
        w["primary_muscle"] = w.get("primary_muscle") or _primary_muscle_from_doc(w)
        w["movement_pattern"] = w.get("movement_pattern") or _infer_movement_from_primary_muscle(
            w.get("primary_muscle")
        )
        w["equipment"] = w.get("equipment") or _infer_equipment_from_style(w.get("style"))
        w["difficulty_tier"] = w.get("difficulty_tier") or _infer_difficulty_tier_from_level(
            w.get("level")
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
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        is_valid = _check_admin_credentials(username, password)

        # Allow successful sign-ins even if the IP is currently rate-limited due
        # to earlier failures.
        if not is_valid and not _allowed_login_attempt(ip):
            flash("Too many failed login attempts. Try again in ~15 minutes.", "danger")
            return render_template("login.html")

        if is_valid:
            _clear_failed_logins(ip)
            login_user(User("admin", role="admin"))
            flash("Logged in.", "success")
            return redirect(_safe_next_url(default_endpoint="admin_index"))

        _record_failed_login(ip)
        flash("Invalid credentials.", "danger")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    if not getattr(current_user, "is_admin", False):
        abort(403)
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("home"))


@app.route("/account/register", methods=["GET", "POST"])
def account_register():
    if getattr(current_user, "is_authenticated", False) and not getattr(current_user, "is_admin", False):
        return redirect(url_for("account_profile"))
    if getattr(current_user, "is_authenticated", False) and getattr(current_user, "is_admin", False):
        return redirect(url_for("admin_index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = (request.form.get("password") or "").strip()
        confirm = (request.form.get("confirm_password") or "").strip()

        if not username or not email or not password:
            flash("Username, email, and password are required.", "danger")
            return render_template("account_register.html")
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("account_register.html")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("account_register.html")
        if not re.match(r"^[a-zA-Z0-9_]{3,30}$", username):
            flash("Username must be 3-30 characters and only letters, numbers, underscore.", "danger")
            return render_template("account_register.html")

        username_lower = username.lower()
        email_lower = email.lower()
        if db.users.find_one({"username_lower": username_lower}):
            flash("That username is already taken.", "danger")
            return render_template("account_register.html")
        if db.users.find_one({"email_lower": email_lower}):
            flash("An account with that email already exists.", "danger")
            return render_template("account_register.html")

        doc = {
            "username": username,
            "username_lower": username_lower,
            "email": email,
            "email_lower": email_lower,
            "password_hash": generate_password_hash(password),
            "active": True,
            "created_at": datetime.datetime.utcnow(),
        }
        res = db.users.insert_one(doc)

        member_user = User(
            f"member:{res.inserted_id}",
            role="member",
            user_oid=str(res.inserted_id),
            username=username,
        )
        login_user(member_user)
        _migrate_guest_state_to_member(_viewer_id(), f"user:{res.inserted_id}")
        flash("Account created.", "success")
        return redirect(url_for("home"))

    return render_template("account_register.html")


@app.route("/account/login", methods=["GET", "POST"])
def account_login():
    if getattr(current_user, "is_authenticated", False) and not getattr(current_user, "is_admin", False):
        return redirect(url_for("account_profile"))
    if getattr(current_user, "is_authenticated", False) and getattr(current_user, "is_admin", False):
        return redirect(url_for("admin_index"))

    if request.method == "POST":
        identity = (request.form.get("identity") or "").strip().lower()
        password = (request.form.get("password") or "").strip()

        if not identity or not password:
            flash("Email/username and password are required.", "danger")
            return render_template("account_login.html")

        doc = db.users.find_one({"$or": [{"email_lower": identity}, {"username_lower": identity}]})
        if not doc or not doc.get("password_hash") or not check_password_hash(doc["password_hash"], password):
            flash("Invalid login.", "danger")
            return render_template("account_login.html")
        if doc.get("active") is False:
            flash("This account is inactive.", "danger")
            return render_template("account_login.html")

        member_key = f"user:{doc['_id']}"
        _migrate_guest_state_to_member(_viewer_id(), member_key)
        login_user(
            User(
                f"member:{doc['_id']}",
                role="member",
                user_oid=str(doc["_id"]),
                username=doc.get("username"),
            )
        )
        db.users.update_one(
            {"_id": doc["_id"]},
            {"$set": {"last_login_at": datetime.datetime.utcnow()}},
        )
        flash("Signed in.", "success")
        return redirect(_safe_next_url(default_endpoint="home"))

    return render_template("account_login.html")


@app.route("/account/logout", methods=["POST"])
def account_logout():
    if not getattr(current_user, "is_authenticated", False):
        return redirect(url_for("account_login"))
    if getattr(current_user, "is_admin", False):
        return redirect(url_for("admin_index"))
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("home"))


@app.route("/account")
def account_profile():
    if not getattr(current_user, "is_authenticated", False):
        return redirect(url_for("account_login", next=request.path))

    if getattr(current_user, "is_admin", False):
        return redirect(url_for("admin_index"))

    owner_key = _member_owner_key()
    if not owner_key:
        return redirect(url_for("account_login"))

    favorites_count = db.program_favorites.count_documents({"viewer_id": owner_key})
    completed_days = db.program_day_progress.count_documents({"viewer_id": owner_key})
    recent_days = list(
        db.program_day_progress.find({"viewer_id": owner_key}).sort([("completed_at", -1)]).limit(8)
    )

    track_slugs = [r.get("track_slug") for r in recent_days if r.get("track_slug")]
    tracks = list(db.programs.find({"slug": {"$in": track_slugs}}, {"slug": 1, "title": 1}))
    track_map = {t.get("slug"): t.get("title") for t in tracks if t.get("slug")}

    return render_template(
        "account_profile.html",
        favorites_count=favorites_count,
        completed_days=completed_days,
        recent_days=recent_days,
        track_map=track_map,
    )


@app.route("/health")
def health():
    return {"status": "ok"}, 200


# -----------------------------------------------------------------------------
# Admin: Workouts
# -----------------------------------------------------------------------------
@app.route("/admin")
@login_required
def admin_index():
    try:
        items = list(db.workouts.find().sort([("created_at", -1)]))
    except Exception as e:
        app.logger.warning("Admin index DB read failed: %s", e)
        flash(
            "Signed in, but database is unreachable. Check MONGO_URI/MONGO_URI_LOCAL for local setup.",
            "warning",
        )
        items = []

    for w in items:
        w["_quality_missing"] = not (
            w.get("primary_muscle")
            and w.get("movement_pattern")
            and w.get("equipment")
            and w.get("difficulty_tier")
        )
        w["primary_muscle"] = w.get("primary_muscle") or _primary_muscle_from_doc(w)
        w["movement_pattern"] = w.get("movement_pattern") or _infer_movement_from_primary_muscle(
            w.get("primary_muscle")
        )
        w["equipment"] = w.get("equipment") or _infer_equipment_from_style(w.get("style"))
        w["difficulty_tier"] = w.get("difficulty_tier") or _infer_difficulty_tier_from_level(
            w.get("level")
        )
    quality_summary = _workout_quality_summary(items)
    link_health = _program_link_health_report()
    return render_template(
        "admin_index.html",
        items=items,
        quality_summary=quality_summary,
        link_health=link_health,
    )


@app.route("/admin/workouts/backfill-metadata", methods=["POST"])
@login_required
def admin_workout_backfill_metadata():
    target_query = {
        "$or": [
            {"primary_muscle": {"$in": [None, ""]}},
            {"movement_pattern": {"$in": [None, ""]}},
            {"equipment": {"$in": [None, ""]}},
            {"difficulty_tier": {"$in": [None, ""]}},
        ]
    }

    updated = 0
    for w in db.workouts.find(target_query):
        patch = {}
        primary = w.get("primary_muscle") or _primary_muscle_from_doc(w)
        movement = w.get("movement_pattern") or _infer_movement_from_primary_muscle(primary)
        equipment = w.get("equipment") or _infer_equipment_from_style(w.get("style"))
        tier = w.get("difficulty_tier") or _infer_difficulty_tier_from_level(w.get("level"))

        if primary and not w.get("primary_muscle"):
            patch["primary_muscle"] = primary
        if movement and not w.get("movement_pattern"):
            patch["movement_pattern"] = movement
        if equipment and not w.get("equipment"):
            patch["equipment"] = equipment
        if tier and not w.get("difficulty_tier"):
            patch["difficulty_tier"] = tier

        if patch:
            db.workouts.update_one({"_id": w["_id"]}, {"$set": patch})
            updated += 1

    flash(f"Metadata backfill complete. Updated {updated} workout(s).", "success")
    return redirect(url_for("admin_index"))


@app.route("/admin/workouts/quality")
@login_required
def admin_workout_quality():
    workouts = list(db.workouts.find().sort([("name", 1)]))
    rows = []
    for w in workouts:
        issues = _workout_quality_issues(w)
        if not issues:
            continue
        rows.append(
            {
                "workout": w,
                "issues": issues,
            }
        )

    summary = _workout_quality_summary(workouts)
    return render_or_fallback(
        "admin_workout_quality.html",
        rows=rows,
        summary=summary,
    )


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
        metadata, meta_errors = _workout_metadata_from_form(request.form)

        if not name:
            flash("Name is required.", "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS,
                parts=BODY_PARTS_MASTER,
                styles=get_styles(),
                movement_patterns=WORKOUT_MOVEMENT_PATTERNS,
                equipment_types=WORKOUT_EQUIPMENT_TYPES,
                difficulty_tiers=WORKOUT_DIFFICULTY_TIERS,
                data=request.form,
            )

        if meta_errors:
            for msg in meta_errors:
                flash(msg, "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS,
                parts=BODY_PARTS_MASTER,
                styles=get_styles(),
                movement_patterns=WORKOUT_MOVEMENT_PATTERNS,
                equipment_types=WORKOUT_EQUIPMENT_TYPES,
                difficulty_tiers=WORKOUT_DIFFICULTY_TIERS,
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
                movement_patterns=WORKOUT_MOVEMENT_PATTERNS,
                equipment_types=WORKOUT_EQUIPMENT_TYPES,
                difficulty_tiers=WORKOUT_DIFFICULTY_TIERS,
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
            "primary_muscle": metadata.get("primary_muscle"),
            "movement_pattern": metadata.get("movement_pattern"),
            "equipment": metadata.get("equipment"),
            "difficulty_tier": metadata.get("difficulty_tier"),
            "created_at": datetime.datetime.utcnow(),
        }

        try:
            db.workouts.insert_one(doc)
            flash("Workout added.", "success")
            return redirect(url_for("admin_index"))
        except Exception as e:
            flash(f"Error: {e}", "danger")

    return render_template(
        "admin_workout_form.html",
        levels=WORKOUT_LEVELS,
        parts=BODY_PARTS_MASTER,
        styles=get_styles(),
        movement_patterns=WORKOUT_MOVEMENT_PATTERNS,
        equipment_types=WORKOUT_EQUIPMENT_TYPES,
        difficulty_tiers=WORKOUT_DIFFICULTY_TIERS,
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
        metadata, meta_errors = _workout_metadata_from_form(request.form, fallback_doc=w)

        if not name:
            flash("Name is required.", "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS,
                parts=BODY_PARTS_MASTER,
                styles=get_styles(),
                movement_patterns=WORKOUT_MOVEMENT_PATTERNS,
                equipment_types=WORKOUT_EQUIPMENT_TYPES,
                difficulty_tiers=WORKOUT_DIFFICULTY_TIERS,
                data=request.form,
                edit=True,
                _id=id,
            )

        if meta_errors:
            for msg in meta_errors:
                flash(msg, "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS,
                parts=BODY_PARTS_MASTER,
                styles=get_styles(),
                movement_patterns=WORKOUT_MOVEMENT_PATTERNS,
                equipment_types=WORKOUT_EQUIPMENT_TYPES,
                difficulty_tiers=WORKOUT_DIFFICULTY_TIERS,
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
                movement_patterns=WORKOUT_MOVEMENT_PATTERNS,
                equipment_types=WORKOUT_EQUIPMENT_TYPES,
                difficulty_tiers=WORKOUT_DIFFICULTY_TIERS,
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
            "primary_muscle": metadata.get("primary_muscle"),
            "movement_pattern": metadata.get("movement_pattern"),
            "equipment": metadata.get("equipment"),
            "difficulty_tier": metadata.get("difficulty_tier"),
        }

        try:
            db.workouts.update_one({"_id": ObjectId(id)}, {"$set": update})
            flash("Workout updated.", "success")
            return redirect(url_for("admin_index"))
        except Exception as e:
            flash(f"Error: {e}", "danger")

    data = dict(w)
    data["primary_muscle"] = data.get("primary_muscle") or _primary_muscle_from_doc(data)
    data["movement_pattern"] = data.get("movement_pattern") or _infer_movement_from_primary_muscle(
        data.get("primary_muscle")
    )
    data["equipment"] = data.get("equipment") or _infer_equipment_from_style(data.get("style"))
    data["difficulty_tier"] = data.get("difficulty_tier") or _infer_difficulty_tier_from_level(
        data.get("level")
    )
    data["tags"] = ", ".join(data.get("tags", []))
    data["images"] = "\n".join(data.get("images", []))
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
        movement_patterns=WORKOUT_MOVEMENT_PATTERNS,
        equipment_types=WORKOUT_EQUIPMENT_TYPES,
        difficulty_tiers=WORKOUT_DIFFICULTY_TIERS,
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
    state = "activated" if not s.get("active", True) else "deactivated"
    flash(f"Style {state}.", "success")
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
        except Exception as e:
            flash(f"Error: {e}", "danger")

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
                "admin_home_plan_form.html", data=request.form, edit=True, _id=id
            )

        if not cta_url:
            flash("Primary button URL is required.", "danger")
            return render_template(
                "admin_home_plan_form.html", data=request.form, edit=True, _id=id
            )

        existing = db.home_plans.find_one({"slug": slug, "_id": {"$ne": ObjectId(id)}})
        if existing:
            flash(f"Slug '{slug}' already exists.", "danger")
            return render_template(
                "admin_home_plan_form.html", data=request.form, edit=True, _id=id
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
        except Exception as e:
            flash(f"Error: {e}", "danger")

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
# Admin: Programs (CRUD)
# -----------------------------------------------------------------------------
@app.route("/admin/programs/link-health")
@login_required
def admin_program_link_health():
    report = _program_link_health_report(include_rows=True)
    return render_or_fallback("admin_program_link_health.html", report=report)


@app.route("/admin/programs/link-health/backfill", methods=["POST"])
@login_required
def admin_program_link_health_backfill():
    items = list(
        db.program_items.find(
            {},
            {"_id": 1, "workout_id": 1, "workout_slug": 1, "workout_name": 1},
        )
    )
    workout_ids = [it.get("workout_id") for it in items if it.get("workout_id")]
    workouts = list(db.workouts.find({"_id": {"$in": workout_ids}}, {"_id": 1, "slug": 1, "name": 1}))
    by_id = {w["_id"]: w for w in workouts if w.get("_id") is not None}

    slugs_needed = [it.get("workout_slug") for it in items if (it.get("workout_slug") or "").strip()]
    by_slug = {}
    if slugs_needed:
        slug_docs = list(db.workouts.find({"slug": {"$in": slugs_needed}}, {"_id": 1, "slug": 1, "name": 1}))
        by_slug = {w.get("slug"): w for w in slug_docs if (w.get("slug") or "").strip()}

    updated = 0
    repaired = 0
    for it in items:
        patch = {}
        wid = it.get("workout_id")
        wslug = (it.get("workout_slug") or "").strip()
        linked = by_id.get(wid) if wid else None

        if linked:
            if it.get("workout_slug") != linked.get("slug"):
                patch["workout_slug"] = linked.get("slug")
            if it.get("workout_name") != linked.get("name"):
                patch["workout_name"] = linked.get("name")
        elif wslug and wslug in by_slug:
            resolved = by_slug[wslug]
            patch["workout_id"] = resolved.get("_id")
            patch["workout_name"] = resolved.get("name")
            repaired += 1

        if patch:
            db.program_items.update_one({"_id": it["_id"]}, {"$set": patch})
            updated += 1

    flash(
        f"Link backfill complete: {updated} item(s) updated, {repaired} broken link(s) repaired.",
        "success",
    )
    return redirect(url_for("admin_program_link_health"))


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
        default_week_split_raw = (request.form.get("default_week_split") or "").strip()
        default_week_split = _parse_day_split(default_week_split_raw) if default_week_split_raw else None
        summary = (request.form.get("summary") or "").strip() or None
        cover_image = (request.form.get("cover_image") or "").strip() or None

        order = _safe_int(request.form.get("order"), default=0)
        active = request.form.get("active") == "on"
        show_on_home = request.form.get("show_on_home") == "on"

        kind = (request.form.get("kind") or "").strip().lower() or "hub"
        if kind not in ("hub", "track"):
            kind = "hub"

        hub_slug = (request.form.get("hub_slug") or "").strip() or None
        track_level = (request.form.get("track_level") or "").strip() or None
        track_env = _norm_choice(request.form.get("track_env")) or None
        if track_env and track_env not in DEFAULT_ENVS:
            track_env = None

        if kind != "track":
            hub_slug = None
            track_level = None
            track_env = None

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
            "track_env": track_env,
            "category": category,
            "duration_label": duration_label,
            "default_week_split": default_week_split,
            "summary": summary,
            "cover_image": cover_image,
            "order": order,
            "active": active,
            "show_on_home": show_on_home,
            "created_at": datetime.datetime.utcnow(),
        }

        try:
            new_program = db.programs.insert_one(doc)
            flash("Program created. Next: build its weeks.", "success")
            return redirect(
                url_for("admin_program_weeks", program_id=str(new_program.inserted_id))
            )
        except Exception as e:
            flash(f"Error: {e}", "danger")

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
        default_week_split_raw = (request.form.get("default_week_split") or "").strip()
        default_week_split = _parse_day_split(default_week_split_raw) if default_week_split_raw else None
        summary = (request.form.get("summary") or "").strip() or None
        cover_image = (request.form.get("cover_image") or "").strip() or None

        order = _safe_int(request.form.get("order"), default=0)
        active = request.form.get("active") == "on"
        show_on_home = request.form.get("show_on_home") == "on"

        kind = (request.form.get("kind") or p.get("kind") or "hub").strip().lower()
        if kind not in ("hub", "track"):
            kind = "hub"

        hub_slug = (request.form.get("hub_slug") or "").strip() or None
        track_level = (request.form.get("track_level") or "").strip() or None
        track_env = _norm_choice(request.form.get("track_env")) or None
        if track_env and track_env not in DEFAULT_ENVS:
            track_env = None

        if kind != "track":
            hub_slug = None
            track_level = None
            track_env = None

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
            "track_env": track_env,
            "category": category,
            "duration_label": duration_label,
            "default_week_split": default_week_split,
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
        except Exception as e:
            flash(f"Error: {e}", "danger")

    return render_template("admin_program_form.html", data=dict(p), edit=True, _id=id)


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
        {"_id": p["_id"]}, {"$set": {"show_on_home": not p.get("show_on_home", False)}}
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
    return render_or_fallback(
        "admin_program_weeks.html",
        program=program,
        weeks=weeks,
        suggested_weeks=_week_count_from_duration_label(program.get("duration_label")),
        default_day_split=", ".join(DEFAULT_TRACK_DAY_SPLIT),
    )


@app.route("/admin/programs/<program_id>/weeks/scaffold", methods=["POST"])
@login_required
def admin_program_weeks_scaffold(program_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week_count = _safe_int(
        request.form.get("week_count"),
        default=_week_count_from_duration_label(program.get("duration_label")),
        min_value=1,
        max_value=52,
    )
    day_split = _parse_day_split(request.form.get("day_split"))
    add_sample_items = request.form.get("add_sample_items") == "on"

    # Keep the selected split as program default for future placeholder week pages.
    db.programs.update_one(
        {"_id": program["_id"]},
        {"$set": {"default_week_split": day_split}},
    )

    weeks_created = 0
    items_created = 0
    weeks_skipped_for_items = 0

    for week_number in range(1, week_count + 1):
        week = db.program_weeks.find_one(
            {"program_id": program["_id"], "week_number": week_number}
        )
        if not week:
            db.program_weeks.insert_one(
                {
                    "program_id": program["_id"],
                    "week_number": week_number,
                    "title": f"Week {week_number}",
                    "order": week_number,
                    "created_at": datetime.datetime.utcnow(),
                }
            )
            week = db.program_weeks.find_one(
                {"program_id": program["_id"], "week_number": week_number}
            )
            weeks_created += 1

        if not add_sample_items or not week:
            continue

        existing_count = db.program_items.count_documents({"week_id": week["_id"]})
        if existing_count > 0:
            weeks_skipped_for_items += 1
            continue

        for idx, day in enumerate(day_split):
            sample = _sample_workout_for_day(day)
            item_doc = {
                "week_id": week["_id"],
                "day": day,
                "custom_name": None if sample else f"{day} Starter Movement",
                "workout_id": sample.get("_id") if sample else None,
                "sets": "3",
                "reps": "8-12",
                "rest": "60-90s",
                "notes": "Sample placeholder. Update this item with your exact plan.",
                "order": idx + 1,
                "created_at": datetime.datetime.utcnow(),
            }
            db.program_items.insert_one(item_doc)
            items_created += 1

    flash(
        (
            f"Scaffold complete: {weeks_created} week(s) created, "
            f"{items_created} sample item(s) added. "
            f"{weeks_skipped_for_items} existing week(s) kept unchanged."
        ),
        "success",
    )
    return redirect(url_for("admin_program_weeks", program_id=program_id))


@app.route("/admin/programs/<program_id>/weeks/new", methods=["POST"])
@login_required
def admin_program_week_new(program_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week_number = _safe_int(request.form.get("week_number"), default=0)
    title = (request.form.get("title") or "").strip() or None
    order = _safe_int(request.form.get("order"), default=week_number)

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


@app.route("/admin/programs/<program_id>/weeks/<week_id>/update", methods=["POST"])
@login_required
def admin_program_week_update(program_id, week_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week = db.program_weeks.find_one({"_id": ObjectId(week_id), "program_id": program["_id"]})
    if not week:
        abort(404)

    week_number = _safe_int(
        request.form.get("week_number"),
        default=(week.get("week_number") or 0),
        min_value=1,
        max_value=52,
    )
    title = (request.form.get("title") or "").strip() or None
    order = _safe_int(
        request.form.get("order"),
        default=(week.get("order") if week.get("order") is not None else week_number),
        min_value=0,
        max_value=999,
    )

    conflicting = db.program_weeks.find_one(
        {
            "program_id": program["_id"],
            "week_number": week_number,
            "_id": {"$ne": week["_id"]},
        },
        {"_id": 1},
    )
    if conflicting:
        flash(f"Week {week_number} already exists for this program.", "danger")
        return redirect(url_for("admin_program_weeks", program_id=program_id))

    db.program_weeks.update_one(
        {"_id": week["_id"]},
        {"$set": {"week_number": week_number, "title": title, "order": order}},
    )
    flash(f"Week {week_number} updated.", "success")
    return redirect(url_for("admin_program_weeks", program_id=program_id))


@app.route("/admin/programs/<program_id>/weeks/<week_id>/clone", methods=["POST"])
@login_required
def admin_program_week_clone(program_id, week_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    source_week = db.program_weeks.find_one({"_id": ObjectId(week_id), "program_id": program["_id"]})
    if not source_week:
        abort(404)

    target_week_number = _safe_int(
        request.form.get("target_week_number"),
        default=0,
        min_value=1,
        max_value=52,
    )
    if target_week_number < 1:
        flash("Target week number must be at least 1.", "danger")
        return redirect(url_for("admin_program_weeks", program_id=program_id))

    source_week_number = _safe_int(source_week.get("week_number"), default=0)
    if target_week_number == source_week_number:
        flash("Choose a different target week number.", "danger")
        return redirect(url_for("admin_program_weeks", program_id=program_id))

    target_title = (request.form.get("target_title") or "").strip() or None
    include_items = request.form.get("include_items") == "on"
    overwrite_items = request.form.get("overwrite_items") == "on"

    target_week = db.program_weeks.find_one(
        {"program_id": program["_id"], "week_number": target_week_number}
    )
    target_created = False
    if not target_week:
        db.program_weeks.insert_one(
            {
                "program_id": program["_id"],
                "week_number": target_week_number,
                "title": target_title or f"Week {target_week_number}",
                "order": target_week_number,
                "created_at": datetime.datetime.utcnow(),
            }
        )
        target_week = db.program_weeks.find_one(
            {"program_id": program["_id"], "week_number": target_week_number}
        )
        target_created = True
    elif target_title:
        db.program_weeks.update_one({"_id": target_week["_id"]}, {"$set": {"title": target_title}})

    copied_count = 0
    if include_items and target_week:
        source_items = list(
            db.program_items.find({"week_id": source_week["_id"]}).sort([("order", 1), ("created_at", 1)])
        )
        if source_items:
            target_existing_count = db.program_items.count_documents({"week_id": target_week["_id"]})
            if target_existing_count > 0 and not overwrite_items:
                flash(
                    (
                        f"Target Week {target_week_number} already has {target_existing_count} item(s). "
                        "Enable overwrite to replace them."
                    ),
                    "warning",
                )
                return redirect(url_for("admin_program_weeks", program_id=program_id))
            if overwrite_items and target_existing_count > 0:
                db.program_items.delete_many({"week_id": target_week["_id"]})

            now = datetime.datetime.utcnow()
            for source in source_items:
                clone_doc = dict(source)
                clone_doc.pop("_id", None)
                clone_doc["week_id"] = target_week["_id"]
                clone_doc["created_at"] = now
                db.program_items.insert_one(clone_doc)
                copied_count += 1

    week_msg = "created" if target_created else "updated"
    if include_items:
        flash(
            (
                f"Week {target_week_number} {week_msg}. "
                f"Copied {copied_count} item(s) from Week {source_week_number}."
            ),
            "success",
        )
    else:
        flash(f"Week {target_week_number} {week_msg}.", "success")
    return redirect(url_for("admin_program_weeks", program_id=program_id))


@app.route("/admin/programs/<program_id>/weeks/<week_id>/delete", methods=["POST"])
@login_required
def admin_program_week_delete(program_id, week_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week = db.program_weeks.find_one({"_id": ObjectId(week_id), "program_id": program["_id"]})
    if not week:
        abort(404)

    db.program_items.delete_many({"week_id": week["_id"]})
    db.program_weeks.delete_one({"_id": week["_id"]})

    flash("Week deleted.", "success")
    return redirect(url_for("admin_program_weeks", program_id=program_id))


# -----------------------------------------------------------------------------
# Admin: Program Week Items
# -----------------------------------------------------------------------------
@app.route("/admin/programs/<program_id>/weeks/<week_id>/items")
@login_required
def admin_program_week_items(program_id, week_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week = db.program_weeks.find_one({"_id": ObjectId(week_id), "program_id": program["_id"]})
    if not week:
        abort(404)

    items = list(
        db.program_items.find({"week_id": week["_id"]}).sort([("order", 1), ("created_at", 1)])
    )
    weeks_in_program = list(
        db.program_weeks.find({"program_id": program["_id"]}, {"week_number": 1, "title": 1}).sort(
            [("week_number", 1), ("order", 1)]
        )
    )
    other_weeks = [w for w in weeks_in_program if w.get("_id") != week.get("_id")]
    workouts = list(
        db.workouts.find(
            {},
            {
                "name": 1,
                "slug": 1,
                "primary_muscle": 1,
                "movement_pattern": 1,
                "equipment": 1,
                "difficulty_tier": 1,
            },
        ).sort([("name", 1)])
    )
    workout_map = {w["_id"]: w for w in workouts}

    return render_or_fallback(
        "admin_program_week_items.html",
        program=program,
        week=week,
        items=items,
        workouts=workouts,
        other_weeks=other_weeks,
        workout_map=workout_map,
    )


@app.route("/admin/programs/<program_id>/weeks/<week_id>/items/new", methods=["POST"])
@login_required
def admin_program_week_item_new(program_id, week_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week = db.program_weeks.find_one({"_id": ObjectId(week_id), "program_id": program["_id"]})
    if not week:
        abort(404)

    day = _normalize_week_day_label(request.form.get("day"))
    custom_name = (request.form.get("custom_name") or "").strip() or None
    workout_id_raw = (request.form.get("workout_id") or "").strip()
    sets = (request.form.get("sets") or "").strip() or None
    reps = (request.form.get("reps") or "").strip() or None
    rest = (request.form.get("rest") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None
    order = _safe_int(request.form.get("order"), default=0)

    if not day:
        flash("Day is required (ex: Push, Pull, Legs, Upper, Lower, Core).", "danger")
        return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))

    workout_id = None
    workout_slug = None
    workout_name = None
    if workout_id_raw:
        try:
            workout_id = ObjectId(workout_id_raw)
        except Exception:
            flash("Invalid workout selection.", "danger")
            return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))
        workout_doc = db.workouts.find_one({"_id": workout_id}, {"slug": 1, "name": 1})
        if not workout_doc:
            flash("Selected workout no longer exists. Pick another workout.", "danger")
            return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))
        workout_slug = workout_doc.get("slug")
        workout_name = workout_doc.get("name")

    db.program_items.insert_one(
        {
            "week_id": week["_id"],
            "day": day,
            "custom_name": custom_name,
            "workout_id": workout_id,
            "workout_slug": workout_slug,
            "workout_name": workout_name,
            "sets": sets,
            "reps": reps,
            "rest": rest,
            "notes": notes,
            "order": order,
            "created_at": datetime.datetime.utcnow(),
        }
    )
    flash("Week item added.", "success")
    return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))


@app.route("/admin/programs/<program_id>/weeks/<week_id>/items/<item_id>/edit", methods=["POST"])
@login_required
def admin_program_week_item_edit(program_id, week_id, item_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week = db.program_weeks.find_one({"_id": ObjectId(week_id), "program_id": program["_id"]})
    if not week:
        abort(404)

    item = db.program_items.find_one({"_id": ObjectId(item_id), "week_id": week["_id"]})
    if not item:
        abort(404)

    day = _normalize_week_day_label(request.form.get("day"))
    custom_name = (request.form.get("custom_name") or "").strip() or None
    workout_id_raw = (request.form.get("workout_id") or "").strip()
    sets = (request.form.get("sets") or "").strip() or None
    reps = (request.form.get("reps") or "").strip() or None
    rest = (request.form.get("rest") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None
    order = _safe_int(request.form.get("order"), default=(item.get("order") or 0))

    if not day:
        flash("Day is required.", "danger")
        return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))

    workout_id = None
    workout_slug = None
    workout_name = None
    if workout_id_raw:
        try:
            workout_id = ObjectId(workout_id_raw)
        except Exception:
            flash("Invalid workout selection.", "danger")
            return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))
        workout_doc = db.workouts.find_one({"_id": workout_id}, {"slug": 1, "name": 1})
        if not workout_doc:
            flash("Selected workout no longer exists. Pick another workout.", "danger")
            return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))
        workout_slug = workout_doc.get("slug")
        workout_name = workout_doc.get("name")

    db.program_items.update_one(
        {"_id": item["_id"]},
        {
            "$set": {
                "day": day,
                "custom_name": custom_name,
                "workout_id": workout_id,
                "workout_slug": workout_slug,
                "workout_name": workout_name,
                "sets": sets,
                "reps": reps,
                "rest": rest,
                "notes": notes,
                "order": order,
            }
        },
    )
    flash("Week item updated.", "success")
    return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))


@app.route("/admin/programs/<program_id>/weeks/<week_id>/items/<item_id>/delete", methods=["POST"])
@login_required
def admin_program_week_item_delete(program_id, week_id, item_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week = db.program_weeks.find_one({"_id": ObjectId(week_id), "program_id": program["_id"]})
    if not week:
        abort(404)

    db.program_items.delete_one({"_id": ObjectId(item_id), "week_id": week["_id"]})
    flash("Week item deleted.", "success")
    return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))


@app.route("/admin/programs/<program_id>/weeks/<week_id>/items/copy", methods=["POST"])
@login_required
def admin_program_week_items_copy(program_id, week_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week = db.program_weeks.find_one({"_id": ObjectId(week_id), "program_id": program["_id"]})
    if not week:
        abort(404)

    source_week_id_raw = (request.form.get("source_week_id") or "").strip()
    overwrite_items = request.form.get("overwrite_items") == "on"
    if not source_week_id_raw:
        flash("Choose a source week to copy from.", "danger")
        return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))

    try:
        source_week_id = ObjectId(source_week_id_raw)
    except Exception:
        flash("Invalid source week.", "danger")
        return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))

    source_week = db.program_weeks.find_one({"_id": source_week_id, "program_id": program["_id"]})
    if not source_week:
        flash("Source week not found.", "danger")
        return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))

    if source_week["_id"] == week["_id"]:
        flash("Choose a different source week.", "danger")
        return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))

    source_items = list(
        db.program_items.find({"week_id": source_week["_id"]}).sort([("order", 1), ("created_at", 1)])
    )
    if not source_items:
        flash("Source week has no items to copy.", "warning")
        return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))

    existing_count = db.program_items.count_documents({"week_id": week["_id"]})
    if existing_count > 0 and overwrite_items:
        db.program_items.delete_many({"week_id": week["_id"]})
        base_order = 0
    elif existing_count > 0:
        last_item = db.program_items.find_one(
            {"week_id": week["_id"]},
            sort=[("order", -1), ("created_at", -1)],
        )
        base_order = _safe_int(last_item.get("order") if last_item else 0, default=0)
    else:
        base_order = 0

    copied = 0
    now = datetime.datetime.utcnow()
    for idx, source in enumerate(source_items, start=1):
        clone_doc = dict(source)
        clone_doc.pop("_id", None)
        clone_doc["week_id"] = week["_id"]
        clone_doc["created_at"] = now
        if existing_count > 0 and not overwrite_items:
            clone_doc["order"] = base_order + idx
        db.program_items.insert_one(clone_doc)
        copied += 1

    flash(
        (
            f"Copied {copied} item(s) from Week {source_week.get('week_number')} "
            f"to Week {week.get('week_number')}."
        ),
        "success",
    )
    return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))


@app.route("/admin/programs/<program_id>/weeks/<week_id>/items/reindex", methods=["POST"])
@login_required
def admin_program_week_items_reindex(program_id, week_id):
    program = db.programs.find_one({"_id": ObjectId(program_id)})
    if not program:
        abort(404)

    week = db.program_weeks.find_one({"_id": ObjectId(week_id), "program_id": program["_id"]})
    if not week:
        abort(404)

    items = list(
        db.program_items.find({"week_id": week["_id"]}, {"_id": 1}).sort([("order", 1), ("created_at", 1)])
    )
    for idx, item in enumerate(items, start=1):
        db.program_items.update_one({"_id": item["_id"]}, {"$set": {"order": idx}})

    flash(f"Reindexed {len(items)} item(s) in Week {week.get('week_number')}.", "success")
    return redirect(url_for("admin_program_week_items", program_id=program_id, week_id=week_id))


# -----------------------------------------------------------------------------
# Errors & health
# -----------------------------------------------------------------------------
@app.errorhandler(403)
def forbidden(e):
    app.logger.warning("403: %s %s", request.method, request.path)
    return render_template("403.html"), 403


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
