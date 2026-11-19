"""The training profile: three quick, skippable questions (experience, where you
train, goal), asked right after sign-up and offered once on Home.

Level and setting are the `nfg_level` / `nfg_env` preferences the rest of the app
already reads (sessions, the Mixer, programs). The goal (`nfg_goal`) picks the program
Home recommends and the macros calculator's starting goal. For members the profile is
also kept on the account (`users.training_profile`, saved by app.py's after-request
hook whenever any of the three changes), so it follows them to other devices: signing
in brings it back (`apply_profile`).

Routes:
    GET  /start          the three questions (?next= to come back to a page)
    POST /start          save them (or action=skip: "not now", and Home stops asking)
    GET  /start/goal     change just the goal (the account page's Goal switch)
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional

from flask import flash, g, redirect, render_template, request, url_for

GOAL_COOKIE = "nfg_goal"
DONE_COOKIE = "nfg_onboarded"  # "1" = set up, "skip" = said not now

GOALS: Dict[str, dict] = {
    "lose_fat": {
        "label": "Lose fat",
        "hint": "Keep your muscle while the fat comes off",
        "macros": "cut",
        "programs": ["8-week-challenge", "12-week-body-recomposition"],
    },
    "build_muscle": {
        "label": "Build muscle",
        "hint": "Get bigger and stronger",
        "macros": "bulk",
        "programs": ["16-week-muscle-building", "12-week-body-recomposition", "8-week-challenge"],
    },
    "recomp": {
        "label": "Both",
        "hint": "Lose fat and build muscle at the same time",
        "macros": "maintain",
        "programs": ["12-week-body-recomposition", "8-week-challenge"],
    },
    "stay_fit": {
        "label": "Stay fit",
        "hint": "Feel good and keep moving",
        "macros": "maintain",
        "programs": ["8-week-challenge", "12-week-body-recomposition"],
    },
}

LEVEL_HINTS = {
    "beginner": "New to lifting, or back after a long break",
    "intermediate": "A year or more of steady training; you know the main lifts",
    "advanced": "Several years of training and you push hard",
}
ENV_HINTS = {
    "home": "Bodyweight, maybe a pull-up bar",
    "hybrid": "Dumbbells, a bench or bands",
    "gym": "A full gym",
}


def goal_of(req=None) -> str:
    value = ((req or request).cookies.get(GOAL_COOKIE) or "").strip()
    return value if value in GOALS else ""


def macros_goal_default(req=None) -> Optional[str]:
    """The macros calculator's starting goal (cut / maintain / bulk) from the profile."""
    goal = goal_of(req)
    return GOALS[goal]["macros"] if goal else None


def needs_setup(req=None) -> bool:
    """Offer the setup on Home only to people who haven't set a level or been asked."""
    r = req or request
    return not r.cookies.get(DONE_COOKIE) and not r.cookies.get("nfg_level")


def recommended(goal: str, programs: Iterable[dict]) -> Optional[str]:
    """The slug of the program to recommend for a goal, among the ones on offer."""
    if goal not in GOALS:
        return None
    slugs = {p.get("slug") for p in programs}
    return next((s for s in GOALS[goal]["programs"] if s in slugs), None)


def with_recommendation(goal: str, programs: List[dict]) -> List[dict]:
    """Put the recommended program first and mark it (`for_you`). Copies the cards."""
    slug = recommended(goal, programs)
    if not slug:
        return programs
    out = [dict(p, for_you=(p.get("slug") == slug)) for p in programs]
    out.sort(key=lambda p: not p["for_you"])
    return out


def apply_profile(profile: Optional[dict], levels: Iterable[str], envs: Iterable[str]) -> None:
    """Queue a saved profile's cookies for this response (after_request sets them)."""
    if not profile:
        return
    if profile.get("level") in levels:
        g._set_level = profile["level"]
    if profile.get("env") in envs:
        g._set_env = profile["env"]
    if profile.get("goal") in GOALS:
        g._set_goal = profile["goal"]
    g._set_onboarded = "1"


def register(
    app,
    *,
    is_safe_path: Callable[[str], bool],
    levels: Dict[str, dict],
    environments: Dict[str, dict],
    level_keys: Iterable[str],
) -> None:
    level_keys = list(level_keys)

    def next_url(default: str) -> str:
        raw = (request.values.get("next") or "").strip()
        return raw if raw and is_safe_path(raw) else default

    def start():
        if request.method == "POST":
            nxt = next_url(url_for("home"))
            if request.form.get("action") == "skip":
                g._set_onboarded = "skip"
                return redirect(nxt)
            fields = {}
            level = (request.form.get("level") or "").strip()
            env = (request.form.get("env") or "").strip()
            goal = (request.form.get("goal") or "").strip()
            if level in level_keys:
                g._set_level = fields["level"] = level
            if env in environments:
                g._set_env = fields["env"] = env
            if goal in GOALS:
                g._set_goal = fields["goal"] = goal
            g._set_onboarded = "1"
            flash("You're set up. Change any of it from your account page.", "success")
            return redirect(nxt)
        current = {
            "level": request.cookies.get("nfg_level") or "",
            "env": request.cookies.get("nfg_env") or "",
            "goal": goal_of(),
        }
        return render_template(
            "onboarding.html",
            current=current,
            levels=[
                (k, levels[k]["label"], LEVEL_HINTS.get(k, "")) for k in level_keys if k in levels
            ],
            environments=[(k, e["label"], ENV_HINTS.get(k, "")) for k, e in environments.items()],
            goals=[(k, v["label"], v["hint"]) for k, v in GOALS.items()],
            next=next_url(""),
        )

    def start_goal():
        goal = (request.args.get("goal") or "").strip()
        if goal in GOALS:
            g._set_goal = goal
            g._set_onboarded = "1"
        return redirect(next_url(url_for("account_profile")))

    app.add_url_rule("/start", "onboarding_start", start, methods=["GET", "POST"])
    app.add_url_rule("/start/goal", "onboarding_goal", start_goal)
