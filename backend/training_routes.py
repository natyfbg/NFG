"""Session mode routes: start a workout, log it set by set, finish it, look back.

The rules live in `training.py` (pure). This file loads and saves `training_sessions`
and renders the pages. It knows nothing about programs, sessions or the Mixer: app.py
passes `build_source` (turns a Start form into a title, a source and items) and
`complete_program_day` (marks the program day a workout came from as done).

Routes (all per viewer: a guest's device or a member's account):
    POST /train/start              start from a program day, session, mix or exercise
    GET  /train/session            the workout in progress (the logger)
    POST /train/set                log or undo one set (JSON for the page's script)
    POST /train/rows               add or remove a set row
    POST /train/units              lb / kg
    POST /train/finish             finish → summary
    POST /train/discard            throw the workout in progress away
    GET  /train                    My workouts: in progress + history
    GET  /train/history/<id>       one finished workout
    POST /train/history/<id>/delete
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Callable, Dict, Iterable, List, Optional

import nutrition
import training as tr
from bson import ObjectId
from bson.errors import InvalidId
from flask import abort, flash, jsonify, redirect, render_template, request, url_for

log = logging.getLogger(__name__)

UNITS_COOKIE = "nfg_units"
HISTORY_PAGE = 30
LAST_TIME_LOOKBACK = 20

ERRORS = {
    "weight_reps": "Enter your reps first.",
    "reps": "Enter your reps first.",
    "seconds": "Enter the seconds first.",
    "minutes": "Enter the minutes first.",
}


def utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


def ensure_indexes(db) -> None:
    try:
        db.training_sessions.create_index([("viewer_id", 1), ("status", 1)])
        db.training_sessions.create_index([("viewer_id", 1), ("finished_at", -1)])
        db.training_sessions.create_index(
            [("viewer_id", 1), ("items.slug", 1), ("finished_at", -1)]
        )
    except Exception as e:  # pragma: no cover - index creation is best effort
        log.warning("training indexes: %s", e)


# -----------------------------------------------------------------------------
# The workout in progress (also used by Home and the guest → member move)
# -----------------------------------------------------------------------------
def _close(db, doc: dict) -> None:
    """End a workout that is being left behind: keep it if a set was logged."""
    if tr.has_logged(doc):
        db.training_sessions.update_one(
            {"_id": doc["_id"], "status": "active"},
            {"$set": {"status": "done", "finished_at": tr.finished_at_for_stale(doc)}},
        )
    else:
        db.training_sessions.delete_one({"_id": doc["_id"], "status": "active"})


def get_active(db, owner: str, now: Optional[dt.datetime] = None) -> Optional[dict]:
    """The viewer's workout in progress. One untouched for 6 hours closes itself here."""
    if not owner:
        return None
    doc = db.training_sessions.find_one(
        {"viewer_id": owner, "status": "active"}, sort=[("started_at", -1)]
    )
    if doc and tr.is_stale(doc, now or utcnow()):
        _close(db, doc)
        return None
    return doc


def sets_progress(items: Iterable[dict]) -> Dict[str, int]:
    done = total = 0
    for item in items:
        d, t = tr.item_progress(item)
        done += d
        total += t
    return {"done": done, "total": total}


def active_card(db, owner: str) -> Optional[dict]:
    """What Home's "Workout in progress" card needs (None when nothing is in progress)."""
    doc = get_active(db, owner)
    if not doc:
        return None
    return {
        "title": doc.get("title") or "Workout",
        "sets": sets_progress(doc.get("items") or []),
        "exercises": len(doc.get("items") or []),
        "source": doc.get("source") or {},
    }


def active_source(db, owner: str) -> Optional[dict]:
    """The source of the workout in progress, so a page can say "Resume" instead of Start."""
    doc = get_active(db, owner)
    return (doc or {}).get("source") if doc else None


def migrate_guest(db, guest: str, member: str) -> None:
    """Guest → member on sign-in: workouts move over. If both have one in progress, the
    member's stays and the guest's is closed (kept in history if it has sets)."""
    if not guest or guest == member:
        return
    if db.training_sessions.find_one({"viewer_id": member, "status": "active"}):
        for doc in list(db.training_sessions.find({"viewer_id": guest, "status": "active"})):
            _close(db, doc)
    db.training_sessions.update_many({"viewer_id": guest}, {"$set": {"viewer_id": member}})


