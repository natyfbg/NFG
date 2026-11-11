# CLAUDE.md — working guide for the NFG fitness app

This repo is public. Never put secrets, connection strings, IP allowlists or
account details in this file, in code, in logs or in commits.

## What this is

NFG is a mobile-first fitness app: exercise library, structured programs,
progress tracking. It is **not** Nate's separate Next.js coaching website — do not
migrate this app to Next.js/React or that project's architecture.

- Python 3.11 · Flask 3 · Jinja2 server-rendered templates · Bootstrap 5 (CDN, SRI)
  · vanilla JS · PyMongo (raw dicts, no ODM) · Gunicorn · Docker
- Production: Render (`nfg-app` web service + manual `nfg-seed` job) → MongoDB Atlas.
  Uploaded media lives on a Render disk.
- Local: Docker Compose with a local `mongo:7` container. Never point local dev at Atlas.
- Nearly all backend code is in `backend/app.py` (monolith, ~6.6k lines). Do not split
  it up without an agreed plan; tests come first.

## Roles

- Nate owns exercise selection, form/technique instructions and nutrition content,
  and owns git (reviews, commits, pushes, promotes).
- Claude is the engineer: makes most technical decisions, explains what and why,
  and flags anything that needs Nate's input.

## Rules

1. Check `git status` / current branch before changing anything; trust the repo
   over any handoff document.
2. Do not commit, push, or touch backup/archive branches or tags unless asked.
3. Never log or print `MONGO_URI`, `SECRET_KEY`, admin passwords/hashes or tokens.
4. Never run anything against production data (seed, content sync, migrations)
   without Nate's explicit go-ahead for that specific run.
5. Preserve ObjectIds, slugs and URLs. URL changes need redirects.
6. Keep guest progress, favorites, member progress, guest→member migration and the
   admin CMS working.
7. Small, reviewable changes. Run lint and tests before handing work back.

## Commands (from `backend/`)

```bash
docker compose up -d --build          # app on http://localhost:5050
docker compose logs -f app
docker compose down
# Code + templates are bind-mounted and reload on their own (DEV_RELOAD=1 in compose).
# Rebuild only when requirements.txt or the Dockerfile change.

pip install -r requirements-dev.txt   # once, in a venv
python -m pytest -q                   # tests run against in-memory Mongo (mongomock)
flake8 . && black --check . && isort --check-only .
```

Exercise content (see `backend/content/README.md`):

```bash
python content_sync.py check                 # validate content/exercises/*.yaml
python content_sync.py placeholders          # SVG placeholder for new exercises
docker compose exec app python content_sync.py sync           # dry run (local DB)
docker compose exec app python content_sync.py sync --apply   # write (local DB)
```

`sync --apply` refuses non-local databases unless `--allow-remote` is passed —
production syncs need Nate's go-ahead every time.

CI (`.github/workflows/ci.yml`) runs lint, tests and a Docker build on pushes to
main/dev/windows-dev and PRs into main/dev.

## Git flow

`windows-dev` (active work) → `dev` (integration) → PR → `main` (Render auto-deploys).

`main` has a GitHub rule forbidding merge commits, so promotions are **squash merges**.
After every squash, re-link dev so the next PR doesn't show phantom conflicts:

```bash
git fetch origin
git diff --quiet origin/main origin/dev && echo IDENTICAL   # must be identical first
git checkout dev && git merge --ff-only origin/dev
git merge -s ours origin/main -m "Record squash promotion #N as merged into dev history"
git push origin dev
git checkout windows-dev && git merge --ff-only dev && git push origin windows-dev
```

Before each promotion, tag main: `backup/main-pre-<what>-<date>`.
Commits authored by Claude keep Nate as author and use `Claude <noreply@anthropic.com>`
as committer, so GitHub shows them as Verified.

## Things that bite

- **Import-time side effects:** importing `app.py` pings Mongo, creates indexes and
  inserts default styles + the 8-week hub/tracks if missing — in every Gunicorn
  worker, on every deploy. Deleting the 8-week programs in admin is undone on deploy.
- **Local reload:** `DEV_RELOAD=1` (compose only) runs gunicorn with `--reload` and
  `TEMPLATES_AUTO_RELOAD`. Without it, pulled code shows the *old* templates with the
  *new* CSS (CSS is read live, templates are cached at start) and pages look broken.
