"""Session mode: a list of exercises becomes a workout you log set by set.

Pure (no Flask, no Mongo), like `macros.py`: the routes in `training_routes.py` load and
save; this module decides what a workout looks like and does the maths.

A workout (`training_sessions` doc) holds snapshots, so history stays readable if an
exercise is renamed later:

    {viewer_id, status: "active" | "done", title, source: {...},
     items: [{key, slug, name, group, kind, role, sets_target, reps, rest, rest_seconds,
              note, rows: [{done, weight_kg?, reps?, seconds?, minutes?, at?}]}],
     started_at, updated_at, finished_at, local_date}

`kind` decides what a set asks for: weight_reps (loaded lifts), reps (bodyweight, bands),
seconds (holds, stretches) or minutes (cardio).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import program_content
import session_builder as sb

KG_PER_LB = 0.45359237
UNITS = ("imperial", "metric")
DEFAULT_UNITS = "imperial"

KINDS = ("weight_reps", "reps", "seconds", "minutes")
LOADED_EQUIPMENT = {"dumbbell", "barbell", "cable", "machine", "smith", "kettlebell"}

# Rest when the source doesn't give one (sessions, the Mixer, a single exercise).
REST_BY_ROLE = {
    "main": "2 min",
    "isolation": "90s",
    "secondary": "60s",
    "core": "45s",
    "hold": "45s",
    "stretch": "15s",
    "cardio": "",
}

STALE_AFTER = dt.timedelta(hours=6)
MAX_ITEMS = 30
MAX_ROWS = 12
DEFAULT_SETS = 3

LIMITS = {"weight_kg": 1000.0, "reps": 1000, "seconds": 36000, "minutes": 600.0}


# -----------------------------------------------------------------------------
# Units
# -----------------------------------------------------------------------------
def norm_units(value: Optional[str]) -> str:
    return value if value in UNITS else DEFAULT_UNITS


def unit_label(units: str) -> str:
    return "lb" if norm_units(units) == "imperial" else "kg"


def to_kg(value: float, units: str) -> float:
    return value * KG_PER_LB if norm_units(units) == "imperial" else value


def from_kg(kg: float, units: str) -> float:
    return kg / KG_PER_LB if norm_units(units) == "imperial" else kg


def show_number(value: Optional[float]) -> str:
    """60.0 -> '60', 62.5 -> '62.5', 61.235 -> '61.2'."""
    if value is None:
        return ""
    rounded = round(float(value), 1)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:.1f}"


def show_weight(kg: Optional[float], units: str) -> str:
    """kg -> the number in the viewer's unit, rounded for display ('135', '61.2')."""
    if kg is None:
        return ""
    return show_number(from_kg(kg, units))


# -----------------------------------------------------------------------------
# What an exercise asks for
# -----------------------------------------------------------------------------
def input_kind(doc: Optional[dict]) -> str:
    if not doc:
        return "weight_reps"
    k = sb.kind(doc)
    if k == "cardio":
        return "minutes"
    if k in ("hold", "stretch"):
        return "seconds"
    if equipment_keys(doc) & LOADED_EQUIPMENT:
        return "weight_reps"
    return "reps"


def equipment_keys(doc: dict) -> set:
    """Equipment keys: `equipment_all` on synced docs (`equipment` there is a display
    label), `equipment` in content files."""
    keys = doc.get("equipment_all")
    if keys is None:
        keys = doc.get("equipment")
    return set(keys) if isinstance(keys, (list, tuple, set)) else set()


def default_rest(doc: Optional[dict], role: str) -> str:
    k = sb.kind(doc) if doc else "compound"
    if k in ("cardio", "stretch", "hold", "core"):
        return REST_BY_ROLE[k]
    if role == "secondary":
        return REST_BY_ROLE["secondary"]
    return REST_BY_ROLE["isolation"] if k == "isolation" else REST_BY_ROLE["main"]


SCHEME_RE = re.compile(r"^\s*(\d+)\s*[×xX]\s*(.+?)\s*$")


def parse_scheme(text: Optional[str]) -> Tuple[int, str]:
    """'3 × 8–10 each side' -> (3, '8–10 each side'); '15–20 min' -> (1, '15–20 min')."""
    text = (text or "").strip()
    m = SCHEME_RE.match(text)
    if m:
        return max(1, min(MAX_ROWS, int(m.group(1)))), m.group(2)
    return (1, text) if text else (DEFAULT_SETS, "")


def rest_seconds(rest: Optional[str]) -> int:
    return program_content.rest_seconds(rest) if (rest or "").strip() else 0


def primary_group(doc: Optional[dict]) -> str:
    if not doc:
        return ""
    groups = doc.get("primary_groups") or doc.get("muscle_groups") or []
    return groups[0] if groups else ""


