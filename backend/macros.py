"""Macros maths: calorie and macro targets, day totals, history and a weight projection.

Pure functions (no Flask, no Mongo) so they're easy to test and reason about.

Targets: Mifflin-St Jeor BMR × activity factor = maintenance (TDEE). Cut eats 20% under,
bulk 10% over. Protein is set per kg (cut 2.2, maintain 1.8, bulk 2.0 g/kg), fat is a
share of calories (25–30%), carbs fill the rest. For a BMI over 30 protein is worked out
from the weight at BMI 30, so targets stay realistic for bigger bodies.

The projection is an estimate, and the page says so: 7,700 kcal ≈ 1 kg, maintenance is
recomputed as weight changes, and about 75% of weight lost (50% of weight gained) is
counted as fat. Real bodies adapt; the line is a direction, not a promise.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Dict, Iterable, Optional, Tuple

KG_PER_LB = 0.45359237
CM_PER_IN = 2.54
KCAL_PER_KG = 7700

SEXES = {"male": "Male", "female": "Female"}
ACTIVITY = {
    "sedentary": {
        "label": "Mostly sitting",
        "hint": "Desk job, little or no training",
        "factor": 1.2,
    },
    "light": {"label": "Lightly active", "hint": "Train 1–3 days a week", "factor": 1.375},
    "moderate": {"label": "Active", "hint": "Train 3–5 days a week", "factor": 1.55},
    "very": {"label": "Very active", "hint": "Train 6–7 days a week", "factor": 1.725},
    "extra": {"label": "Athlete", "hint": "Hard training plus a physical job", "factor": 1.9},
}
GOALS = {
    "cut": {
        "label": "Cut",
        "hint": "Lose fat",
        "adjust": -0.20,
        "protein_per_kg": 2.2,
        "fat_share": 0.25,
    },
    "maintain": {
        "label": "Maintain",
        "hint": "Stay where you are",
        "adjust": 0.0,
        "protein_per_kg": 1.8,
        "fat_share": 0.30,
    },
    "bulk": {
        "label": "Bulk",
        "hint": "Build muscle",
        "adjust": 0.10,
        "protein_per_kg": 2.0,
        "fat_share": 0.25,
    },
}
UNITS = {"imperial": "lb · ft", "metric": "kg · cm"}
KCAL_FLOOR = {"male": 1500, "female": 1200}
LIMITS = {"age": (18, 90), "height_cm": (120, 230), "weight_kg": (35, 300)}
FAT_SHARE_OF_LOSS = 0.75
FAT_SHARE_OF_GAIN = 0.5


# -----------------------------------------------------------------------------
# Units
# -----------------------------------------------------------------------------
def lb_to_kg(lb: float) -> float:
    return lb * KG_PER_LB


def kg_to_lb(kg: float) -> float:
    return kg / KG_PER_LB


def ft_in_to_cm(feet: float, inches: float = 0) -> float:
    return (feet * 12 + inches) * CM_PER_IN


def cm_to_ft_in(cm: float) -> Tuple[int, int]:
    total = round(cm / CM_PER_IN)
    return total // 12, total % 12


def show_weight(kg: float, units: str) -> str:
    return f"{kg_to_lb(kg):.0f} lb" if units == "imperial" else f"{kg:.1f} kg"


def show_height(cm: float, units: str) -> str:
    if units == "imperial":
        ft, inch = cm_to_ft_in(cm)
        return f"{ft} ft {inch} in"
    return f"{cm:.0f} cm"


# -----------------------------------------------------------------------------
# Profile
# -----------------------------------------------------------------------------
def _num(value) -> Optional[float]:
    try:
        n = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def parse_profile(form) -> Tuple[dict, Dict[str, str]]:
    """Read the calculator form (either unit system) into a metric profile + errors."""
    errors: Dict[str, str] = {}
    units = form.get("units") if form.get("units") in UNITS else "imperial"
    sex = form.get("sex")
    if sex not in SEXES:
        errors["sex"] = "Pick one: it changes the formula."
    age = _num(form.get("age"))
    if units == "imperial":
        feet, inches = _num(form.get("height_ft")), _num(form.get("height_in")) or 0
        height = ft_in_to_cm(feet, inches) if feet is not None else None
        lb = _num(form.get("weight_lb"))
        weight = lb_to_kg(lb) if lb is not None else None
    else:
        height = _num(form.get("height_cm"))
        weight = _num(form.get("weight_kg"))
    lo, hi = LIMITS["age"]
    if age is None or not lo <= age <= hi:
        errors["age"] = f"Enter an age from {lo} to {hi}. This calculator is for adults."
    lo, hi = LIMITS["height_cm"]
    if height is None or not lo <= height <= hi:
        errors["height"] = "Enter your height."
    lo, hi = LIMITS["weight_kg"]
    if weight is None or not lo <= weight <= hi:
        errors["weight"] = "Enter your weight."
    activity = form.get("activity")
    if activity not in ACTIVITY:
        errors["activity"] = "Pick how active you are."
    goal = form.get("goal") if form.get("goal") in GOALS else "maintain"

    profile = {
        "units": units,
        "sex": sex,
        "age": int(age) if age is not None else None,
        "height_cm": round(height, 1) if height is not None else None,
        "weight_kg": round(weight, 2) if weight is not None else None,
        "activity": activity,
        "goal": goal,
    }
    # Optional body-fat estimate (US Navy method): tape measurements.
    tape = {}
    for key in ("waist", "neck", "hip"):
        raw = _num(form.get(key))
        if raw:
            tape[f"{key}_cm"] = round(raw * CM_PER_IN if units == "imperial" else raw, 1)
    profile.update(tape)
    return profile, errors


def body_fat(profile: dict) -> Optional[float]:
    """US Navy tape estimate (% body fat), or None without the measurements."""
    h = profile.get("height_cm")
    waist, neck, hip = profile.get("waist_cm"), profile.get("neck_cm"), profile.get("hip_cm")
    try:
        if profile.get("sex") == "male" and waist and neck and waist > neck:
            bf = 495 / (1.0324 - 0.19077 * math.log10(waist - neck) + 0.15456 * math.log10(h)) - 450
        elif profile.get("sex") == "female" and waist and neck and hip and waist + hip > neck:
            bf = (
                495 / (1.29579 - 0.35004 * math.log10(waist + hip - neck) + 0.22100 * math.log10(h))
                - 450
            )
        else:
            return None
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return round(bf, 1) if 2 <= bf <= 60 else None


# -----------------------------------------------------------------------------
# Targets
# -----------------------------------------------------------------------------
def bmr(sex: str, age: float, height_cm: float, weight_kg: float) -> float:
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + 5 if sex == "male" else base - 161


def tdee(profile: dict, weight_kg: Optional[float] = None) -> float:
    w = weight_kg if weight_kg is not None else profile["weight_kg"]
    return (
        bmr(profile["sex"], profile["age"], profile["height_cm"], w)
        * ACTIVITY[profile["activity"]]["factor"]
    )


def protein_weight(height_cm: float, weight_kg: float) -> float:
    """The weight protein is worked out from: actual weight, capped at BMI 30."""
    return min(weight_kg, 30 * (height_cm / 100) ** 2)


def targets(profile: dict, goal: Optional[str] = None) -> dict:
    goal = goal if goal in GOALS else profile.get("goal") or "maintain"
    g = GOALS[goal]
    base = tdee(profile)
    kcal = base * (1 + g["adjust"])
    kcal = max(kcal, KCAL_FLOOR.get(profile["sex"], 1200))
    kcal = int(round(kcal / 10) * 10)
    protein = round(
        g["protein_per_kg"] * protein_weight(profile["height_cm"], profile["weight_kg"])
    )
    fat = round(kcal * g["fat_share"] / 9)
    carbs = round((kcal - protein * 4 - fat * 9) / 4)
    if carbs < 50:  # tiny bodies on a deep cut: keep some carbs, trim fat to 20%
        fat = round(kcal * 0.20 / 9)
        carbs = max(0, round((kcal - protein * 4 - fat * 9) / 4))
    return {
        "goal": goal,
        "kcal": kcal,
        "protein": int(protein),
        "carbs": int(carbs),
        "fat": int(fat),
        "tdee": int(round(base / 10) * 10),
        "bmr": int(
            round(bmr(profile["sex"], profile["age"], profile["height_cm"], profile["weight_kg"]))
        ),
    }


def all_targets(profile: dict) -> Dict[str, dict]:
    return {goal: targets(profile, goal) for goal in GOALS}


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
MACRO_KEYS = ("kcal", "protein", "carbs", "fat")
ENTRY_LIMITS = {"kcal": 5000, "protein": 400, "carbs": 800, "fat": 400}


def parse_entry(form) -> Tuple[dict, Dict[str, str]]:
    """A quick-add line: name (optional) and numbers. Empty numbers count as 0;
    kcal left empty is worked out from the macros."""
    errors: Dict[str, str] = {}
    out = {"name": (form.get("name") or "").strip()[:80]}
    for key in MACRO_KEYS:
        raw = form.get(key)
        if raw in (None, ""):
            out[key] = None
            continue
        n = _num(raw)
        if n is None or n < 0 or n > ENTRY_LIMITS[key]:
            errors[key] = "Enter a number."
            continue
        out[key] = round(n, 1)
    if errors:
        return out, errors
    for key in ("protein", "carbs", "fat"):
        out[key] = out[key] or 0.0
    if out["kcal"] is None:
        out["kcal"] = round(out["protein"] * 4 + out["carbs"] * 4 + out["fat"] * 9)
    if not any(out[k] for k in MACRO_KEYS):
        errors["kcal"] = "Add calories or at least one macro."
    if not out["name"]:
        out["name"] = "Quick add"
    return out, errors


def day_totals(entries: Iterable[dict]) -> Dict[str, float]:
    total = {k: 0.0 for k in MACRO_KEYS}
    for e in entries:
        for k in MACRO_KEYS:
            total[k] += float(e.get(k) or 0)
    return {k: round(v, 1) for k, v in total.items()}


def on_target(totals: Dict[str, float], goal_targets: dict) -> bool:
    """A day counts as on target when calories land within 10% and protein reaches 90%."""
    kcal_t = goal_targets["kcal"] or 1
    return (
        abs(totals["kcal"] - kcal_t) <= 0.10 * kcal_t
        and totals["protein"] >= 0.9 * goal_targets["protein"]
    )


def percent(value: float, target: float) -> int:
    return int(round(100 * value / target)) if target else 0


# -----------------------------------------------------------------------------
# History (chart) and projection
# -----------------------------------------------------------------------------
PERIODS = {"week": 7, "month": 30, "year": 52}


def history(
    totals_by_date: Dict[str, Dict[str, float]], goal_targets: dict, today: dt.date, period: str
) -> dict:
    """Bars for the chart: one per day (week, month) or per week (year, average of the
    days that have entries), plus how many logged days hit the target."""
    period = period if period in PERIODS else "week"
    bars = []
    if period == "year":
        start = today - dt.timedelta(days=today.weekday())  # Monday of this week
        for i in range(PERIODS["year"] - 1, -1, -1):
            week_start = start - dt.timedelta(weeks=i)
            days = [week_start + dt.timedelta(days=d) for d in range(7)]
            logged = [
                totals_by_date[d.isoformat()] for d in days if d.isoformat() in totals_by_date
            ]
            kcal = sum(t["kcal"] for t in logged) / len(logged) if logged else 0
            bars.append(
                {
                    "key": week_start.isoformat(),
                    "label": week_start.strftime("%b %d").replace(" 0", " "),
                    "short": week_start.strftime("%b") if week_start.day <= 7 else "",
                    "kcal": round(kcal),
                    "logged": len(logged),
                    "on_target": None,
                }
            )
        logged_days = [(today - dt.timedelta(days=i)).isoformat() for i in range(364)]
    else:
        n = PERIODS[period]
        for i in range(n - 1, -1, -1):
            day = today - dt.timedelta(days=i)
            totals = totals_by_date.get(day.isoformat())
            short = (
                day.strftime("%a")[0]
                if period == "week"
                else (str(day.day) if day.day in (1, 8, 15, 22, 29) else "")
            )
            bars.append(
                {
                    "key": day.isoformat(),
                    "label": day.strftime("%a %b %d").replace(" 0", " "),
                    "short": short,
                    "kcal": round(totals["kcal"]) if totals else 0,
                    "logged": 1 if totals else 0,
                    "on_target": on_target(totals, goal_targets) if totals else None,
                }
            )
        logged_days = [b["key"] for b in bars]
    in_range = [totals_by_date[d] for d in logged_days if d in totals_by_date]
    hits = sum(1 for t in in_range if on_target(t, goal_targets))
    avg = sum(t["kcal"] for t in in_range) / len(in_range) if in_range else 0
    return {
        "period": period,
        "bars": bars,
        "target": goal_targets["kcal"],
        "logged_days": len(in_range),
        "days_on_target": hits,
        "average_kcal": round(avg),
    }


def recent_intake(
    totals_by_date: Dict[str, Dict[str, float]], today: dt.date, days: int = 14
) -> Optional[float]:
    """Average calories over logged days in the last two weeks (today excluded, it's
    not over yet); None with fewer than 3 logged days."""
    logged = [
        totals_by_date[(today - dt.timedelta(days=i)).isoformat()]["kcal"]
        for i in range(1, days + 1)
        if (today - dt.timedelta(days=i)).isoformat() in totals_by_date
    ]
    return sum(logged) / len(logged) if len(logged) >= 3 else None


def projection(
    profile: dict,
    goal: str,
    weeks: int,
    intake_kcal: Optional[float] = None,
    start_weight_kg: Optional[float] = None,
) -> dict:
    """Weekly weight (and body fat, when known) for the next `weeks` weeks."""
    goal_targets = targets(profile, goal)
    intake = intake_kcal if intake_kcal else goal_targets["kcal"]
    weight = start_weight_kg or profile["weight_kg"]
    bf = body_fat(profile)
    fat_mass = weight * bf / 100 if bf is not None else None
    floor = 18.5 * (profile["height_cm"] / 100) ** 2  # never project below a healthy BMI
    bf_floor = 8.0 if profile["sex"] == "male" else 15.0
    points = [{"week": 0, "weight_kg": round(weight, 2), "body_fat": bf}]
    stopped = False
    for week in range(1, weeks + 1):
        change = (intake - tdee(profile, weight)) * 7 / KCAL_PER_KG
        if weight + change < floor:
            change, stopped = max(0.0, floor - weight), True
        if fat_mass is not None:
            share = FAT_SHARE_OF_LOSS if change < 0 else FAT_SHARE_OF_GAIN
            fat_mass = max(weight * bf_floor / 100, fat_mass + change * share)
        weight += change
        point = {"week": week, "weight_kg": round(weight, 2), "body_fat": None}
        if fat_mass is not None:
            point["body_fat"] = round(100 * fat_mass / weight, 1)
        points.append(point)
    return {
        "goal": goal,
        "weeks": weeks,
        "intake": int(round(intake)),
        "from_logs": bool(intake_kcal),
        "points": points,
        "change_kg": round(points[-1]["weight_kg"] - points[0]["weight_kg"], 1),
        "floor_reached": stopped,
    }