- **Hosted config fails fast:** on Render the app refuses to start without a real
  `SECRET_KEY` and `ADMIN_PASSWORD_HASH`. Plaintext `ADMIN_PASSWORD` is local-only.
- **Swipe navigation** (`base.html`) hijacks horizontal drags on home/workouts/
  programs/recipes. Any horizontal scroller must carry `data-no-page-swipe`
  (or `.swipe-block`) — use the `ui.rail()` macro, which already does.
  Smoothness: the page follows the finger, neighbouring tabs are prefetched
  (`<script type="speculationrules">`), and cross-document View Transitions slide the
  next page in (`@view-transition` + `html[data-nav-dir]` in app.css). The top bar,
  bottom nav and swipe rail have their own `view-transition-name` so they stay still —
  keep those names unique on the page.
- **UI components** live in `templates/_components.html` (`{% import "_components.html"
  as ui %}`). Full-width pages override `{% block page %}` in `base.html`.
- Grid layouts containing rails need `min-width: 0` on grid children, or the rail's
  content stretches the page wider than the phone screen.
- **Bottom sheets / modals go in `{% block overlays %}`**, never inside `page`/`content`:
  `#page-shell` gets a transform while being swiped, which traps `position: fixed`
  children inside it and under the bottom nav.
- Icons: `ui.icon('name')` inlines `templates/icons/<name>.svg` (Bootstrap Icons, MIT,
  copied from the npm package). Add new ones by copying from `bootstrap-icons`.
- **`workouts` collection = the exercise library** (single exercises). Do not rename it
  or its `/workouts/<slug>` URLs; program items reference it by `workout_id`/`workout_slug`.
- **`is_favorite` on a workout is a global admin flag**, not a per-user favorite.
  Only programs have per-viewer favorites today (`program_favorites`).
- **Viewer identity:** guests get an `nfg_vid` cookie; progress/favorites are keyed by
  `_progress_owner_key()` (`user:<id>` for members, the cookie for guests) and migrated
  on login/register by `_migrate_guest_state_to_member()`. Any new per-user data must
  use the same key and be added to that migration.
- **Library snapshot:** Home, Exercises and session pages read exercises from
  `_public_library()` (one query per worker, cached `LIBRARY_CACHE_SECONDS`=60; admins
  read fresh; any `/admin` POST clears it). Treat its docs as read-only — `dict(doc)`
  before adding keys. A CLI content sync shows up within a minute. Tests run with the
  cache off (conftest) except `tests/test_tidy.py`.
- Stylesheet link carries `?v=<commit>` (`RENDER_GIT_COMMIT`, else file mtime) and is
  cached for a year; add `v=asset_v` to any new static link that changes on deploy.
- Admin-only sentences stored in public fields are hidden with the `visitor_text`
  filter (`ADMIN_ONLY_SENTENCES`), so production data needs no migration.
- `/healthz` returns 503 when Mongo is unreachable (Render health check uses it).
- Templates must tolerate missing fields (`x is defined and x is not none`).
- Never pass a template variable named `session`, `request`, `g`, `config` or
  `current_user`: Flask/Flask-Login already define them in every template, so a missing
  value silently falls back to theirs (this caused the Mixer 500, f0df57e).
- `seed.py` now seeds only the sample recipes; exercises come from content files.
- `backend/taxonomy.py` is the single source of truth for muscle/group/equipment/
  movement keys. Keys are stored in the DB and content files — never rename one
  without a migration. It has no Flask/Mongo imports on purpose.
- Content-synced exercise docs carry both new fields (`muscles`, `primary_groups`,
  `muscle_groups`, `equipment_required`, `environments`, `movement`, `instructions`,
  `publish_status`) and the legacy fields current templates/filters read
  (`body_part(s)`, `primary_muscle`, `equipment`, `style`, `level`, `info`, `tips`).
  New code should read the new fields.
- Draft exercises are hidden from visitors via `_public_workout_query()` but visible
  to a signed-in admin (with a Draft badge) for previewing.

## Decisions log

- 2026-11-08 — Phase 0 hardening: hash-only admin auth, hosted fail-fast config,
  Mongo-backed login throttle (checked before credentials), open-redirect guard,
  literal + length-capped search, safe pagination, 503 healthz, Pillow upload
  validation, security headers, Bootstrap SRI, pytest suite.
