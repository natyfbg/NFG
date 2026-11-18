"""Admin → Content sync: bring the site's exercises and programs in line with the
content files in the deployed code, from a browser.

Same code paths as `content_sync.py sync` (never deletes, keeps progress, skips
exercises edited in Admin and program weeks built by hand). The page shows the dry run;
Apply writes in a background thread (Atlas' free tier allows ~100 operations a second,
so a full program rewrite takes a few seconds) and records each run in
`content_sync_runs`, which every worker can read. One run at a time.

It runs inside the app, which already holds the database connection: nobody has to
copy a connection string anywhere. Error messages are reduced to the exception type so
host names never reach the page or the logs.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import threading
from typing import Callable, Optional

import content_sync as cs
import program_content as pc
from flask import abort, flash, jsonify, redirect, render_template, url_for

log = logging.getLogger(__name__)
STALE_AFTER = dt.timedelta(minutes=15)


def build_plan(db) -> dict:
    """The dry run, in plain terms. Raises on invalid content (shown on the page)."""
    exercises, warnings = cs.load_exercises()
    names = {ex["slug"]: ex["name"] for ex in exercises}
    ex_plan = cs.plan_sync(db, exercises)
    programs, _ = pc.load_programs(exercises)
    prog_plan = pc.plan_program_sync(db, programs, exercises)

    hubs: dict = {}
    for row in prog_plan:
        hub = row["hub"]
        entry = hubs.setdefault(
            hub["slug"],
            {
                "slug": hub["slug"],
                "title": hub["title"],
                "status": hub["status"],
                "tracks": [],
                "new": 0,
                "replace": 0,
                "unchanged": 0,
                "skip": 0,
            },
        )
        entry["tracks"].append(
            {
                "slug": row["track"]["slug"],
                "action": row["action"],
                "weeks": len(row["track"]["weeks"]),
                "items": row["item_count"],
            }
        )
        entry[row["action"]] += 1
    creates = [
        {
            "slug": c["slug"],
            "name": names.get(c["slug"], c["slug"]),
            "status": c["doc"]["publish_status"],
        }
        for c in ex_plan["creates"]
    ]
    updates = [
        {"slug": u["slug"], "name": names.get(u["slug"], u["slug"]), "fields": sorted(u["set"])}
        for u in ex_plan["updates"]
    ]
    kept = [
        {"slug": d["slug"], "name": names.get(d["slug"], d["slug"])} for d in ex_plan["drifted"]
    ]
    program_changes = sum(h["new"] + h["replace"] for h in hubs.values())
    return {
        "exercises": {
            "creates": creates,
            "creates_draft": sum(1 for c in creates if c["status"] == "draft"),
            "updates": updates,
            "kept": kept,
            "unchanged": len(ex_plan["unchanged"]),
            "unmanaged": ex_plan["unmanaged"],
            "total": len(exercises),
        },
        "programs": list(hubs.values()),
        "changes": len(creates) + len(updates) + program_changes,
        "warnings": warnings,
    }


def apply_content(db) -> dict:
    """Write the sync (fresh plan, so it matches the files exactly). Returns counts."""
    exercises, _ = cs.load_exercises()
    ex_plan = cs.plan_sync(db, exercises)
    programs, _ = pc.load_programs(exercises)
    prog_plan = pc.plan_program_sync(db, programs, exercises)
    cs.apply_sync(db, ex_plan)
    pc.apply_program_sync(db, prog_plan)
    return {
        "created": len(ex_plan["creates"]),
        "updated": len(ex_plan["updates"]),
        "kept": len(ex_plan["drifted"]),
        "tracks": sum(1 for r in prog_plan if r["action"] in ("new", "replace")),
        "tracks_skipped": sum(1 for r in prog_plan if r["action"] == "skip"),
    }


def register(app, *, db, invalidate: Callable[[], None]) -> None:
    def latest_run() -> Optional[dict]:
        run = db.content_sync_runs.find_one({}, sort=[("started_at", -1)])
        if (
            run
            and run.get("status") == "running"
            and run["started_at"] < dt.datetime.utcnow() - STALE_AFTER
        ):
            run["status"] = "stale"  # the worker went away; a new run is allowed
        return run

    def run_in_background(run_id) -> None:
        def work():
            try:
                counts = apply_content(db)
                db.content_sync_runs.update_one(
                    {"_id": run_id},
                    {
                        "$set": {
                            "status": "done",
                            "finished_at": dt.datetime.utcnow(),
                            "counts": counts,
                        }
                    },
                )
            except Exception as e:  # never surface connection details
                log.error("Content sync failed: %s", type(e).__name__)
                db.content_sync_runs.update_one(
                    {"_id": run_id},
                    {
                        "$set": {
                            "status": "failed",
                            "finished_at": dt.datetime.utcnow(),
                            "error": type(e).__name__,
                        }
                    },
                )
            finally:
                invalidate()

        if app.config.get("CONTENT_SYNC_INLINE"):  # tests
            work()
        else:
            threading.Thread(target=work, name="content-sync", daemon=True).start()

    def admin_content_sync():
        error = None
        plan = None
        try:
            plan = build_plan(db)
        except (cs.ContentError, pc.ProgramError) as e:
            error = str(e)
        commit = (os.getenv("RENDER_GIT_COMMIT") or "").strip()[:7]
        return render_template(
            "admin_content_sync.html",
            plan=plan,
            error=error,
            run=latest_run(),
            commit=commit,
        )

    def admin_content_sync_apply():
        run = latest_run()
        if run and run.get("status") == "running":
            flash("A sync is already running. This page updates when it's done.", "info")
            return redirect(url_for("admin_content_sync"))
        try:
            plan = build_plan(db)
        except (cs.ContentError, pc.ProgramError):
            flash("The content files have problems; fix them before applying.", "danger")
            return redirect(url_for("admin_content_sync"))
        if not plan["changes"]:
            flash("Everything is already up to date.", "info")
            return redirect(url_for("admin_content_sync"))
        run_id = db.content_sync_runs.insert_one(
            {
                "status": "running",
                "started_at": dt.datetime.utcnow(),
                "planned_changes": plan["changes"],
            }
        ).inserted_id
        run_in_background(run_id)
        return redirect(url_for("admin_content_sync"))

    def admin_content_sync_status():
        run = latest_run()
        if not run:
            abort(404)
        return jsonify({"status": run.get("status"), "counts": run.get("counts") or {}})

    app.add_url_rule("/admin/content-sync", "admin_content_sync", admin_content_sync)
    app.add_url_rule(
        "/admin/content-sync/apply",
        "admin_content_sync_apply",
        admin_content_sync_apply,
        methods=["POST"],
    )
    app.add_url_rule(
        "/admin/content-sync/status", "admin_content_sync_status", admin_content_sync_status
    )
