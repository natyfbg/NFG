"""Recipes (content files), the macros maths, the tracker and guest → member moves."""

import copy
import datetime as dt

import macros as mx
import nutrition
import pytest
import recipes_content as rc

GUEST = "guest-macros-1"
NUTRITION_COLLECTIONS = ("macro_profiles", "food_log", "weight_log")

PROFILE_FORM = {
    "units": "imperial",
    "sex": "male",
    "age": "32",
    "height_ft": "5",
    "height_in": "10",
    "weight_lb": "185",
    "activity": "moderate",
    "goal": "cut",
}


@pytest.fixture
def guest(client, nfg_app):
    client.set_cookie("nfg_vid", GUEST)
    yield client
    for name in NUTRITION_COLLECTIONS:
        nfg_app.db[name].delete_many({})


@pytest.fixture
def with_profile(guest):
    resp = guest.post("/macros/profile", data=PROFILE_FORM)
    assert resp.status_code == 302
    return guest


def _today():
    return dt.datetime.utcnow().date()


# --------------------------------------------------------------------------
# Recipe content
# --------------------------------------------------------------------------
def test_every_recipe_loads_with_30g_protein_or_more():
    recipes, foods = rc.load_recipes()
    assert len(recipes) >= 24
    assert {r["meal"] for r in recipes} == set(rc.MEALS)
    for r in recipes:
        assert r["macros"]["protein"] >= rc.MIN_PROTEIN, r["slug"]
        assert r["goals"] and set(r["goals"]) <= set(rc.GOALS)
        assert "high-protein" in r["tags"]


def test_macros_come_from_the_ingredients():
    recipes, foods = rc.load_recipes()
    oats = next(r for r in recipes if r["slug"] == "protein-overnight-oats")
    # 50 g oats + 30 g whey + 100 g yogurt + 150 ml milk + 75 g blueberries + 10 g chia
    protein = (50 * 13.2 + 30 * 80 + 100 * 10.2 + 150 * 3.4 + 75 * 0.7 + 10 * 16.5) / 100
    assert oats["macros"]["protein"] == round(protein)
    assert "vegetarian" in oats["tags"] and "quick" in oats["tags"]
    chili = next(r for r in recipes if r["slug"] == "turkey-black-bean-chili")
    assert "vegetarian" not in chili["tags"] and chili["servings"] == 6


@pytest.mark.parametrize(
    "change, message",
    [
        (
            lambda r: r["ingredients"].append(
                {"amount": 1, "unit": "g", "item": "x", "food": "nope"}
            ),
            "no food",
        ),
        (lambda r: r.update(meal="brunch"), "meal must be"),
        (
            lambda r: r.update(
                ingredients=[{"amount": 50, "unit": "g", "item": "oats", "food": "oats"}]
            ),
            "protein per serving",
        ),
        (
            lambda r: r["ingredients"].append({"amount": 2, "item": "eggs", "food": "egg"}),
            "needs grams",
        ),
        (lambda r: r.update(tags=["keto"]), "unknown tag"),
    ],
)
def test_recipe_validation_catches_mistakes(change, message):
    import yaml

    foods = rc.load_foods()
    raw = yaml.safe_load((rc.RECIPES_DIR / "protein-overnight-oats.yaml").read_text())
    raw = copy.deepcopy(raw)
    change(raw)
    errors = rc.validate_recipe(raw, "protein-overnight-oats.yaml", foods)
    assert any(message in e for e in errors), errors


def test_scaling_keeps_lines_readable():
    assert rc.format_amount(1.5, "tbsp") == "1½"
    assert rc.format_amount(0.25, None) == "¼"
    assert rc.format_amount(452, "g") == "450"
    assert rc.format_amount(2 / 3, "cup") == "⅔"
    egg = {"amount": 2, "item": "large egg|large eggs", "food": "egg", "grams": 100}
    assert rc.ingredient_line(egg)["text"] == "2 large eggs"
    assert rc.ingredient_line(egg, 0.5)["text"] == "1 large egg"
    whey = {"amount": 30, "unit": "g", "item": "whey protein", "food": "whey", "note": "1 scoop"}
    assert rc.ingredient_line(whey)["note"] == "1 scoop"
    assert rc.ingredient_line(whey, 2)["note"] == ""  # "1 scoop" isn't true at 60 g


