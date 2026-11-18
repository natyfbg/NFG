"""Recipes, the macros calculator, the daily tracker and its charts.

Routes live here (not in app.py) and are registered with plain endpoint names
(`recipes`, `recipe_detail`, `macros`, ...), so `url_for("recipes")` keeps working.
Maths is in `macros.py`, recipe content in `recipes_content.py`.

Per-viewer data (keyed like program progress: `user:<id>` or the guest cookie, moved to
the account on sign-in by `migrate_guest`):
  macro_profiles  one per viewer: sex, age, height, weight, activity, goal, units, tape
  food_log        one per entry: date (the viewer's local day), name, kcal, P/C/F
  weight_log      one per viewer per day: weight_kg
A "day" is the viewer's own date: the browser sends it (hidden `date` field, `nfg_tzo`
cookie), so the tracker resets at their midnight with no server job.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
from typing import Callable, Dict, List, Optional

import macros as mx
import recipes_content as rc
from bson import ObjectId
from bson.errors import InvalidId
from flask import abort, flash, jsonify, redirect, render_template, request, url_for

log = logging.getLogger(__name__)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HORIZONS = (12, 26, 52)
FILTER_TAGS = ("quick", "meal-prep", "vegetarian", "no-cook")

try:
    RECIPES, FOODS = rc.load_recipes()
except rc.RecipeError as e:  # a broken file never takes the site down; `check` reports it
    log.error("Recipe content has problems:\n%s", e)
    RECIPES, FOODS = [], {}
RECIPES = [r for r in RECIPES if r["status"] == "published"]
MEAL_ORDER = list(rc.MEALS)
RECIPES.sort(key=lambda r: (MEAL_ORDER.index(r["meal"]), -r["macros"]["protein"]))
BY_SLUG = {r["slug"]: r for r in RECIPES}


def search(q: str, limit: int = 10) -> List[dict]:
    words = [w for w in (q or "").lower().split() if w]
    if not words:
        return []
    hits = [r for r in RECIPES if all(w in r["search_text"] for w in words)]
    return hits[:limit]


def migrate_guest(db, guest: str, member: str) -> None:
    """Guest → member on sign-in: the member's own profile wins; entries and weigh-ins
    move over (a weigh-in the member already has for that day wins)."""
    if not guest or guest == member:
        return
    if db.macro_profiles.find_one({"viewer_id": member}) is None:
        db.macro_profiles.update_many({"viewer_id": guest}, {"$set": {"viewer_id": member}})
    db.macro_profiles.delete_many({"viewer_id": guest})
    db.food_log.update_many({"viewer_id": guest}, {"$set": {"viewer_id": member}})
    for row in list(db.weight_log.find({"viewer_id": guest})):
        db.weight_log.update_one(
            {"viewer_id": member, "date": row.get("date")},
            {
                "$setOnInsert": {
                    "weight_kg": row.get("weight_kg"),
                    "created_at": row.get("created_at"),
                }
            },
            upsert=True,
        )
    db.weight_log.delete_many({"viewer_id": guest})


# -----------------------------------------------------------------------------
# Chart geometry (rendered as inline SVG by the template; no chart library)
# -----------------------------------------------------------------------------
def _nice_step(span: float, max_ticks: int = 4) -> float:
    raw = span / max_ticks
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _bar_path(x: float, y: float, w: float, h: float) -> str:
    """A column with a 4px rounded top, square at the baseline."""
    r = min(4.0, w / 2, h)
    return (
        f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
        f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{y + h:.1f}Z"
    )


def bar_chart(hist: dict, width: int = 340, height: int = 160) -> dict:
    bars = hist["bars"]
    left, right, top, bottom = 38, 6, 10, 20
    plot_w, plot_h = width - left - right, height - top - bottom
    peak = max([b["kcal"] for b in bars] + [hist["target"], 500])
    step = _nice_step(peak)
    ymax = math.ceil(peak * 1.08 / step) * step
    band = plot_w / max(1, len(bars))
    bw = min(24.0, band * 0.62)
    out = []
    for i, b in enumerate(bars):
        h = plot_h * b["kcal"] / ymax if b["kcal"] else 0
        x = left + i * band + (band - bw) / 2
        y = top + plot_h - h
        out.append(
            dict(
                b,
                x=round(x + bw / 2, 1),
                path=_bar_path(x, y, bw, h) if h >= 1 else "",
                hit_x=round(left + i * band, 1),
                hit_w=round(band, 1),
            )
        )
    ticks = [
        {"y": round(top + plot_h - plot_h * v / ymax, 1), "label": f"{int(v):,}"}
        for v in range(0, int(ymax) + 1, int(step))
    ]
    return {
        "width": width,
        "height": height,
        "left": left,
        "right": width - right,
        "base": top + plot_h,
        "bars": out,
        "ticks": ticks,
        "target_y": round(top + plot_h - plot_h * hist["target"] / ymax, 1),
        "label_y": height - 5,
    }


def line_chart(
    series: List[List[float]],
    labels: List[str],
    fmt: Callable[[float], str],
    width: int = 340,
    height: int = 150,
) -> dict:
    """One or more lines on one shared axis (same unit), with an end label each."""
    left, right, top, bottom = 44, 58, 12, 20
    plot_w, plot_h = width - left - right, height - top - bottom
    values = [v for s in series for v in s]
    lo, hi = min(values), max(values)
    if hi - lo < 1:
        lo, hi = lo - 1, hi + 1
    step = _nice_step(hi - lo, 3)
    lo = math.floor(lo / step) * step
    hi = math.ceil(hi / step) * step
    n = len(labels) - 1 or 1

    def px(i):
        return round(left + plot_w * i / n, 1)

    def py(v):
        return round(top + plot_h - plot_h * (v - lo) / (hi - lo), 1)

    lines = []
    for s in series:
        points = [
            {"x": px(i), "y": py(v), "value": fmt(v), "label": labels[i]} for i, v in enumerate(s)
        ]
        lines.append(
            {
                "points": points,
                "last": points[-1],
                "path": "M" + " L".join(f"{p['x']},{p['y']}" for p in points),
                "area": f"M{points[0]['x']},{top + plot_h} L"
                + " L".join(f"{p['x']},{p['y']}" for p in points)
                + f" L{points[-1]['x']},{top + plot_h} Z",
            }
        )
    # End labels never overlap: nudge the second one away from the first.
    if len(lines) == 2 and abs(lines[0]["last"]["y"] - lines[1]["last"]["y"]) < 12:
        a, b = lines[0]["last"], lines[1]["last"]
        upper, lower = (a, b) if a["y"] <= b["y"] else (b, a)
        mid = (upper["y"] + lower["y"]) / 2
        upper["label_y"], lower["label_y"] = mid - 6, mid + 6
    for line in lines:
        line["last"].setdefault("label_y", line["last"]["y"])
    tick_values = []
    v = lo
    while v <= hi + 1e-9:
        tick_values.append(v)
        v += step
    return {
        "width": width,
        "height": height,
        "left": left,
        "right": width - right,
        "base": top + plot_h,
        "lines": lines,
        "ticks": [{"y": py(t), "label": fmt(t)} for t in tick_values],
        "label_y": height - 5,
        "x_labels": [
            {"x": px(0), "label": labels[0], "anchor": "start"},
            {"x": px(n // 2), "label": labels[n // 2], "anchor": "middle"},
            {"x": px(n), "label": labels[n], "anchor": "end"},
        ],
    }


def ring(value: float, target: float, radius: float = 42) -> dict:
    pct = (value / target) if target else 0
    circ = 2 * math.pi * radius
    return {
        "r": radius,
        "circ": round(circ, 2),
        "dash": round(circ * min(pct, 1), 2),
        "pct": int(round(100 * pct)),
        "over": value > target * 1.0 if target else False,
    }


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
def register(app, *, db, owner_key: Callable[[], str], is_safe_path: Callable[[str], bool]) -> None:
    try:
        db.macro_profiles.create_index([("viewer_id", 1)], unique=True)
        db.food_log.create_index([("viewer_id", 1), ("date", 1)])
        db.weight_log.create_index([("viewer_id", 1), ("date", 1)], unique=True)
    except Exception as e:  # pragma: no cover - index creation is best effort
        log.warning("nutrition indexes: %s", e)

    # ---- helpers -------------------------------------------------------------
    def today() -> dt.date:
        """The viewer's local date: an explicit `date` (from the page's script) within a
        day of UTC, else the `nfg_tzo` offset cookie, else UTC."""
        utc_now = dt.datetime.utcnow()
        raw = (request.values.get("date") or "").strip()
        if DATE_RE.match(raw):
            try:
                day = dt.date.fromisoformat(raw)
                if abs((day - utc_now.date()).days) <= 1:
                    return day
            except ValueError:
                pass
        try:
            offset = int(request.cookies.get("nfg_tzo", ""))
        except ValueError:
            offset = None
        if offset is not None and -900 <= offset <= 900:
            return (utc_now - dt.timedelta(minutes=offset)).date()
        return utc_now.date()

    def profile_for(key: str) -> Optional[dict]:
        return db.macro_profiles.find_one({"viewer_id": key}) if key else None

    def totals_by_date(key: str, since: dt.date) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        rows = db.food_log.find(
            {"viewer_id": key, "date": {"$gte": since.isoformat()}},
            {"date": 1, "kcal": 1, "protein": 1, "carbs": 1, "fat": 1},
        )
        for row in rows:
            day = out.setdefault(row["date"], {k: 0.0 for k in mx.MACRO_KEYS})
            for k in mx.MACRO_KEYS:
                day[k] += float(row.get(k) or 0)
        return out

    def back_to(default: str) -> str:
        nxt = (request.form.get("next") or "").strip()
        return nxt if nxt and is_safe_path(nxt) else default

    def wants_json() -> bool:
        return "application/json" in (request.headers.get("Accept") or "")

    def day_summary(key: str, day: dt.date, profile: Optional[dict]) -> Optional[dict]:
        if not profile:
            return None
        entries = list(db.food_log.find({"viewer_id": key, "date": day.isoformat()}))
        totals = mx.day_totals(entries)
        goal_targets = mx.targets(profile)
        return {"totals": totals, "targets": goal_targets, "entries": entries}

    # ---- recipes ---------------------------------------------------------------
    def recipes():
        meal = request.args.get("meal") if request.args.get("meal") in rc.MEALS else ""
        tag = request.args.get("tag") if request.args.get("tag") in FILTER_TAGS else ""
        goal = request.args.get("goal") if request.args.get("goal") in rc.GOALS else ""
        shown = [
            r
            for r in RECIPES
            if (not meal or r["meal"] == meal)
            and (not tag or tag in r["tags"])
            and (not goal or goal in r["goals"])
        ]

        def link(**changes):
            args = {"meal": meal, "tag": tag, "goal": goal}
            args.update(changes)
            return url_for("recipes", **{k: v for k, v in args.items() if v})

        key = owner_key()
        profile = profile_for(key)
        summary = day_summary(key, today(), profile)
        return render_template(
            "recipes.html",
            recipes=shown,
            total=len(RECIPES),
            meal=meal,
            tag=tag,
            goal=goal,
            meals=rc.MEALS,
            meal_links=[("", "All", link(meal=""))]
            + [(k, m["label"], link(meal=k)) for k, m in rc.MEALS.items()],
            tag_links=[
                (t, rc.TAGS.get(t) or rc.DERIVED_TAGS[t], link(tag="" if tag == t else t))
                for t in FILTER_TAGS
            ],
            goal_links=[
                (
                    k,
                    f"Good for a {v.lower()}" if k != "maintain" else "Good to maintain",
                    link(goal="" if goal == k else k),
                )
                for k, v in rc.GOALS.items()
            ],
            clear_url=url_for("recipes"),
            profile=profile,
            summary=summary,
            rings=_rings(summary) if summary else None,
            goals=mx.GOALS,
        )

    def recipe_detail(slug):
        recipe = BY_SLUG.get(slug)
        if not recipe:
            abort(404)
        try:
            servings = int(request.args.get("servings") or recipe["servings"])
        except ValueError:
            servings = recipe["servings"]
        view = rc.scaled(recipe, servings)
        key = owner_key()
        profile = profile_for(key)
        share = None
        if profile:
            t = mx.targets(profile)
            share = {
                "protein": mx.percent(recipe["macros"]["protein"], t["protein"]),
                "kcal": mx.percent(recipe["macros"]["kcal"], t["kcal"]),
                "goal": mx.GOALS[t["goal"]]["label"],
            }
        scale_data = {
            "base": recipe["servings"],
            "ingredients": [
                {k: i.get(k) for k in ("amount", "unit", "item", "note")}
                for i in recipe["ingredients"]
            ],
        }
        return render_template(
            "recipe_detail.html",
            recipe=recipe,
            view=view,
            share=share,
            profile=profile,
            scale_data=scale_data,
            goals=rc.GOALS,
        )

    # ---- macros page -----------------------------------------------------------
    def macros_page():
        key = owner_key()
        profile = profile_for(key)
        day = today()
        if not profile or request.args.get("edit"):
            return _calculator(profile)
        units = profile.get("units") or "imperial"
        entries = list(
            db.food_log.find({"viewer_id": key, "date": day.isoformat()}).sort([("created_at", 1)])
        )
        totals = mx.day_totals(entries)
        targets_all = mx.all_targets(profile)
        goal = profile.get("goal") or "maintain"
        goal_targets = targets_all[goal]
        since = day - dt.timedelta(days=371)
        by_date = totals_by_date(key, since)
        if day.isoformat() not in by_date and entries:
            by_date[day.isoformat()] = totals

        view = (
            request.args.get("view")
            if request.args.get("view") in ("intake", "projection")
            else "intake"
        )
        period = request.args.get("period") if request.args.get("period") in mx.PERIODS else "week"
        try:
            weeks = int(request.args.get("weeks") or 12)
        except ValueError:
            weeks = 12
        weeks = weeks if weeks in HORIZONS else 12

        hist = mx.history(by_date, goal_targets, day, period)
        chart = bar_chart(hist)

        last_weight = db.weight_log.find_one({"viewer_id": key}, sort=[("date", -1)])
        start_weight = (last_weight or {}).get("weight_kg") or profile["weight_kg"]
        intake = mx.recent_intake(by_date, day)
        # Two paths: hitting the target every day, and carrying on at the logged average.
        proj = mx.projection(profile, goal, weeks, start_weight_kg=start_weight)
        proj_logged = (
            mx.projection(profile, goal, weeks, intake_kcal=intake, start_weight_kg=start_weight)
            if intake
            else None
        )
        show = (lambda kg: mx.kg_to_lb(kg)) if units == "imperial" else (lambda kg: kg)
        unit = "lb" if units == "imperial" else "kg"
        week_labels = ["Now"] + [f"Wk {p['week']}" for p in proj["points"][1:]]
        paths = [proj] + ([proj_logged] if proj_logged else [])
        weight_chart = line_chart(
            [[show(p["weight_kg"]) for p in pr["points"]] for pr in paths],
            week_labels,
            lambda v: f"{v:.0f}" if units == "imperial" else f"{v:.1f}",
        )
        bf_series = [[p["body_fat"] for p in pr["points"]] for pr in paths]
        bf_chart = (
            line_chart(bf_series, week_labels, lambda v: f"{v:.0f}%")
            if all(v is not None for s in bf_series for v in s)
            else None
        )
        recent = []
        seen = set()
        for row in db.food_log.find({"viewer_id": key}).sort([("created_at", -1)]).limit(60):
            sig = (row.get("name"), round(float(row.get("kcal") or 0)))
            if sig in seen:
                continue
            seen.add(sig)
            recent.append(row)
            if len(recent) == 6:
                break

        def link(**changes):
            args = {"view": view, "period": period, "weeks": weeks}
            args.update(changes)
            if args["view"] == "intake":
                args.pop("weeks", None)
            else:
                args.pop("period", None)
            return url_for("macros", **args) + "#progress"

        summary = {"totals": totals, "targets": goal_targets, "entries": entries}
        return render_template(
            "macros.html",
            profile=profile,
            units=units,
            unit=unit,
            day=day,
            entries=entries,
            totals=totals,
            goal=goal,
            goal_targets=goal_targets,
            targets_all=targets_all,
            goals=mx.GOALS,
            activity=mx.ACTIVITY,
            rings=_rings(summary),
            recent=recent,
            view=view,
            period=period,
            weeks=weeks,
            hist=hist,
            chart=chart,
            proj=proj,
            proj_logged=proj_logged,
            logged_end=(
                mx.show_weight(proj_logged["points"][-1]["weight_kg"], units) if proj_logged else ""
            ),
            weight_chart=weight_chart,
            bf_chart=bf_chart,
            body_fat=mx.body_fat(profile),
            start_weight=mx.show_weight(start_weight, units),
            end_weight=mx.show_weight(proj["points"][-1]["weight_kg"], units),
            change=show(proj["points"][-1]["weight_kg"]) - show(proj["points"][0]["weight_kg"]),
            last_weight=last_weight,
            view_links=[
                ("intake", "Intake", link(view="intake")),
                ("projection", "Projection", link(view="projection")),
            ],
            period_links=[(k, k.capitalize(), link(period=k)) for k in mx.PERIODS],
            horizon_links=[(w, f"{w} weeks", link(weeks=w)) for w in HORIZONS],
            height_text=mx.show_height(profile["height_cm"], units),
            weight_text=mx.show_weight(profile["weight_kg"], units),
            sexes=mx.SEXES,
        )

    def _calculator(profile: Optional[dict], form=None, errors=None, status=200):
        values = dict(form or {})
        if not form and profile:
            units = profile.get("units") or "imperial"
            values = {
                "units": units,
                "sex": profile.get("sex"),
                "age": profile.get("age"),
                "activity": profile.get("activity"),
                "goal": profile.get("goal"),
                "height_cm": round(profile["height_cm"]),
                "weight_kg": round(profile["weight_kg"], 1),
                "weight_lb": round(mx.kg_to_lb(profile["weight_kg"])),
            }
            values["height_ft"], values["height_in"] = mx.cm_to_ft_in(profile["height_cm"])
            for key in ("waist", "neck", "hip"):
                cm = profile.get(f"{key}_cm")
                if cm:
                    values[key] = (
                        round(cm / mx.CM_PER_IN, 1) if units == "imperial" else round(cm, 1)
                    )
        values.setdefault("units", "imperial")
        values.setdefault("goal", "maintain")
        return (
            render_template(
                "macros_calculator.html",
                profile=profile,
                values=values,
                errors=errors or {},
                sexes=mx.SEXES,
                activity=mx.ACTIVITY,
                goals=mx.GOALS,
                units_options=mx.UNITS,
            ),
            status,
        )

    def macros_profile():
        key = owner_key()
        if not key:
            return redirect(url_for("macros"))
        profile, errors = mx.parse_profile(request.form)
        if errors:
            return _calculator(profile_for(key), form=request.form, errors=errors, status=400)
        now = dt.datetime.utcnow()
        db.macro_profiles.update_one(
            {"viewer_id": key},
            {"$set": dict(profile, updated_at=now), "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        # Keep only the tape measurements given this time.
        missing = {f"{k}_cm": "" for k in ("waist", "neck", "hip") if f"{k}_cm" not in profile}
        if missing:
            db.macro_profiles.update_one({"viewer_id": key}, {"$unset": missing})
        db.weight_log.update_one(
            {"viewer_id": key, "date": today().isoformat()},
            {"$set": {"weight_kg": profile["weight_kg"]}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        t = mx.targets(profile)
        goal_name = mx.GOALS[t["goal"]]["label"].lower()
        flash(
            f"Your {goal_name} target: {t['kcal']:,} kcal and {t['protein']} g protein a day.",
            "success",
        )
        return redirect(url_for("macros"))

    def macros_goal():
        key = owner_key()
        goal = request.form.get("goal")
        profile = profile_for(key)
        if goal not in mx.GOALS or not profile:
            if wants_json():
                return jsonify({"ok": False}), 400
            return redirect(url_for("macros"))
        db.macro_profiles.update_one({"viewer_id": key}, {"$set": {"goal": goal}})
        if wants_json():
            return jsonify({"ok": True, "goal": goal, "targets": mx.targets(profile, goal)})
        return redirect(back_to(url_for("macros")))

    def macros_log_add():
        key = owner_key()
        if not key:
            return redirect(url_for("macros"))
        day = today().isoformat()
        slug = (request.form.get("recipe") or "").strip()
        if slug:
            recipe = BY_SLUG.get(slug)
            if not recipe:
                abort(404)
            try:
                servings = float(request.form.get("servings") or 1)
            except ValueError:
                servings = 1.0
            servings = min(max(servings, 0.25), 6)
            per = recipe["macros_exact"]
            entry = {
                "name": recipe["name"],
                "recipe_slug": slug,
                "servings": servings,
                **{k: round(per[k] * servings, 1) for k in mx.MACRO_KEYS},
            }
            entry["kcal"] = round(entry["kcal"])
        else:
            entry, errors = mx.parse_entry(request.form)
            if errors:
                flash("Add calories or at least one macro (numbers only).", "warning")
                return redirect(back_to(url_for("macros")))
        db.food_log.insert_one(
            dict(entry, viewer_id=key, date=day, created_at=dt.datetime.utcnow())
        )
        flash(f"Added {entry['name']} to today.", "success")
        return redirect(back_to(url_for("macros")))

    def macros_log_delete(entry_id):
        key = owner_key()
        try:
            oid = ObjectId(entry_id)
        except (InvalidId, TypeError):
            abort(404)
        db.food_log.delete_one({"_id": oid, "viewer_id": key})
        return redirect(back_to(url_for("macros")))

    def macros_weight():
        key = owner_key()
        profile = profile_for(key)
        if not profile:
            return redirect(url_for("macros"))
        units = profile.get("units") or "imperial"
        raw = mx._num(request.form.get("weight"))
        kg = mx.lb_to_kg(raw) if raw and units == "imperial" else raw
        lo, hi = mx.LIMITS["weight_kg"]
        if not kg or not lo <= kg <= hi:
            flash("Enter your weight as a number.", "warning")
            return redirect(url_for("macros", view="projection") + "#progress")
        now = dt.datetime.utcnow()
        db.weight_log.update_one(
            {"viewer_id": key, "date": today().isoformat()},
            {"$set": {"weight_kg": round(kg, 2)}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        # Targets follow the latest weight.
        db.macro_profiles.update_one(
            {"viewer_id": key}, {"$set": {"weight_kg": round(kg, 2), "updated_at": now}}
        )
        flash(f"Logged {mx.show_weight(kg, units)}.", "success")
        return redirect(url_for("macros", view="projection") + "#progress")

    def _rings(summary: dict) -> dict:
        t, v = summary["targets"], summary["totals"]
        out = {"kcal": ring(v["kcal"], t["kcal"], 46)}
        for k in ("protein", "carbs", "fat"):
            out[k] = ring(v[k], t[k], 30)
        return out

    app.add_url_rule("/recipes", "recipes", recipes)
    app.add_url_rule("/recipes/<slug>", "recipe_detail", recipe_detail)
    app.add_url_rule("/macros", "macros", macros_page)
    app.add_url_rule("/macros/profile", "macros_profile", macros_profile, methods=["POST"])
    app.add_url_rule("/macros/goal", "macros_goal", macros_goal, methods=["POST"])
    app.add_url_rule("/macros/log", "macros_log_add", macros_log_add, methods=["POST"])
    app.add_url_rule(
        "/macros/log/<entry_id>/delete", "macros_log_delete", macros_log_delete, methods=["POST"]
    )
    app.add_url_rule("/macros/weight", "macros_weight", macros_weight, methods=["POST"])
