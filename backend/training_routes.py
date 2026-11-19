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
    GET  /train                    My workouts: in progress, saved workouts, history, saved
    GET  /train/history/<id>       one finished workout
    POST /train/history/<id>/delete
    POST /train/save               save a finished workout, the one in progress, or a
                                   session / Mixer build as a saved workout
    POST /train/workouts/new       a new, empty saved workout
    GET  /train/workouts/<id>      a saved workout: rename, reorder, edit sets/reps/rest
    POST /train/workouts/<id>/edit
    POST /train/workouts/<id>/delete
    GET  /train/pick               find an exercise to add (or to swap in)
    GET  /train/add/<slug>         "Add to workout" from an exercise page
    POST /train/add                add / swap an exercise
    POST /train/remove-item        drop an exercise from the workout in progress
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
        db.saved_workouts.create_index([("viewer_id", 1), ("updated_at", -1)])
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
    db.saved_workouts.update_many({"viewer_id": guest}, {"$set": {"viewer_id": member}})


def _local_date_label(value: str) -> str:
    try:
        day = dt.date.fromisoformat(value or "")
    except ValueError:
        return ""
    return day.strftime("%a %d %b").replace(" 0", " ")


def viewer_units(db, owner: str) -> str:
    """lb or kg: the `nfg_units` cookie, else the macros profile's units, else lb."""
    chosen = request.cookies.get(UNITS_COOKIE)
    if chosen in tr.UNITS:
        return chosen
    profile = db.macro_profiles.find_one({"viewer_id": owner}, {"units": 1}) if owner else None
    return tr.norm_units((profile or {}).get("units"))