# --------------------------------------------------------------------------
# Macros maths
# --------------------------------------------------------------------------
def _profile(**over):
    p, errors = mx.parse_profile(dict(PROFILE_FORM, **over))
    assert not errors, errors
    return p


def test_targets_follow_mifflin_st_jeor():
    p = _profile()
    assert p["height_cm"] == pytest.approx(177.8) and p["weight_kg"] == pytest.approx(
        83.91, abs=0.01
    )
    bmr = 10 * p["weight_kg"] + 6.25 * p["height_cm"] - 5 * 32 + 5
    t = mx.targets(p, "maintain")
    assert t["tdee"] == int(round(bmr * 1.55 / 10) * 10)
    cut, bulk = mx.targets(p, "cut"), mx.targets(p, "bulk")
    assert cut["kcal"] == int(round(bmr * 1.55 * 0.8 / 10) * 10)
    assert bulk["kcal"] == int(round(bmr * 1.55 * 1.1 / 10) * 10)
    assert cut["protein"] == round(2.2 * p["weight_kg"])
    for goal_t in (cut, t, bulk):  # the macros add up to the calories
        assert (
            abs(goal_t["protein"] * 4 + goal_t["carbs"] * 4 + goal_t["fat"] * 9 - goal_t["kcal"])
            < 15
        )


def test_targets_stay_sensible_at_the_edges():
    small = _profile(
        sex="female", age="60", height_ft="4", height_in="11", weight_lb="95", activity="sedentary"
    )
    assert mx.targets(small, "cut")["kcal"] >= mx.KCAL_FLOOR["female"]
    big = _profile(weight_lb="400")  # BMI ~57: protein from the weight at BMI 30
    assert mx.targets(big, "cut")["protein"] == round(2.2 * 30 * 1.778**2)


def test_profile_form_errors_and_metric_units():
    _, errors = mx.parse_profile(dict(PROFILE_FORM, age="15", sex="", weight_lb="abc"))
    assert {"age", "sex", "weight"} <= set(errors)
    p, errors = mx.parse_profile(
        {
            "units": "metric",
            "sex": "female",
            "age": "28",
            "height_cm": "165",
            "weight_kg": "62",
            "activity": "light",
        }
    )
    assert not errors and p["goal"] == "maintain" and p["height_cm"] == 165


def test_navy_body_fat_and_entry_parsing():
    p = _profile(waist="36", neck="16")
    assert mx.body_fat(p) == pytest.approx(19.3, abs=0.3)
    assert mx.body_fat(_profile()) is None
    entry, errors = mx.parse_entry({"protein": "30", "carbs": "40", "fat": "10"})
    assert not errors and entry["kcal"] == 370 and entry["name"] == "Quick add"
    assert mx.parse_entry({"kcal": "-5"})[1]
    assert mx.parse_entry({})[1]


def test_history_and_projection():
    p = _profile()
    t = mx.targets(p, "cut")
    today = dt.date(2026, 11, 17)
    on = {"kcal": t["kcal"], "protein": t["protein"], "carbs": 200, "fat": 60}
    off = {"kcal": t["kcal"] + 600, "protein": 100, "carbs": 300, "fat": 90}
    by_date = {"2026-11-16": on, "2026-11-15": on, "2026-11-14": off}
    week = mx.history(by_date, t, today, "week")
    assert len(week["bars"]) == 7 and week["logged_days"] == 3 and week["days_on_target"] == 2
    assert len(mx.history(by_date, t, today, "year")["bars"]) == 52
    assert mx.recent_intake(by_date, today) == pytest.approx((2 * t["kcal"] + t["kcal"] + 600) / 3)
    cut = mx.projection(p, "cut", 12)
    bulk = mx.projection(p, "bulk", 12)
    assert cut["change_kg"] < 0 < bulk["change_kg"] and len(cut["points"]) == 13
    starving = mx.projection(p, "cut", 52, intake_kcal=800)
    assert starving["floor_reached"]  # never projects below a healthy BMI
    floor = 18.5 * 1.778**2
    assert min(pt["weight_kg"] for pt in starving["points"]) >= floor - 0.01


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
def test_recipes_page_lists_and_filters(guest):
    html = guest.get("/recipes").get_data(as_text=True)
    assert "Work out your macros" in html
    assert html.count('class="nfg-rcard"') == len(nutrition.RECIPES)
    dinner = guest.get("/recipes?meal=dinner").get_data(as_text=True)
    assert "Sheet-Pan Chicken Fajitas" in dinner and "Protein Overnight Oats" not in dinner
    veg = guest.get("/recipes?tag=vegetarian").get_data(as_text=True)
    assert "Peanut Tofu Noodles" in veg and "Turkey Black Bean Chili" not in veg


