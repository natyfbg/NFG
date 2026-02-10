# app.py
import datetime
import logging
import os
import re
import time
import uuid
from collections import deque
from logging.handlers import RotatingFileHandler
from time import perf_counter

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
    request,
    send_from_directory,
    url_for,
)
from flask_login import LoginManager, UserMixin, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from pymongo import ASCENDING, MongoClient
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

# -----------------------------------------------------------------------------
# Config / DB
# -----------------------------------------------------------------------------
load_dotenv(override=False)

MONGO_URI = os.environ.get("MONGO_URI") or "mongodb://localhost:27017/NFG"
MONGO_DB = os.environ.get("MONGO_DB")  # optional override to align with seed.py
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

client = MongoClient(MONGO_URI)
if MONGO_DB:
    db = client[MONGO_DB]
else:
    # If URI includes a default db name, use it; otherwise fallback to "NFG"
    db = client.get_default_database() if "/" in MONGO_URI.split("://", 1)[-1] else client["NFG"]

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
app.logger.info(f"Using Mongo at: {MONGO_URI} (db={db.name})")

# -----------------------------------------------------------------------------
# Helpers (general)
# -----------------------------------------------------------------------------
def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _split_list(text: str):
    if not text:
        return []
    return [p.strip() for p in re.split(r"[\n,]+", text) if p.strip()]


_YT_PAT = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/))?([A-Za-z0-9_-]{11})"
)


def _extract_youtube_id(val: str | None):
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

app.logger.info(f"Uploads: saving to {UPLOAD_ROOT_ABS} ; public at {PUBLIC_BASE}")

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB
ALLOWED_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def _allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTS


def _save_one_file(file_storage) -> str | None:
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
    return f"{PUBLIC_BASE}{day}/{fname}".replace("//", "/").replace("//", "/")


def _collect_ordered_images_from_form(req) -> list[str]:
    ordered: list[str] = []
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


def _collect_muscle_image_from_form(req) -> str | None:
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
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to access Admin."
login_manager.login_message_category = "warning"


class User(UserMixin):
    def __init__(self, user_id: str):
        self.id = user_id


@login_manager.user_loader
def load_user(user_id):
    if user_id == "admin":
        return User("admin")
    return None


def _check_admin_credentials(username: str, password: str) -> bool:
    if username != ADMIN_USERNAME:
        return False
    if ADMIN_PASSWORD_HASH:
        return check_password_hash(ADMIN_PASSWORD_HASH, password)
    return password == ADMIN_PASSWORD


FAILED_LOGINS: dict[str, deque] = {}


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "unknown")


def _allowed_login_attempt(ip: str, limit=5, window_sec=900):
    now = time.time()
    dq = FAILED_LOGINS.get(ip)
    if dq is None:
        return True
    while dq and now - dq[0] > window_sec:
        dq.popleft()
    return len(dq) < limit


def _record_failed_login(ip: str):
    FAILED_LOGINS.setdefault(ip, deque()).append(time.time())


def _clear_failed_logins(ip: str):
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

# Legacy "home_plans" (keep for now so nothing breaks)
db.home_plans.create_index([("slug", 1)], unique=True, sparse=True)
db.home_plans.create_index([("order", 1)])
db.home_plans.create_index([("created_at", -1)])
db.home_plans.create_index([("active", 1)])

# Programs (new hierarchy)
db.programs.create_index([("slug", 1)], unique=True, sparse=True)
db.programs.create_index([("active", 1)])
db.programs.create_index([("order", 1)])
db.programs.create_index([("created_at", -1)])
db.programs.create_index([("show_on_home", 1)])

db.program_weeks.create_index([("program_id", 1)])
db.program_weeks.create_index([("week_number", 1)])
db.program_weeks.create_index([("order", 1)])

db.program_items.create_index([("week_id", 1)])
db.program_items.create_index([("order", 1)])
db.program_items.create_index([("created_at", 1)])
db.program_items.create_index([("workout_id", 1)])


def get_styles() -> list[str]:
    styles = list(db.styles.find({"active": {"$ne": False}}).sort([("order", 1), ("name", 1)]))
    if styles:
        return [s["name"] for s in styles]
    return DEFAULT_WORKOUT_STYLES


def _ensure_style_seed_once():
    try:
        if db.styles.count_documents({}) == 0:
            docs = [
                {"name": n, "slug": slugify(n), "order": i, "active": True}
                for i, n in enumerate(DEFAULT_WORKOUT_STYLES)
            ]
            if docs:
                db.styles.insert_many(docs)
    except Exception as e:
        app.logger.warning(f"Styles seed skipped: {e}")


_ensure_style_seed_once()


# -----------------------------------------------------------------------------
# Quick menu (sidebar)
# -----------------------------------------------------------------------------
QUICK_OPTIONS = [
    {"label": "Favorites", "url": "/workouts?filter=favorites"},
    {"label": "Recently Added", "url": "/workouts?filter=recent"},
    {"label": "Top Rated", "url": "/workouts?filter=top"},
]


