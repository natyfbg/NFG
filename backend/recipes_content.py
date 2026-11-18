"""Recipes as content files: `content/recipes/<slug>.yaml` + the food table `content/foods.yaml`.

No Flask or Mongo here. The app loads every recipe at startup (like Coach's Picks), so a
new or edited recipe goes live with the deploy; there is no database sync for recipes.

A recipe lists its ingredients with grams; macros per serving are the sum of each
ingredient's grams × the food's values per 100 g, divided by servings. Change an amount
and the numbers follow. `content_sync.py check` validates every file.

Ingredient lines:

    - {amount: 50, unit: g, item: rolled oats, food: oats}          # g/ml: grams = amount
    - {amount: 2, item: large egg|large eggs, food: egg, grams: 100} # singular|plural
    - {amount: 1, unit: tsp, item: ground cumin}                     # no food: no macros
    - {item: salt and pepper}                                        # to taste
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

BASE = Path(__file__).resolve().parent / "content"
RECIPES_DIR = BASE / "recipes"
FOODS_FILE = BASE / "foods.yaml"

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MEALS = {
    "breakfast": {"label": "Breakfast", "color": "#B5832A"},
    "lunch": {"label": "Lunch", "color": "#2E8A5F"},
    "dinner": {"label": "Dinner", "color": "#C0492F"},
    "snack": {"label": "Snack", "color": "#7A4FB0"},
}
TAGS = {
    "meal-prep": "Meal prep",
    "no-cook": "No cooking",
}
# Labels the app derives from the numbers (not written in files).
DERIVED_TAGS = {"quick": "Quick", "vegetarian": "Vegetarian", "high-protein": "High protein"}
GOALS = {"cut": "Cut", "maintain": "Maintain", "bulk": "Bulk"}
MIN_PROTEIN = 30  # every recipe: at least 30 g protein per serving
QUICK_MINUTES = 15
FOOD_KINDS = {
    "meat",
    "fish",
    "egg",
    "dairy",
    "plant",
    "grain",
    "veg",
    "fruit",
    "fat",
    "sweet",
    "sauce",
    "supplement",
}
MACROS = ("kcal", "protein", "carbs", "fat")
KEYS = {
    "slug",
    "name",
    "meal",
    "minutes",
    "wait",
    "servings",
    "tags",
    "summary",
    "ingredients",
    "steps",
    "tip",
    "status",
}
INGREDIENT_KEYS = {"amount", "unit", "item", "food", "grams", "note"}
WEIGHT_UNITS = {"g", "ml"}


class RecipeError(Exception):
    pass


# -----------------------------------------------------------------------------
# Loading + validation
# -----------------------------------------------------------------------------
def load_foods(path: Path = FOODS_FILE) -> Dict[str, dict]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    problems = []
    for key, food in raw.items():
        if not isinstance(food, dict):
            problems.append(f"foods.yaml {key}: must be a mapping")
            continue
        for m in MACROS:
            if not isinstance(food.get(m), (int, float)) or food[m] < 0:
                problems.append(f"foods.yaml {key}: {m} must be a number ≥ 0")
        if food.get("kind") not in FOOD_KINDS:
            problems.append(f"foods.yaml {key}: kind must be one of {sorted(FOOD_KINDS)}")
    if problems:
        raise RecipeError("\n".join(problems))
    return raw


def validate_recipe(raw, filename: str, foods: Dict[str, dict]) -> List[str]:
    errors: List[str] = []
    where = filename
    if not isinstance(raw, dict):
        return [f"{where}: must be a mapping"]
    for key in ("slug", "name", "meal", "minutes", "servings", "ingredients", "steps"):
        if key not in raw:
            errors.append(f"{where}: missing '{key}'")
    if errors:
        return errors
    unknown = set(raw) - KEYS
    if unknown:
        errors.append(f"{where}: unknown field(s) {sorted(unknown)}")
    if raw["slug"] != Path(filename).stem or not SLUG_RE.match(str(raw["slug"])):
        errors.append(f"{where}: slug must match the file name (lowercase-with-hyphens)")
    if raw["meal"] not in MEALS:
        errors.append(f"{where}: meal must be one of {list(MEALS)}")
    if not isinstance(raw["minutes"], int) or not 1 <= raw["minutes"] <= 240:
        errors.append(f"{where}: minutes must be a whole number from 1 to 240")
    if not isinstance(raw["servings"], int) or not 1 <= raw["servings"] <= 12:
        errors.append(f"{where}: servings must be a whole number from 1 to 12")
    for tag in raw.get("tags") or []:
        if tag not in TAGS:
            errors.append(f"{where}: unknown tag '{tag}' (use {list(TAGS)})")
    if raw.get("status", "published") not in ("published", "draft"):
        errors.append(f"{where}: status must be published or draft")
    steps = raw["steps"]
    if not isinstance(steps, list) or not steps or not all(isinstance(s, str) for s in steps):
        errors.append(f"{where}: steps must be a list of sentences")
    ingredients = raw["ingredients"]
    if not isinstance(ingredients, list) or not ingredients:
        errors.append(f"{where}: ingredients must be a non-empty list")
        return errors
    for i, ing in enumerate(ingredients, 1):
        label = f"{where} ingredient {i}"
        if not isinstance(ing, dict) or not ing.get("item"):
            errors.append(f"{label}: needs an item")
            continue
        extra = set(ing) - INGREDIENT_KEYS
        if extra:
            errors.append(f"{label}: unknown field(s) {sorted(extra)}")
        amount = ing.get("amount")
        if amount is not None and (not isinstance(amount, (int, float)) or amount <= 0):
            errors.append(f"{label}: amount must be a positive number")
        food = ing.get("food")
        if food is None:
            continue
        if food not in foods:
            errors.append(f"{label}: no food '{food}' in foods.yaml")
            continue
        if _grams(ing) is None:
            errors.append(f"{label}: needs grams (or an amount in g/ml)")
    if not errors:
        per = macros_per_serving(raw, foods)
        if per["protein"] < MIN_PROTEIN:
            errors.append(
                f"{where}: {per['protein']:.0f} g protein per serving; recipes need {MIN_PROTEIN} g+"
            )
    return errors


def load_recipes(
    directory: Path = RECIPES_DIR, foods_file: Path = FOODS_FILE
) -> Tuple[List[dict], Dict[str, dict]]:
    """Every recipe file, validated and built (macros, tags, goals). Raises RecipeError."""
    foods = load_foods(foods_file)
    errors: List[str] = []
    recipes: List[dict] = []
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{path.name}: invalid YAML ({e})")
            continue
        file_errors = validate_recipe(raw, path.name, foods)
        errors.extend(file_errors)
        if not file_errors:
            recipes.append(build_recipe(raw, foods))
    if errors:
        raise RecipeError("\n".join(errors))
    return recipes, foods


# -----------------------------------------------------------------------------
# Building
# -----------------------------------------------------------------------------
def _grams(ing: dict) -> Optional[float]:
    if ing.get("grams") is not None:
        return float(ing["grams"])
    if ing.get("unit") in WEIGHT_UNITS and ing.get("amount") is not None:
        return float(ing["amount"])
    return None


def macros_per_serving(raw: dict, foods: Dict[str, dict]) -> Dict[str, float]:
    total = {m: 0.0 for m in MACROS}
    for ing in raw["ingredients"]:
        food = foods.get(ing.get("food") or "")
        grams = _grams(ing)
        if not food or grams is None:
            continue
        for m in MACROS:
            total[m] += food[m] * grams / 100
    servings = raw["servings"] or 1
    return {m: total[m] / servings for m in MACROS}


def goal_fit(per: Dict[str, float]) -> List[str]:
    """Which goals a serving suits, from its numbers: lean and protein-dense → cut;
    a solid meal → maintain; calorie- or carb-heavy → bulk."""
    kcal = per["kcal"] or 1
    share = per["protein"] * 4 / kcal
    fits = []
    if kcal <= 550 and share >= 0.30:
        fits.append("cut")
    if 350 <= kcal <= 700:
        fits.append("maintain")
    if kcal >= 550 or per["carbs"] >= 60:
        fits.append("bulk")
    return fits or ["maintain"]


def build_recipe(raw: dict, foods: Dict[str, dict]) -> dict:
    per = macros_per_serving(raw, foods)
    kinds = {foods[i["food"]]["kind"] for i in raw["ingredients"] if i.get("food")}
    tags = list(raw.get("tags") or [])
    derived = ["high-protein"]
    if raw["minutes"] <= QUICK_MINUTES:
        derived.append("quick")
    if not kinds & {"meat", "fish"}:
        derived.append("vegetarian")
    rounded = {
        "kcal": int(round(per["kcal"] / 5.0) * 5),
        "protein": int(round(per["protein"])),
        "carbs": int(round(per["carbs"])),
        "fat": int(round(per["fat"])),
    }
    return {
        "slug": raw["slug"],
        "name": raw["name"],
        "meal": raw["meal"],
        "meal_label": MEALS[raw["meal"]]["label"],
        "color": MEALS[raw["meal"]]["color"],
        "minutes": raw["minutes"],
        "wait": raw.get("wait") or "",
        "servings": raw["servings"],
        "summary": raw.get("summary") or "",
        "tags": tags + derived,
        "tag_labels": [TAGS.get(t) or DERIVED_TAGS[t] for t in tags + derived],
        "goals": goal_fit(per),
        "macros": rounded,
        "macros_exact": per,
        "ingredients": [dict(i) for i in raw["ingredients"]],
        "steps": list(raw["steps"]),
        "tip": raw.get("tip") or "",
        "status": raw.get("status", "published"),
        "search_text": " ".join(
            [raw["name"], raw.get("summary") or ""] + [i["item"] for i in raw["ingredients"]]
        ).lower(),
    }


# -----------------------------------------------------------------------------
# Display: scaled ingredient lines
# -----------------------------------------------------------------------------
NICE_FRACTIONS = {
    Fraction(1, 8): "⅛",
    Fraction(1, 4): "¼",
    Fraction(1, 3): "⅓",
    Fraction(1, 2): "½",
    Fraction(2, 3): "⅔",
    Fraction(3, 4): "¾",
}


def format_amount(value: float, unit: Optional[str]) -> str:
    """300.0 g → "300"; 1.5 tbsp → "1½"; 0.333 → "⅓"; 7.0 → "7"."""
    if unit in WEIGHT_UNITS:
        if value >= 100:
            return str(int(round(value / 5.0) * 5))
        return str(int(round(value))) if value >= 10 else f"{value:.0f}" if value >= 1 else "1"
    whole = int(value)
    rest = value - whole
    best = min(NICE_FRACTIONS, key=lambda f: abs(float(f) - rest)) if rest > 0.06 else None
    if rest > 0.88:
        whole, best = whole + 1, None
    frac = NICE_FRACTIONS[best] if best is not None and abs(float(best) - rest) < 0.09 else ""
    if not frac and rest > 0.06 and rest <= 0.88:  # e.g. 0.6 → "0.6"
        return f"{value:.1f}".rstrip("0").rstrip(".")
    if whole == 0:
        return frac or "1"
    return f"{whole}{frac}"


def _pick(text: str, plural: bool) -> str:
    if "|" not in text:
        return text
    one, many = text.split("|", 1)
    return many if plural else one


def ingredient_line(ing: dict, factor: float = 1.0) -> dict:
    """{"qty": "1½", "unit": "tbsp", "item": "olive oil", "note": "", "text": "..."}.
    A note ("1 scoop", "2 cans") describes the written amount, so it only shows unscaled."""
    amount = ing.get("amount")
    unit = ing.get("unit") or ""
    note = (ing.get("note") or "") if abs(factor - 1) < 1e-9 else ""
    if amount is None:
        item = _pick(ing["item"], False)
        return {"qty": "", "unit": "", "item": item, "note": note, "text": item}
    scaled = amount * factor
    qty = format_amount(scaled, unit)
    plural = scaled > 1.01
    unit_text = _pick(unit, plural)
    item = _pick(ing["item"], plural)
    text = " ".join(p for p in (qty, unit_text, item) if p)
    return {"qty": qty, "unit": unit_text, "item": item, "note": note, "text": text}


def scaled(recipe: dict, servings: int) -> dict:
    """Ingredient lines and macros for a number of servings (1–12)."""
    servings = max(1, min(12, int(servings or recipe["servings"])))
    factor = servings / recipe["servings"]
    return {
        "servings": servings,
        "lines": [ingredient_line(i, factor) for i in recipe["ingredients"]],
    }