def new_key() -> str:
    return uuid.uuid4().hex[:8]


def make_item(
    doc: Optional[dict],
    *,
    name: str = "",
    sets: Optional[int] = None,
    reps: str = "",
    rest: Optional[str] = None,
    role: str = "main",
    note: str = "",
) -> dict:
    """One exercise of a workout, with `sets` empty rows to fill in."""
    count = max(1, min(MAX_ROWS, int(sets or DEFAULT_SETS)))
    rest_text = default_rest(doc, role) if rest is None else str(rest or "")
    return {
        "key": new_key(),
        "slug": (doc or {}).get("slug") or "",
        "name": (doc or {}).get("name") or name or "Exercise",
        "group": primary_group(doc),
        "kind": input_kind(doc),
        "role": role,
        "sets_target": count,
        "reps": str(reps or ""),
        "rest": rest_text,
        "rest_seconds": rest_seconds(rest_text),
        "note": note or "",
        "rows": [{"done": False} for _ in range(count)],
    }


# -----------------------------------------------------------------------------
# Building a workout from each source
# -----------------------------------------------------------------------------
def items_from_session(built: dict) -> List[dict]:
    """A built session or Mixer mix: main lifts, then secondary lifts (not "more")."""
    items = []
    for role in ("main", "secondary"):
        for doc in built.get(role) or []:
            sets, reps = parse_scheme(doc.get("scheme"))
            items.append(make_item(doc, sets=sets, reps=reps, role=role))
    return items[:MAX_ITEMS]


def items_from_program_day(day_items: Iterable[dict]) -> List[dict]:
    """A program day's items (from the day page): their own sets, reps and rest."""
    items = []
    for it in day_items:
        doc = it.get("exercise")
        try:
            sets = int(str(it.get("sets") or "").strip() or 0) or DEFAULT_SETS
        except ValueError:
            sets = DEFAULT_SETS
        note = it.get("notes") or ""
        if it.get("swap_for"):
            note = (note + " · " if note else "") + f"At home: {it['swap_for']}"
        role = "secondary" if (it.get("role") or "") in ("accessory", "secondary") else "main"
        items.append(
            make_item(
                doc,
                name=it.get("name") or "",
                sets=sets,
                reps=str(it.get("reps") or ""),
                rest=str(it.get("rest") or "") or None,
                role=role,
                note=note,
            )
        )
    return items[:MAX_ITEMS]


def items_from_exercise(doc: dict, level: str) -> List[dict]:
    sets, reps = parse_scheme(sb.scheme_for(doc, sb.norm_level(level), "main"))
    return [make_item(doc, sets=sets, reps=reps, role="main")]


def new_session(
    viewer_id: str, title: str, source: dict, items: List[dict], now: dt.datetime, local_date: str
) -> dict:
    return {
        "viewer_id": viewer_id,
        "status": "active",
        "title": title[:80] or "Workout",
        "source": source,
        "items": items,
        "started_at": now,
        "updated_at": now,
        "local_date": local_date,
    }


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
NUMBER_RE = re.compile(r"^\d+(?:[.,]\d+)?$")


def _number(raw: Optional[str]) -> Optional[float]:
    text = (raw or "").strip()
    if not text or not NUMBER_RE.match(text):
        return None
    return float(text.replace(",", "."))


def clean_row(kind: str, form: Dict[str, str], units: str) -> Optional[dict]:
    """The values typed for one set -> a done row, or None if nothing usable was typed.
    Blank fields fall back to the placeholder values the page sends (`ph_*`)."""

    def value(name: str) -> Optional[float]:
        got = _number(form.get(name))
        return got if got is not None else _number(form.get("ph_" + name))

    row: dict = {"done": True}
    if kind == "minutes":
        minutes = value("minutes")
        if minutes is None or minutes <= 0 or minutes > LIMITS["minutes"]:
            return None
        row["minutes"] = round(minutes, 1)
        return row
    if kind == "seconds":
        seconds = value("seconds")
        if seconds is None or seconds <= 0 or seconds > LIMITS["seconds"]:
            return None
        row["seconds"] = int(seconds)
        return row
    reps = value("reps")
    if reps is None or reps <= 0 or reps > LIMITS["reps"] or reps != int(reps):
        return None
    row["reps"] = int(reps)
    if kind == "weight_reps":
        weight = value("weight")
        if weight is not None:
            kg = to_kg(weight, units)
            if kg > LIMITS["weight_kg"]:
                return None
            row["weight_kg"] = round(kg, 3)
    return row


def show_row(row: dict, kind: str, units: str) -> str:
    """A done set for history: '60 kg × 8', '× 12', '45 s', '20 min'."""
    if kind == "minutes":
        return f"{show_number(row.get('minutes'))} min"
    if kind == "seconds":
        return f"{row.get('seconds')} s"
    reps = row.get("reps")
    if kind == "weight_reps" and row.get("weight_kg") is not None:
        return f"{show_weight(row['weight_kg'], units)} {unit_label(units)} × {reps}"
    return f"× {reps}"


