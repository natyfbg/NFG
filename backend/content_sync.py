"""Exercise content-as-code: validate YAML exercise files and sync them into MongoDB.

Exercise files live in backend/content/exercises/<slug>.yaml (one per exercise).
See backend/content/README.md for the file format.

Usage (from backend/, or `docker compose exec app python content_sync.py ...`):

    python content_sync.py check                   # validate files only
    python content_sync.py placeholders            # write missing SVG placeholders
    python content_sync.py sync                    # DRY RUN: show what would change
    python content_sync.py sync --apply            # write changes (local DB)
    python content_sync.py sync --apply --allow-remote   # required for non-local DBs

Sync rules:
- Exercises are matched by slug; existing documents keep their _id and created_at.
- Nothing is ever deleted. DB exercises without a file are reported as "unmanaged".
- Optional text fields are written only when the file defines them, so the file
  never blanks out content that only exists in the database.
- rating, is_favorite and other admin-only fields are never touched.
- Images: a file's explicit `images` always win, and `images: []` clears photos back
  to the generated placeholder. Otherwise the SVG placeholder is used only when the
  document has no real images (admin uploads are kept), and dead
  via.placeholder.com links are cleaned up.
- If an exercise was edited in admin after its last sync, it is skipped unless
  --force is given (the dry run shows which fields would be overwritten).
- The connection string is never printed; only host and database name.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import taxonomy as tx
import yaml

BACKEND_DIR = Path(__file__).resolve().parent
CONTENT_DIR = BACKEND_DIR / "content" / "exercises"
PLACEHOLDER_DIR = BACKEND_DIR / "static" / "img" / "exercises"
PLACEHOLDER_URL = "/static/img/exercises/{slug}.svg"

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
VIDEO_HOSTS = {
    "youtube": ("youtube.com", "youtu.be"),
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
}
# Slugs that collide with fixed /workouts/<...> routes.
RESERVED_SLUGS = {"all", "styles", "body-parts", "browse", "mix"}

REQUIRED_KEYS = ("slug", "name", "status", "level", "category", "movement", "muscles", "equipment")
TEXT_LIST_KEYS = ("setup", "steps", "cues", "mistakes", "safety", "tags")
SLUG_LIST_KEYS = ("regressions", "progressions")
OPTIONAL_KEYS = (
    (
        "summary",
        "breathing",
        "mechanics",
        "laterality",
        "full_body",
        "style",
        "youtube_id",
        "images",
        "videos",
        "notes",  # free-form authoring notes; never synced
    )
    + TEXT_LIST_KEYS
    + SLUG_LIST_KEYS
)
ALLOWED_KEYS = set(REQUIRED_KEYS) | set(OPTIONAL_KEYS)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "mongo"}


class ContentError(Exception):
    """Raised when content files fail validation. Message lists every problem."""


# -----------------------------------------------------------------------------
# Loading and validation
# -----------------------------------------------------------------------------
def _is_text_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def _is_video_url(platform: str, url) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == h or host.endswith("." + h) for h in VIDEO_HOSTS[platform]
    )


def validate_exercise(raw, filename: str) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings) for one parsed YAML document."""
    errors: List[str] = []
    warnings: List[str] = []

    def err(msg):
        errors.append(f"{filename}: {msg}")

    if not isinstance(raw, dict):
        return [f"{filename}: file must contain a mapping of fields"], []

    unknown = sorted(set(raw) - ALLOWED_KEYS)
    if unknown:
        err(f"unknown field(s) {unknown} (typo?)")
    for key in REQUIRED_KEYS:
        if key not in raw:
            err(f"missing required field '{key}'")
    if errors:
        return errors, warnings

    slug = raw["slug"]
    stem = Path(filename).stem
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        err(f"slug '{slug}' must be lowercase words joined by hyphens")
    elif slug != stem:
        err(f"slug '{slug}' must match the file name '{stem}.yaml'")
    elif slug in RESERVED_SLUGS:
        err(f"slug '{slug}' is reserved for a site page; pick another")

    if not isinstance(raw["name"], str) or not raw["name"].strip():
        err("name must be non-empty text")
    if raw["status"] not in tx.PUBLISH_STATUSES:
        err(f"status must be one of {list(tx.PUBLISH_STATUSES)}")
    if raw["level"] not in tx.LEVELS:
        err(f"level must be one of {list(tx.LEVELS)}")
    if raw["category"] not in tx.CATEGORIES:
        err(f"category must be one of {list(tx.CATEGORIES)}")
    if raw["movement"] not in tx.MOVEMENTS:
        err(f"movement must be one of {list(tx.MOVEMENTS)}")

    muscles = raw["muscles"]
    if not isinstance(muscles, dict) or set(muscles) - {"primary", "secondary"}:
        err("muscles must have 'primary' and optional 'secondary' lists")
    else:
        primary = muscles.get("primary") or []
        secondary = muscles.get("secondary") or []
        if not isinstance(primary, list) or not primary:
            err("muscles.primary needs at least one muscle")
        if not isinstance(secondary, list):
            err("muscles.secondary must be a list")
        if isinstance(primary, list) and isinstance(secondary, list):
            bad = [m for m in primary + secondary if m not in tx.MUSCLES]
            if bad:
                err(f"unknown muscle key(s) {bad}; valid: {sorted(tx.MUSCLES)}")
            overlap = set(primary) & set(secondary)
            if overlap:
                err(f"muscle(s) {sorted(overlap)} listed as both primary and secondary")

    equipment = raw["equipment"]
    if not isinstance(equipment, list):
        err("equipment must be a list ([] means bodyweight)")
    else:
        bad = [e for e in equipment if e not in tx.EQUIPMENT]
        if bad:
            err(f"unknown equipment key(s) {bad}; valid: {sorted(tx.EQUIPMENT)}")

    if "mechanics" in raw and raw["mechanics"] not in tx.MECHANICS:
        err(f"mechanics must be one of {list(tx.MECHANICS)}")
    if "laterality" in raw and raw["laterality"] not in tx.LATERALITY:
        err(f"laterality must be one of {list(tx.LATERALITY)}")
    if "full_body" in raw and not isinstance(raw["full_body"], bool):
        err("full_body must be true or false")
    for key in ("summary", "breathing", "style"):
        if key in raw and (not isinstance(raw[key], str) or not raw[key].strip()):
            err(f"{key} must be non-empty text")
    for key in TEXT_LIST_KEYS:
        if key in raw and not _is_text_list(raw[key]):
            err(f"{key} must be a list of non-empty text items")
    for key in SLUG_LIST_KEYS:
        if key in raw and not (
            isinstance(raw[key], list)
            and all(isinstance(s, str) and SLUG_RE.match(s) for s in raw[key])
        ):
            err(f"{key} must be a list of exercise slugs")
    if "images" in raw and not _is_text_list(raw["images"]):
        err("images must be a list of image paths/URLs")
    videos = raw.get("videos")
    if videos is not None:
        if not isinstance(videos, dict) or set(videos) - set(VIDEO_HOSTS):
            err(f"videos must map {sorted(VIDEO_HOSTS)} to links")
        else:
            for platform, url in videos.items():
                if not _is_video_url(platform, url):
                    err(f"videos.{platform} must be an https link on {VIDEO_HOSTS[platform][0]}")
    youtube_id = raw.get("youtube_id")
    if youtube_id and not (isinstance(youtube_id, str) and YOUTUBE_ID_RE.match(youtube_id)):
        err("youtube_id must be the 11-character video id (or empty)")

    if not errors and raw["status"] == "published":
        for key in ("summary", "steps", "cues"):
            if not raw.get(key):
                warnings.append(f"{filename}: published without '{key}'")
    return errors, warnings


