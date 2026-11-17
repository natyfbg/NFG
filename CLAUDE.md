# CLAUDE.md — working guide for the Qiltsim fitness app (repo: NFG)

This repo is public. Never put secrets, connection strings, IP allowlists or
account details in this file, in code, in logs or in commits.

## What this is

Qiltsim (qiltsim.com; called NFG until 2026-11-16) is a mobile-first fitness app:
exercise library, structured programs, progress tracking. It is **not** Nate's separate Next.js coaching website — do not
migrate this app to Next.js/React or that project's architecture.

- Python 3.11 · Flask 3 · Jinja2 server-rendered templates · Bootstrap 5 (CDN, SRI)
  · vanilla JS · PyMongo (raw dicts, no ODM) · Gunicorn · Docker
- Production: Render (`nfg-app` web service + manual `nfg-seed` job) → MongoDB Atlas.
  Uploaded media lives on a Render disk. Domain qiltsim.com (DNS on Cloudflare, DNS-only
  records pointing at Render). Email goes out through Resend's HTTP API (`mailer.py`).
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
python content_sync.py check-videos          # look up every YouTube demo (internet)
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
- **The name lives in one place:** `APP_NAME` (env, default "Qiltsim") → `app_name` in
  every template; the wordmark is `app_name|upper`; placeholders use
  `placeholders.BRAND_MARK`. Internal names keep "nfg" on purpose: the Mongo database
  (`NFG`), Render service names, cookies (`nfg_vid`, `nfg_env`, `nfg_level`), CSS classes
  (`nfg-*`). Renaming any of those breaks data, sessions or styles.
- **Canonical domain:** `CANONICAL_HOST` (Render env) makes www. and `*.onrender.com`
  301 to it (GET/HEAD only; `/healthz` exempt) and adds `<link rel="canonical">`. Set it
  only after the custom domain is verified in Render, or the site redirects to a domain
  that doesn't answer yet. Links that leave the site (emails, sitemap) use
  `_public_url()`, which never trusts the request's Host header when hosted.
