"""Exercise content files, taxonomy derivations, sync safety and draft visibility."""

import datetime
from pathlib import Path

import content_sync as cs
import pytest
import taxonomy as tx
import yaml
from placeholders import render_exercise_svg

VALID = {
    "slug": "test-press",
    "name": "Test Press",
    "status": "draft",
    "level": "beginner",
    "category": "strength",
    "movement": "horizontal_push",
    "muscles": {"primary": ["chest"], "secondary": ["triceps"]},
    "equipment": ["dumbbell", "bench"],
    "summary": "A test exercise.",
    "steps": ["Lie down.", "Press up."],
    "cues": ["Keep shoulders back."],
}


def _write(tmp_path: Path, data: dict, name: str = None) -> Path:
    path = tmp_path / f"{name or data['slug']}.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The real content files
# --------------------------------------------------------------------------


def test_repo_content_files_are_valid():
    exercises, _warnings = cs.load_exercises()
    assert {"push-up", "goblet-squat", "barbell-row", "deadlift"} <= {e["slug"] for e in exercises}


def test_every_exercise_without_images_has_a_placeholder_file():
    exercises, _ = cs.load_exercises()
    for ex in exercises:
        if not ex.get("images"):
            assert (cs.PLACEHOLDER_DIR / f"{ex['slug']}.svg").exists(), ex["slug"]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_valid_file_loads(tmp_path):
    _write(tmp_path, VALID)
    exercises, warnings = cs.load_exercises(tmp_path)
    assert [e["slug"] for e in exercises] == ["test-press"]
    assert warnings == []


@pytest.mark.parametrize(
    "change, message",
    [
        ({"muscles": {"primary": ["pecs"]}}, "unknown muscle"),
        ({"muscles": {"primary": []}}, "at least one muscle"),
        ({"muscles": {"primary": ["chest"], "secondary": ["chest"]}}, "both primary and secondary"),
        ({"equipment": ["treadmill"]}, "unknown equipment"),
        ({"equipment": "dumbbell"}, "must be a list"),
        ({"status": "live"}, "status must be"),
        ({"level": "expert"}, "level must be"),
        ({"movement": "press"}, "movement must be"),
        ({"youtube_id": "not-an-id"}, "youtube_id"),
        ({"steps": ["ok", ""]}, "steps must be"),
        ({"cue": ["typo field"]}, "unknown field"),
    ],
)
def test_invalid_files_are_rejected(tmp_path, change, message):
    _write(tmp_path, {**VALID, **change})
    with pytest.raises(cs.ContentError, match=message):
        cs.load_exercises(tmp_path)


def test_slug_must_match_file_name(tmp_path):
    _write(tmp_path, VALID, name="other-name")
    with pytest.raises(cs.ContentError, match="must match the file name"):
        cs.load_exercises(tmp_path)


def test_missing_required_field(tmp_path):
    data = {k: v for k, v in VALID.items() if k != "movement"}
    _write(tmp_path, data)
    with pytest.raises(cs.ContentError, match="missing required field 'movement'"):
        cs.load_exercises(tmp_path)


def test_duplicate_names_rejected(tmp_path):
    _write(tmp_path, VALID)
    _write(tmp_path, {**VALID, "slug": "test-press-2"})
    with pytest.raises(cs.ContentError, match="duplicates"):
        cs.load_exercises(tmp_path)


def test_published_without_instructions_warns(tmp_path):
    data = {k: v for k, v in VALID.items() if k not in ("steps", "cues")}
    _write(tmp_path, {**data, "status": "published"})
    _ex, warnings = cs.load_exercises(tmp_path)
    assert any("published without 'steps'" in w for w in warnings)


# --------------------------------------------------------------------------
# Derivations
# --------------------------------------------------------------------------


def test_environment_derivation():
    assert tx.environments_for_equipment([]) == ["home", "gym", "hybrid"]
    assert tx.environments_for_equipment(["dumbbell", "bench"]) == ["home", "gym", "hybrid"]
    assert tx.environments_for_equipment(["barbell"]) == ["gym", "hybrid"]
    assert tx.environments_for_equipment(["cable"]) == ["gym", "hybrid"]


def test_muscle_belongs_to_multiple_groups():
    # Rear delts headline under Shoulders but still count for Back.
    assert tx.groups_for_muscles(["rear_delts"]) == ["shoulders", "back"]
    assert "rear_delts" in tx.muscles_for_groups(["shoulders"])