def load_exercises(content_dir: Path = CONTENT_DIR) -> Tuple[List[dict], List[str]]:
    """Load and validate every exercise file. Raises ContentError listing all problems."""
    errors: List[str] = []
    warnings: List[str] = []
    exercises: List[dict] = []
    seen: Dict[str, str] = {}

    for path in sorted(Path(content_dir).glob("*.y*ml")):
        if path.suffix not in (".yaml", ".yml"):
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{path.name}: invalid YAML ({e})")
            continue
        file_errors, file_warnings = validate_exercise(raw, path.name)
        errors.extend(file_errors)
        warnings.extend(file_warnings)
        if file_errors:
            continue
        name_key = raw["name"].strip().lower()
        if name_key in seen:
            errors.append(f"{path.name}: name '{raw['name']}' duplicates {seen[name_key]}")
            continue
        seen[name_key] = path.name
        exercises.append(raw)

    slugs = {ex["slug"] for ex in exercises}
    for ex in exercises:
        for key in SLUG_LIST_KEYS:
            missing = [s for s in ex.get(key, []) if s not in slugs]
            if missing:
                warnings.append(
                    f"{ex['slug']}.yaml: {key} reference(s) not in content yet {missing}"
                )

    if errors:
        raise ContentError("\n".join(errors))
    return exercises, warnings