# -----------------------------------------------------------------------------
# Static/media serving for Render Disk
# -----------------------------------------------------------------------------
# IMPORTANT: serve from UPLOAD_ROOT_ABS (resolved), not raw MEDIA_ROOT,
# so relative MEDIA_ROOT works reliably.
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
# Public pages
# -----------------------------------------------------------------------------
@app.route("/")
def home():
    # Featured Programs on home (admin controlled later)
    featured_programs = list(
        db.programs.find({"active": {"$ne": False}, "show_on_home": True})
        .sort([("order", 1), ("created_at", -1)])
        .limit(6)
    )
    return render_template("home.html", name="NFG", featured_programs=featured_programs)


# ---------------------------------------------------------------------------
# Public: Programs (read-only)
# ---------------------------------------------------------------------------
@app.route("/programs")
def programs_index():
    programs = list(
        db.programs.find({"active": {"$ne": False}})
        .sort([("order", 1), ("created_at", -1)])
        .limit(100)
    )
    return render_template("programs.html", programs=programs)


@app.route("/programs/<slug>")
def program_detail(slug):
    program = db.programs.find_one({"slug": slug, "active": {"$ne": False}})
    if not program:
        abort(404)

    weeks = list(
        db.program_weeks.find({"program_id": program["_id"]})
        .sort([("week_number", 1), ("order", 1)])
    )

    week_ids = [w["_id"] for w in weeks]
    rows = []
    if week_ids:
        rows = list(
            db.program_items.find({"week_id": {"$in": week_ids}})
            .sort([("order", 1), ("created_at", 1)])
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
    rel_q = {
        "$and": [
            {"slug": {"$ne": w["slug"]}},
            {
                "$or": ([{"body_parts": {"$in": parts}}] if parts else [])
                + ([{"style": w.get("style")}] if w.get("style") else [])
            },
        ]
    }
    if not rel_q["$and"][1]["$or"]:
        rel_q = {"slug": {"$ne": w["slug"]}}

    related = list(
        db.workouts.find(rel_q).sort([("rating", -1), ("created_at", -1), ("name", 1)]).limit(6)
    )
    return render_template("workout_detail.html", w=w, related=related)


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
            login_user(User("admin"))
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
        body_part = body_parts[0] if body_parts else (request.form.get("body_part", "").strip() or "")
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
        except Exception as e:
            flash(f"Error: {e}", "danger")

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
        body_part = body_parts[0] if body_parts else (request.form.get("body_part", "").strip() or "")
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
        except Exception as e:
            flash(f"Error: {e}", "danger")

    data = dict(w)
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
# Admin: Home Plans (legacy) — keep for now so nothing breaks
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
            return render_template("admin_home_plan_form.html", data=request.form, edit=True, _id=id)

        if not cta_url:
            flash("Primary button URL is required.", "danger")
            return render_template("admin_home_plan_form.html", data=request.form, edit=True, _id=id)

        existing = db.home_plans.find_one({"slug": slug, "_id": {"$ne": ObjectId(id)}})
        if existing:
            flash(f"Slug '{slug}' already exists.", "danger")
            return render_template("admin_home_plan_form.html", data=request.form, edit=True, _id=id)

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
# Admin: Programs (NEW)
# -----------------------------------------------------------------------------
@app.route("/admin/programs")
@login_required
def admin_programs():
    programs = list(
        db.programs.find().sort(
            [
                ("active", -1),
                ("show_on_home", -1),
                ("order", 1),
                ("created_at", -1),
            ]
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

        if not title:
            flash("Title is required.", "danger")
            return render_template("admin_program_form.html", data=request.form, edit=False)

        if not slug:
            slug = slugify(title)

        if db.programs.find_one({"slug": slug}):
            flash(f"Slug '{slug}' already exists.", "danger")
            return render_template("admin_program_form.html", data=request.form, edit=False)

        doc = {
            "title": title,
            "slug": slug,
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
        summary = (request.form.get("summary") or "").strip() or None
        cover_image = (request.form.get("cover_image") or "").strip() or None

        order = int(request.form.get("order") or 0)
        active = request.form.get("active") == "on"
        show_on_home = request.form.get("show_on_home") == "on"

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

    # Cascade delete weeks + items so we don't leave orphan data.
    weeks = list(db.program_weeks.find({"program_id": prog["_id"]}, {"_id": 1}))
    week_ids = [w["_id"] for w in weeks]

    if week_ids:
        db.program_items.delete_many({"week_id": {"$in": week_ids}})
        db.program_weeks.delete_many({"_id": {"$in": week_ids}})

    db.programs.delete_one({"_id": prog["_id"]})

    flash("Program deleted.", "success")
    return redirect(url_for("admin_programs"))

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
    # In dev, serve MEDIA_ROOT too if set (mirrors production behavior)
    # NOTE: if MEDIA_ROOT is set, the route is already registered above.
    app.run(host="0.0.0.0", debug=True)