def test_build_doc_writes_legacy_fields_for_current_templates():
    doc = cs.build_doc({**VALID, "muscles": {"primary": ["lats"], "secondary": ["biceps"]}})
    assert doc["body_part"] == "Back"
    assert doc["body_parts"] == ["Back", "Lats"]
    assert doc["primary_muscle"] == "Lats"
    assert doc["equipment"] == "Dumbbell"
    assert doc["style"] == "Dumbbell"
    assert doc["level"] == "Beginner"
    assert doc["difficulty_tier"] == "Easy"
    assert doc["movement_pattern"] == "Push"
    assert doc["info"] == "A test exercise."
    assert doc["tips"] == ["Keep shoulders back."]
    assert doc["instructions"]["steps"] == ["Lie down.", "Press up."]
    assert doc["primary_groups"] == ["back"]
    assert doc["muscle_groups"] == ["back", "biceps"]


def test_full_body_and_category_groups():
    doc = cs.build_doc(
        {
            **VALID,
            "category": "cardio",
            "movement": "cardio",
            "muscles": {"primary": ["quads", "glutes", "chest"]},
            "equipment": [],
        }
    )
    assert "full_body" in doc["primary_groups"]
    assert "cardio" in doc["muscle_groups"]
    assert doc["style"] == "Cardio/Endurance"


def test_optional_text_fields_are_not_blanked():
    data = {k: v for k, v in VALID.items() if k not in ("summary", "steps", "cues")}
    doc = cs.build_doc(data)
    assert "info" not in doc and "tips" not in doc and "instructions" not in doc


def test_placeholder_svg_is_abstract_and_escaped():
    svg = render_exercise_svg({**VALID, "name": "Press & <Hold>"})
    assert svg.startswith("<svg") and "Press &amp; &lt;Hold&gt;" in svg
    assert "Muscles worked" in svg
    assert 'viewBox="0 0 1080 1920"' in svg  # 9:16, the standard exercise image size
    # The name only appears in the accessible label, never drawn on the image.
    assert svg.count("Press &amp; &lt;Hold&gt;") == 1


# --------------------------------------------------------------------------
# Sync against an in-memory database
# --------------------------------------------------------------------------


@pytest.fixture
def db(nfg_app):
    return nfg_app.db


def _sync(db, exercises, force=False):
    plan = cs.plan_sync(db, exercises)
    cs.apply_sync(db, plan, force=force)
    return plan


def test_sync_creates_then_is_idempotent(db):
    plan = _sync(db, [VALID])
    assert [c["slug"] for c in plan["creates"]] == ["test-press"]
    doc = db.workouts.find_one({"slug": "test-press"})
    assert doc["publish_status"] == "draft"
    assert doc["images"] == ["/static/img/exercises/test-press.svg"]

    again = cs.plan_sync(db, [VALID])
    assert again["unchanged"] == ["test-press"] and not again["updates"]


def test_sync_preserves_id_admin_fields_and_real_images(db):
    db.workouts.insert_one(
        {
            "slug": "test-press",
            "name": "Old Name",
            "rating": 4.8,
            "is_favorite": True,
            "images": ["/media/20260101/real-photo.jpg"],
            "info": "Admin-written text",
            "created_at": datetime.datetime(2026, 1, 1),
        }
    )
    original_id = db.workouts.find_one({"slug": "test-press"})["_id"]
    data = {k: v for k, v in VALID.items() if k != "summary"}
    _sync(db, [data])

    doc = db.workouts.find_one({"slug": "test-press"})
    assert doc["_id"] == original_id
    assert doc["name"] == "Test Press"
    assert doc["rating"] == 4.8 and doc["is_favorite"] is True
    assert doc["images"] == ["/media/20260101/real-photo.jpg"]
    assert doc["info"] == "Admin-written text"  # file has no summary -> untouched
    assert doc["created_at"] == datetime.datetime(2026, 1, 1)


def test_sync_replaces_dead_placeholder_images(db):
    db.workouts.insert_one(
        {
            "slug": "test-press",
            "name": "Test Press",
            "images": ["https://via.placeholder.com/900x1600?text=Push-Up"],
            "muscle_image": "https://via.placeholder.com/900x1600?text=Push-Up+muscle",
        }
    )
    _sync(db, [VALID])
    doc = db.workouts.find_one({"slug": "test-press"})
    assert doc["images"] == ["/static/img/exercises/test-press.svg"]
    assert doc["muscle_image"] is None


