from flask import Flask, render_template, request, redirect, url_for, flash, abort
from pymongo import MongoClient, ASCENDING
from bson.regex import Regex
from dotenv import load_dotenv
import os, re, datetime

load_dotenv()
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/NFG")

client = MongoClient(MONGO_URI)
db = client.get_default_database() if "/" in MONGO_URI.split("://", 1)[-1] else client["NFG"]

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")

# ---------- NEW: canonical lists ----------
WORKOUT_LEVELS = ["Beginner", "Intermediate", "Advanced"]

WORKOUT_STYLES = [
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

FEATURED_BODY_PARTS = ["Chest", "Back", "Legs"]          # shown on landing (3 items)
FEATURED_STYLES = ["BodyWeight", "Barbell", "Machines"]  # shown on landing (3 items)

BODY_PARTS_MASTER = [
    "Chest","Back","Lats","Shoulders","Arms","Biceps","Triceps","Forearms",
    "Core","Abs","Obliques","Lower Back","Upper Back",
    "Legs","Quads","Hamstrings","Glutes","Calves","Hips",
    "Full Body","Neck"
]

FEATURED_BODY_PARTS = ["Chest","Back","Legs"]  # change these 3 any time

# ---------- helpers ----------
def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

# indexes (safe to call repeatedly)
db.workouts.create_index([("slug", 1)], unique=True, sparse=True)
db.workouts.create_index([("name", 1)])
db.workouts.create_index([("level", 1)])
db.workouts.create_index([("body_part", 1)])
db.workouts.create_index([("style", 1)])
db.workouts.create_index([("created_at", -1)])
db.workouts.create_index([("rating", -1)])

# quick menu
QUICK_OPTIONS = [
    {"label": "Favorites",      "url": "/workouts?filter=favorites"},
    {"label": "Recently Added", "url": "/workouts?filter=recent"},
    {"label": "Top Rated",      "url": "/workouts?filter=top"},
]
@app.context_processor
def inject_quick_options():
    return {"quick_options": QUICK_OPTIONS}

# ---------- public ----------
@app.route("/")
def home():
    return render_template("home.html", name="NFG")

@app.route("/workouts")
def workouts():
    # Quick filter options (Favorites / Recent / Top)
    filt = request.args.get("filter")
    query, sort, limit = {}, [("name", ASCENDING)], None
    if filt == "favorites":
        query["is_favorite"] = True
    elif filt == "recent":
        sort, limit = [("created_at", -1)], 20
    elif filt == "top":
        sort, limit = [("rating", -1), ("name", ASCENDING)], 20

    cursor = db.workouts.find(query).sort(sort)
    if limit:
        cursor = cursor.limit(limit)
    all_ws = list(cursor)

    # Featured Body Parts (3 items)
    FEATURED_BODY_PARTS = ["Chest", "Back", "Legs"]
    parts_in_db = sorted(set(db.workouts.distinct("body_part")))
    body_parts_featured = [p for p in FEATURED_BODY_PARTS if p in parts_in_db] or FEATURED_BODY_PARTS[:]

    # Featured Styles (3 items)
    FEATURED_STYLES = ["BodyWeight", "Barbell", "Machines"]
    styles_in_db = sorted(set(db.workouts.distinct("style")))
    # Always show featured styles, whether or not they exist in DB
    workout_styles_featured = FEATURED_STYLES

    # Full lists (for other pages)
    WORKOUT_LEVELS = ["Beginner", "Intermediate", "Advanced"]
    WORKOUT_STYLES = [
        "BodyWeight", "Barbell", "Dumbbell", "Kettlebell", "Resistance Bands",
        "Machines", "Calisthenics", "Cardio/Endurance",
        "Plyometric/Explosive", "CrossFit/Functional", "Yoga/Mobility",
    ]

    return render_template(
        "workouts.html",
        workout_levels=WORKOUT_LEVELS,
        body_parts_featured=body_parts_featured,
        workout_styles=WORKOUT_STYLES,              # full list
        workout_styles_featured=workout_styles_featured,  # just 3
        all_workouts=all_ws,
    )

# ---------- NEW: Body Parts index (lists them all) ----------
@app.route("/workouts/body-parts")
def body_parts_index():
    # Use canonical list so users discover options even if none seeded yet
    # Optionally add counts from DB
    counts = {bp: db.workouts.count_documents({"body_part": bp}) for bp in BODY_PARTS_MASTER}
    return render_template("body_parts_index.html", body_parts=BODY_PARTS_MASTER, counts=counts)

@app.route("/workouts/browse")
def workouts_browse():
    """Unified browse with filters, search, sort, pagination."""
    level = request.args.get("level") or ""
    body  = request.args.get("body") or ""
    style = request.args.get("style") or ""
    q     = (request.args.get("q") or "").strip()
    sort_key = request.args.get("sort", "name")  # name|recent|rating|favorites
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 20)), 1), 100)

    query = {}
    if level: query["level"] = level
    if body:  query["body_part"] = body
    if style: query["style"] = style
    if sort_key == "favorites":
        query["is_favorite"] = True
    if q:
        rx = Regex(q, "i")
        query["$or"] = [{"name": rx}, {"level": rx}, {"body_part": rx}, {"style": rx}, {"tags": rx}]

    sort = [("name", ASCENDING)]
    if sort_key == "recent": sort = [("created_at", -1)]
    elif sort_key == "rating": sort = [("rating", -1), ("name", ASCENDING)]

    total = db.workouts.count_documents(query)
    items = list(db.workouts.find(query).sort(sort).skip((page-1)*per_page).limit(per_page))

    return render_template(
        "browse_workouts.html",
        items=items, total=total, page=page, per_page=per_page, sort=sort_key,
        level=level, body=body, style=style, q=q,
        workout_levels=WORKOUT_LEVELS,
        body_parts=BODY_PARTS_MASTER,                 # use canonical set in filters
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
        "$or": [{"name": rx},{"level": rx},{"body_part": rx},{"style": rx},{"tags": rx}]
    }).sort([("name", ASCENDING)]))
    rs = list(db.recipes.find({"name": rx}).sort([("name", ASCENDING)]))
    return render_template("search_results.html", q=q, workouts=ws, recipes=rs)

# ---------- Admin (unchanged from last step; you can keep your version) ----------
@app.route("/admin")
def admin_index():
    items = list(db.workouts.find().sort([("created_at", -1)]))
    return render_template("admin_index.html", items=items)

@app.route("/workouts/styles")
def styles_index():
    # Uses the canonical list already defined near the top: WORKOUT_STYLES
    counts = {st: db.workouts.count_documents({"style": st}) for st in WORKOUT_STYLES}
    return render_template("styles_index.html", styles=WORKOUT_STYLES, counts=counts)


@app.route("/admin/workouts/new", methods=["GET", "POST"])
def admin_workout_new():
    if request.method == "POST":
        name = request.form["name"].strip()
        level = request.form["level"].strip()
        body_part = request.form["body_part"].strip()
        style = request.form["style"].strip()
        tags = [t.strip() for t in (request.form.get("tags") or "").split(",") if t.strip()]
        is_favorite = request.form.get("is_favorite") == "on"
        rating = float(request.form.get("rating") or 0)
        slug = request.form.get("slug") or slugify(name)
        doc = {
            "name": name, "slug": slug, "level": level,
            "body_part": body_part, "style": style,
            "tags": tags, "is_favorite": is_favorite,
            "rating": rating, "created_at": datetime.datetime.utcnow()
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
        styles=WORKOUT_STYLES
    )

if __name__ == "__main__":
    app.run(debug=True)
