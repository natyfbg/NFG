"""training.py: pure session-mode logic (no app, no DB)."""

import datetime as dt

import training as tr

BENCH = {
    "slug": "barbell-bench-press",
    "name": "Barbell Bench Press",
    "category": "strength",
    "movement": "horizontal_push",
    "mechanics": "compound",
    "equipment": ["barbell", "bench"],
    "primary_groups": ["chest"],
    "level": "Intermediate",
}
PUSH_UP = dict(BENCH, slug="push-up", name="Push-Up", equipment=[])
PLANK = dict(BENCH, slug="plank", name="Plank", movement="core", equipment=[], tags=["isometric"])
ROW = dict(BENCH, slug="rowing-machine", name="Rowing Machine", category="cardio")
CURL = dict(
    BENCH,
    slug="dumbbell-curl",
    name="Dumbbell Curl",
    mechanics="isolation",
    equipment=["dumbbell"],
    primary_groups=["biceps"],
)


def test_input_kind_follows_the_exercise():
    assert tr.input_kind(BENCH) == "weight_reps"
    assert tr.input_kind(PUSH_UP) == "reps"
    assert tr.input_kind(PLANK) == "seconds"
    assert tr.input_kind(ROW) == "minutes"
    assert tr.input_kind(None) == "weight_reps"


def test_parse_scheme():
    assert tr.parse_scheme("3 × 8–10") == (3, "8–10")
    assert tr.parse_scheme("4 x 12–15 each side") == (4, "12–15 each side")
    assert tr.parse_scheme("15–20 min") == (1, "15–20 min")
    assert tr.parse_scheme("") == (3, "")
    assert tr.parse_scheme("99 × 5")[0] == tr.MAX_ROWS


def test_default_rest_by_role():
    assert tr.default_rest(BENCH, "main") == "2 min"
    assert tr.default_rest(CURL, "main") == "90s"
    assert tr.default_rest(BENCH, "secondary") == "60s"
    assert tr.default_rest(PLANK, "main") == "45s"
    assert tr.default_rest(ROW, "main") == ""


def test_items_from_session_and_program_day():
    built = {
        "main": [dict(BENCH, scheme="4 × 6–10")],
        "secondary": [dict(CURL, scheme="3 × 12–15")],
        "more": [dict(PUSH_UP, scheme="3 × 10")],
    }
    items = tr.items_from_session(built)
    assert [i["slug"] for i in items] == ["barbell-bench-press", "dumbbell-curl"]
    assert items[0]["sets_target"] == 4 and len(items[0]["rows"]) == 4
    assert items[0]["rest"] == "2 min" and items[0]["rest_seconds"] == 120
    assert items[1]["rest"] == "60s" and items[1]["group"] == "biceps"

    day = [
        {
            "exercise": BENCH,
            "name": "Barbell Bench Press",
            "sets": 4,
            "reps": "8–10",
            "rest": "2 min 30s",
            "notes": "",
            "swap_for": "Push-Up",
            "role": "main",
        },
        {
            "exercise": None,
            "name": "Walk",
            "sets": "",
            "reps": "20 min",
            "rest": "",
            "notes": "Easy pace",
            "swap_for": "",
            "role": "accessory",
        },
    ]
    items = tr.items_from_program_day(day)
    assert items[0]["rest_seconds"] == 150 and "At home: Push-Up" in items[0]["note"]
    assert items[1]["slug"] == "" and items[1]["name"] == "Walk" and items[1]["sets_target"] == 3
    assert items[1]["role"] == "secondary"
    assert len({i["key"] for i in items}) == 2


def test_clean_row_validates_and_converts_units():
    row = tr.clean_row("weight_reps", {"weight": "135", "reps": "8"}, "imperial")
    assert row["reps"] == 8 and abs(row["weight_kg"] - 61.235) < 0.001
    assert (
        tr.clean_row("weight_reps", {"weight": "60,5", "reps": "8"}, "metric")["weight_kg"] == 60.5
    )
    # blank fields use the placeholders the page sends
    row = tr.clean_row("weight_reps", {"ph_weight": "60", "ph_reps": "10"}, "metric")
    assert row == {"done": True, "reps": 10, "weight_kg": 60.0}
    assert tr.clean_row("weight_reps", {"weight": "60"}, "metric") is None  # reps needed
    assert tr.clean_row("weight_reps", {"reps": "8"}, "metric") == {"done": True, "reps": 8}
    assert tr.clean_row("reps", {"reps": "8.5"}, "metric") is None
    assert tr.clean_row("reps", {"reps": "-3"}, "metric") is None
    assert tr.clean_row("weight_reps", {"weight": "5000", "reps": "1"}, "metric") is None
    assert tr.clean_row("seconds", {"seconds": "45"}, "metric") == {"done": True, "seconds": 45}
    assert tr.clean_row("minutes", {"minutes": "20.5"}, "metric") == {"done": True, "minutes": 20.5}
    assert tr.clean_row("minutes", {"minutes": "abc"}, "metric") is None