# -----------------------------------------------------------------------------
# Building database documents
# -----------------------------------------------------------------------------
def derived_groups(ex: dict) -> Tuple[List[str], List[str]]:
    """(primary_groups, muscle_groups) for an exercise, including category groups."""
    primary = ex["muscles"]["primary"]
    secondary = ex["muscles"].get("secondary") or []
    primary_groups = tx.groups_for_muscles(primary)
    all_groups = tx.groups_for_muscles(primary + secondary)
    extra: List[str] = []
    if ex.get("full_body") or len(primary_groups) >= tx.FULL_BODY_MIN_GROUPS:
        extra.append("full_body")
    for gk, group in tx.MUSCLE_GROUPS.items():
        if group.get("category") and group["category"] == ex["category"]:
            extra.append(gk)
    for gk in extra:
        if gk not in primary_groups:
            primary_groups.append(gk)
        if gk not in all_groups:
            all_groups.append(gk)
    return primary_groups, all_groups


def build_doc(ex: dict) -> dict:
    """Database fields managed by the content file (images handled separately)."""
    primary = list(ex["muscles"]["primary"])
    secondary = list(ex["muscles"].get("secondary") or [])
    equipment = list(ex["equipment"])
    level = tx.LEVELS[ex["level"]]
    primary_groups, muscle_groups = derived_groups(ex)
    body_parts = tx.legacy_body_parts(primary)

    doc = {
        "name": ex["name"].strip(),
        "slug": ex["slug"],
        "publish_status": ex["status"],
        "category": ex["category"],
        "movement": ex["movement"],
        "muscles": {"primary": primary, "secondary": secondary},
        "primary_groups": primary_groups,
        "muscle_groups": muscle_groups,
        "equipment_required": equipment,
        "equipment_label": tx.equipment_text(equipment),
        "environments": tx.environments_for_equipment(equipment),
        # Legacy fields the current templates, filters and admin form read.
        "level": level["label"],
        "difficulty_tier": level["tier"],
        "body_part": body_parts[0],
        "body_parts": body_parts,
        "primary_muscle": tx.MUSCLES[primary[0]]["legacy"],
        "movement_pattern": tx.MOVEMENTS[ex["movement"]]["legacy"],
        "equipment": tx.legacy_equipment_label(equipment),
        "style": ex.get("style") or tx.legacy_style_label(equipment, ex["category"]),
        "content_source": "repo",
    }
    for key in ("mechanics", "laterality"):
        if key in ex:
            doc[key] = ex[key]
    if "summary" in ex:
        doc["info"] = ex["summary"].strip()
    if "cues" in ex:
        doc["tips"] = list(ex["cues"])
    instructions = {}
    for key in ("setup", "steps"):
        if key in ex:
            instructions[key] = list(ex[key])
    if "breathing" in ex:
        instructions["breathing"] = ex["breathing"].strip()
    if instructions:
        doc["instructions"] = instructions
    for key in ("mistakes", "safety", "tags", "regressions", "progressions"):
        if key in ex:
            doc[key] = list(ex[key])
    if "youtube_id" in ex:
        doc["youtube_id"] = ex["youtube_id"] or ""
    if "videos" in ex:
        doc["videos"] = {k: v.strip() for k, v in ex["videos"].items()}
    return doc


def is_placeholder_image(url: Optional[str]) -> bool:
    u = (url or "").lower()
    return (
        not u
        or "via.placeholder.com" in u
        or "placehold.co" in u
        or (u.startswith("/static/img/exercises/") and u.endswith(".svg"))
    )


def image_fields(ex: dict, existing: Optional[dict]) -> dict:
    """Image fields to write, respecting real images already in the database."""
    out: dict = {}
    if "images" in ex:
        # An explicit list always wins. `images: []` means "no photos": it clears
        # admin uploads and puts the generated muscle map back.
        out["images"] = list(ex["images"]) or [PLACEHOLDER_URL.format(slug=ex["slug"])]
    else:
        current = (existing or {}).get("images") or []
        if all(is_placeholder_image(u) for u in current):
            out["images"] = [PLACEHOLDER_URL.format(slug=ex["slug"])]
    muscle_image = (existing or {}).get("muscle_image")
    if muscle_image and "via.placeholder.com" in muscle_image.lower():
        out["muscle_image"] = None  # dead placeholder service
    return out


