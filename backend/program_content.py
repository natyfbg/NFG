"""Programs as content files: content/programs/<hub-slug>.yaml.

One file describes a whole program (e.g. the 8 Week Challenge): the phases that
set sets/reps/rest week by week, and for each level the training days with their
exercises. A day lists the gym exercise and, where it needs gym-only equipment, a
named home alternative (`home:`). Nate chooses the swaps; nothing is substituted
automatically.

`expand_program()` turns a file into the database shape the app already uses:
one hub program, one track per level × environment (home / gym / hybrid), one
`program_weeks` row per week and one `program_items` row per exercise. Progress is
keyed by track slug + week number + day key, so re-syncing never loses progress.

The sync never overwrites weeks that were built by hand in Admin unless forced.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import taxonomy as tx
import yaml

PROGRAMS_DIR = Path(__file__).resolve().parent / "content" / "programs"

ENVS = ("home", "gym", "hybrid")
ROLES = ("main", "accessory")
# A program's Home track assumes the basic home kit (dumbbells, a bench, bands):
# the planner's Hybrid tier. Programs offer Home or Gym only (Nate, 2026-11-16).
HOME_EQUIPMENT = set(tx.ENVIRONMENTS["hybrid"]["equipment"])
WORK_SECONDS_PER_SET = 40  # rough time under tension + setup, for the time estimate


class ProgramError(Exception):
    """Raised when program files fail validation. Message lists every problem."""


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def slugify(text: str) -> str:
    """Same rule as app.slugify, so day keys match the progress records."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def rest_seconds(rest: Optional[str]) -> int:
    """'90s' -> 90, '2 min' -> 120, '1:30' -> 90; unknown -> 60."""
    text = (rest or "").strip().lower()
    if not text:
        return 60
    m = re.match(r"^(\d+):(\d{2})$", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"^(\d+)\s*(?:m|min|mins|minutes?)\s*(\d+)\s*(?:s|sec|secs|seconds?)?$", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?)?$", text)
    if m:
        return int(float(m.group(1)))
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?)$", text)
    if m:
        return int(float(m.group(1)) * 60)
    return 60


def estimate_minutes(items: List[dict]) -> int:
    """Rough session length: sets × (work + rest), plus 5 minutes to warm up."""
    seconds = 0
    for it in items:
        sets = int(it.get("sets") or 0)
        seconds += sets * (WORK_SECONDS_PER_SET + rest_seconds(it.get("rest")))
    return int(round(seconds / 60)) + 5 if seconds else 0


def _home_ok(exercise: dict) -> bool:
    return set(exercise.get("equipment") or []) <= HOME_EQUIPMENT


def _default_role(exercise: dict) -> str:
    holds = "isometric" in (exercise.get("tags") or [])
    compound = exercise.get("mechanics") == "compound" and exercise.get("movement") != "isolation"
    return "main" if compound and not holds else "accessory"


def _phase_for(phases: List[dict], week: int) -> dict:
    for phase in phases:
        if week in phase.get("weeks", []):
            return phase
    return {}


# -----------------------------------------------------------------------------
# Loading and validation
# -----------------------------------------------------------------------------
def _validate_scheme(scheme, where: str, errors: List[str]) -> None:
    if not isinstance(scheme, dict):
        errors.append(f"{where}: must be a mapping with sets, reps and rest")
        return
    if not isinstance(scheme.get("sets"), int) or scheme["sets"] < 1:
        errors.append(f"{where}: sets must be a whole number of 1 or more")
    for key in ("reps", "rest"):
        if not isinstance(scheme.get(key), (str, int)) or not str(scheme.get(key)).strip():
            errors.append(f"{where}: {key} is required")


