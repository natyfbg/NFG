"""The Muscle Mixer body art: generated paths, mapping to the taxonomy, contrast."""

import importlib.util
from pathlib import Path

import bodymap
import bodymap_paths as art
import pytest
import taxonomy as tx

BACKEND = Path(__file__).resolve().parent.parent


def _importer():
    spec = importlib.util.spec_from_file_location(
        "import_bodymap", BACKEND / "scripts" / "import_bodymap.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "view, slug, group",
    [
        ("front", "chest", "chest"),
        ("front", "deltoids", "shoulders"),
        ("back", "deltoids", "shoulders"),  # rear delts live in Shoulders
        ("front", "trapezius", "back"),
        ("back", "trapezius", "back"),
        ("back", "upper-back", "back"),
        ("back", "lower-back", "back"),
        ("front", "biceps", "biceps"),
        ("front", "triceps", "triceps"),
        ("back", "triceps", "triceps"),
        ("front", "forearm", "forearms"),
        ("back", "forearm", "forearms"),
        ("front", "abs", "core"),
        ("front", "obliques", "core"),
        ("front", "quadriceps", "quads"),
        ("front", "adductors", "quads"),
        ("back", "adductors", "quads"),
        ("back", "gluteal", "glutes"),
        ("back", "hamstring", "hamstrings"),
        ("front", "calves", "calves"),
        ("back", "calves", "calves"),
        ("front", "tibialis", "calves"),
    ],
)
def test_region_mapping(view, slug, group):
    assert bodymap.region_group(view, slug) == group


@pytest.mark.parametrize("slug", ["head", "hair", "neck", "hands", "feet", "ankles", "knees"])
def test_decorative_parts_are_not_tappable(slug):
    assert bodymap.region_group("front", slug) is None
    assert slug in bodymap.DECORATIVE


def test_every_muscle_group_can_be_tapped():
    # Adding a group to taxonomy.py without mapping it on the body fails here.
    tappable = {r["group"] for v in bodymap.VIEWS for r in bodymap.body_regions(v) if r["group"]}
    expected = {k for k in tx.MUSCLE_GROUPS if k not in ("full_body", "cardio", "mobility")}
    assert expected <= tappable


def test_every_region_maps_to_a_real_group_or_is_decorative():
    for view in bodymap.VIEWS:
        for region in bodymap.body_regions(view):
            if region["group"] is None:
                assert region["slug"] in bodymap.DECORATIVE, (view, region["slug"])
            else:
                assert region["group"] in tx.MUSCLE_GROUPS


def test_generated_paths_load():
    assert len(art.FRONT) >= 15 and len(art.BACK) >= 12
    for region in art.FRONT + art.BACK:
        assert region["slug"] and region["paths"]
        assert all(p[0] in "Mm" for p in region["paths"])
    assert art.OUTLINE_FRONT.startswith("M") and art.OUTLINE_BACK.startswith("M")


def test_viewbox_crop_holds_every_path():
    imp = _importer()
    for regions, outline, viewbox in (
        (art.FRONT, art.OUTLINE_FRONT, art.VIEWBOX_FRONT),
        (art.BACK, art.OUTLINE_BACK, art.VIEWBOX_BACK),
    ):
        x, y, w, h = (float(v) for v in viewbox.split())
        min_x, min_y, max_x, max_y = imp.union_bounds(
            [outline] + [p for r in regions for p in r["paths"]]
        )
        assert x <= min_x and max_x <= x + w
        assert y <= min_y and max_y <= y + h
        assert h < 1448  # actually cropped


def test_path_bounds_handles_compact_arc_flags():
    imp = _importer()
    # "01" packs both arc flags; the arc bulges no further than its radius.
    box = imp.path_bounds("M0 0a5 5 0 01 10 0z")
    assert box[0] == pytest.approx(0, abs=1e-6) and box[2] == pytest.approx(10, abs=1e-6)
    assert -5.01 <= box[1] <= -4.99 or 4.99 <= box[3] <= 5.01


def test_licence_is_kept():
    text = (BACKEND / "LICENSE-react-native-body-highlighter.txt").read_text(encoding="utf-8")
    assert "MIT" in text and "ELABBASSI Hicham" in text


@pytest.mark.parametrize("key", list(tx.MUSCLE_GROUPS))
def test_chip_colours_pass_contrast_with_white_text(key):
    color = bodymap.readable_on_white(tx.MUSCLE_GROUPS[key]["color"])
    assert bodymap.contrast_with_white(color) >= 4.5


def test_contrast_helper_leaves_passing_colours_alone():
    assert bodymap.readable_on_white("#2F5D8A") == "#2F5D8A"
    assert bodymap.readable_on_white("#B58A1E") != "#B58A1E"  # Core's gold fails as-is