# -----------------------------------------------------------------------------
# Sync planning and applying
# -----------------------------------------------------------------------------
def plan_sync(db, exercises: List[dict], only_slug: Optional[str] = None) -> dict:
    """Compare files with the DB. Returns {creates, updates, unchanged, drifted, unmanaged}."""
    plan = {"creates": [], "updates": [], "unchanged": [], "drifted": [], "unmanaged": []}
    file_slugs = set()
    for ex in exercises:
        if only_slug and ex["slug"] != only_slug:
            continue
        file_slugs.add(ex["slug"])
        existing = db.workouts.find_one({"slug": ex["slug"]})
        desired = build_doc(ex)
        desired.update(image_fields(ex, existing))
        if not existing:
            plan["creates"].append({"slug": ex["slug"], "doc": desired})
            continue
        changes = {k: v for k, v in desired.items() if existing.get(k) != v}
        if not changes:
            plan["unchanged"].append(ex["slug"])
            continue
        entry = {"slug": ex["slug"], "_id": existing["_id"], "set": changes}
        edited_at = existing.get("updated_at")
        synced_at = existing.get("content_synced_at")
        if edited_at and (not synced_at or edited_at > synced_at):
            plan["drifted"].append(entry)
        else:
            plan["updates"].append(entry)
    if not only_slug:
        for doc in db.workouts.find({}, {"slug": 1}):
            if doc.get("slug") and doc["slug"] not in file_slugs:
                plan["unmanaged"].append(doc["slug"])
    return plan


def apply_sync(db, plan: dict, force: bool = False, now: Optional[datetime.datetime] = None):
    now = now or datetime.datetime.utcnow()
    for item in plan["creates"]:
        doc = dict(item["doc"], created_at=now, content_synced_at=now)
        db.workouts.insert_one(doc)
    targets = plan["updates"] + (plan["drifted"] if force else [])
    for item in targets:
        db.workouts.update_one(
            {"_id": item["_id"]},
            {"$set": dict(item["set"], content_synced_at=now)},
        )


def describe_plan(plan: dict, force: bool) -> List[str]:
    lines = []
    for item in plan["creates"]:
        lines.append(f"  + create  {item['slug']} ({item['doc']['publish_status']})")
    for item in plan["updates"]:
        lines.append(f"  ~ update  {item['slug']}: {', '.join(sorted(item['set']))}")
    for item in plan["drifted"]:
        verb = "overwrite" if force else "SKIP    "
        lines.append(
            f"  ! {verb} {item['slug']}: edited in admin since last sync; "
            f"would change {', '.join(sorted(item['set']))}"
        )
    if plan["unchanged"]:
        lines.append(f"  = unchanged: {len(plan['unchanged'])}")
    if plan["unmanaged"]:
        lines.append(f"  ? in DB but not in content/ (left alone): {', '.join(plan['unmanaged'])}")
    return lines


# -----------------------------------------------------------------------------
# Database connection (never prints credentials)
# -----------------------------------------------------------------------------
def uri_hosts_and_db(uri: str) -> Tuple[List[str], Optional[str]]:
    """Host names and database from a Mongo URI, with credentials stripped."""
    tail = uri.split("://", 1)[-1]
    authority, _, rest = tail.partition("/")
    authority = authority.rsplit("@", 1)[-1]
    hosts = []
    for part in authority.split(","):
        host = part.strip()
        if host.startswith("["):  # IPv6 like [::1]:27017
            host = host[1:].split("]", 1)[0]
        else:
            host = host.split(":", 1)[0]
        if host:
            hosts.append(host)
    db_name = rest.split("?", 1)[0] or None
    return hosts, db_name


def resolve_target() -> Tuple[str, List[str], str, bool]:
    """(uri, hosts, db_name, is_local) from the environment, without connecting."""
    from dotenv import load_dotenv

    load_dotenv()
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    hosts, uri_db = uri_hosts_and_db(uri)
    db_name = os.getenv("MONGO_DB") or uri_db or "NFG"
    is_local = bool(hosts) and all(h in LOCAL_HOSTS for h in hosts)
    return uri, hosts, db_name, is_local


def connect(uri: str, db_name: str):
    from pymongo import MongoClient

    return MongoClient(uri, serverSelectionTimeoutMS=5000)[db_name]


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def _load_or_exit() -> List[dict]:
    try:
        exercises, warnings = load_exercises()
    except ContentError as e:
        print("Content validation failed:\n" + str(e), file=sys.stderr)
        sys.exit(1)
    for w in warnings:
        print(f"warning: {w}")
    return exercises


