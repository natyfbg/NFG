# Exercise content

Every exercise in the library is one YAML file in `exercises/`, named after its
slug (`exercises/push-up.yaml`). These files are the source of truth for exercise
content. `content_sync.py` validates them and syncs them into MongoDB.

## Workflow

1. Add or edit a file in `exercises/` (new exercises start as `status: draft`).
2. `python content_sync.py check` validates every file.
3. `python content_sync.py placeholders` creates an SVG placeholder for any new
   exercise (in `static/img/exercises/`). Add `--overwrite` to redraw them all, e.g.
   after changing an exercise's muscles.
4. `python content_sync.py sync` shows what would change (dry run);
   add `--apply` to write it. Locally with Docker:
   `docker compose exec app python content_sync.py sync --apply`.
5. Flip `status: published` once the instructions are reviewed, then sync again.

Production syncs are always a deliberate, separate step: the script refuses to
write to a non-local database without `--allow-remote`.

## File format

```yaml
slug: dumbbell-bench-press        # must match the file name
name: Dumbbell Bench Press
status: draft                     # draft | published (drafts are hidden from the public site)
level: beginner                   # beginner | intermediate | advanced
category: strength                # strength | plyometric | cardio | mobility
movement: horizontal_push         # see MOVEMENTS in taxonomy.py
mechanics: compound               # optional: compound | isolation
laterality: bilateral             # optional: bilateral | unilateral
full_body: false                  # optional: force the Full Body group
muscles:
  primary: [chest]                # muscle keys from taxonomy.py
  secondary: [triceps, front_delts]
equipment: [dumbbell, bench]      # everything REQUIRED; [] means bodyweight
summary: >-                       # one or two sentences shown at the top
  ...
setup:                            # optional lists of short, plain sentences
  - ...
steps:
  - ...
cues:                             # shown as "Tips"
  - ...
mistakes:
  - ...
safety:
  - ...
breathing: Exhale as you press.   # optional
regressions: [incline-push-up]    # optional: easier variations (slugs)
progressions: [barbell-bench-press]
youtube_id: ""                    # 11-character id, optional
videos:                           # "Watch on YouTube" under the viewer; the YouTube
  youtube: https://www.youtube.com/watch?v=...   # link also fills the how-to Video tab
  instagram: https://www.instagram.com/reel/...
  tiktok: https://www.tiktok.com/@nfg/video/...
images: [...]                     # optional; form photos (see Images). [] clears
                                  # admin photos back to the muscle map
tags: [push, chest]
notes: free-form authoring notes  # never shown or synced
```

### Keys

- **Muscles:** chest, upper_chest, front_delts, side_delts, rear_delts, lats,
  upper_back, traps, lower_back, biceps, triceps, forearms, abs, obliques, quads,
  hamstrings, glutes, adductors, abductors, hip_flexors, calves
- **Equipment:** dumbbell, barbell, kettlebell, cable, machine, bands, suspension,
  bench, pullup_bar, box, cardio_machine
- **Movements:** horizontal_push, vertical_push, horizontal_pull, vertical_pull,
  squat, hinge, lunge, carry, core, isolation, cardio, mobility, full_body

Muscle groups (Chest, Back, Shoulders, …, Cardio, Mobility) and training
environments (Home / Gym / Hybrid) are derived from these; don't set them by hand.
The full definitions live in `backend/taxonomy.py`.

## Images

Exercise images are **9:16 portrait, 1080 × 1920 px** (JPEG or WebP, under ~300 KB),
the shape of a phone held upright. Cards and thumbnails crop the **centre 4:5 band**
(1080 × 1350, i.e. 285 px off the top and bottom), so keep the body and the
movement inside it. Put them in `static/img/exercises/` (e.g. `push-up-1.webp`) and
list them in order, one per position of the movement:

```yaml
images:
  - /static/img/exercises/push-up-1.webp   # start
  - /static/img/exercises/push-up-2.webp   # bottom
```

On the exercise page the viewer shows these photos first (swipe or tap the bars at
the top) and the generated muscle map last. `images: []` clears photos uploaded in
admin and leaves just the muscle map.

The muscle map (`static/img/exercises/<slug>.svg`, from `python content_sync.py
placeholders`) is the muscle group's colour plus a front/back figure drawn from
`muscles:` (primary bright, secondary dimmer; `musclemap.py`). It's also the card
image until an exercise has photos.

## Programs (`programs/<hub-slug>.yaml`)

One file per program (e.g. `programs/8-week-challenge.yaml`). It holds the program's
goal, rules and `phases` (which weeks use which sets/reps/rest for `main` and
`accessory` work), and for each level its training `days`, each with `items`:

```yaml
- exercise: barbell-back-squat     # an exercise slug
  home: goblet-squat               # swap used in the Home track (needed for gym-only gear)
  role: accessory                  # optional; default comes from the exercise
  sets: 3                          # optional overrides of the phase's numbers
  reps: "30–45 s"
- custom: Plank                    # a line without an exercise page (needs sets + reps)
  sets: 3
  reps: "20–40 s"
```

`content_sync.py check` validates programs too: every slug must exist, Home tracks may
only use home equipment, and phases must cover every week once. `sync` writes one track
per level × Home/Gym/Hybrid (Hybrid uses the gym version) and replaces their weeks; it
never touches weeks built by hand in Admin unless you pass `--force-programs`. Progress
is keyed by track + week + day label, so re-syncing keeps everyone's progress — but
renaming a day label resets that day's ticks.
