"""
Seed the MongoDB with example workouts & recipes.

Usage (local):
  export MONGO_URI="mongodb://localhost:27017"
  export MONGO_DB="NFG"           # optional if DB is embedded in URI
  python3 seed.py                 # safe upsert (no drops)
  python3 seed.py --drop          # dev reset: drop & reinsert

Usage (Render / production):
  Set MONGO_URI and (optionally) MONGO_DB in environment variables and run:
  python3 seed.py                 # SAFE upsert (recommended)
  (You can pass --drop only for development environments.)

Flags:
  -d, --drop            Drop collections (workouts, recipes) before seeding (DEV ONLY)
  -q, --quiet           Less verbose output
  --no-placeholders     Don’t add placeholder images when missing
  --keep-placeholders   Force placeholder images when missing (default)
"""
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
import os
import datetime
import re
import sys
import argparse

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", None)  # optional override

def get_db():
    client = MongoClient(MONGO_URI)
    # Determine database: prefer MONGO_DB, then URI default, else "NFG"
    if MONGO_DB:
        return client[MONGO_DB]
    try:
        db = client.get_default_database()
        if db is None or db.name is None:
            db = client["NFG"]
        return db
    except Exception:
        return client["NFG"]

def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", text)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")

def ensure_indexes(db, quiet=False):
    try:
        db.workouts.create_index([("slug", ASCENDING)], unique=True, name="slug_unique")
        db.recipes.create_index([("slug", ASCENDING)], unique=True, name="slug_unique")
        if not quiet:
            print("✓ Indexes ensured.")
    except Exception as e:
        print("Warning: could not create indexes:", e)

def make_default_image(name):
    key = re.sub(r"\W+", "+", (name or "").strip()) or "Workout"
    return f"https://via.placeholder.com/900x1600?text={key}"

def seed_data(now, use_placeholders=True):
    workouts = [
        {
            "name": "Push-Up",
            "level": "Beginner",
            "body_part": "Chest",
            "style": "BodyWeight",
            "is_favorite": True,
            "rating": 4.6,
            "tags": ["push", "chest", "bodyweight"],
            "images": [],
            "muscle_image": None,
            "info": "Push-ups build chest, triceps, shoulders, and core. Great for endurance and stability.",
            "tips": [
                "Keep a straight line from head to heels.",
                "Elbows ~45° from your torso.",
                "Brace your core and squeeze glutes."
            ],
            "youtube_id": "",  # optionally add a valid ID
        },
        {
            "name": "Goblet Squat",
            "level": "Beginner",
            "body_part": "Legs",
            "style": "Dumbbell",
            "is_favorite": False,
            "rating": 4.2,
            "tags": ["squat", "legs", "dumbbell"],
        },
        {
            "name": "Barbell Row",
            "level": "Intermediate",
            "body_part": "Back",
            "style": "Barbell",
            "is_favorite": False,
            "rating": 4.5,
            "tags": ["row", "back", "barbell", "lats"],
        },
        {
            "name": "Deadlift",
            "level": "Advanced",
            "body_part": "Posterior Chain",
            "style": "Barbell",
            "is_favorite": True,
            "rating": 4.9,
            "tags": ["hinge", "back", "hamstrings", "barbell"],
        },
    ]

    for w in workouts:
        if use_placeholders:
            if not w.get("images"):
                w["images"] = [make_default_image(w["name"])]
            if not w.get("muscle_image"):
                w["muscle_image"] = make_default_image((w["name"] or "") + "+muscle")
        w["created_at"] = now
        w["slug"] = slugify(w["name"])
        w.setdefault("tips", [])
        w.setdefault("tags", [])
        w.setdefault("youtube_id", "")

    recipes = [
        {"name": "Protein Pancakes", "url": "https://example.com/protein-pancakes"},
        {"name": "Avocado Toast", "url": "https://example.com/avocado-toast"},
        {"name": "Green Smoothie Bowl", "url": "https://example.com/green-smoothie"},
    ]
    for r in recipes:
        r["slug"] = slugify(r["name"])
        r["created_at"] = now

    return workouts, recipes

def parse_args():
    p = argparse.ArgumentParser(description="Seed MongoDB with sample NFG data.")
    p.add_argument("-d", "--drop", action="store_true",
                   help="Drop collections (workouts, recipes) before seeding (DEV ONLY).")
    p.add_argument("-q", "--quiet", action="store_true", help="Reduce output.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--no-placeholders", action="store_true", help="Do NOT add placeholder images.")
    g.add_argument("--keep-placeholders", action="store_true", help="Force placeholder images (default).")
    return p.parse_args()

def main():
    args = parse_args()
    quiet = args.quiet
    use_placeholders = not args.no_placeholders  # default True

    db = get_db()
    if not quiet:
        # Don’t print full URI (avoid leaking creds)
        print(f"DB target: {db.name}")

    ensure_indexes(db, quiet=quiet)

    if args.drop:
        if not quiet:
            print("Dropping existing collections (workouts, recipes) — DEV RESET.")
        try:
            db.workouts.drop()
            db.recipes.drop()
        except Exception as e:
            print("Warning dropping collections:", e)
        ensure_indexes(db, quiet=quiet)

    now = datetime.datetime.utcnow()
    workouts, recipes = seed_data(now, use_placeholders=use_placeholders)

    # Upsert workouts by slug (safe default)
    upserted_w = 0
    for w in workouts:
        try:
            res = db.workouts.replace_one({"slug": w["slug"]}, w, upsert=True)
            if res.upserted_id or res.modified_count:
                upserted_w += 1
        except DuplicateKeyError:
            db.workouts.replace_one({"slug": w["slug"]}, w, upsert=True)
            upserted_w += 1
        except Exception as e:
            print(f"Workout upsert failed ({w.get('name')}):", e)

    # Upsert recipes by slug
    upserted_r = 0
    for r in recipes:
        try:
            res = db.recipes.replace_one({"slug": r["slug"]}, r, upsert=True)
            if res.upserted_id or res.modified_count:
                upserted_r += 1
        except Exception as e:
            print(f"Recipe upsert failed ({r.get('name')}):", e)

    if not quiet:
        print(f"Upserted/updated: {upserted_w} workouts, {upserted_r} recipes.")
        print(f"Totals now: {db.workouts.count_documents({})} workouts, {db.recipes.count_documents({})} recipes.")
        print("Seed complete.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Seeding failed:", exc, file=sys.stderr)
        sys.exit(1)