def test_recipe_page_scales_servings(guest):
    html = guest.get("/recipes/chicken-burrito-bowls").get_data(as_text=True)
    assert "600 g" in html and "Add to today" in html and "(about 3 cups)" in html
    double = guest.get("/recipes/chicken-burrito-bowls?servings=8").get_data(as_text=True)
    assert "1200 g" in double or "1,200 g" in double
    assert guest.get("/recipes/not-a-recipe").status_code == 404


def test_macros_starts_with_the_calculator(guest):
    html = guest.get("/macros").get_data(as_text=True)
    assert "Work out my macros" in html and 'name="activity"' in html
    resp = guest.post("/macros/profile", data=dict(PROFILE_FORM, age="12"))
    assert resp.status_code == 400 and "This calculator is for adults" in resp.get_data(
        as_text=True
    )


def test_saving_a_profile_shows_the_tracker(with_profile, nfg_app):
    doc = nfg_app.db.macro_profiles.find_one({"viewer_id": GUEST})
    assert doc["goal"] == "cut" and doc["units"] == "imperial"
    assert nfg_app.db.weight_log.count_documents({"viewer_id": GUEST}) == 1
    html = with_profile.get("/macros").get_data(as_text=True)
    t = mx.targets(doc)
    assert f"of {t['kcal']:,}" in html and "Today's food" in html and "Your numbers" in html
    assert "Recalculate" in html


def test_adding_food_from_a_recipe_and_by_hand(with_profile, nfg_app):
    db = nfg_app.db
    with_profile.post("/macros/log", data={"recipe": "protein-overnight-oats", "servings": "2"})
    with_profile.post(
        "/macros/log", data={"name": "Chicken wrap", "protein": "38", "carbs": "42", "fat": "14"}
    )
    rows = list(db.food_log.find({"viewer_id": GUEST}))
    assert len(rows) == 2 and all(r["date"] == _today().isoformat() for r in rows)
    oats = next(r for r in rows if r.get("recipe_slug"))
    recipe = nutrition.BY_SLUG["protein-overnight-oats"]
    assert oats["protein"] == pytest.approx(recipe["macros_exact"]["protein"] * 2, abs=0.1)
    html = with_profile.get("/macros").get_data(as_text=True)
    assert "Chicken wrap" in html and "Protein Overnight Oats" in html
    # Remove one; someone else's id does nothing.
    wrap = next(r for r in rows if not r.get("recipe_slug"))
    other = db.food_log.insert_one(
        {"viewer_id": "someone-else", "date": "2026-01-01", "name": "x", "kcal": 1}
    )
    with_profile.post(f"/macros/log/{other.inserted_id}/delete")
    with_profile.post(f"/macros/log/{wrap['_id']}/delete")
    assert db.food_log.count_documents({"viewer_id": GUEST}) == 1
    assert db.food_log.count_documents({"viewer_id": "someone-else"}) == 1


