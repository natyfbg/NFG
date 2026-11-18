"""Session builder: counts by level, secondary lifts, Add options, variety, Mixer tiers."""

import content_sync as cs
import pytest
import session_builder as sb
import taxonomy as tx


@pytest.fixture(scope="module")
def library():
    """Every content file as a synced doc (drafts included: the library as it will be)."""
    exercises, _ = cs.load_exercises()
    return [cs.build_doc(ex) for ex in exercises]


def _pool(library, env):
    return [d for d in library if env in d["environments"]]


@pytest.mark.parametrize("key", ["push", "pull", "lower", "core", "stretch", "full-body"])
@pytest.mark.parametrize("level", sb.LEVELS)
def test_gym_sessions_follow_the_level_counts(library, key, level):
    built = sb.build_session(_pool(library, "gym"), tx.SESSION_PRESETS[key], level)
    low, high = sb.SESSION_COUNTS[level]
    assert low <= len(built["main"]) <= high, [d["name"] for d in built["main"]]
    assert 1 <= len(built["secondary"]) <= 3
    assert all(item["scheme"] for item in built["main"] + built["secondary"])


def test_sessions_never_repeat_an_exercise_in_another_version(library):
    for key in tx.SESSION_PRESETS:
        for level in sb.LEVELS:
            built = sb.build_session(_pool(library, "gym"), tx.SESSION_PRESETS[key], level)
            items = built["main"] + built["secondary"]
            stems = [sb.stem(d["name"]) for d in items]
            assert len(stems) == len(set(stems)), (key, level, stems)


def test_beginner_sessions_skip_advanced_exercises(library):
    for key in ("push", "pull", "lower"):
        built = sb.build_session(_pool(library, "gym"), tx.SESSION_PRESETS[key], "beginner")
        assert all(d["level"] != "Advanced" for d in built["main"] + built["secondary"])


def test_split_days_mix_compounds_and_isolation(library):
    built = sb.build_session(_pool(library, "gym"), tx.SESSION_PRESETS["push"], "intermediate")
    kinds = [sb.kind(d) for d in built["main"]]
    assert kinds[0] == "compound"
    assert "isolation" in kinds  # raises, flies or pushdowns make the cut
    assert kinds == sorted(kinds, key=lambda k: k != "compound")  # compounds first


def test_lower_day_has_a_squat_a_hinge_and_a_lunge(library):
    built = sb.build_session(_pool(library, "gym"), tx.SESSION_PRESETS["lower"], "advanced")
    movements = {d["movement"] for d in built["main"]}
    assert {"squat", "hinge", "lunge"} <= movements


def test_add_options_are_contextual_and_join_secondary(library):
    assert sb.add_options_for("lower") == ["calves", "hips", "core"]
    assert sb.add_options_for("pull") == ["rear_delts", "traps", "forearms"]
    assert sb.add_options_for("push") == ["forearms", "core", "rotator_cuff"]
    plain = sb.build_session(_pool(library, "gym"), tx.SESSION_PRESETS["pull"], "intermediate")
    added = sb.build_session(
        _pool(library, "gym"), tx.SESSION_PRESETS["pull"], "intermediate", ["traps"]
    )
    extra = [d for d in added["secondary"] if d.get("added_by") == "traps"]
    assert len(extra) == 1 and "traps" in extra[0]["muscles"]["primary"]
    assert added["count"] == plain["count"] + 1


def test_home_sessions_only_use_home_equipment(library):
    built = sb.build_session(_pool(library, "home"), tx.SESSION_PRESETS["push"], "intermediate")
    assert built["main"]
    assert all("home" in d["environments"] for d in built["main"] + built["secondary"])


def test_stems_ignore_equipment_and_grip():
    assert sb.stem("Smith Machine Bench Press") == sb.stem("Dumbbell Bench Press") == "bench press"
    assert sb.stem("Close-Grip Push-Up") == "push up"
    assert sb.stem("Chest Dip") == sb.stem("Triceps Dip") == "dip"
    assert sb.stem("Seated Cable Row") != sb.stem("Barbell Row")  # different exercises


@pytest.mark.parametrize("level", sb.LEVELS)
def test_mixer_counts_and_tiers(library, level):
    groups = ["back", "biceps", "shoulders"]
    built = sb.build_mix(_pool(library, "gym"), groups, level)
    low, high = sb.MIXER_COUNTS[level]
    assert low <= built["count"] <= high
    tiers = built["tiers"]
    assert tiers[0]["hits"] == 3 and tiers[0]["label"] == "Hits all of them"
    assert [t["hits"] for t in tiers] == sorted((t["hits"] for t in tiers), reverse=True)
    items = [d for t in tiers for d in t["items"]]
    for g in groups:  # every picked group is a main target somewhere
        assert any(g in d["mix_primary"] for d in items), g


def test_mixer_single_group_has_no_tier_labels(library):
    built = sb.build_mix(_pool(library, "gym"), ["glutes"], "beginner")
    assert len(built["tiers"]) == 1 and built["tiers"][0]["label"] == ""
    assert all(d["level"] != "Advanced" for d in built["tiers"][0]["items"])
