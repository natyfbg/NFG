from flask import Flask, render_template, request, redirect, url_for, flash, abort
from pymongo import MongoClient, ASCENDING
from bson.regex import Regex
from bson.objectid import ObjectId
from dotenv import load_dotenv
import os, re, datetime

from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import check_password_hash

# -----------------------------------------------------------------------------
# Config / DB
# -----------------------------------------------------------------------------
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/NFG")

client = MongoClient(MONGO_URI)
db = client.get_default_database() if "/" in MONGO_URI.split("://", 1)[-1] else client["NFG"]

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")

# ---- Auth config (basic single-admin) ----
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")          # plain text (MVP/dev)
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")        # preferred in prod

login_manager = LoginManager(app)
login_manager.login_view = "login"  # redirect here when not logged in

class User(UserMixin):
    def __init__(self, user_id: str):
        self.id = user_id

@login_manager.user_loader
def load_user(user_id):
    if user_id == "admin":
        return User("admin")
    return None

def _check_admin_credentials(username: str, password: str) -> bool:
    """Prefer a hashed password if provided; fall back to plain for dev."""
    if username != ADMIN_USERNAME:
        return False
    if ADMIN_PASSWORD_HASH:
        return check_password_hash(ADMIN_PASSWORD_HASH, password)
    return password == ADMIN_PASSWORD

# -----------------------------------------------------------------------------
# Canonical lists
# -----------------------------------------------------------------------------
WORKOUT_LEVELS = ["Beginner", "Intermediate", "Advanced"]

WORKOUT_STYLES = [
    "BodyWeight", "Barbell", "Dumbbell", "Kettlebell", "Resistance Bands",
    "Machines", "Calisthenics", "Cardio/Endurance",
    "Plyometric/Explosive", "CrossFit/Functional", "Yoga/Mobility",
]

BODY_PARTS_MASTER = [
    "Chest","Back","Lats","Shoulders","Arms","Biceps","Triceps","Forearms",
    "Core","Abs","Obliques","Lower Back","Upper Back",
    "Legs","Quads","Hamstrings","Glutes","Calves","Hips",
    "Full Body","Neck"
]

FEATURED_BODY_PARTS = ["Chest", "Back", "Legs"]               # landing card (3)
FEATURED_STYLES     = ["BodyWeight", "Barbell", "Machines"]   # landing card (3)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

def _split_list(text: str):
    """Accept comma or newline separated; trim blanks."""
    if not text:
        return []
    return [p.strip() for p in re.split(r"[\n,]+", text) if p.strip()]

_YT_PAT = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/))?([A-Za-z0-9_-]{11})")

def _extract_youtube_id(val: str | None):
    if not val:
        return None
    m = _YT_PAT.search(val.strip())
    return m.group(1) if m else val.strip()

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

# -----------------------------------------------------------------------------
# Quick menu
# -----------------------------------------------------------------------------
QUICK_OPTIONS = [
    {"label": "Favorites",      "url": "/workouts?filter=favorites"},
    {"label": "Recently Added", "url": "/workouts?filter=recent"},
    {"label": "Top Rated",      "url": "/workouts?filter=top"},
]
@app.context_processor
def inject_quick_options():
    return {"quick_options": QUICK_OPTIONS}

# -----------------------------------------------------------------------------
# Public
# -----------------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("home.html", name="NFG")

@app.route("/workouts")
def workouts():
    # Sidebar quick filters (kept for future use)
    filt = request.args.get("filter")
    query, sort, limit = {}, [("name", ASCENDING)], None
    if filt == "favorites":
        query["is_favorite"] = True
    elif filt == "recent":
        sort, limit = [("created_at", -1)], 20
    elif filt == "top":
        sort, limit = [("rating", -1), ("name", ASCENDING)], 20

    # Featured Body Parts (support legacy single + new list)
    parts_single = set(db.workouts.distinct("body_part"))
    parts_multi  = set(db.workouts.distinct("body_parts"))
    parts_in_db  = parts_single | parts_multi
    body_parts_featured = [p for p in FEATURED_BODY_PARTS if p in parts_in_db] or FEATURED_BODY_PARTS[:]

    # Featured Styles: always show the three picks
    workout_styles_featured = FEATURED_STYLES

    # Limit landing "All Workouts (A–Z)" card to 3 items
    all_ws = list(db.workouts.find({}).sort([("name", ASCENDING)]).limit(3))

    return render_template(
        "workouts.html",
        workout_levels=WORKOUT_LEVELS,
        body_parts_featured=body_parts_featured,
        workout_styles=WORKOUT_STYLES,                  # full list
        workout_styles_featured=workout_styles_featured, # 3 on landing
        all_workouts=all_ws,
    )