def collection_warnings(exercises: List[dict]) -> List[str]:
    """Curated collections that reference missing or draft exercises."""
    import curation

    status = {ex["slug"]: ex["status"] for ex in exercises}
    warnings = []
    for key, col in curation.load_collections().items():
        for slug in col["items"]:
            if slug not in status:
                warnings.append(f"collections/{key}.yaml: '{slug}' has no exercise file yet")
            elif status[slug] != "published":
                warnings.append(f"collections/{key}.yaml: '{slug}' is still a draft")
    return warnings


def _load_programs_or_exit(exercises: List[dict]) -> List[dict]:
    import program_content

    try:
        programs, _ = program_content.load_programs(exercises)
    except program_content.ProgramError as e:
        print("Program validation failed:\n" + str(e), file=sys.stderr)
        sys.exit(1)
    return programs


def cmd_check(_args) -> None:
    import curation

    exercises = _load_or_exit()
    try:
        for w in collection_warnings(exercises):
            print(f"warning: {w}")
    except curation.CurationError as e:
        print("Collection validation failed:\n" + str(e), file=sys.stderr)
        sys.exit(1)
    programs = _load_programs_or_exit(exercises)
    drafts = sum(1 for ex in exercises if ex["status"] == "draft")
    print(
        f"OK: {len(exercises)} exercise files ({drafts} draft, {len(exercises) - drafts} published)"
        f", {len(programs)} program file(s)"
    )


def cmd_placeholders(args) -> None:
    from placeholders import render_exercise_svg

    exercises = _load_or_exit()
    PLACEHOLDER_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for ex in exercises:
        path = PLACEHOLDER_DIR / f"{ex['slug']}.svg"
        if path.exists() and not args.overwrite:
            continue
        path.write_text(render_exercise_svg(ex), encoding="utf-8")
        written += 1
    print(f"Wrote {written} placeholder(s) to {PLACEHOLDER_DIR.relative_to(BACKEND_DIR)}")


def cmd_sync(args) -> None:
    exercises = _load_or_exit()
    uri, hosts, db_name, is_local = resolve_target()
    print(f"Target: {', '.join(hosts)} / db {db_name} ({'local' if is_local else 'REMOTE'})")
    # Refuse remote writes before connecting at all.
    if args.apply and not is_local and not args.allow_remote:
        print("Refusing to write to a non-local database without --allow-remote.", file=sys.stderr)
        sys.exit(2)
    db = connect(uri, db_name)
    plan = plan_sync(db, exercises, only_slug=args.slug)
    for line in describe_plan(plan, args.force) or ["  nothing to do"]:
        print(line)

    # Programs run after exercises so new exercise slugs resolve. Skipped with --slug.
    program_plan = []
    if not args.slug:
        import program_content

        programs = _load_programs_or_exit(exercises)
        program_plan = program_content.plan_program_sync(db, programs, exercises)
        if program_plan:
            print("Programs:")
            for line in program_content.describe_program_plan(program_plan, args.force_programs):
                print(line)
    if not args.apply:
        print("Dry run only. Re-run with --apply to write these changes.")
        return
    apply_sync(db, plan, force=args.force)
    skipped = 0 if args.force else len(plan["drifted"])
    print(
        f"Applied: {len(plan['creates'])} created, "
        f"{len(plan['updates']) + (len(plan['drifted']) if args.force else 0)} updated, "
        f"{skipped} skipped (admin-edited)."
    )
    if program_plan:
        import program_content

        program_content.apply_program_sync(db, program_plan, force=args.force_programs)
        print("Programs applied.")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="NFG exercise content tools")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="validate content files")
    p_ph = sub.add_parser("placeholders", help="write SVG placeholders for exercises")
    p_ph.add_argument("--overwrite", action="store_true", help="regenerate existing files")
    p_sync = sub.add_parser("sync", help="sync content files into MongoDB (dry run by default)")
    p_sync.add_argument("--apply", action="store_true", help="write changes")
    p_sync.add_argument("--slug", help="only this exercise")
    p_sync.add_argument("--force", action="store_true", help="overwrite admin-edited exercises")
    p_sync.add_argument(
        "--force-programs",
        action="store_true",
        help="replace program weeks that were built by hand in Admin",
    )
    p_sync.add_argument(
        "--allow-remote", action="store_true", help="permit --apply against a non-local DB"
    )
    args = parser.parse_args(argv)
    {"check": cmd_check, "placeholders": cmd_placeholders, "sync": cmd_sync}[args.command](args)


if __name__ == "__main__":
    main()
