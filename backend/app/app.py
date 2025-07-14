from flask import Flask, render_template

app = Flask(__name__)

# 1️⃣ Define your menu once
QUICK_OPTIONS = [
    {"label": "Favorites",      "url": "/workouts?filter=favorites"},
    {"label": "Recently Added", "url": "/workouts?filter=recent"},
    {"label": "Top Rated",      "url": "/workouts?filter=top"},
]

# 2️⃣ Inject into every template’s context automatically
@app.context_processor
def inject_quick_options():
    return {"quick_options": QUICK_OPTIONS}

@app.route("/")
def home():
    return render_template("home.html", name="Nathnael")

@app.route("/workouts")
def workouts():
    workout_levels = ["Beginner", "Intermediate", "Advanced"]
    body_parts     = ["Chest", "Back", "Legs", "Arms"]
    workout_styles = ["Strength", "HIIT", "Yoga"]
    all_workouts   = [
        {"name": "Full Body Circuit"},
        {"name": "Leg Day Blast"},
        {"name": "Upper Body Strength"},
        {"name": "Core Crusher"}
    ]
    return render_template(
        "workouts.html",
        workout_levels=workout_levels,
        body_parts=body_parts,
        workout_styles=workout_styles,
        all_workouts=all_workouts
    )

@app.route("/recipes")
def recipes():
    recipes = [
        {"name": "Protein Pancakes",     "url": "#"},
        {"name": "Avocado Toast",        "url": "#"},
        {"name": "Green Smoothie Bowl",  "url": "#"},
        {"name": "Grilled Chicken Salad","url": "#"}
    ]
    return render_template("recipes.html", recipes=recipes)

if __name__ == "__main__":
    app.run(debug=True)