def test_show_row_and_weights():
    assert tr.show_row({"weight_kg": 61.235, "reps": 8}, "weight_reps", "imperial") == "135 lb × 8"
    assert tr.show_row({"weight_kg": 62.5, "reps": 5}, "weight_reps", "metric") == "62.5 kg × 5"
    assert tr.show_row({"reps": 12}, "reps", "metric") == "× 12"
    assert tr.show_row({"seconds": 45}, "seconds", "metric") == "45 s"
    assert tr.show_row({"minutes": 20.0}, "minutes", "metric") == "20 min"


def test_last_rows_and_placeholders():
    old = {"items": [{"slug": "push-up", "rows": [{"done": True, "reps": 10}]}]}
    newer = {
        "items": [
            {
                "slug": "barbell-bench-press",
                "rows": [
                    {"done": True, "weight_kg": 60, "reps": 8},
                    {"done": True, "weight_kg": 60, "reps": 7},
                    {"done": False},
                ],
            },
            {"slug": "push-up", "rows": [{"done": False}]},  # nothing logged: look further back
        ]
    }
    last = tr.last_rows_by_slug([newer, old], ["barbell-bench-press", "push-up", "plank"])
    assert [r["reps"] for r in last["barbell-bench-press"]] == [8, 7]
    assert last["push-up"][0]["reps"] == 10 and "plank" not in last

    item = tr.make_item(BENCH, sets=3, reps="8–10")
    ph = tr.placeholders(item, last["barbell-bench-press"], "metric")
    assert [p["reps"] for p in ph] == ["8", "7", "7"]  # the last set repeats
    assert ph[0]["weight"] == "60" and ph[0]["from_history"] and ph[0]["target"] == "8–10"
    fresh = tr.placeholders(item, None, "metric")
    assert fresh[0]["reps"] == "" and not fresh[0]["from_history"]
    assert tr.last_time_text(item, last["barbell-bench-press"], "metric") == "60 kg × 8, 7"
    mixed = [
        {"done": True, "weight_kg": 60, "reps": 8},
        {"done": True, "weight_kg": 62.5, "reps": 6},
    ]
    assert tr.last_time_text(item, mixed, "metric") == "60 kg × 8, 62.5 kg × 6"


def test_progress_current_index_and_summary():
    t0 = dt.datetime(2026, 11, 18, 10, 0)
    a = tr.make_item(BENCH, sets=2)
    b = tr.make_item(PUSH_UP, sets=2)
    a["rows"] = [
        {"done": True, "weight_kg": 60, "reps": 8, "at": t0 + dt.timedelta(minutes=5)},
        {"done": True, "weight_kg": 60, "reps": 8, "at": t0 + dt.timedelta(minutes=8)},
    ]
    b["rows"][0] = {"done": True, "reps": 15, "at": t0 + dt.timedelta(minutes=12)}
    sess = {"items": [a, b], "started_at": t0, "updated_at": t0}
    assert tr.item_progress(a) == (2, 2) and tr.current_index(sess["items"]) == 1
    s = tr.summary(dict(sess, finished_at=t0 + dt.timedelta(minutes=40)))
    assert s == {"sets": 3, "exercises": 2, "reps": 31, "volume_kg": 960.0, "minutes": 40}
    assert tr.show_volume(960.0, "metric") == "960 kg"
    assert tr.show_duration(40) == "40 min" and tr.show_duration(75) == "1 h 15 min"


def test_stale_rules():
    t0 = dt.datetime(2026, 11, 18, 10, 0)
    item = tr.make_item(BENCH, sets=1)
    sess = {"items": [item], "started_at": t0, "updated_at": t0}
    assert not tr.is_stale(sess, t0 + dt.timedelta(hours=5))
    assert tr.is_stale(sess, t0 + dt.timedelta(hours=7))
    assert not tr.has_logged(sess)
    item["rows"][0] = {"done": True, "reps": 5, "at": t0 + dt.timedelta(minutes=20)}
    assert tr.has_logged(sess)
    assert tr.finished_at_for_stale(sess) == t0 + dt.timedelta(minutes=20)