- 2026-11-08 — UI calls single movements **"Exercises"**; "workout" is reserved for
  composed sessions (ordered exercises with sets/reps/rest).
- 2026-11-08 — Exercise content is **content-as-code**: one YAML file per exercise in
  the repo, synced to Mongo by slug (dry-run by default, never deletes, never touches
  admin-uploaded images). New exercises start `draft`; Nate publishes.
- 2026-11-08 — Placeholder images are generated SVGs in the repo (no fake form
  diagrams). Real images replace them by filename later.
- 2026-11-08 — Training environment is derived from required equipment (Home/Gym/
  Hybrid are equipment presets), not stored per exercise.
- 2026-11-08 — Muscle taxonomy uses stable keys; a muscle can belong to several groups.
  Exercises store primary + secondary muscle keys.
- 2026-11-08 — Favorites become one generic collection (viewer, item type, item);
  composed workouts will reuse the program-day item shape (exercise + sets/reps/rest).
- 2026-11-08 — Exercise images: `content/exercises` + `static/img/exercises/<slug>.svg`
  placeholders; real images go in the file's `images:` list. Muscle-group colours
  live in `taxonomy.MUSCLE_GROUPS`.
- 2026-11-08 — Coach's Picks (and future shelves) are content files in
  `backend/content/collections/<key>.yaml` (title, subtitle, exercise slugs), loaded at
  startup by `curation.py`. Missing/draft slugs are skipped at render time.
- 2026-11-08 — Favorites are generic: `favorites` collection keyed by viewer + item_type
  + item_slug (only "exercise" so far); migrated guest→member. Program favorites still
  use `program_favorites`.
- 2026-11-08 — Training environment preference lives in the `nfg_env` cookie
  (home/gym/hybrid; hybrid = no filtering).
- 2026-11-10 — Design language: cream surfaces, near-black text, muscle-group colour
  as the accent (tiles, chips, step numbers), **brand gold for primary buttons**
  (matches the black/gold logo). Lively, not cartoonish; no intro paragraphs.
- 2026-11-10 — Placeholders carry no exercise name (the page and cards show it).
- 2026-11-10 — Placeholders carry a front/back muscle map (`musclemap.py`, regions keyed
  by taxonomy muscle keys): anatomy only, never technique. Session pages lead with
  bilateral compounds in the preset's movement order; isometric holds (`tags:
  [isometric]`) count as accessories.
- 2026-11-10 — Exercise page: chips row replaces the Overview block; instructions
  live in a tabbed popup (Steps · Cues · Mistakes · Safety · Video) opened by the "?"
  on the image; per-exercise `videos:` links (YouTube/Instagram/TikTok, https +
  allow-listed hosts only) show under the image.
- 2026-11-10 — One global search (top bar → `/search`: exercises, programs, recipes).
  Browse has its own compact search + Filters sheet; filters use taxonomy keys
  (`group`, `eq`, `env`), legacy label params still work.
- 2026-11-10 — Brand assets in `static/img/brand/` (see its README). The black/gold
  glyph needs a cream tile on dark backgrounds; the navbar currently uses the wordmark.
- 2026-11-10 — **Each page has one job.** Home = "what do I do today?" (continue,
  build a session, Coach's Picks, programs). Exercises = the catalogue (search, muscle
  group / equipment / level tiles → browse grid). Programs = plans + progress. Don't
  duplicate a section across pages.
- 2026-11-10 — Home "Build a session": full-width Home/Gym/Hybrid switch, then one
  colour card per split (Push / Pull / Lower / Core / Full body, colour in
  `taxonomy.SESSION_SPLITS`) opening its full session `/workouts/mix?p=<key>`; focus
  variants (chest vs shoulder push, vertical vs horizontal pull, quad vs glute/ham
  dominant) are named on the card and switched on the session page. Below, tinted
  muscle chips (only groups with exercises) feed the Mixer with a live count. No tabs.
  Continue training stays at the top, compact, only when there is progress.
- 2026-11-10 — The Home/Gym preference (`nfg_env`) applies to Home, the Mixer and
  session pages only. The Exercises page always shows the whole library; browse takes
  an explicit `?env=` filter that is kept in pager links but never saved.
- 2026-11-10 — Exercises page: muscle groups (groups with exercises first, curated
  order) and equipment show 6 with "Show all"; equipment tiles are tinted and say
  where they work; level cards have difficulty bars + who they suit; "New in the
  library" rail.
- 2026-11-10 — Top bar: "NFG" wordmark (logo kept for favicon/home-screen icons),
  search (drawer on mobile), Sign in / Join for guests, hamburger menu (`#appMenu`).
  The exercise how-to is a small centred modal, not a bottom sheet.