@app.route("/workouts/all")
def workouts_all():
    items = list(db.workouts.find({}).sort([("name", ASCENDING)]))
    return render_template("all_workouts_index.html", items=items)

@app.route("/workouts/styles")
def styles_index():
    counts = {st: db.workouts.count_documents({"style": st}) for st in WORKOUT_STYLES}
    return render_template("styles_index.html", styles=WORKOUT_STYLES, counts=counts)

@app.route("/workouts/body-parts")
def body_parts_index():
    counts = {
        bp: db.workouts.count_documents({"$or": [{"body_part": bp}, {"body_parts": bp}]})
        for bp in BODY_PARTS_MASTER
    }
    return render_template("body_parts_index.html", body_parts=BODY_PARTS_MASTER, counts=counts)

@app.route("/workouts/browse")
def workouts_browse():
    """Unified browse with filters, search, sort, pagination."""
    level    = request.args.get("level") or ""
    body     = request.args.get("body") or ""
    style    = request.args.get("style") or ""
    q        = (request.args.get("q") or "").strip()
    sort_key = request.args.get("sort", "name")  # name|recent|rating|favorites
    page     = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 20)), 1), 100)

    # Build filters using $and so $or blocks don't overwrite each other
    and_clauses = []

    if level:
        and_clauses.append({"level": level})

    if style:
        and_clauses.append({"style": style})

    if body:
        and_clauses.append({
            "$or": [
                {"body_part": body},
                {"body_parts": body},
            ]
        })

    if sort_key == "favorites":
        and_clauses.append({"is_favorite": True})

    if q:
        rx = Regex(q, "i")
        and_clauses.append({
            "$or": [
                {"name": rx},
                {"level": rx},
                {"body_part": rx},
                {"body_parts": rx},
                {"style": rx},
                {"tags": rx},
            ]
        })

    query = {"$and": and_clauses} if and_clauses else {}

    # Sorting
    sort = [("name", ASCENDING)]
    if sort_key == "recent":
        sort = [("created_at", -1)]
    elif sort_key == "rating":
        sort = [("rating", -1), ("name", ASCENDING)]

    total = db.workouts.count_documents(query)
    items = list(
        db.workouts.find(query)
        .sort(sort)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )

    return render_template(
        "browse_workouts.html",
        items=items, total=total, page=page, per_page=per_page, sort=sort_key,
        level=level, body=body, style=style, q=q,
        workout_levels=WORKOUT_LEVELS,
        body_parts=BODY_PARTS_MASTER,
        workout_styles=WORKOUT_STYLES
    )

@app.route("/workouts/<slug>")
def workout_detail(slug):
    w = db.workouts.find_one({"slug": slug})
    if not w: abort(404)
    return render_template("workout_detail.html", w=w)

@app.route("/recipes")
def recipes():
    recs = list(db.recipes.find().sort([("name", ASCENDING)]))
    return render_template("recipes.html", recipes=recs)

@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return render_template("home.html", name="NFG")
    rx = Regex(q, "i")
    ws = list(db.workouts.find({
        "$or": [
            {"name": rx},
            {"level": rx},
            {"body_part": rx},
            {"body_parts": rx},   # include list field
            {"style": rx},
            {"tags": rx}
        ]
    }).sort([("name", ASCENDING)]))
    rs = list(db.recipes.find({"name": rx}).sort([("name", ASCENDING)]))
    return render_template("search_results.html", q=q, workouts=ws, recipes=rs)

# -----------------------------------------------------------------------------
# Auth (login/logout)
# -----------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if _check_admin_credentials(username, password):
            login_user(User("admin"))
            flash("Logged in.", "success")
            next_url = request.args.get("next") or url_for("admin_index")
            return redirect(next_url)
        else:
            flash("Invalid credentials.", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("home"))

