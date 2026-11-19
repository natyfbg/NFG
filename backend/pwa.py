"""Install to home screen and work without signal (a progressive web app).

Routes:
    GET /manifest.webmanifest   name, icons, colours and shortcuts for "Add to Home Screen"
    GET /sw.js                  the service worker (templates/sw.js), versioned per deploy
    GET /offline                shown when there's no signal and a page isn't saved yet

What the worker does (templates/sw.js): pages come from the network first and a copy is
kept, so a page opened before still loads with no signal; static files and Bootstrap are
kept and served from the phone. Account, admin and sign-in pages are never kept, and
signing out (or deleting the account / forgetting the device) throws the kept pages away.
The workout logger keeps sets logged with no signal on the phone itself and sends them
when it's back online (train_session.html); the worker never replays requests.

PWA_ENABLED=0 (env) turns it all off: base.html stops registering the worker and /sw.js
serves a worker that deletes its caches and unregisters itself on every phone.
"""

from __future__ import annotations

import os
from typing import Callable

from flask import Response, jsonify, render_template, url_for

ENABLED = os.getenv("PWA_ENABLED", "1").strip() != "0"

THEME_COLOR = "#121512"  # the top bar (same in both themes)
BACKGROUND_COLOR = "#f3f1e8"  # the cream page, used for the splash screen

# Paths whose pages are never kept on the phone (personal or sign-in screens).
NO_KEEP_PREFIXES = ("/account", "/admin", "/login", "/logout", "/privacy/forget-device")
# Posting to these signs out or wipes data, so the kept pages are thrown away too.
CLEAR_ON_POST = ("/account/logout", "/logout", "/account/delete", "/privacy/forget-device")

MAX_PAGES = 40  # pages kept for offline use (oldest go first)
MAX_FILES = 160  # static files and images kept


def manifest(app_name: str, icon: Callable[[str], str]) -> dict:
    return {
        "id": "/",
        "name": app_name,
        "short_name": app_name,
        "description": "Workouts, exercises with form guides, programs and macros.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": BACKGROUND_COLOR,
        "theme_color": THEME_COLOR,
        "icons": [
            {"src": icon("icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": icon("icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {
                "src": icon("icon-maskable-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "shortcuts": [
            {"name": "My workouts", "url": "/train"},
            {"name": "Exercises", "url": "/workouts"},
            {"name": "Macros", "url": "/macros"},
        ],
    }


def register(app, *, app_name: str, asset_version: Callable[[], str]) -> None:
    def brand(name: str) -> str:
        return url_for("static", filename=f"img/brand/{name}")

    def web_manifest():
        resp = jsonify(manifest(app_name, brand))
        resp.mimetype = "application/manifest+json"
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    def service_worker():
        version = asset_version()
        body = render_template(
            "sw.js",
            enabled=ENABLED,
            version=version,
            offline_url=url_for("pwa_offline"),
            shell=[
                url_for("pwa_offline"),
                url_for("static", filename="css/app.css", v=version),
                brand("icon-192.png"),
                brand("logo-128.png"),
            ],
            no_keep=list(NO_KEEP_PREFIXES),
            clear_on=list(CLEAR_ON_POST),
            max_pages=MAX_PAGES,
            max_files=MAX_FILES,
        )
        resp = Response(body, mimetype="application/javascript")
        # The browser checks for a new worker on every visit; never let a cache delay it.
        resp.headers["Cache-Control"] = "no-cache, max-age=0"
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    def offline():
        return render_template("offline.html")

    app.add_url_rule("/manifest.webmanifest", "pwa_manifest", web_manifest)
    app.add_url_rule("/sw.js", "pwa_service_worker", service_worker)
    app.add_url_rule("/offline", "pwa_offline", offline)

    @app.context_processor
    def _pwa_globals():
        return {"pwa_enabled": ENABLED}