def done_rows(item: dict) -> List[dict]:
    return [r for r in item.get("rows") or [] if r.get("done")]


def item_progress(item: dict) -> Tuple[int, int]:
    rows = item.get("rows") or []
    return len([r for r in rows if r.get("done")]), len(rows)


def current_index(items: Sequence[dict]) -> int:
    """The first exercise with a set still to do (the last one when all are done)."""
    for i, item in enumerate(items):
        done, total = item_progress(item)
        if done < total:
            return i
    return max(0, len(items) - 1)


# -----------------------------------------------------------------------------
# Last time, placeholders
# -----------------------------------------------------------------------------
def last_rows_by_slug(sessions: Iterable[dict], slugs: Iterable[str]) -> Dict[str, List[dict]]:
    """For each slug, the done sets from the most recent workout that logged it.
    `sessions` must come newest first."""
    wanted = {s for s in slugs if s}
    out: Dict[str, List[dict]] = {}
    for sess in sessions:
        for item in sess.get("items") or []:
            slug = item.get("slug")
            if slug in wanted and slug not in out:
                rows = done_rows(item)
                if rows:
                    out[slug] = rows
        if len(out) == len(wanted):
            break
    return out


def placeholders(item: dict, last: Optional[List[dict]], units: str) -> List[dict]:
    """Per row: what to show greyed in each field (last time's numbers, else the target)."""
    target = (item.get("reps") or "").replace(" each side", "")
    out = []
    for i, _ in enumerate(item.get("rows") or []):
        prev = (last[i] if i < len(last) else last[-1]) if last else None
        if prev:
            out.append(
                {
                    "weight": show_weight(prev.get("weight_kg"), units),
                    "reps": str(prev.get("reps") or ""),
                    "seconds": str(prev.get("seconds") or ""),
                    "minutes": show_number(prev.get("minutes")),
                    "from_history": True,
                }
            )
        else:
            out.append(
                {"weight": "", "reps": "", "seconds": "", "minutes": "", "from_history": False}
            )
        out[-1]["target"] = target
    return out


def last_time_text(item: dict, last: Optional[List[dict]], units: str) -> str:
    """'135 lb × 10, 9, 8' when every set used the same weight, else each set in full."""
    if not last:
        return ""
    kind = item.get("kind") or "weight_reps"
    weights = {r.get("weight_kg") for r in last}
    if kind == "weight_reps" and len(weights) == 1 and None not in weights and len(last) > 1:
        reps = ", ".join(str(r.get("reps")) for r in last)
        return f"{show_weight(last[0]['weight_kg'], units)} {unit_label(units)} × {reps}"
    return ", ".join(show_row(r, kind, units) for r in last)


# -----------------------------------------------------------------------------
# Finishing, history
# -----------------------------------------------------------------------------
def last_activity(session: dict) -> Optional[dt.datetime]:
    times = [session.get("updated_at"), session.get("started_at")]
    for item in session.get("items") or []:
        times += [r.get("at") for r in item.get("rows") or [] if r.get("at")]
    times = [t for t in times if t]
    return max(times) if times else None


def is_stale(session: dict, now: dt.datetime) -> bool:
    last = last_activity(session)
    return bool(last and now - last > STALE_AFTER)


def has_logged(session: dict) -> bool:
    return any(done_rows(item) for item in session.get("items") or [])


def finished_at_for_stale(session: dict) -> Optional[dt.datetime]:
    """A stale workout ends at its last logged set."""
    times = [
        r.get("at") for item in session.get("items") or [] for r in done_rows(item) if r.get("at")
    ]
    return max(times) if times else last_activity(session)


def summary(session: dict) -> dict:
    sets = exercises = reps = 0
    volume = 0.0
    for item in session.get("items") or []:
        rows = done_rows(item)
        if rows:
            exercises += 1
        sets += len(rows)
        for r in rows:
            reps += int(r.get("reps") or 0)
            if r.get("weight_kg") is not None and r.get("reps"):
                volume += float(r["weight_kg"]) * int(r["reps"])
    start = session.get("started_at")
    end = session.get("finished_at") or last_activity(session)
    minutes = int(round((end - start).total_seconds() / 60)) if start and end else 0
    return {
        "sets": sets,
        "exercises": exercises,
        "reps": reps,
        "volume_kg": round(volume, 1),
        "minutes": max(0, minutes),
    }


def show_volume(kg: float, units: str) -> str:
    value = from_kg(kg, units)
    return f"{value:,.0f} {unit_label(units)}"


def show_duration(minutes: int) -> str:
    if minutes < 1:
        return "<1 min"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"