# -----------------------------------------------------------------------------
# Admin (protected)
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
        name = request.form.get("name","").strip()
        level = request.form.get("level","").strip()
        style = request.form.get("style","").strip()

        # multiple muscles
        body_parts = _split_list(request.form.get("body_parts",""))
        body_part  = body_parts[0] if body_parts else (request.form.get("body_part","").strip() or "")

        tags = _split_list(request.form.get("tags",""))
        images = _split_list(request.form.get("images",""))
        muscle_image = (request.form.get("muscle_image") or "").strip() or None
        info = (request.form.get("info") or "").strip() or None
        tips = _split_list(request.form.get("tips",""))
        youtube_id = _extract_youtube_id(request.form.get("youtube_id"))
        is_favorite = request.form.get("is_favorite") == "on"
        rating = float(request.form.get("rating") or 0)
        slug = (request.form.get("slug") or slugify(name))

        if not name:
            flash("Name is required.", "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS, parts=BODY_PARTS_MASTER, styles=WORKOUT_STYLES,
                data=request.form
            )

        doc = {
            "name": name, "slug": slug, "level": level,
            "body_part": body_part,           # legacy primary
            "body_parts": body_parts,         # list
            "style": style,
            "tags": tags, "images": images, "muscle_image": muscle_image,
            "info": info, "tips": tips, "youtube_id": youtube_id,
            "is_favorite": is_favorite, "rating": rating,
            "created_at": datetime.datetime.utcnow()
        }
        try:
            db.workouts.insert_one(doc)
            flash("Workout added.", "success")
            return redirect(url_for("admin_index"))
        except Exception as e:
            flash(f"Error: {e}", "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS, parts=BODY_PARTS_MASTER, styles=WORKOUT_STYLES,
                data=request.form
            )

    # GET
    return render_template(
        "admin_workout_form.html",
        levels=WORKOUT_LEVELS, parts=BODY_PARTS_MASTER, styles=WORKOUT_STYLES,
        data={}
    )

@app.route("/admin/workouts/<id>/edit", methods=["GET", "POST"])
@login_required
def admin_workout_edit(id):
    w = db.workouts.find_one({"_id": ObjectId(id)})
    if not w:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name","").strip()
        level = request.form.get("level","").strip()
        style = request.form.get("style","").strip()

        # multiple muscles
        body_parts = _split_list(request.form.get("body_parts",""))
        body_part  = body_parts[0] if body_parts else (request.form.get("body_part","").strip() or "")

        tags = _split_list(request.form.get("tags",""))
        images = _split_list(request.form.get("images",""))
        muscle_image = (request.form.get("muscle_image") or "").strip() or None
        info = (request.form.get("info") or "").strip() or None
        tips = _split_list(request.form.get("tips",""))
        youtube_id = _extract_youtube_id(request.form.get("youtube_id"))
        is_favorite = request.form.get("is_favorite") == "on"
        rating = float(request.form.get("rating") or 0)
        slug = (request.form.get("slug") or slugify(name))

        if not name:
            flash("Name is required.", "danger")
            return render_template(
                "admin_workout_form.html",
                levels=WORKOUT_LEVELS, parts=BODY_PARTS_MASTER, styles=WORKOUT_STYLES,
                data=request.form, edit=True, _id=id
            )

        update = {
            "name": name, "slug": slug, "level": level,
            "body_part": body_part,          # legacy primary
            "body_parts": body_parts,        # list
            "style": style, "tags": tags,
            "images": images, "muscle_image": muscle_image,
            "info": info, "tips": tips, "youtube_id": youtube_id,
            "is_favorite": is_favorite, "rating": rating,
        }
        try:
            db.workouts.update_one({"_id": ObjectId(id)}, {"$set": update})
            flash("Workout updated.", "success")
            return redirect(url_for("admin_index"))
        except Exception as e:
            flash(f"Error: {e}", "danger")

    # GET populate form
    data = dict(w)
    data["tags"]   = ", ".join(data.get("tags", []))
    data["images"] = "\n".join(data.get("images", []))
    data["tips"]   = "\n".join(data.get("tips", []))
    if isinstance(data.get("body_parts"), list):
        data["body_parts"] = ", ".join(data["body_parts"])
    else:
        data["body_parts"] = data.get("body_parts") or data.get("body_part","")

    return render_template(
        "admin_workout_form.html",
        levels=WORKOUT_LEVELS, parts=BODY_PARTS_MASTER, styles=WORKOUT_STYLES,
        data=data, edit=True, _id=id
    )

@app.route("/admin/workouts/<id>/delete", methods=["POST"])
@login_required
def admin_workout_delete(id):
    db.workouts.delete_one({"_id": ObjectId(id)})
    flash("Workout deleted.", "success")
    return redirect(url_for("admin_index"))

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
