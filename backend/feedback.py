"""Feedback: a page anyone can use (and Nate can send people to), saved and emailed.

Routes:
    GET  /feedback          the form (?back=<path> remembers the page they came from;
                            ?sent=1 shows the thank-you)
    POST /feedback          save it, email it to FEEDBACK_EMAIL, then the thank-you
    GET  /admin/feedback    every message, newest first (?show=all, ?page=N)
    POST /admin/feedback    mark one handled / new again

What's kept (`feedback` collection, per viewer, so it's in export / delete account /
forget this device): rating 1-5 (optional), topic (optional), the message, name and email
only if given, "OK to share" (for quoting on the site later), the page they came from,
installed app or browser, a short device label, the app version and when. The IP is only
kept as a salted hash, to limit how often one place can send (5 an hour).

The email goes to FEEDBACK_EMAIL (Render env; else CONTACT_EMAIL, else ALERT_EMAIL) with
Reply-To set to the sender's email, so a reply goes straight to them. At most 40 emails a
day; past that, feedback is still saved and shows in Admin.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import threading
from typing import Callable, Dict, List, Optional

import mailer
from bson.objectid import ObjectId
from flask import abort, flash, redirect, render_template, request, url_for

TOPICS: List[tuple] = [
    ("workouts", "Workouts"),
    ("exercises", "Exercises"),
    ("programs", "Programs"),
    ("nutrition", "Recipes & macros"),
    ("bug", "Something's broken"),
    ("idea", "An idea"),
]
TOPIC_LABELS = dict(TOPICS)
RATING_WORDS = {1: "Poor", 2: "Not great", 3: "OK", 4: "Good", 5: "Love it"}

MESSAGE_MIN = 3
MESSAGE_MAX = 3000
NAME_MAX = 80
EMAIL_MAX = 254
PER_IP_PER_HOUR = 5
EMAILS_PER_DAY = 40
ADMIN_PAGE_SIZE = 30

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def recipient() -> str:
    for name in ("FEEDBACK_EMAIL", "CONTACT_EMAIL", "ALERT_EMAIL"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def device_label(ua: str) -> str:
    """A short "iPhone · Safari" label from a user agent (no full string kept)."""
    ua = ua or ""
    if "iPhone" in ua:
        device = "iPhone"
    elif "iPad" in ua:
        device = "iPad"
    elif "Android" in ua:
        device = "Android"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        device = "Mac"
    elif "Windows" in ua:
        device = "Windows"
    elif "Linux" in ua:
        device = "Linux"
    else:
        device = "Other"
    if "Edg/" in ua:
        browser = "Edge"
    elif "SamsungBrowser" in ua:
        browser = "Samsung Internet"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "CriOS" in ua or ("Chrome/" in ua and "Chromium" not in ua):
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Browser"
    return f"{device} · {browser}"


def stars(rating: Optional[int]) -> str:
    return ("★" * rating + "☆" * (5 - rating)) if rating else "No rating"


def clean(form, *, is_safe_path: Callable[[str], bool]) -> tuple:
    """(values, errors) from the posted form. Values keep what was typed for a re-render."""
    values: Dict[str, object] = {
        "message": (form.get("message") or "").strip(),
        "name": (form.get("name") or "").strip()[:NAME_MAX],
        "email": (form.get("email") or "").strip()[:EMAIL_MAX],
        "topic": (form.get("topic") or "").strip(),
        "may_share": form.get("may_share") == "1",
        "back": (form.get("back") or "").strip(),
        "app": (form.get("app") or "").strip(),
    }
    errors: List[str] = []
    try:
        rating = int(form.get("rating") or 0)
    except ValueError:
        rating = 0
    values["rating"] = rating if 1 <= rating <= 5 else None
    if values["topic"] not in TOPIC_LABELS:
        values["topic"] = ""
    if values["app"] not in ("installed", "browser"):
        values["app"] = ""
    if not is_safe_path(str(values["back"])) or str(values["back"]).startswith("/feedback"):
        values["back"] = ""
    message = str(values["message"])
    if len(message) < MESSAGE_MIN:
        errors.append("Write a few words about what's working or what should change.")
    elif len(message) > MESSAGE_MAX:
        errors.append(f"That's a lot. Keep it under {MESSAGE_MAX} characters, or send it in two.")
    if values["email"] and not EMAIL_RE.match(str(values["email"])):
        errors.append("That email doesn't look right. Leave it empty if you don't need a reply.")
    return values, errors


def email_text(doc: dict, app_name: str, admin_url: str) -> tuple:
    topic = TOPIC_LABELS.get(doc.get("topic") or "", "")
    subject = f"[{app_name}] Feedback: {stars(doc.get('rating'))}" + (
        f" · {topic}" if topic else ""
    )
    who = doc.get("name") or "Someone"
    if doc.get("email"):
        who += f" <{doc['email']}>"
    lines = [
        doc.get("message") or "",
        "",
        "—",
        f"From: {who}" + (" (member)" if doc.get("user_id") else " (guest)"),
        f"Rating: {stars(doc.get('rating'))}"
        + (f" ({RATING_WORDS[doc['rating']]})" if doc.get("rating") else ""),
    ]
    if topic:
        lines.append(f"About: {topic}")
    if doc.get("page"):
        lines.append(f"Page: {doc['page']}")
    lines.append(
        f"Device: {doc.get('device') or 'unknown'}"
        + (" · home-screen app" if doc.get("app") == "installed" else "")
    )
    if doc.get("may_share"):
        lines.append("OK to share on the site (first name only).")
    lines += [
        "",
        "Reply to this email to answer them." if doc.get("email") else "No email given.",
        f"All feedback: {admin_url}",
    ]
    return subject, "\n".join(lines)


def register(
    app,
    *,
    db,
    owner_key: Callable[[], str],
    member_doc: Callable[[], Optional[dict]],
    is_safe_path: Callable[[str], bool],
    client_ip: Callable[[], str],
    secret: str,
    app_name: str,
    asset_version: Callable[[], str],
    public_url: Callable[..., str],
) -> None:
    try:
        db.feedback.create_index([("created_at", -1)])
        db.feedback.create_index([("ip_key", 1), ("created_at", -1)])
    except Exception as e:  # an index is never worth a failed start
        app.logger.warning("feedback indexes not created: %s", type(e).__name__)

    def ip_key() -> str:
        return hashlib.sha256(f"{secret}|feedback|{client_ip()}".encode("utf-8")).hexdigest()[:24]

    def utcnow() -> datetime.datetime:
        return datetime.datetime.utcnow()

    def too_many(key: str) -> bool:
        since = utcnow() - datetime.timedelta(hours=1)
        try:
            return (
                db.feedback.count_documents({"ip_key": key, "created_at": {"$gte": since}})
                >= PER_IP_PER_HOUR
            )
        except Exception:
            return False

    def may_email() -> bool:
        since = utcnow() - datetime.timedelta(days=1)
        try:
            return (
                db.feedback.count_documents({"emailed": True, "created_at": {"$gte": since}})
                < EMAILS_PER_DAY
            )
        except Exception:
            return False

    def deliver(doc: dict, to: str) -> None:
        subject, text = email_text(doc, app_name, public_url("admin_feedback"))

        def run():
            try:
                mailer.send(to, subject, text, reply_to=doc.get("email") or None)
            except Exception as e:  # feedback is saved either way
                app.logger.warning("Feedback email not sent: %s", type(e).__name__)

        if app.config.get("FEEDBACK_INLINE"):
            run()
        else:
            threading.Thread(target=run, name="feedback-email", daemon=True).start()

    def page():
        member = member_doc()
        if request.method == "POST":
            values, errors = clean(request.form, is_safe_path=is_safe_path)
            if (request.form.get("website") or "").strip():
                # The hidden field only bots fill in: act as if it worked, keep nothing.
                return redirect(url_for("feedback", sent=1))
            key = ip_key()
            if not errors and too_many(key):
                errors.append("Thanks! You've sent a few already. Try again in an hour.")
            if errors:
                return (
                    render_template(
                        "feedback.html",
                        values=values,
                        errors=errors,
                        topics=TOPICS,
                        ratings=RATING_WORDS,
                    ),
                    400,
                )
            to = recipient()
            emailing = bool(to and mailer.configured() and may_email())
            doc = {
                "viewer_id": owner_key(),
                "user_id": member["_id"] if member else None,
                "rating": values["rating"],
                "topic": values["topic"],
                "message": values["message"],
                "name": values["name"],
                "email": values["email"],
                "may_share": values["may_share"],
                "page": values["back"],
                "app": values["app"],
                "device": device_label(request.headers.get("User-Agent", "")),
                "version": asset_version(),
                "ip_key": key,
                "created_at": utcnow(),
                "handled": False,
                "emailed": emailing,
            }
            db.feedback.insert_one(doc)
            if emailing:
                deliver(doc, to)
            return redirect(url_for("feedback", sent=1, back=values["back"] or None))
        back = (request.args.get("back") or "").strip()
        values = {
            "name": (member or {}).get("username") or "",
            "email": (member or {}).get("email") or "",
            "back": back if is_safe_path(back) and not back.startswith("/feedback") else "",
        }
        return render_template(
            "feedback.html",
            values=values,
            errors=[],
            topics=TOPICS,
            ratings=RATING_WORDS,
            sent=request.args.get("sent") == "1",
        )

    def admin():
        if request.method == "POST":
            fid = request.form.get("id") or ""
            if not ObjectId.is_valid(fid):
                abort(400)
            handled = request.form.get("action") == "handled"
            db.feedback.update_one(
                {"_id": ObjectId(fid)},
                {"$set": {"handled": handled, "handled_at": utcnow() if handled else None}},
            )
            flash("Marked as handled." if handled else "Moved back to New.", "success")
            return redirect(url_for("admin_feedback", show=request.form.get("show") or None))
        show = "all" if request.args.get("show") == "all" else "new"
        try:
            page_no = max(1, int(request.args.get("page") or 1))
        except ValueError:
            page_no = 1
        query = {} if show == "all" else {"handled": {"$ne": True}}
        total = db.feedback.count_documents({})
        new = db.feedback.count_documents({"handled": {"$ne": True}})
        rated = list(db.feedback.find({"rating": {"$gte": 1}}, {"rating": 1}))
        counts = {n: 0 for n in range(1, 6)}
        for r in rated:
            counts[int(r["rating"])] = counts.get(int(r["rating"]), 0) + 1
        average = round(sum(int(r["rating"]) for r in rated) / len(rated), 1) if rated else None
        shown = db.feedback.count_documents(query)
        items = list(
            db.feedback.find(query)
            .sort("created_at", -1)
            .skip((page_no - 1) * ADMIN_PAGE_SIZE)
            .limit(ADMIN_PAGE_SIZE)
        )
        return render_template(
            "admin_feedback.html",
            items=items,
            show=show,
            total=total,
            new=new,
            average=average,
            rated=len(rated),
            counts=counts,
            topics=TOPIC_LABELS,
            page_no=page_no,
            more=shown > page_no * ADMIN_PAGE_SIZE,
            to=recipient(),
            mail_on=mailer.configured(),
            share_url=public_url("feedback"),
        )

    app.add_url_rule("/feedback", "feedback", page, methods=["GET", "POST"])
    app.add_url_rule("/admin/feedback", "admin_feedback", admin, methods=["GET", "POST"])