def exercise_history(db, owner: str, slug: str) -> Optional[dict]:
    """The viewer's history with one exercise for its page (None if never logged):
    last time, best set, the chart and the last workouts. One indexed query."""
    if not owner or not slug:
        return None
    sessions = (
        db.training_sessions.find(
            {"viewer_id": owner, "status": "done", "items.slug": slug},
            {"items": 1, "local_date": 1, "finished_at": 1},
        )
        .sort("finished_at", -1)
        .limit(40)
    )
    units = viewer_units(db, owner)
    summary = tr.exercise_summary(tr.exercise_log(sessions, slug), units)
    if summary and len(summary["values"]) >= 2:
        unit = summary["unit"]

        def fmt(v: float) -> str:
            return tr.show_number(v)

        summary["chart"] = nutrition.line_chart([summary["values"]], summary["labels"], fmt)
        summary["chart_suffix"] = f" {unit}"
    return summary


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
    environments: Optional[Dict[str, dict]] = None,
    library_for_env: Optional[Callable[[str], List[dict]]] = None,
    current_env: Optional[Callable[[], str]] = None,
    current_level: Optional[Callable[[], str]] = None,
    saved_items: Optional[Callable[[str], dict]] = None,
) -> None:
    ensure_indexes(db)
    environments = environments or {}
    library_for_env = library_for_env or (lambda env: [])
    current_env = current_env or (lambda: "gym")
    current_level = current_level or (lambda: tr.sb.DEFAULT_LEVEL)
    saved_items = saved_items or (lambda owner: {"exercises": [], "programs": []})

    def units() -> str:
        return viewer_units(db, owner_key())

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

    def find_saved(workout_id: str, owner: str) -> Optional[dict]:
        try:
            oid = ObjectId(workout_id)
        except (InvalidId, TypeError):
            return None
        return db.saved_workouts.find_one({"_id": oid, "viewer_id": owner}) if owner else None

    def own_source(form):
        """Start forms this module answers itself: a saved workout, or an empty one."""
        kind = (form.get("from") or "").strip()
        if kind == "empty":
            return "Workout", {"kind": "empty", "key": "", "label": "My workouts"}, []
        if kind == "saved":
            saved = find_saved(form.get("id") or "", owner_key())
            if not saved:
                return None
            slugs = [s.get("slug") for s in saved.get("items") or [] if s.get("slug")]
            items = tr.items_from_saved(saved, exercise_lookup(slugs))
            wid = str(saved["_id"])
            source = {
                "kind": "saved",
                "key": f"saved:{wid}",
                "label": "My workouts",
                "url": url_for("train_workout", workout_id=wid),
                "id": wid,
            }
            return saved.get("title") or "Workout", source, items
        return None

    def saved_count(owner: str) -> int:
        return db.saved_workouts.count_documents({"viewer_id": owner}) if owner else 0

    def default_item(doc: dict) -> dict:
        sets, reps = tr.parse_scheme(
            tr.sb.scheme_for(doc, tr.sb.norm_level(current_level()), "main")
        )
        return tr.make_item(doc, sets=sets, reps=reps, role="main")

    # ---- start ---------------------------------------------------------------
    def train_start():
        owner = owner_key()
        if not owner:
            flash("Couldn't start a workout. Please try again.", "warning")
            return redirect(back_to(url_for("train_index")))
        kind = (request.form.get("from") or "").strip()
        built = (
            own_source(request.form) if kind in ("saved", "empty") else build_source(request.form)
        )
        if not built or (not built[2] and kind != "empty"):
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
        slugs = [it.get("slug") for it in doc.get("items") or [] if it.get("slug")]
        previous = (
            db.training_sessions.find(
                {
                    "viewer_id": owner,
                    "status": "done",
                    "items.slug": {"$in": slugs},
                    "_id": {"$ne": doc["_id"]},
                },
                {"items.slug": 1, "items.rows": 1},
            ).limit(400)
            if slugs
            else []
        )
        prs = tr.personal_bests(doc.get("items") or [], previous)
        db.training_sessions.update_one(
            {"_id": doc["_id"], "status": "active"},
            {"$set": {"status": "done", "finished_at": now, "updated_at": now, "prs": prs}},
        )
        source = doc.get("source") or {}
        if source.get("kind") == "saved" and source.get("id"):
            saved = find_saved(source["id"], owner)
            if saved:
                db.saved_workouts.update_one({"_id": saved["_id"]}, {"$set": {"last_done_at": now}})
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

    def saved_card(doc: dict) -> dict:
        items = doc.get("items") or []
        last = doc.get("last_done_at")
        return {
            "id": str(doc["_id"]),
            "title": doc.get("title") or "My workout",
            "count": len(items),
            "minutes": tr.estimate_minutes(items),
            "last": last.strftime("%a %d %b").replace(" 0", " ") if last else "",
            "colors": [color_for(it.get("group") or "") for it in items][:8],
        }

    def history_months(owner: str, u: str, before: str = "", limit: int = HISTORY_PAGE):
        query: dict = {"viewer_id": owner, "status": "done"}
        if before:
            try:
                query["finished_at"] = {"$lt": dt.datetime.fromisoformat(before)}
            except ValueError:
                pass
        docs = (
            list(db.training_sessions.find(query).sort("finished_at", -1).limit(limit + 1))
            if owner
            else []
        )
        more = len(docs) > limit
        docs = docs[:limit]
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
        return months, older, more

    def train_index():
        owner = owner_key()
        u = units()
        tab = (
            request.args.get("tab")
            if request.args.get("tab") in ("history", "saved")
            else "workouts"
        )
        active = get_active(db, owner)
        context = {
            "tab": tab,
            "tabs": [
                ("workouts", "Workouts", url_for("train_index")),
                ("history", "History", url_for("train_index", tab="history")),
                ("saved", "Saved", url_for("train_index", tab="saved")),
            ],
            "active": active,
            "active_sets": sets_progress((active or {}).get("items") or []),
        }
        if tab == "workouts":
            saved = (
                list(db.saved_workouts.find({"viewer_id": owner}).sort("updated_at", -1))
                if owner
                else []
            )
            recent, _, more = history_months(owner, u, limit=3)
            context.update(
                saved=[saved_card(d) for d in saved],
                recent=[e for m in recent for e in m["entries"]],
                more_history=more,
                can_add=len(saved) < tr.MAX_SAVED,
            )
        elif tab == "history":
            months, older, _ = history_months(owner, u, request.args.get("before") or "")
            context.update(months=months, older=older)
        else:
            context.update(saved_items(owner))
        return render_template("train_index.html", **context)

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
        prs = doc.get("prs") or []
        pr_slugs = {p.get("slug") for p in prs}
        for view in done_items:
            view["is_pr"] = view.get("slug") in pr_slugs
        return render_template(
            "train_history_detail.html",
            workout=doc,
            prs=[tr.pr_text(p, u) for p in prs],
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

    # ---- saved workouts ---------------------------------------------------------
    def train_save():
        owner = owner_key()
        what = request.form.get("what") or ""
        now = utcnow()
        if saved_count(owner) >= tr.MAX_SAVED:
            flash(f"You have {tr.MAX_SAVED} saved workouts. Delete one to save another.", "warning")
            return redirect(back_to(url_for("train_index")))
        title, items = "", []
        if what == "current":
            doc = get_active(db, owner)
            if doc:
                title, items = doc.get("title") or "", tr.saved_items_from_session(doc)
        elif what == "source":
            built = build_source(request.form)
            if built:
                title, items = built[0], [tr.saved_item_from(it) for it in built[2]]
        else:
            try:
                oid = ObjectId(what)
            except (InvalidId, TypeError):
                oid = None
            doc = (
                db.training_sessions.find_one({"_id": oid, "viewer_id": owner, "status": "done"})
                if oid and owner
                else None
            )
            if doc:
                title, items = doc.get("title") or "", tr.saved_items_from_session(doc)
        if not items or not owner:
            flash("There was nothing to save.", "warning")
            return redirect(back_to(url_for("train_index")))
        res = db.saved_workouts.insert_one(tr.new_saved(owner, title, items, now))
        flash("Saved to My workouts. Rename it or change the sets here.", "success")
        return redirect(url_for("train_workout", workout_id=str(res.inserted_id)))

    def train_workout_new():
        owner = owner_key()
        if not owner or saved_count(owner) >= tr.MAX_SAVED:
            flash(f"You have {tr.MAX_SAVED} saved workouts. Delete one to make another.", "warning")
            return redirect(url_for("train_index"))
        res = db.saved_workouts.insert_one(tr.new_saved(owner, "New workout", [], utcnow()))
        return redirect(url_for("train_workout", workout_id=str(res.inserted_id)))

    def train_workout(workout_id: str):
        saved = find_saved(workout_id, owner_key())
        if not saved:
            abort(404)
        items = saved.get("items") or []
        docs = exercise_lookup([it.get("slug") for it in items if it.get("slug")])
        view = [
            dict(it, doc=docs.get(it.get("slug")), color=color_for(it.get("group") or ""))
            for it in items
        ]
        wid = str(saved["_id"])
        return render_template(
            "train_workout.html",
            saved=saved,
            wid=wid,
            items=view,
            minutes=tr.estimate_minutes(items),
            rest_options=tr.REST_OPTIONS,
            max_items=tr.MAX_ITEMS,
            train_key=f"saved:{wid}",
            train_fields=[("from", "saved"), ("id", wid)],
        )

    def train_workout_edit(workout_id: str):
        saved = find_saved(workout_id, owner_key())
        if not saved:
            abort(404)
        action = request.form.get("action") or ""
        key = request.form.get("key") or ""
        items = list(saved.get("items") or [])
        title = saved.get("title") or "My workout"
        if action == "rename":
            title = tr.clean_title(request.form.get("title")) or title
        elif action in ("up", "down"):
            items = tr.move(items, key, -1 if action == "up" else 1)
        elif action == "remove":
            items = [it for it in items if it.get("key") != key]
        elif action == "item":
            for it in items:
                if it.get("key") == key:
                    it["sets"] = tr.clean_sets(
                        request.form.get("sets"), it.get("sets") or tr.DEFAULT_SETS
                    )
                    it["reps"] = tr.clean_reps(request.form.get("reps"))
                    it["rest"] = tr.clean_rest(request.form.get("rest"))
        db.saved_workouts.update_one(
            {"_id": saved["_id"]},
            {"$set": {"title": title, "items": items, "updated_at": utcnow()}},
        )
        anchor = f"#it-{key}" if key and action != "remove" else ""
        return redirect(url_for("train_workout", workout_id=workout_id) + anchor)

    def train_workout_delete(workout_id: str):
        saved = find_saved(workout_id, owner_key())
        if not saved:
            abort(404)
        db.saved_workouts.delete_one({"_id": saved["_id"]})
        flash("Workout deleted.", "info")
        return redirect(url_for("train_index"))

    # ---- finding an exercise to add or swap in -----------------------------------
    def search(pool: List[dict], q: str) -> List[dict]:
        words = [w for w in q.lower().split() if w]
        scored = []
        for doc in pool:
            name = (doc.get("name") or "").lower()
            hay = " ".join(
                [name, (doc.get("equipment_label") or ""), " ".join(doc.get("muscle_groups") or [])]
            ).lower()
            if not all(w in hay for w in words):
                continue
            first = words[0]
            rank = 0 if name.startswith(first) else (1 if f" {first}" in f" {name}" else 2)
            scored.append((rank, doc.get("name") or "", doc))
        scored.sort(key=lambda s: (s[0], s[1]))
        return [s[2] for s in scored]

    def recent_slugs(owner: str, limit: int = 12) -> List[str]:
        out: List[str] = []
        if not owner:
            return out
        for doc in (
            db.training_sessions.find({"viewer_id": owner, "status": "done"}, {"items.slug": 1})
            .sort("finished_at", -1)
            .limit(10)
        ):
            for it in doc.get("items") or []:
                s = it.get("slug")
                if s and s not in out:
                    out.append(s)
        return out[:limit]

    def train_pick():
        owner = owner_key()
        to = request.args.get("to") or "session"
        swap = request.args.get("swap") or ""
        q = " ".join((request.args.get("q") or "").split())[:60]
        group = request.args.get("g") if request.args.get("g") in muscle_groups else ""
        active = get_active(db, owner) if to == "session" else None
        saved = None if to == "session" else find_saved(to, owner)
        if to == "session" and not active:
            return redirect(url_for("train_index"))
        if to != "session" and not saved:
            abort(404)
        swap_item = None
        if active and swap:
            idx = item_index(active, swap)
            swap_item = active["items"][idx] if idx is not None else None
        env_arg = request.args.get("env")
        source_env = ((active or {}).get("source") or {}).get("env")
        env = env_arg if env_arg in environments else (source_env or current_env())
        pool = library_for_env(env)
        in_workout = {
            it.get("slug") for it in ((active or saved or {}).get("items") or []) if it.get("slug")
        }
        sections: List[dict] = []
        if q:
            sections.append({"title": "", "docs": search(pool, q)[:50]})
        elif group:
            docs = sorted(
                [d for d in pool if tr.primary_group(d) == group], key=lambda d: d.get("name") or ""
            )
            sections.append({"title": muscle_groups[group]["label"], "docs": docs[:60]})
        else:
            if swap_item and swap_item.get("slug"):
                old = exercise_lookup([swap_item["slug"]]).get(swap_item["slug"])
                alts = tr.alternatives(old, pool, in_workout, current_level())
                sections.append({"title": "Good swaps", "docs": alts})
            by_slug = {d.get("slug"): d for d in pool}
            recent = [by_slug[s] for s in recent_slugs(owner) if s in by_slug]
            if recent:
                sections.append({"title": "Logged recently", "docs": recent})
        shown = [d.get("slug") for s in sections for d in s["docs"]]
        docs = exercise_lookup(shown)
        for s in sections:
            s["docs"] = [docs[d.get("slug")] for d in s["docs"] if d.get("slug") in docs]
        if to == "session":
            back = url_for("train_session") + (f"#ex-{swap}" if swap else "")
        else:
            back = url_for("train_workout", workout_id=to)

        def link(**changes):
            args = {"to": to, "swap": swap or None, "q": q or None, "g": group or None, "env": env}
            args.update(changes)
            return url_for("train_pick", **{k: v for k, v in args.items() if v})

        return render_template(
            "train_pick.html",
            to=to,
            swap=swap,
            swap_item=swap_item,
            target_title=(active or saved or {}).get("title") or "",
            q=q,
            group=group,
            env=env,
            sections=sections,
            in_workout=in_workout,
            back=back,
            group_links=[
                (k, g["label"], g["color"], link(g=None if k == group else k, q=None))
                for k, g in muscle_groups.items()
            ],
            env_links=[(k, e["label"], link(env=k)) for k, e in environments.items()],
            clear_url=link(q=None, g=None),
        )

    def train_add_menu(slug: str):
        owner = owner_key()
        doc = exercise_lookup([slug]).get(slug)
        if not doc:
            abort(404)
        active = get_active(db, owner)
        saved = (
            list(db.saved_workouts.find({"viewer_id": owner}).sort("updated_at", -1))
            if owner
            else []
        )
        return render_template(
            "train_add.html",
            doc=doc,
            active=active,
            saved=[saved_card(d) for d in saved],
            can_add=len(saved) < tr.MAX_SAVED,
            back=url_for("workout_detail", slug=slug),
        )

    def train_add():
        owner = owner_key()
        slug = request.form.get("slug") or ""
        doc = exercise_lookup([slug]).get(slug)
        to = request.form.get("to") or ""
        if not doc or not owner:
            flash("That exercise couldn't be added.", "warning")
            return redirect(back_to(url_for("train_index")))
        now = utcnow()
        if to == "session":
            active = get_active(db, owner)
            if not active:
                flash("There's no workout in progress.", "warning")
                return redirect(back_to(url_for("train_index")))
            items = list(active.get("items") or [])
            swap = request.form.get("swap") or ""
            idx = item_index(active, swap) if swap else None
            if idx is not None:
                old = items[idx]
                new = default_item(doc)
                sets = len(old.get("rows") or []) or tr.DEFAULT_SETS
                reps = old.get("reps") if old.get("kind") == new["kind"] else new["reps"]
                new = tr.make_item(
                    doc,
                    sets=sets,
                    reps=reps or "",
                    rest=old.get("rest") or None,
                    role=old.get("role") or "main",
                )
                items = tr.swap_items(items, idx, new)
                flash(f"Swapped in {doc.get('name')}.", "success")
            else:
                if len(items) >= tr.MAX_ITEMS:
                    flash("This workout is full.", "warning")
                    return redirect(url_for("train_session"))
                new = default_item(doc)
                items.append(new)
            db.training_sessions.update_one(
                {"_id": active["_id"], "status": "active"},
                {"$set": {"items": items, "updated_at": now}},
            )
            return redirect(url_for("train_session") + f"#ex-{new['key']}")
        item = tr.saved_item_from(default_item(doc))
        if to == "new":
            if saved_count(owner) >= tr.MAX_SAVED:
                flash(
                    f"You have {tr.MAX_SAVED} saved workouts. Delete one to make another.",
                    "warning",
                )
                return redirect(back_to(url_for("train_index")))
            res = db.saved_workouts.insert_one(tr.new_saved(owner, "New workout", [item], now))
            flash(f"Added {doc.get('name')} to a new workout.", "success")
            return redirect(url_for("train_workout", workout_id=str(res.inserted_id)))
        saved = find_saved(to, owner)
        if not saved:
            abort(404)
        if len(saved.get("items") or []) >= tr.MAX_ITEMS:
            flash("That workout is full.", "warning")
        else:
            db.saved_workouts.update_one(
                {"_id": saved["_id"]},
                {"$push": {"items": item}, "$set": {"updated_at": now}},
            )
            flash(f"Added {doc.get('name')} to {saved.get('title') or 'your workout'}.", "success")
        return redirect(back_to(url_for("train_workout", workout_id=to)))

    def train_remove_item():
        owner = owner_key()
        active = get_active(db, owner)
        if not active:
            return redirect(url_for("train_index"))
        key = request.form.get("key") or ""
        idx = item_index(active, key)
        if idx is not None and not tr.done_rows(active["items"][idx]):
            items = [it for i, it in enumerate(active["items"]) if i != idx]
            db.training_sessions.update_one(
                {"_id": active["_id"], "status": "active"},
                {"$set": {"items": items, "updated_at": utcnow()}},
            )
        return redirect(url_for("train_session"))

    app.add_url_rule("/train/start", "train_start", train_start, methods=["POST"])
    app.add_url_rule("/train/session", "train_session", train_session)
    app.add_url_rule("/train/set", "train_set", train_set, methods=["POST"])
    app.add_url_rule("/train/rows", "train_rows", train_rows, methods=["POST"])
    app.add_url_rule("/train/units", "train_units", train_units, methods=["POST"])
    app.add_url_rule("/train/finish", "train_finish", train_finish, methods=["POST"])
    app.add_url_rule("/train/discard", "train_discard", train_discard, methods=["POST"])
    app.add_url_rule("/train", "train_index", train_index)
    app.add_url_rule("/train/save", "train_save", train_save, methods=["POST"])
    app.add_url_rule(
        "/train/workouts/new", "train_workout_new", train_workout_new, methods=["POST"]
    )
    app.add_url_rule("/train/workouts/<workout_id>", "train_workout", train_workout)
    app.add_url_rule(
        "/train/workouts/<workout_id>/edit",
        "train_workout_edit",
        train_workout_edit,
        methods=["POST"],
    )
    app.add_url_rule(
        "/train/workouts/<workout_id>/delete",
        "train_workout_delete",
        train_workout_delete,
        methods=["POST"],
    )
    app.add_url_rule("/train/pick", "train_pick", train_pick)
    app.add_url_rule("/train/add/<slug>", "train_add_menu", train_add_menu)
    app.add_url_rule("/train/add", "train_add", train_add, methods=["POST"])
    app.add_url_rule("/train/remove-item", "train_remove_item", train_remove_item, methods=["POST"])
    app.add_url_rule("/train/history/<session_id>", "train_history_detail", train_history_detail)
    app.add_url_rule(
        "/train/history/<session_id>/delete",
        "train_history_delete",
        train_history_delete,
        methods=["POST"],
    )