def _local_date_label(value: str) -> str:
    try:
        day = dt.date.fromisoformat(value or "")
    except ValueError:
        return ""
    return day.strftime("%a %d %b").replace(" 0", " ")


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
def register(
    app,
    *,
    db,
    owner_key: Callable[[], str],
    build_source: Callable,
    complete_program_day: Callable[[str, dict], None],
    exercise_lookup: Callable[[List[str]], Dict[str, dict]],
    is_safe_path: Callable[[str], bool],
    muscle_groups: Dict[str, dict],
) -> None:
    ensure_indexes(db)

    def units() -> str:
        chosen = request.cookies.get(UNITS_COOKIE)
        if chosen in tr.UNITS:
            return chosen
        key = owner_key()
        profile = db.macro_profiles.find_one({"viewer_id": key}, {"units": 1}) if key else None
        return tr.norm_units((profile or {}).get("units"))

    def wants_json() -> bool:
        return "application/json" in (request.headers.get("Accept") or "")

    def back_to(default: str) -> str:
        nxt = (request.form.get("next") or "").strip()
        return nxt if nxt and is_safe_path(nxt) else default

    def source_url(doc: dict, default: str) -> str:
        url = ((doc or {}).get("source") or {}).get("url") or ""
        return url if url and is_safe_path(url) else default

    def item_index(doc: dict, key: str) -> Optional[int]:
        for i, item in enumerate(doc.get("items") or []):
            if item.get("key") == key:
                return i
        return None

    def color_for(group: str) -> str:
        return (muscle_groups.get(group) or {}).get("color") or "var(--ink-3, #8a8a80)"

    def fail(message: str, status: int = 400):
        if wants_json():
            return jsonify({"ok": False, "error": message}), status
        flash(message, "warning")
        return redirect(url_for("train_session"))

    # ---- start ---------------------------------------------------------------
    def train_start():
        owner = owner_key()
        if not owner:
            flash("Couldn't start a workout. Please try again.", "warning")
            return redirect(back_to(url_for("train_index")))
        built = build_source(request.form)
        if not built or not built[2]:
            flash("That workout has no exercises to log yet.", "warning")
            return redirect(back_to(url_for("train_index")))
        title, source, items = built
        now = utcnow()
        active = get_active(db, owner, now)
        if active:
            same = (active.get("source") or {}).get("key") == source.get("key")
            if same and request.form.get("replace") != "1":
                return redirect(url_for("train_session"))
            if request.form.get("replace") != "1":
                resend = [
                    (k, v)
                    for k, v in request.form.items(multi=True)
                    if k not in ("csrf_token", "replace")
                ]
                return render_template(
                    "train_conflict.html",
                    active=active,
                    active_sets=sets_progress(active.get("items") or []),
                    active_logged=tr.has_logged(active),
                    new_title=title,
                    resend=resend,
                )
            _close(db, active)
        doc = tr.new_session(owner, title, source, items, now, nutrition.local_today().isoformat())
        db.training_sessions.insert_one(doc)
        return redirect(url_for("train_session"))

    # ---- the logger ----------------------------------------------------------
    def train_session():
        owner = owner_key()
        doc = get_active(db, owner)
        if not doc:
            return redirect(url_for("train_index"))
        u = units()
        items = doc.get("items") or []
        slugs = [it.get("slug") for it in items if it.get("slug")]
        history = (
            db.training_sessions.find(
                {"viewer_id": owner, "status": "done", "items.slug": {"$in": slugs}},
                {"items.slug": 1, "items.rows": 1},
            )
            .sort("finished_at", -1)
            .limit(LAST_TIME_LOOKBACK)
            if slugs
            else []
        )
        last = tr.last_rows_by_slug(history, slugs)
        docs = exercise_lookup(slugs)
        view = []
        for item in items:
            prev = last.get(item.get("slug"))
            done, total = tr.item_progress(item)
            view.append(
                dict(
                    item,
                    doc=docs.get(item.get("slug")),
                    ph=tr.placeholders(item, prev, u),
                    last_text=tr.last_time_text(item, prev, u),
                    done=done,
                    total=total,
                    color=color_for(item.get("group") or ""),
                    shown=[
                        {
                            "weight": tr.show_weight(r.get("weight_kg"), u),
                            "reps": r.get("reps") or "",
                            "seconds": r.get("seconds") or "",
                            "minutes": tr.show_number(r.get("minutes")),
                        }
                        for r in item.get("rows") or []
                    ],
                )
            )
        return render_template(
            "train_session.html",
            workout=doc,
            items=view,
            current=tr.current_index(items),
            progress=sets_progress(items),
            units=u,
            unit=tr.unit_label(u),
            started_iso=doc["started_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            exit_url=source_url(doc, url_for("train_index")),
        )

    def train_set():
        owner = owner_key()
        doc = get_active(db, owner)
        if not doc:
            return fail("This workout has already ended.", 409)
        idx = item_index(doc, request.form.get("key") or "")
        if idx is None:
            return fail("That exercise isn't in this workout.")
        item = doc["items"][idx]
        try:
            r = int(request.form.get("row") or "")
        except ValueError:
            r = -1
        rows = item.get("rows") or []
        if not 0 <= r < len(rows):
            return fail("That set isn't in this workout.")
        now = utcnow()
        u = units()
        if request.form.get("action") == "undo":
            new = {"done": False}
        else:
            new = tr.clean_row(item.get("kind") or "weight_reps", request.form, u)
            if new is None:
                return fail(ERRORS.get(item.get("kind"), ERRORS["reps"]))
            new["at"] = now
        db.training_sessions.update_one(
            {"_id": doc["_id"], "status": "active"},
            {"$set": {f"items.{idx}.rows.{r}": new, "updated_at": now}},
        )
        rows[r] = new
        if not wants_json():
            return redirect(url_for("train_session") + f"#ex-{item['key']}")
        done, total = tr.item_progress(item)
        return jsonify(
            {
                "ok": True,
                "done": bool(new.get("done")),
                "shown": {
                    "weight": tr.show_weight(new.get("weight_kg"), u),
                    "reps": new.get("reps") or "",
                    "seconds": new.get("seconds") or "",
                    "minutes": tr.show_number(new.get("minutes")),
                },
                "item": {"done": done, "total": total},
                "sets": sets_progress(doc["items"]),
                "rest": int(item.get("rest_seconds") or 0) if new.get("done") else 0,
            }
        )

    def train_rows():
        owner = owner_key()
        doc = get_active(db, owner)
        if not doc:
            return redirect(url_for("train_index"))
        idx = item_index(doc, request.form.get("key") or "")
        if idx is None:
            return redirect(url_for("train_session"))
        item = doc["items"][idx]
        rows = item.get("rows") or []
        now = utcnow()
        if request.form.get("action") == "remove":
            if len(rows) > 1 and not rows[-1].get("done"):
                db.training_sessions.update_one(
                    {"_id": doc["_id"]},
                    {"$pop": {f"items.{idx}.rows": 1}, "$set": {"updated_at": now}},
                )
        elif len(rows) < tr.MAX_ROWS:
            db.training_sessions.update_one(
                {"_id": doc["_id"]},
                {"$push": {f"items.{idx}.rows": {"done": False}}, "$set": {"updated_at": now}},
            )
        return redirect(url_for("train_session") + f"#ex-{item['key']}")

    def train_units():
        chosen = tr.norm_units(request.form.get("units"))
        resp = redirect(back_to(url_for("train_session")))
        resp.set_cookie(
            UNITS_COOKIE, chosen, max_age=60 * 60 * 24 * 365, samesite="Lax", httponly=True
        )
        return resp

    def train_finish():
        owner = owner_key()
        doc = get_active(db, owner)
        if not doc:
            return redirect(url_for("train_index"))
        if not tr.has_logged(doc):
            db.training_sessions.delete_one({"_id": doc["_id"]})
            flash("Nothing was logged, so there was nothing to save.", "info")
            return redirect(source_url(doc, url_for("train_index")))
        now = utcnow()
        db.training_sessions.update_one(
            {"_id": doc["_id"], "status": "active"},
            {"$set": {"status": "done", "finished_at": now, "updated_at": now}},
        )
        source = doc.get("source") or {}
        if source.get("kind") == "program":
            try:
                complete_program_day(owner, source)
            except Exception as e:  # the workout is saved either way
                log.warning("program day completion failed: %s", type(e).__name__)
        return redirect(url_for("train_history_detail", session_id=str(doc["_id"]), done=1))

    def train_discard():
        owner = owner_key()
        doc = get_active(db, owner)
        if doc:
            db.training_sessions.delete_one({"_id": doc["_id"], "status": "active"})
            flash("Workout discarded.", "info")
            return redirect(source_url(doc, url_for("train_index")))
        return redirect(url_for("train_index"))

    # ---- My workouts, history -------------------------------------------------
    def entry(doc: dict, u: str) -> dict:
        s = tr.summary(doc)
        return {
            "id": str(doc["_id"]),
            "title": doc.get("title") or "Workout",
            "date": _local_date_label(doc.get("local_date") or ""),
            "month": (doc.get("local_date") or "")[:7],
            "summary": s,
            "duration": tr.show_duration(s["minutes"]),
            "volume": tr.show_volume(s["volume_kg"], u) if s["volume_kg"] else "",
            "colors": [
                color_for(it.get("group") or "")
                for it in doc.get("items") or []
                if tr.done_rows(it)
            ][:8],
        }

    def train_index():
        owner = owner_key()
        u = units()
        active = get_active(db, owner)
        query: dict = {"viewer_id": owner, "status": "done"}
        before = request.args.get("before") or ""
        if before:
            try:
                query["finished_at"] = {"$lt": dt.datetime.fromisoformat(before)}
            except ValueError:
                before = ""
        docs = (
            list(db.training_sessions.find(query).sort("finished_at", -1).limit(HISTORY_PAGE + 1))
            if owner
            else []
        )
        more = len(docs) > HISTORY_PAGE
        docs = docs[:HISTORY_PAGE]
        months: List[dict] = []
        for doc in docs:
            e = entry(doc, u)
            label = ""
            try:
                label = dt.date.fromisoformat(e["month"] + "-01").strftime("%B %Y")
            except ValueError:
                pass
            if not months or months[-1]["label"] != label:
                months.append({"label": label, "entries": []})
            months[-1]["entries"].append(e)
        older = docs[-1]["finished_at"].isoformat() if more and docs else ""
        return render_template(
            "train_index.html",
            active=active,
            active_sets=sets_progress((active or {}).get("items") or []),
            months=months,
            older=older,
        )

    def find_done(session_id: str) -> dict:
        try:
            oid = ObjectId(session_id)
        except (InvalidId, TypeError):
            abort(404)
        doc = db.training_sessions.find_one(
            {"_id": oid, "viewer_id": owner_key(), "status": "done"}
        )
        if not doc:
            abort(404)
        return doc

    def train_history_detail(session_id: str):
        doc = find_done(session_id)
        u = units()
        docs = exercise_lookup([it.get("slug") for it in doc.get("items") or [] if it.get("slug")])
        done_items, skipped = [], []
        for item in doc.get("items") or []:
            rows = tr.done_rows(item)
            view = dict(
                item,
                doc=docs.get(item.get("slug")),
                color=color_for(item.get("group") or ""),
                sets=[tr.show_row(r, item.get("kind") or "weight_reps", u) for r in rows],
            )
            (done_items if rows else skipped).append(view)
        return render_template(
            "train_history_detail.html",
            workout=doc,
            info=entry(doc, u),
            items=done_items,
            skipped=skipped,
            just_done=request.args.get("done") == "1",
            source=doc.get("source") or {},
            source_href=source_url(doc, ""),
        )

    def train_history_delete(session_id: str):
        doc = find_done(session_id)
        db.training_sessions.delete_one({"_id": doc["_id"]})
        flash("Workout deleted.", "info")
        return redirect(url_for("train_index"))

    app.add_url_rule("/train/start", "train_start", train_start, methods=["POST"])
    app.add_url_rule("/train/session", "train_session", train_session)
    app.add_url_rule("/train/set", "train_set", train_set, methods=["POST"])
    app.add_url_rule("/train/rows", "train_rows", train_rows, methods=["POST"])
    app.add_url_rule("/train/units", "train_units", train_units, methods=["POST"])
    app.add_url_rule("/train/finish", "train_finish", train_finish, methods=["POST"])
    app.add_url_rule("/train/discard", "train_discard", train_discard, methods=["POST"])
    app.add_url_rule("/train", "train_index", train_index)
    app.add_url_rule("/train/history/<session_id>", "train_history_detail", train_history_detail)
    app.add_url_rule(
        "/train/history/<session_id>/delete",
        "train_history_delete",
        train_history_delete,
        methods=["POST"],
    )