- **Email:** `mailer.send()` posts to Resend when `RESEND_API_KEY` is set; without it the
  email is logged (that's how reset links show up locally). Email-dependent features
  check `mail_enabled`, which is off on Render until the key exists.
- **Password reset:** `/account/forgot` → emailed link `/account/reset/<token>`. Only the
  token's SHA-256 is stored (`password_resets`, TTL on `expires_at`), 1 hour, single use,
  3 emails per account per hour, the forgot form shares the login throttle per IP, and
  the reply is the same whether or not the email has an account. A reset bumps the
  user's `auth_gen`, which is part of the session id (`member:<id>:<gen>`), so every
  other session is signed out.
- `robots.txt` and `sitemap.xml` are routes (public pages, published exercises,
  program hubs; no drafts even for an admin).
- **Theme tokens:** every colour in `app.css` is a `:root` variable; dark mode is the
  `:root[data-theme="dark"]` block of overrides and nothing else. `data-theme` (and
  Bootstrap's `data-bs-theme`) is set on `<html>` from the `nfg_theme` cookie
  (light/dark; absent = follow the device, decided by the inline script before the
  stylesheet, so no flash). The menu's Light/Dark/Auto switch writes the cookie from
  JS; `POST /theme` is the no-JS fallback. Rules: solid controls use `--solid-bg/fg`
  (ink on light, cream on dark); text on muscle-colour or gold surfaces uses
  `--on-color`/`--on-gold` (constant); dark cards and media use `--card-dark`/
  `--media-bg`; `color-mix()` ramps end in `--mix-deep`/`--mix-base`, which swap in dark
  mode so tints go lighter instead of darker. Never write a literal colour into a
  component rule for a light surface. Check new pages in both themes (the preview
  screenshot scripts take a `nfg_theme` cookie).
- **Swipe back:** every sub-page declares `{% block back_url %}` (where one step back
  goes). `base.html` puts it in `data-back-url` on `<body>`, and a right-swipe (or a
  `.nfg-back` link) slides the page out and goes there, via the browser's history when
  the previous page was ours. Horizontal scrollers still need `data-no-page-swipe`.
  Sub-pages get `touch-action: pan-y` on `#page-shell` so the browser leaves sideways
  drags to us.
- Responses are compressed by Flask-Compress (gzip/brotli; `app.css` ~90 KB → ~17 KB).
  `/static/img/*` is cached for a day with stale-while-revalidate; versioned links keep
  the one-year immutable cache. Dead CSS was removed on 2026-11-16 (171 rules): check
  `templates/` + `static/js` for a class before deleting a rule.

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
- 2026-11-10 — Placeholders carry a front/back muscle map (regions keyed by taxonomy
  muscle keys): anatomy only, never technique (2026-11-17: now `musclecard.py`).
  Session pages lead with bilateral compounds in the preset's movement order;
  isometric holds (`tags: [isometric]`) count as accessories.
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
  2026-11-12: the menu is a small dropdown card under the button (not a full-height
  drawer) holding only what the bars don't show: Muscle Mixer, Search, About, then
  account actions. It pops in from the corner in 160ms and closes instantly.
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
  `youtube_id` is empty). Picks favour consistent, credible libraries (NASM's
  "Proper Form & Technique" series first, then Live Lean TV's exercise library and
  OPEX) over influencer videos; every id came from a search result for that exercise.
  `content_sync.py check-videos` confirms channel/title and flags dead or
  non-embeddable links. Nate can swap any link.
- 2026-11-16 — **Renamed NFG → Qiltsim**, domain qiltsim.com (Cloudflare DNS, DNS-only
  so Render issues the certificate; www redirects to the bare domain). Email through
  Resend (free tier: 3,000 emails/month, 100/day — plenty for password resets).
  Password reset by email shipped first; the favicon stays Nate's glyph until there's a
  Qiltsim logo.
- 2026-11-16 — Muscle Mixer stays in the menu (no floating button). Picker design:
  body first, picked muscles listed under the body as chips with × to remove
  (canvas option E2). Phase 1 code is stashed until the revamp starts.
- 2026-11-16 — **Revamp phase 1 (foundation):** colour tokens + dark mode (menu
  switch Light/Dark/Auto, cookie `nfg_theme`); top bar shows the logo tile and the
  QILTSIM wordmark; bottom nav slightly translucent with blur; Home/Gym/Hybrid active
  segment is charcoal (#2A2A26) in light mode, cream in dark; Home says "Hi, <name>
  · What are we training today?", sessions are Push · Pull · Lower · Core · Stretching
  (Full body lives in the Mixer and programs; Stretching shows once mobility exercises
  exist), programs section is "Coach's Plans" with dark gold cards; swipe-back on all
  sub-pages; tab swipe follows the finger more closely; compression + image caching.
  Full plan: project doc `qiltsim-revamp-plan-2026-11-16.md` (phases 2–6: library,
  sessions + Mixer, programs, recipes + macros, account/admin/polish).
- 2026-11-17 — **Revamp phase 2 (library):** setting model from the planner's Key tab
  (Home < Hybrid < Gym, derived from equipment; optional kit like the yoga mat never
  raises it); Nate's 12 equipment tiles; all 316 planner exercises as content files
  (234 new drafts with instructions, `lists:` session flags and a YouTube demo).
  **Muscle cards** (`musclecard.py` + `placeholders.py`): dark card, front/back
  figures from the licensed react-native-body-highlighter art (`bodymap_paths.py`,
  regenerated by `scripts/import_bodymap.py`, paths minified), primary muscles red,
  secondary yellow, group colour on the chip and glow; men for upper-body exercises,
  women for lower-body. **Form steps** (`formfigures.py`): every exercise page shows
  Start · Movement · Finish with an illustrated figure (movement family → two poses on
  one rig; Movement/Finish add a ghost of the other pose and an arrow), the caption
  and tip from the exercise's own instructions (`form:` in the file overrides them,
  `form_pose:` forces a family). Real photos replace a figure when
  `static/img/exercises/<slug>/step-<n>.webp|jpg|png` exists.
  **Exercises filters:** an always-visible, sticky filter bar (search, then Muscles ·
  Equipment · Level · more) on the Exercises page and browse. Muscles are
  multi-select and combine with AND (`group=a&group=b`: every group worked, at least
  one as a main target; exercises with more of them as main targets first); equipment
  and level are one each. The panel (`_filterpanel.html`) is a bottom sheet on phones
  and a popup under the bar on desktop, with a live "Show N exercises" count from
  `/workouts/browse/count` and a one-tap "Remove <last muscle>" when nothing matches.
  All 12 equipment tiles show on the Exercises page.
- 2026-11-17 — **Revamp phase 3 (sessions + Mixer):** one builder for both,
  `session_builder.py` (pure, no Flask). Main lifts by level: sessions Beginner 6–7,
  Intermediate 7–9, Advanced 8–9; the Mixer 6–7 / 7–8 / 8–9. Beginner sessions never
  use Advanced exercises. "Accessories" are now **Secondary lifts** (2–3 complements);
  each **Add** option joins them with one exercise. Add is contextual (Push: forearms,
  core, rotator cuff · Pull: rear delts, traps, forearms · Lower: calves, hips, core ·
  Core: lower back, hips · Full body: core, calves, forearms), `?add=` (old `?g=` add-on
  links still work). Variety: one version per exercise (`stem()` drops equipment and
  grip words, so Smith/Dumbbell "Bench Press" count once), at most two per movement on
  the main group and one on the others, one isolation per muscle, about 60% compounds
  on split days, every preset movement and target group covered; Nate's planner
  `lists:` and a short list of staples lead the ranking. Full body picks one lift per
  slot (squat, push, pull, hinge, core, ...). Every row shows sets × reps for its role
  and level. Session page: title left, Home/Gym/Hybrid and **+ Add** top-right, level
  switch under it (level remembered in `nfg_level`, shared with programs), "More
  options" collapsed at the end. **Mixer:** tap the body (licensed figures, picked
  muscles glow in their group colour), picked chips with × underneath, "Pick from a
  list" for the rest; results in tiers ("Hits all of them", "Hits 2 of 3", "One at a
  time") and every picked group gets at least one main-target exercise.
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
4. 8 Week Challenge content: ✅ drafted (all levels, home swaps, phases; promoted in PR #13)
   — awaiting Nate's review. ✅ Core batch (15 new, 82 total): holds, crunches, leg raises,
   Pallof press, woodchop, suitcase carry; the program's Plank/Side Plank lines now link
   to real exercises and Coach's Picks resolves fully.
5. Accounts and trust — new design for auth/account; ✅ password reset (email, 2026-11-16);
   delete account + export; privacy/terms; skippable training profile; error alerts;
   backups confirmed.
6. Workouts + session mode — saved workouts (exercise + sets/reps/rest), "Save as workout",
   set logging, rest timer, swap exercise, history (guests and members).
Alongside: full body (S3), cardio + mobility (S5), first 12 recipes (S6).
Later, each gated: progress analytics · PWA · personalised Home · nutrition · AI coach ·
native/wearables research · paid tier.

Standing decisions: workouts/session mode is the signature feature; programs and recipes
are content-as-code (Claude drafts, Nate approves); form images are consistent illustrated
figures (Nate, 2026-11-16) until real photos exist, never AI-generated photos; admin is
frozen except the Coach's Picks screen; guests stay first-class; analytics cookie-free.