- 2026-11-11 — **Programs v2.** Programs are content files (`content/programs/*.yaml`,
  `program_content.py`) synced into the existing hub/track/week/item collections (one
  track per level × Home/Gym/Hybrid; Hybrid = gym version; home swaps named per item).
  One program page per hub (`/programs/<hub>`: next session, setup, week strip, this
  week's days, how it works); the day page keeps the old week URL
  (`/programs/<hub>/week/<n>?level&env&day=`). The old level → environment → weeks steps
  and track URLs redirect. Level is remembered in `nfg_level`, Home/Gym in `nfg_env`;
  otherwise the last progress row for that hub decides.
- 2026-11-11 — 8 Week Challenge (Nate): goal "lose fat, keep muscle"; Beginner 3 days
  (full body), Intermediate 4 (upper/lower), Advanced 5 (push/pull/legs/upper/lower);
  ~45-minute sessions; phases Foundation (wk 1–2), Build (3–5), Push (6–7), Peak (8).
- 2026-11-11 — **Exercise media is 9:16** (1080×1920), with everything that matters in
  the centre 4:5 band, which cards and thumbnails crop to. Exercise page: name, Save
  and chips first, then a 9:16 viewer (height-capped, centred; sticky left column on
  desktop) with form photos in order and the muscle map always last; story-style bars
  at the top show the count and position (tap to jump; arrows on desktop). The "?" is
  the only how-to entry (the bottom "How to do it" button is gone). In content files
  `images: []` clears admin photos back to the muscle map (used for Push-Up's stand-in
  EZ-bar photos). Placeholder subline reads "Muscles worked".
- 2026-11-11 — Every exercise gets a YouTube demo (`videos.youtube`) → "Watch on
  YouTube" under the viewer and the how-to Video tab (id derived from the link when
  `youtube_id` is empty). Picks favour one consistent, credible library (NASM's
  "Proper Form & Technique" series) over influencer videos; Nate can swap any link.
- 2026-11-08 — Muscle Mixer ships first as a filter (rank exercises by selected
  muscles); "Build Workout" comes after composed workouts exist.

## Roadmap (current)

Full assessment and plan: the "NFG — Assessment & Plan" doc (2026-11-10, Nate's claude.ai
docs). Summary:

Done: Phase 0 hardening + tests · content pipeline (taxonomy, YAML, sync, placeholders) ·
Home (session cards, Mixer chips, Continue training) · app shell, Exercises, browse,
search, exercise page.

Next six sprints, in order:
1. ✅ Ship and tidy (promoted in PR #11, 2026-11-10) —
   visitor-facing placeholder/admin copy removed, Sign in pill, versioned stylesheet,
   library snapshot (Home 20 → 5 DB calls, Exercises 11 → 0).
2. ✅ Library: legs and glutes (27 new, 67 total) + deadlift/goblet squat text;
   muscle-map placeholders (promoted in PR #12, 2026-11-11; production content sync by Nate).
3. ✅ Programs v2 — content files + sync, program page, day page, redirects (2026-11-11).
4. 8 Week Challenge content: ✅ drafted (all levels, home swaps, phases) — awaiting Nate's
   review; core exercises (~15) still to add, then link the program's custom core lines.
5. Accounts and trust — new design for auth/account; password reset (email); delete account
   + export; privacy/terms; skippable training profile; error alerts; backups confirmed.
6. Workouts + session mode — saved workouts (exercise + sets/reps/rest), "Save as workout",
   set logging, rest timer, swap exercise, history (guests and members).
Alongside: full body (S3), cardio + mobility (S5), first 12 recipes (S6).
Later, each gated: progress analytics · PWA · personalised Home · nutrition · AI coach ·
native/wearables research · paid tier.

Standing decisions: workouts/session mode is the signature feature; programs and recipes
are content-as-code (Claude drafts, Nate approves); no AI-generated form images (Nate films,
Claude processes); admin is frozen (no new features); guests stay first-class; analytics
cookie-free.
