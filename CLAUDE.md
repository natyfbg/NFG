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
docker compose restart app            # pick up code changes (bind-mounted)
docker compose down

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
- **Hosted config fails fast:** on Render the app refuses to start without a real
  `SECRET_KEY` and `ADMIN_PASSWORD_HASH`. Plaintext `ADMIN_PASSWORD` is local-only.
- **Swipe navigation** (`base.html`) hijacks horizontal drags on home/workouts/
  programs/recipes. Any horizontal scroller must carry `data-no-page-swipe`
  (or `.swipe-block`) — use the `ui.rail()` macro, which already does.
- **UI components** live in `templates/_components.html` (`{% import "_components.html"
  as ui %}`). Full-width pages override `{% block page %}` in `base.html`.
- Grid layouts containing rails need `min-width: 0` on grid children, or the rail's
  content stretches the page wider than the phone screen.
- **Bottom sheets / modals go in `{% block overlays %}`**, never inside `page`/`content`:
  `#page-shell` has a transform (swipe animation), which traps `position: fixed`
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
- `/healthz` returns 503 when Mongo is unreachable (Render health check uses it).
- Templates must tolerate missing fields (`x is defined and x is not none`).
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
- 2026-11-10 — Exercise images are 4:5 (1080×1350). Placeholders carry no exercise
  name and keep the bottom-right corner free for the "?" (how-to) button.
- 2026-11-10 — Exercise page: chips row replaces the Overview block; instructions
  live in a tabbed bottom sheet (Steps · Cues · Mistakes · Safety · Video) opened by
  the "?" on the image or the "How to do it" button; per-exercise `videos:` links
  (YouTube/Instagram/TikTok, https + allow-listed hosts only) show as small icons.
- 2026-11-10 — One global search (top bar → `/search`: exercises, programs, recipes).
  Browse has its own compact search + Filters sheet; filters use taxonomy keys
  (`group`, `eq`, `env`), legacy label params still work.
- 2026-11-10 — Brand assets in `static/img/brand/` (see its README); navbar logo sits
  on a cream tile because the black glyph vanishes on the dark top bar.
- 2026-11-10 — **Each page has one job.** Home = "what do I do today?" (continue,
  build a session, Coach's Picks, programs). Exercises = the catalogue (search, muscle
  group / equipment / level tiles → browse grid). Programs = plans + progress. Don't
  duplicate a section across pages.
- 2026-11-10 — Home "Build a session": Home/Gym/Hybrid in the card header; split tabs
  Push / Pull / Lower / Core / Full body, each with focus presets (chest vs shoulder
  push, vertical vs horizontal pull, quad vs glute/ham dominant) defined in
  `taxonomy.SESSION_SPLITS`; presets open `/workouts/mix?p=<key>` (main lifts, then
  accessories; add-ons like calves via `&g=`). Below, muscle chips feed the Mixer with
  a live count. Empty presets/splits are hidden automatically.
- 2026-11-08 — Muscle Mixer ships first as a filter (rank exercises by selected
  muscles); "Build Workout" comes after composed workouts exist.

## Roadmap (current)

1. ✅ Phase 0: hardening + tests
2. ✅ Content pipeline: taxonomy module, YAML schema, sync script, SVG placeholders,
   existing 4 exercises migrated
3. Starter exercise library: Nate approves the list, Claude drafts instructions in
   batches, Nate reviews and publishes
4. ✅ Home v2: continue training (with %), environment switch, muscle-group tiles,
   Muscle Mixer v1 (`/workouts/mix`), Coach's Picks, saved exercises, programs rail
5. Content batches 2+ (Shoulders/Arms, Legs/Glutes, Core, Full body/Cardio/Mobility)
6. ✅ App shell + Exercises/browse/search/detail redesign (2026-11-10)
7. Next: recently viewed, equipment chips in Quick setup, programs content
   (8 Week Challenge weeks/days), optional native dark theme