def test_day_is_the_viewers_local_date(with_profile, nfg_app):
    tomorrow = (_today() + dt.timedelta(days=1)).isoformat()
    far = (_today() + dt.timedelta(days=5)).isoformat()
    with_profile.post("/macros/log", data={"name": "Late snack", "kcal": "200", "date": tomorrow})
    with_profile.post("/macros/log", data={"name": "Bad date", "kcal": "200", "date": far})
    dates = {r["name"]: r["date"] for r in nfg_app.db.food_log.find({"viewer_id": GUEST})}
    assert dates["Late snack"] == tomorrow  # the browser's date is trusted within a day
    assert dates["Bad date"] != far
    # Time-zone cookie: 14 hours ahead of UTC is tomorrow somewhere for most of the day.
    with_profile.set_cookie("nfg_tzo", "-840")
    html = with_profile.get("/macros").get_data(as_text=True)
    local = (dt.datetime.utcnow() + dt.timedelta(hours=14)).date().isoformat()
    assert f'data-today="{local}"' in html


def test_goal_switch_and_weigh_in(with_profile, nfg_app):
    db = nfg_app.db
    resp = with_profile.post(
        "/macros/goal", data={"goal": "bulk"}, headers={"Accept": "application/json"}
    )
    data = resp.get_json()
    assert data["ok"] and data["targets"]["goal"] == "bulk"
    assert db.macro_profiles.find_one({"viewer_id": GUEST})["goal"] == "bulk"
    with_profile.post("/macros/weight", data={"weight": "180"})
    doc = db.macro_profiles.find_one({"viewer_id": GUEST})
    assert doc["weight_kg"] == pytest.approx(mx.lb_to_kg(180), abs=0.01)
    html = with_profile.get("/macros?view=projection&weeks=26").get_data(as_text=True)
    assert "180 lb" in html and "26 weeks" in html and "bulk target" in html


def test_charts_render_with_history(with_profile, nfg_app):
    db = nfg_app.db
    today = _today()
    for i in range(1, 6):
        db.food_log.insert_one(
            {
                "viewer_id": GUEST,
                "date": (today - dt.timedelta(days=i)).isoformat(),
                "name": "x",
                "kcal": 2100,
                "protein": 180,
                "carbs": 200,
                "fat": 60,
                "created_at": dt.datetime.utcnow(),
            }
        )
    week = with_profile.get("/macros").get_data(as_text=True)
    assert "5 logged days" in week and "nfg-chart-bar" in week and "Show as a table" in week
    month = with_profile.get("/macros?period=month").get_data(as_text=True)
    assert month.count('class="nfg-chart-col') == 30
    proj = with_profile.get("/macros?view=projection").get_data(as_text=True)
    assert "At your logged average" in proj and "2,100" in proj


def test_recipes_page_shows_todays_rings_once_set_up(with_profile):
    with_profile.post("/macros/log", data={"recipe": "berry-protein-smoothie"})
    html = with_profile.get("/recipes").get_data(as_text=True)
    assert "Today · Cut" in html and "Work out your macros" not in html
    page = with_profile.get("/recipes/berry-protein-smoothie").get_data(as_text=True)
    assert "of your protein" in page


def test_guest_macros_move_to_the_account(guest, nfg_app):
    db = nfg_app.db
    guest.post("/macros/profile", data=PROFILE_FORM)
    guest.post("/macros/log", data={"name": "Eggs", "kcal": "300"})
    resp = guest.post(
        "/account/register",
        data={
            "username": "macrofan",
            "email": "macro@example.com",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    assert resp.status_code == 302
    user = db.users.find_one({"username_lower": "macrofan"})
    member = f"user:{user['_id']}"
    for coll in NUTRITION_COLLECTIONS:
        assert db[coll].count_documents({"viewer_id": GUEST}) == 0, coll
        assert db[coll].count_documents({"viewer_id": member}) == 1, coll
    for coll in NUTRITION_COLLECTIONS:
        db[coll].delete_many({"viewer_id": member})


def test_search_sitemap_and_menu(client):
    html = client.get("/search?q=burrito").get_data(as_text=True)
    assert "/recipes/chicken-burrito-bowls" in html and "g protein" in html
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/recipes/protein-overnight-oats" in sitemap
    home = client.get("/").get_data(as_text=True)
    assert 'href="/macros"' in home  # Macros in the menu
