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
videos:                           # optional; tiny icons under the image link here
  youtube: https://www.youtube.com/watch?v=...
  instagram: https://www.instagram.com/reel/...
  tiktok: https://www.tiktok.com/@nfg/video/...
images: []                        # optional; omit to use the generated placeholder
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

Exercise images are **4:5 portrait, 1080 × 1350 px** (JPEG or WebP, under ~250 KB),
with the movement centred and a little margin on every side, since cards crop
slightly. Put them in `static/img/exercises/` (e.g. `push-up-1.webp`) and list them:

```yaml
images:
  - /static/img/exercises/push-up-1.webp
  - /static/img/exercises/push-up-2.webp
```

Until then each exercise uses its generated placeholder
(`static/img/exercises/<slug>.svg`): the muscle group's colour plus a front/back
muscle map drawn from `muscles:` (primary bright, secondary dimmer; `musclemap.py`).