def validate_program(raw, filename: str, exercises: Dict[str, dict]) -> List[str]:
    errors: List[str] = []
    if not isinstance(raw, dict):
        return [f"{filename}: must be a mapping"]
    for key in ("slug", "title", "weeks", "phases", "levels"):
        if key not in raw:
            errors.append(f"{filename}: missing '{key}'")
    if errors:
        return errors
    if raw["slug"] != Path(filename).stem:
        errors.append(f"{filename}: slug '{raw['slug']}' must match the file name")
    if raw.get("status", "published") not in tx.PUBLISH_STATUSES:
        errors.append(f"{filename}: status must be one of {list(tx.PUBLISH_STATUSES)}")
    accent = raw.get("accent")
    if accent is not None and not (
        isinstance(accent, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", accent)
    ):
        errors.append(f"{filename}: accent must be a colour like '#E07A2E'")
    order = raw.get("order")
    if order is not None and (not isinstance(order, int) or isinstance(order, bool)):
        errors.append(f"{filename}: order must be a whole number")
    weeks = raw["weeks"]
    if not isinstance(weeks, int) or not 1 <= weeks <= 52:
        errors.append(f"{filename}: weeks must be a whole number from 1 to 52")
        return errors

    def check_phases(phases, where):
        if not isinstance(phases, list) or not phases:
            errors.append(f"{where}: phases must be a non-empty list")
            return
        covered: List[int] = []
        for i, phase in enumerate(phases, 1):
            label = f"{where} phase {i}"
            if not isinstance(phase, dict) or not phase.get("name"):
                errors.append(f"{label}: needs a name")
                continue
            if not isinstance(phase.get("weeks"), list) or not phase["weeks"]:
                errors.append(f"{label}: weeks must be a list of week numbers")
                continue
            covered.extend(phase["weeks"])
            for role in ROLES:
                _validate_scheme(phase.get(role), f"{label} {role}", errors)
        if sorted(covered) != list(range(1, weeks + 1)):
            errors.append(f"{where}: phases must cover weeks 1–{weeks} exactly once")

    check_phases(raw["phases"], filename)

    levels = raw["levels"]
    if not isinstance(levels, dict) or not levels:
        errors.append(f"{filename}: levels must be a mapping")
        return errors
    for level, body in levels.items():
        where = f"{filename} {level}"
        if level not in tx.LEVELS:
            errors.append(f"{where}: unknown level (use {list(tx.LEVELS)})")
            continue
        if not isinstance(body, dict) or not isinstance(body.get("days"), list) or not body["days"]:
            errors.append(f"{where}: needs a list of days")
            continue
        if "phases" in body:
            check_phases(body["phases"], where)
        labels = set()
        for d, day in enumerate(body["days"], 1):
            dwhere = f"{where} day {d}"
            label = (day or {}).get("label") if isinstance(day, dict) else None
            if not label:
                errors.append(f"{dwhere}: needs a label")
                continue
            if slugify(label) in labels:
                errors.append(f"{dwhere}: label '{label}' is used twice")
            labels.add(slugify(label))
            items = day.get("items")
            if not isinstance(items, list) or not items:
                errors.append(f"{dwhere} ({label}): needs a list of items")
                continue
            for n, item in enumerate(items, 1):
                iwhere = f"{dwhere} ({label}) item {n}"
                if not isinstance(item, dict):
                    errors.append(f"{iwhere}: must be a mapping")
                    continue
                slug, custom = item.get("exercise"), item.get("custom")
                if bool(slug) == bool(custom):
                    errors.append(f"{iwhere}: give exactly one of exercise or custom")
                    continue
                if item.get("role") and item["role"] not in ROLES:
                    errors.append(f"{iwhere}: role must be main or accessory")
                if custom:
                    for key in ("sets", "reps"):
                        if not item.get(key):
                            errors.append(f"{iwhere}: custom items need {key}")
                    continue
                ex = exercises.get(slug)
                if not ex:
                    errors.append(f"{iwhere}: no exercise file for '{slug}'")
                    continue
                home = item.get("home")
                if home:
                    alt = exercises.get(home)
                    if not alt:
                        errors.append(f"{iwhere}: no exercise file for home swap '{home}'")
                    elif not _home_ok(alt):
                        errors.append(f"{iwhere}: home swap '{home}' needs gym equipment")
                elif not _home_ok(ex):
                    errors.append(
                        f"{iwhere}: '{slug}' needs gym equipment; add a home: alternative"
                    )
    return errors


def load_programs(
    exercises: List[dict], content_dir: Path = PROGRAMS_DIR
) -> Tuple[List[dict], List[str]]:
    """Load and validate every program file. Raises ProgramError listing all problems."""
    by_slug = {e["slug"]: e for e in exercises}
    errors: List[str] = []
    programs: List[dict] = []
    for path in sorted(Path(content_dir).glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{path.name}: invalid YAML ({e})")
            continue
        file_errors = validate_program(raw, path.name, by_slug)
        errors.extend(file_errors)
        if not file_errors:
            programs.append(raw)
    if errors:
        raise ProgramError("\n".join(errors))
    return programs, []


# -----------------------------------------------------------------------------
# Expansion
# -----------------------------------------------------------------------------
def track_slug(hub_slug: str, level: str, env: str) -> str:
    return f"{hub_slug}-{level}-{env}"


def _item_rows(day: dict, env: str, phase: dict, exercises: Dict[str, dict]) -> List[dict]:
    rows = []
    for item in day["items"]:
        if item.get("custom"):
            role = item.get("role") or "accessory"
            slug, name = None, item["custom"]
        else:
            slug = item["exercise"]
            if env == "home" and item.get("home"):
                slug = item["home"]
            ex = exercises[slug]
            role = item.get("role") or _default_role(ex)
            name = ex["name"]
        swap_for = None if item.get("custom") or slug == item["exercise"] else item["exercise"]
        scheme = phase.get(role) or {}
        rows.append(
            {
                "workout_slug": slug,
                "custom_name": None if slug else name,
                "name": name,
                "role": role,
                "sets": int(item.get("sets") or scheme.get("sets") or 0),
                "reps": str(item.get("reps") or scheme.get("reps") or ""),
                "rest": str(item.get("rest") or scheme.get("rest") or ""),
                "notes": item.get("note") or "",
                "swap_for": swap_for,
            }
        )
    return rows


def expand_program(raw: dict, exercises: List[dict]) -> dict:
    """The hub, its tracks and every week's items, ready to write."""
    by_slug = {e["slug"]: e for e in exercises}
    hub_slug = raw["slug"]
    weeks = raw["weeks"]
    level_days = {}
    tracks = []
    for level, body in raw["levels"].items():
        phases = body.get("phases") or raw["phases"]
        level_days[level] = len(body["days"])
        for env in ENVS:
            track_weeks = []
            for w in range(1, weeks + 1):
                phase = _phase_for(phases, w)
                days = []
                for day in body["days"]:
                    items = _item_rows(day, env, phase, by_slug)
                    days.append(
                        {
                            "label": day["label"],
                            "key": slugify(day["label"]),
                            "focus": day.get("focus") or "",
                            "items": items,
                            "minutes": estimate_minutes(items),
                        }
                    )
                track_weeks.append(
                    {
                        "week_number": w,
                        "title": phase.get("name") or "",
                        "notes": phase.get("note") or "",
                        "days": days,
                    }
                )
            tracks.append(
                {
                    "slug": track_slug(hub_slug, level, env),
                    "level": level,
                    "env": env,
                    "title": (
                        f"{raw['title']} — {tx.LEVELS[level]['label']} • "
                        f"{tx.ENVIRONMENTS[env]['label']}"
                    ),
                    "summary": body.get("summary") or raw.get("summary") or "",
                    "days_per_week": len(body["days"]),
                    "weeks": track_weeks,
                }
            )
    counts = sorted(set(level_days.values()))
    return {
        "hub": {
            "slug": hub_slug,
            "title": raw["title"],
            "summary": raw.get("summary") or "",
            "goal": raw.get("goal") or "",
            "rules": list(raw.get("rules") or []),
            "weeks": weeks,
            "session_minutes": raw.get("session_minutes"),
            "days_per_week": counts,
            "accent": raw.get("accent") or "",
            "order": raw.get("order"),
            "status": raw.get("status", "published"),
            "phases": [
                {
                    "name": p["name"],
                    "weeks": list(p["weeks"]),
                    "note": p.get("note") or "",
                    "main": p.get("main"),
                    "accessory": p.get("accessory"),
                }
                for p in raw["phases"]
            ],
        },
        "tracks": tracks,
    }


# -----------------------------------------------------------------------------
# Sync
# -----------------------------------------------------------------------------
def content_hash(hub: dict, track: dict) -> str:
    """Fingerprint of what a sync writes for a track (its hub's fields included), so an
    unchanged track is left alone instead of being rewritten every time."""
    payload = json.dumps({"hub": hub, "track": track}, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def plan_program_sync(db, programs: List[dict], exercises: List[dict]) -> List[dict]:
    """What a sync would do, per track: new, replace (content-managed) or skip (Admin-built)."""
    plan = []
    expanded_all = [expand_program(raw, exercises) for raw in programs]
    slugs = [t["slug"] for e in expanded_all for t in e["tracks"]]
    # Two reads for every track: their docs, then weeks built by hand in Admin.
    docs = {
        d["slug"]: d
        for d in db.programs.find(
            {"slug": {"$in": slugs}}, {"_id": 1, "slug": 1, "content_hash": 1}
        )
    }
    hand_built_by_id: Dict[object, int] = {}
    synced_weeks_by_id: Dict[object, int] = {}
    if docs:
        for w in db.program_weeks.find(
            {"program_id": {"$in": [d["_id"] for d in docs.values()]}},
            {"program_id": 1, "content_managed": 1},
        ):
            counts = synced_weeks_by_id if w.get("content_managed") is True else hand_built_by_id
            counts[w["program_id"]] = counts.get(w["program_id"], 0) + 1
    for expanded in expanded_all:
        for track in expanded["tracks"]:
            doc = docs.get(track["slug"])
            hand_built = hand_built_by_id.get(doc["_id"], 0) if doc else 0
            items = sum(len(d["items"]) for w in track["weeks"] for d in w["days"])
            digest = content_hash(expanded["hub"], track)
            if hand_built:
                action = "skip"
            elif not doc:
                action = "new"
            else:
                complete = synced_weeks_by_id.get(doc["_id"], 0) == len(track["weeks"])
                action = (
                    "unchanged" if complete and doc.get("content_hash") == digest else "replace"
                )
            plan.append(
                {
                    "hub": expanded["hub"],
                    "track": track,
                    "hash": digest,
                    "action": action,
                    "hand_built_weeks": hand_built,
                    "item_count": items,
                }
            )
    return plan


def describe_program_plan(plan: List[dict], force: bool = False) -> List[str]:
    lines = []
    for row in plan:
        t = row["track"]
        action = "replace" if (row["action"] == "skip" and force) else row["action"]
        if action == "skip":
            lines.append(
                f"  ! {t['slug']}: {row['hand_built_weeks']} week(s) built in Admin; "
                "skipped (use --force to replace)"
            )
        elif action == "unchanged":
            lines.append(f"  = {t['slug']}: unchanged")
        else:
            sign = "+" if action == "new" else "~"
            lines.append(
                f"  {sign} {t['slug']}: {len(t['weeks'])} weeks, {row['item_count']} items"
            )
    return lines


def _workout_ids(db, slugs: List[str]) -> Dict[str, object]:
    found = db.workouts.find({"slug": {"$in": sorted(set(slugs))}}, {"_id": 1, "slug": 1})
    return {w["slug"]: w["_id"] for w in found}


def apply_program_sync(db, plan: List[dict], force: bool = False) -> None:
    now = datetime.datetime.utcnow()
    hubs_done = set()
    for row in plan:
        hub, track = row["hub"], row["track"]
        if row["action"] in ("skip", "unchanged") and not force:
            continue
        publish = "published" if hub["status"] == "published" else "draft"
        if hub["slug"] not in hubs_done:
            hubs_done.add(hub["slug"])
            # A file's `order:` sets the list position; otherwise Admin's order is kept.
            on_insert = {"created_at": now, "show_on_home": True}
            placed = {"order": hub["order"]} if hub.get("order") is not None else {}
            if not placed:
                on_insert["order"] = 0
            db.programs.update_one(
                {"slug": hub["slug"]},
                {
                    "$set": {
                        **placed,
                        "title": hub["title"],
                        "kind": "hub",
                        "summary": hub["summary"],
                        "goal": hub["goal"],
                        "rules": hub["rules"],
                        "duration_label": f"{hub['weeks']} weeks",
                        "session_minutes": hub["session_minutes"],
                        "days_per_week": hub["days_per_week"],
                        "phases": hub["phases"],
                        "accent": hub.get("accent") or "",
                        "publish_status": publish,
                        "active": True,
                        "content_managed": True,
                        "content_synced_at": now,
                    },
                    "$setOnInsert": on_insert,
                },
                upsert=True,
            )
        order = {"beginner": 10, "intermediate": 20, "advanced": 30}.get(track["level"], 90)
        order += ENVS.index(track["env"])
        db.programs.update_one(
            {"slug": track["slug"]},
            {
                "$set": {
                    "title": track["title"],
                    "kind": "track",
                    "hub_slug": hub["slug"],
                    "track_level": track["level"],
                    "track_env": track["env"],
                    "summary": track["summary"],
                    "rules": hub["rules"],
                    "duration_label": f"{hub['weeks']} weeks",
                    "days_per_week": track["days_per_week"],
                    "publish_status": publish,
                    "active": True,
                    "content_managed": True,
                    "content_synced_at": now,
                    "content_hash": row.get("hash"),
                },
                "$setOnInsert": {"created_at": now, "show_on_home": False, "order": order},
            },
            upsert=True,
        )
        program_id = db.programs.find_one({"slug": track["slug"]}, {"_id": 1})["_id"]

        # Replace this track's weeks and items. Progress lives elsewhere, keyed by
        # track slug + week number + day key, so it survives.
        old_weeks = [
            w["_id"] for w in db.program_weeks.find({"program_id": program_id}, {"_id": 1})
        ]
        if old_weeks:
            db.program_items.delete_many({"week_id": {"$in": old_weeks}})
            db.program_weeks.delete_many({"_id": {"$in": old_weeks}})

        slugs = [
            it["workout_slug"]
            for w in track["weeks"]
            for d in w["days"]
            for it in d["items"]
            if it["workout_slug"]
        ]
        ids = _workout_ids(db, slugs)
        for week in track["weeks"]:
            week_id = db.program_weeks.insert_one(
                {
                    "program_id": program_id,
                    "week_number": week["week_number"],
                    "order": week["week_number"],
                    "title": week["title"],
                    "notes": week["notes"],
                    "content_managed": True,
                    "created_at": now,
                }
            ).inserted_id
            order_n = 0
            rows = []
            for day in week["days"]:
                for it in day["items"]:
                    order_n += 1
                    rows.append(
                        {
                            "program_id": program_id,
                            "week_id": week_id,
                            "day": day["label"],
                            "day_focus": day["focus"],
                            "order": order_n,
                            "workout_slug": it["workout_slug"],
                            "workout_id": ids.get(it["workout_slug"]),
                            "custom_name": it["custom_name"],
                            "role": it["role"],
                            "sets": it["sets"],
                            "reps": it["reps"],
                            "rest": it["rest"],
                            "notes": it["notes"],
                            "swap_for": it.get("swap_for"),
                            "content_managed": True,
                            "created_at": now,
                        }
                    )
            if rows:
                db.program_items.insert_many(rows)
