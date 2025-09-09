"""
Seed the MongoDB with a few workouts & recipes.
Usage:
  export MONGO_URI="mongodb://localhost:27017/NFG"
  python seed.py
"""
from dotenv import load_dotenv
from pymongo import MongoClient
import os, datetime, re

load_dotenv()
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/NFG")
client = MongoClient(MONGO_URI)
db = client.get_default_database() if "/" in MONGO_URI.split("://",1)[-1] else client["NFG"]

def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

workouts = [
    {
        "name": "Push-Up",
        "level": "Beginner",
        "body_part": "Chest",
        "style": "BodyWeight",
        "is_favorite": True,
        "rating": 4.6,
        "tags": ["push", "chest", "bodyweight"],
        "images": ["/static/img/pushup_1.jpg", "/static/img/pushup_2.jpg"],  # gallery
        "muscle_image": "/static/img/muscles_chest.png",                      # target diagram
        "info": "Push-ups build chest, triceps, shoulders, and core. Great for endurance and stability.",
        "tips": [
            "Keep a straight line from head to heels.",
            "Elbows ~45° from your torso.",
            "Brace your core and squeeze glutes."
        ],
        "youtube_id": "dQw4w9WgXcQ",  # replace with your own YouTube ID
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
        "body_part": "Back",
        "style": "Barbell",
        "is_favorite": True,
        "rating": 4.9,
        "tags": ["hinge", "back", "hamstrings", "barbell"],
    },
]

# after your existing workouts list
workouts[0].update({
    "images": ["/static/img/pushup_1.jpg", "/static/img/pushup_2.jpg"],
    "muscle_image": "/static/img/muscles_chest.png",
    "info": "Push-ups build chest, triceps, shoulders, and core. Great for upper body endurance and stability.",
    "tips": [
        "Keep a straight line from head to heels.",
        "Elbows ~45° from torso; don't flare.",
        "Brace core, squeeze glutes, control the descent."
    ],
    "youtube_id": "dQw4w9WgXcQ"  # replace with your real video ID later
})


# stamp created_at and create slugs
now = datetime.datetime.utcnow()
for w in workouts:
    w["created_at"] = now
    w["slug"] = slugify(w["name"])

recipes = [
    {"name": "Protein Pancakes", "url": "https://example.com/protein-pancakes"},
    {"name": "Avocado Toast", "url": "https://example.com/avocado-toast"},
    {"name": "Green Smoothie Bowl", "url": "https://example.com/green-smoothie"},
]

db.workouts.drop()
db.recipes.drop()
db.workouts.insert_many(workouts)
db.recipes.insert_many(recipes)
print("Seeded: %d workouts, %d recipes" % (db.workouts.count_documents({}), db.recipes.count_documents({})))
