"""
Seed the MongoDB with example recipes.

Exercises are NOT seeded here any more: they are content files in
content/exercises/ synced by content_sync.py (see content/README.md).

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
  -d, --drop            Drop the recipes collection before seeding (DEV ONLY)
  -q, --quiet           Less verbose output
  --no-placeholders / --keep-placeholders   accepted for compatibility; no effect
"""

import argparse
import datetime
import os
import re
import sys

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient

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


def seed_data(now):
    recipes = [
        {"name": "Protein Pancakes", "url": "https://example.com/protein-pancakes"},
        {"name": "Avocado Toast", "url": "https://example.com/avocado-toast"},
        {"name": "Green Smoothie Bowl", "url": "https://example.com/green-smoothie"},
    ]
    for r in recipes:
        r["slug"] = slugify(r["name"])
        r["created_at"] = now

    return recipes


def parse_args():
    p = argparse.ArgumentParser(description="Seed MongoDB with sample NFG data.")
    p.add_argument(
        "-d",
        "--drop",
        action="store_true",
        help="Drop the recipes collection before seeding (DEV ONLY).",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="Reduce output.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--no-placeholders", action="store_true", help="No effect (compatibility).")
    g.add_argument("--keep-placeholders", action="store_true", help="No effect (compatibility).")
    return p.parse_args()


def main():
    args = parse_args()
    quiet = args.quiet

    db = get_db()
    if not quiet:
        # Don’t print full URI (avoid leaking creds)
        print(f"DB target: {db.name}")

    ensure_indexes(db, quiet=quiet)

    if args.drop:
        if not quiet:
            print("Dropping recipes collection — DEV RESET (exercises are left alone).")
        try:
            db.recipes.drop()
        except Exception as e:
            print("Warning dropping collections:", e)
        ensure_indexes(db, quiet=quiet)

    now = datetime.datetime.utcnow()
    recipes = seed_data(now)

    # Exercises are no longer seeded here. They live in content/exercises/*.yaml
    # and are synced with content_sync.py, which (unlike replace_one) never wipes
    # admin edits or uploaded images.
    if not quiet:
        print("Exercises: skipped — use `python content_sync.py sync` (content/README.md).")

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
        print(f"Upserted/updated: {upserted_r} recipes.")
        print(
            f"Totals now: {db.workouts.count_documents({})} workouts, {db.recipes.count_documents({})} recipes."
        )
        print("Seed complete.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Seeding failed:", exc, file=sys.stderr)
        sys.exit(1)