def test_sync_never_deletes_and_reports_unmanaged(db):
    db.workouts.insert_one({"slug": "admin-only-exercise", "name": "Admin Only"})
    plan = _sync(db, [VALID])
    assert "admin-only-exercise" in plan["unmanaged"]
    assert db.workouts.find_one({"slug": "admin-only-exercise"}) is not None


def test_sync_skips_admin_edited_exercises_unless_forced(db):
    _sync(db, [VALID])
    synced_at = db.workouts.find_one({"slug": "test-press"})["content_synced_at"]
    db.workouts.update_one(
        {"slug": "test-press"},
        {
            "$set": {
                "name": "Edited In Admin",
                "updated_at": synced_at + datetime.timedelta(hours=1),
            }
        },
    )

    plan = _sync(db, [VALID])
    assert [d["slug"] for d in plan["drifted"]] == ["test-press"]
    assert db.workouts.find_one({"slug": "test-press"})["name"] == "Edited In Admin"

    _sync(db, [VALID], force=True)
    assert db.workouts.find_one({"slug": "test-press"})["name"] == "Test Press"


@pytest.mark.parametrize(
    "uri, hosts, db_name",
    [
        ("mongodb://localhost:27017/NFG", ["localhost"], "NFG"),
        ("mongodb://mongo:27017/NFG", ["mongo"], "NFG"),
        (
            "mongodb+srv://user:secret@cluster0.abc.mongodb.net/NFG?x=1",
            ["cluster0.abc.mongodb.net"],
            "NFG",
        ),
        ("mongodb://u:p@h1:27017,h2:27017/?replicaSet=rs", ["h1", "h2"], None),
    ],
)
def test_uri_parsing_strips_credentials(uri, hosts, db_name):
    assert cs.uri_hosts_and_db(uri) == (hosts, db_name)


# --------------------------------------------------------------------------
# Draft visibility on the public site
# --------------------------------------------------------------------------


def test_drafts_hidden_from_visitors(client, db):
    _sync(db, [VALID])
    assert client.get("/workouts/test-press").status_code == 404
    assert b"Test Press" not in client.get("/workouts/browse").data
    assert b"Test Press" not in client.get("/search?q=Test").data
    assert b"Test Press" not in client.get("/workouts/all").data


def test_published_exercises_visible(client, db):
    _sync(db, [{**VALID, "status": "published"}])
    assert client.get("/workouts/test-press").status_code == 200
    assert b"Test Press" in client.get("/workouts/browse").data


def test_admin_can_preview_drafts(client, db, nfg_app, monkeypatch):
    from werkzeug.security import generate_password_hash

    monkeypatch.setattr(nfg_app, "ADMIN_PASSWORD_HASH", generate_password_hash("pw"))
    _sync(db, [VALID])
    client.post("/login", data={"username": "admin", "password": "pw"})
    resp = client.get("/workouts/test-press")
    assert resp.status_code == 200
    assert b"Draft" in resp.data


def test_apply_to_remote_db_refused_without_flag(monkeypatch, capsys):
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://user:secret@cluster0.example.net/NFG")

    def _no_connect(*_args, **_kwargs):
        raise AssertionError("must not connect before the remote guard")

    monkeypatch.setattr(cs, "connect", _no_connect)
    with pytest.raises(SystemExit) as exc:
        cs.main(["sync", "--apply"])
    assert exc.value.code == 2
    out = capsys.readouterr()
    assert "secret" not in out.out + out.err


def test_detail_page_renders_instructions(client, db):
    data = {
        **VALID,
        "status": "published",
        "setup": ["Set the bench flat."],
        "breathing": "Breathe out as you press.",
        "mistakes": ["Bouncing the weight."],
        "safety": ["Use a spotter."],
    }
    _sync(db, [data])
    html = client.get("/workouts/test-press").get_data(as_text=True)
    for text in (
        "How to do it",
        "Set the bench flat.",
        "Press up.",
        "Breathe out as you press.",
        "Mistakes",
        "Bouncing the weight.",
        "Use a spotter.",
    ):
        assert text in html, text
