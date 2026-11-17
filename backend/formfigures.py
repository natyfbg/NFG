"""Illustrated form figures for the three form steps (Start · Movement · Finish).

Nate picked consistent illustrated figures over photos (2026-11-16). Each exercise maps
to a movement *family* (squat, hinge, bench press, curl, ...); a family is two poses,
start and end, drawn on one rig in one style: Start shows the start pose, Movement the
end pose with a faint ghost of the start and an arrow, Finish the start pose again with a
ghost of the end (the way back). Holds (plank, stretches) show the hold twice instead.

Men demonstrate upper-body exercises, women lower-body ones (same rule as the muscle
cards). Equipment is drawn from the exercise's own equipment list (barbell, dumbbells,
cable, band, bench, bar...). A content file can force a family with `form_pose:`.

Pure functions, no Flask or Mongo, like taxonomy.py. Output is a small inline SVG.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

W, H = 240, 300  # 4:5
FLOOR = 274
BAR_Y = 36
MARGIN = 14

# Palette (dark card)
SKIN = "#D29A73"
SKIN_FAR = "#A87455"
HAIR = "#2A1E18"
SHIRT = "#59615D"
SHIRT_FAR = "#3F4542"
SHORTS = "#1E2221"
SHORTS_FAR = "#151817"
SHOE = "#E9E5DA"
SHOE_FAR = "#B9B5AB"
METAL = "#C4C8C5"
IRON = "#3A3F3C"
IRON_RIM = "#6C726E"
PAD = "#4A514D"
FRAME = "#7C837F"
CABLE = "#9AA19D"
GHOST = "#FFFFFF"
FLOOR_LINE = "#3A3F3C"

# -----------------------------------------------------------------------------
# Rig
# -----------------------------------------------------------------------------
RIG = {
    "male": dict(
        head=10.5,
        neck=6,
        torso=48,
        ua=28,
        fa=25,
        hand=6,
        thigh=42,
        shin=40,
        foot=14,
        w_torso=23,
        w_ua=10,
        w_fa=8.5,
        w_thigh=14.5,
        w_shin=11,
        w_foot=6.5,
        shoulder=19,
        hip=11,
    ),
    "female": dict(
        head=10,
        neck=6.5,
        torso=45,
        ua=26.5,
        fa=23.5,
        hand=5.5,
        thigh=41,
        shin=39,
        foot=13,
        w_torso=19.5,
        w_ua=8.5,
        w_fa=7.2,
        w_thigh=14.5,
        w_shin=10.5,
        w_foot=6,
        shoulder=15.5,
        hip=12.5,
    ),
}

SIDE_DEFAULTS = dict(t=0.0, n=0.0, au=0.0, al=0.0, lu=0.0, ll=0.0, lf=90.0)


def _down(a: float) -> Tuple[float, float]:
    r = math.radians(a)
    return math.sin(r), math.cos(r)


def _up(a: float) -> Tuple[float, float]:
    r = math.radians(a)
    return math.sin(r), -math.cos(r)


def _add(p, d, k):
    return (p[0] + d[0] * k, p[1] + d[1] * k)


def _full_pose(p: dict) -> dict:
    q = dict(SIDE_DEFAULTS)
    q.update(p)
    for near, far in (("au", "bu"), ("al", "bl"), ("lu", "mu"), ("ll", "ml"), ("lf", "mf")):
        q.setdefault(far, q[near])
    return q


def side_joints(pose: dict, sex: str, arm_rel: bool = False) -> Dict[str, Tuple[float, float]]:
    """Joint positions for a side-view pose (facing right), hip at the origin."""
    r = RIG[sex]
    p = _full_pose(pose)
    j: Dict[str, Tuple[float, float]] = {"hip": (0.0, 0.0)}
    j["shoulder"] = _add(j["hip"], _up(p["t"]), r["torso"])
    j["neck"] = _add(j["shoulder"], _up(p["t"] + p["n"]), r["neck"])
    j["head"] = _add(j["neck"], _up(p["t"] + p["n"]), r["head"] * 0.95)
    off = p["t"] if arm_rel else 0.0
    for side, (u, low) in (("a", ("au", "al")), ("b", ("bu", "bl"))):
        elbow = _add(j["shoulder"], _down(p[u] + off), r["ua"])
        wrist = _add(elbow, _down(p[low] + off), r["fa"])
        j[f"{side}_elbow"], j[f"{side}_wrist"] = elbow, wrist
        j[f"{side}_hand"] = _add(wrist, _down(p[low] + off), r["hand"])
    for side, (u, low, f) in (("l", ("lu", "ll", "lf")), ("m", ("mu", "ml", "mf"))):
        knee = _add(j["hip"], _down(p[u]), r["thigh"])
        ankle = _add(knee, _down(p[low]), r["shin"])
        j[f"{side}_knee"], j[f"{side}_ankle"] = knee, ankle
        j[f"{side}_toe"] = _add(ankle, _down(p[f]), r["foot"])
        j[f"{side}_heel"] = _add(ankle, _down(p[f]), -r["foot"] * 0.28)
    j["_t"] = (p["t"], p["n"])
    return j


FRONT_DEFAULTS = dict(au=8.0, al=8.0, lu=4.0, ll=4.0, sh=0.0, rot=0.0, n=0.0)


def front_joints(pose: dict, sex: str) -> Dict[str, Tuple[float, float]]:
    """Front view: symmetric unless the right side ('bu'...) is given. Angles open outward."""
    r = RIG[sex]
    p = dict(FRONT_DEFAULTS)
    p.update(pose)
    for near, far in (("au", "bu"), ("al", "bl"), ("lu", "mu"), ("ll", "ml")):
        p.setdefault(far, p[near])
    j: Dict[str, Tuple[float, float]] = {"hip": (0.0, 0.0)}
    top = (0.0, -r["torso"])
    j["neck"] = (0.0, top[1] - r["neck"] - p["sh"] * 0.3)
    j["head"] = (0.0, j["neck"][1] - r["head"] * 0.95)
    for sign, (u, low, lu, ll) in ((-1, ("au", "al", "lu", "ll")), (1, ("bu", "bl", "mu", "ml"))):
        key = "a" if sign < 0 else "b"
        sh = (sign * r["shoulder"], top[1] + 3 - p["sh"])
        elbow = (
            sh[0] + sign * math.sin(math.radians(p[u])) * r["ua"],
            sh[1] + math.cos(math.radians(p[u])) * r["ua"],
        )
        wrist = (
            elbow[0] + sign * math.sin(math.radians(p[low])) * r["fa"],
            elbow[1] + math.cos(math.radians(p[low])) * r["fa"],
        )
        hand = (
            wrist[0] + sign * math.sin(math.radians(p[low])) * r["hand"],
            wrist[1] + math.cos(math.radians(p[low])) * r["hand"],
        )
        j[f"{key}_shoulder"], j[f"{key}_elbow"], j[f"{key}_wrist"], j[f"{key}_hand"] = (
            sh,
            elbow,
            wrist,
            hand,
        )
        leg = "l" if sign < 0 else "m"
        hp = (sign * r["hip"] * 0.62, 2.0)
        if "tf" in p:  # seated: thighs point at the viewer, so they look short
            knee = (
                hp[0] + sign * math.sin(math.radians(p[lu])) * r["thigh"],
                hp[1] + p["tf"] * r["thigh"],
            )
        else:
            knee = (
                hp[0] + sign * math.sin(math.radians(p[lu])) * r["thigh"],
                hp[1] + math.cos(math.radians(p[lu])) * r["thigh"],
            )
        ankle = (
            knee[0] + sign * math.sin(math.radians(p[ll])) * r["shin"],
            knee[1] + math.cos(math.radians(p[ll])) * r["shin"],
        )
        j[f"{leg}_hipj"], j[f"{leg}_knee"], j[f"{leg}_ankle"] = hp, knee, ankle
        j[f"{leg}_toe"] = (ankle[0] + sign * 5, ankle[1] + 2)
        j[f"{leg}_heel"] = (ankle[0] - sign * 1, ankle[1] + 2)
    if p.get("bend"):  # side bend: upper body only, about the hips
        c, s = math.cos(math.radians(p["bend"])), math.sin(math.radians(p["bend"]))
        for k in list(j):
            if k in ("neck", "head") or k[:2] in ("a_", "b_"):
                x, y = j[k]
                j[k] = (x * c - y * s, x * s + y * c)
    if p["rot"]:
        c, s = math.cos(math.radians(p["rot"])), math.sin(math.radians(p["rot"]))
        for k, (x, y) in list(j.items()):
            j[k] = (x * c - y * s, x * s + y * c)
    j["_rot"] = (p["rot"] + p.get("bend", 0.0), p["n"])
    return j


# -----------------------------------------------------------------------------
# Families: start and end poses. Angles in degrees.
#   side view: t torso (0 upright, + leans toward the facing side, 90 = horizontal head
#   forward, -90 = lying face up head behind); n neck; au/al near arm upper/lower and
#   lu/ll/lf near leg thigh/shin/foot, measured from straight down (+ = forward,
#   180 = overhead); bu/bl, mu/ml/mf the far limbs (default: same as near).
#   front view: au/al arms and lu/ll legs open outward from straight down; sh shrug;
#   rot rotates the whole figure.
# -----------------------------------------------------------------------------
STAND = {}
F = {
    # ---- legs
    "squat": dict(
        start={},
        end=dict(t=38, lu=86, ll=-32, n=-12),
        fix="l_ankle",
        arms={
            "barbell": dict(rel=True, au=-50, al=160),
            "dumbbell": dict(rel=True, au=20, al=172),
            "kettlebell": dict(rel=True, au=20, al=172),
            None: dict(au=90, al=90),
        },
    ),
    "front_squat": dict(
        start={},
        end=dict(t=22, lu=92, ll=-38, n=-10),
        fix="l_ankle",
        arms={"*": dict(rel=True, au=100, al=-114)},
    ),
    "sumo_squat": dict(
        view="front",
        start=dict(lu=14, ll=10),
        end=dict(lu=62, ll=-2),
        fix="hip_floor",
        arms={"*": dict(au=-6, al=-8)},
    ),
    "wall_sit": dict(
        start=dict(t=0, lu=50, ll=-20),
        end=dict(t=0, lu=90, ll=0),
        fix="l_ankle",
        hold=True,
        props=["wall_back"],
        arms={"*": dict(au=0, al=0)},
    ),
    "leg_press": dict(
        start=dict(t=-50, lu=150, ll=60, lf=150),
        end=dict(t=-50, lu=118, ll=118, lf=170),
        fix="hip",
        props=["leg_press"],
        arms={"*": dict(rel=True, au=10, al=40)},
    ),
    "hack_squat": dict(
        start=dict(t=-12),
        end=dict(t=-12, lu=80, ll=-30),
        fix="l_ankle",
        props=["hack"],
        arms={"*": dict(rel=True, au=-30, al=170)},
    ),
    "lunge": dict(
        start={},
        end=dict(t=4, lu=82, ll=-4, mu=-8, ml=-84, mf=72),
        fix="l_ankle",
        arms={"*": dict(au=0, al=0)},
    ),
    "split_squat_rear": dict(
        start=dict(lu=14, ll=-6, mu=-40, ml=-110, mf=-60),
        end=dict(t=8, lu=80, ll=-10, mu=-8, ml=-100, mf=-60),
        fix="l_ankle",
        pin="m_ankle",
        props=["bench_rear_foot"],
        arms={"*": dict(au=0, al=0)},
    ),
    "step_up": dict(
        start=dict(lu=95, ll=-5, mu=0, ml=0),
        end=dict(t=4, lu=0, ll=0, mu=-10, ml=30),
        fix="l_ankle",
        props=["box_front"],
        lift_end=44,
        arms={"*": dict(au=0, al=0)},
    ),
    "lateral_lunge": dict(
        view="front",
        start=dict(lu=10, ll=6),
        end=dict(lu=70, ll=-10, mu=38, ml=38),
        fix="hip_floor",
        arms={"*": dict(au=-10, al=-30)},
    ),
    "pistol": dict(
        start=dict(au=90, al=90, mu=90, ml=90),
        end=dict(t=42, lu=98, ll=-38, mu=88, ml=88, au=90, al=90),
        fix="l_ankle",
    ),
    "calf_raise": dict(start={}, end=dict(lf=145), fix="l_toe", arms={"*": dict(au=0, al=0)}),
    "seated_calf": dict(
        start=dict(lu=90, ll=0),
        end=dict(lu=86, ll=-6, lf=140),
        fix="hip",
        props=["seat"],
        arms={"*": dict(au=40, al=90)},
    ),
    "tibialis_raise": dict(
        start=dict(t=-8, lu=-8, ll=-8),
        end=dict(t=-8, lu=-8, ll=-8, lf=55),
        fix="l_heel",
        props=["wall_back"],
        arms={"*": dict(au=0, al=0)},
    ),
    "leg_extension": dict(
        start=dict(t=-10, lu=90, ll=5, lf=100),
        end=dict(t=-10, lu=90, ll=88, lf=170),
        fix="hip",
        props=["seat_back"],
        arms={"*": dict(au=20, al=40)},
        hip_y=66,
    ),
    "leg_curl_lying": dict(
        start=dict(t=96, lu=-84, ll=-86, lf=-170, n=-20),
        end=dict(t=96, lu=-84, ll=14, lf=-80, n=-20),
        fix="hip",
        props=["pad_lying"],
        arms={"*": dict(rel=True, au=150, al=130)},
        hip_y=50,
    ),
    "leg_curl_seated": dict(
        start=dict(t=-10, lu=90, ll=86, lf=170),
        end=dict(t=-10, lu=90, ll=-10, lf=90),
        fix="hip",
        props=["seat_back"],
        arms={"*": dict(au=30, al=60)},
        hip_y=66,
    ),
    "nordic": dict(
        start=dict(t=0, lu=0, ll=-90, lf=-110, au=0, al=0),
        end=dict(t=58, lu=-58, ll=-90, lf=-110, au=40, al=40, n=-10),
        fix="l_knee",
        props=["mat"],
    ),
    "sissy": dict(
        start=dict(t=0),
        end=dict(t=-28, lu=62, ll=-60, lf=140),
        fix="l_toe",
        arms={"*": dict(au=60, al=60)},
    ),
    "abduction_band": dict(
        view="front",
        start=dict(lu=6, ll=6),
        end=dict(lu=22, ll=22),
        fix="hip_floor",
        arms={"*": dict(au=10, al=10)},
        band="knees",
    ),
    "abduction_machine": dict(
        view="front",
        start=dict(lu=6, ll=0, tf=0.25),
        end=dict(lu=32, ll=0, tf=0.25),
        fix="hip_floor",
        props=["seat_front"],
        arms={"*": dict(au=20, al=-20)},
    ),
    "adduction_machine": dict(
        view="front",
        start=dict(lu=32, ll=0, tf=0.25),
        end=dict(lu=6, ll=0, tf=0.25),
        fix="hip_floor",
        props=["seat_front"],
        arms={"*": dict(au=20, al=-20)},
    ),
    "clamshell": dict(
        start=dict(t=-90, lu=120, ll=-40, mu=120, ml=-40, n=10),
        end=dict(t=-90, lu=120, ll=-40, mu=160, ml=-10, n=10),
        fix="hip",
        props=["mat"],
        arms={"*": dict(rel=True, au=180, al=180)},
    ),
    "kickback_quad": dict(
        start=dict(t=90, lu=0, ll=-90, mu=0, ml=-90, n=-10, au=0, al=0),
        end=dict(t=90, lu=0, ll=-90, mu=-100, ml=-130, n=-10, au=0, al=0),
        fix="hip",
        props=["mat"],
    ),
    "cable_kickback": dict(
        start=dict(t=30, mu=10, ml=0),
        end=dict(t=30, mu=-50, ml=-30),
        fix="l_ankle",
        cable=("behind", "low"),
        cable_from="m_ankle",
        arms={"*": dict(au=70, al=80)},
    ),
    # ---- hinge
    "deadlift": dict(
        start=dict(t=55, lu=68, ll=-20, n=-20), end={}, fix="l_ankle", arms={"*": dict(au=0, al=0)}
    ),
    "rdl": dict(
        start={}, end=dict(t=76, lu=12, ll=-4, n=-30), fix="l_ankle", arms={"*": dict(au=0, al=0)}
    ),
    "single_leg_rdl": dict(
        start={},
        end=dict(t=80, lu=8, ll=-4, mu=-78, ml=-78, n=-30),
        fix="l_ankle",
        arms={"*": dict(au=0, al=0)},
    ),
    "rack_pull": dict(
        start=dict(t=40, lu=28, ll=-8, n=-15), end={}, fix="l_ankle", arms={"*": dict(au=0, al=0)}
    ),
    "swing": dict(
        start=dict(t=62, lu=40, ll=-14, au=-24, al=-24, n=-20),
        end=dict(au=92, al=92),
        fix="l_ankle",
    ),
    "pull_through": dict(
        start=dict(t=70, lu=30, ll=-10, au=-28, al=-28, n=-25),
        end=dict(au=-4, al=-4),
        fix="l_ankle",
        cable=("behind", "low"),
    ),
    "hip_thrust": dict(
        start=dict(t=-70, lu=132, ll=0, n=30),
        end=dict(t=-90, lu=90, ll=0, n=25),
        fix="l_ankle",
        props=["bench_behind"],
        arms={
            "barbell": dict(au=110, al=60),
            "smith": dict(au=110, al=60),
            "dumbbell": dict(au=110, al=60),
            None: dict(rel=True, au=10, al=10),
        },
    ),
    "bridge": dict(
        start=dict(t=-92, lu=140, ll=-22, n=0),
        end=dict(t=-112, lu=112, ll=-2, n=10),
        fix="l_ankle",
        props=["mat"],
        arms={"*": dict(au=-80, al=-90)},
    ),
    "back_extension": dict(
        start=dict(t=150, lu=-45, ll=-45, lf=45, n=0),
        end=dict(t=48, lu=-45, ll=-45, lf=45, n=0),
        fix="l_ankle",
        props=["hyper"],
        arms={"*": dict(rel=True, au=15, al=120)},
    ),
    "superman": dict(
        start=dict(t=90, lu=-90, ll=-90, lf=-170, au=90, al=90, n=-5),
        end=dict(t=78, lu=-100, ll=-100, lf=-180, au=100, al=100, n=-15),
        fix="hip",
        props=["mat"],
        hold=True,
    ),
    # ---- push
    "bench_press": dict(
        start=dict(t=-90, lu=70, ll=0, au=180, al=180, n=0),
        end=dict(t=-90, lu=70, ll=0, au=118, al=180, n=0),
        fix="hip",
        props=["bench_flat"],
    ),
    "incline_press": dict(
        start=dict(t=-58, lu=80, ll=0, au=168, al=168),
        end=dict(t=-58, lu=80, ll=0, au=112, al=174),
        fix="hip",
        props=["bench_incline"],
    ),
    "floor_press": dict(
        start=dict(t=-90, lu=140, ll=-20, au=180, al=180),
        end=dict(t=-90, lu=140, ll=-20, au=95, al=180),
        fix="hip",
        props=["mat"],
    ),
    "machine_press": dict(
        start=dict(t=-8, lu=90, ll=0, au=60, al=95),
        end=dict(t=-8, lu=90, ll=0, au=88, al=90),
        fix="hip",
        props=["seat_back"],
    ),
    "standing_press_fwd": dict(
        start=dict(t=10, au=50, al=95, lu=10, ll=0, mu=-12, ml=-12),
        end=dict(t=10, au=88, al=88, lu=10, ll=0, mu=-12, ml=-12),
        fix="l_ankle",
        cable=("behind", "mid"),
    ),
    "push_up": dict(
        start=dict(t=78, au=0, al=0, lu=-78, ll=-78, lf=10, n=-15),
        end=dict(t=88, au=-62, al=8, lu=-88, ll=-88, lf=10, n=-15),
        fix="l_toe",
    ),
    "knee_push_up": dict(
        start=dict(t=64, au=0, al=0, lu=-64, ll=-90, lf=-110, n=-15),
        end=dict(t=80, au=-60, al=8, lu=-80, ll=-90, lf=-110, n=-15),
        fix="l_knee",
    ),
    "incline_push_up": dict(
        start=dict(t=56, au=8, al=8, lu=-56, ll=-56, lf=10, n=-15),
        end=dict(t=66, au=-55, al=20, lu=-66, ll=-66, lf=10, n=-15),
        fix="l_toe",
        props=["bench_hands"],
    ),
    "wall_push_up": dict(
        start=dict(t=20, au=95, al=95, lu=-20, ll=-20, lf=60),
        end=dict(t=32, au=40, al=150, lu=-32, ll=-32, lf=55),
        fix="l_toe",
        props=["wall_front"],
    ),
    "decline_push_up": dict(
        start=dict(t=100, au=0, al=0, lu=-100, ll=-100, lf=-20, n=-20),
        end=dict(t=106, au=-60, al=10, lu=-106, ll=-106, lf=-20, n=-20),
        fix="l_hand",
        props=["bench_feet"],
    ),
    "pike_push_up": dict(
        start=dict(t=130, au=-6, al=-6, lu=-40, ll=-40, lf=-120, n=-30),
        end=dict(t=150, au=-56, al=20, lu=-40, ll=-40, lf=-120, n=-30),
        fix="l_toe",
    ),
    "dip": dict(
        start=dict(t=6, au=0, al=0, lu=10, ll=-60, lf=10),
        end=dict(t=22, au=-62, al=8, lu=10, ll=-60, lf=10),
        fix="l_hand",
        anchor="dip",
        props=["dip_bars"],
    ),
    "bench_dip": dict(
        start=dict(t=0, au=-10, al=-10, lu=90, ll=0),
        end=dict(t=6, au=-72, al=4, lu=72, ll=-6),
        fix="l_hand",
        props=["bench_behind_hands"],
    ),
    "overhead_press": dict(start=dict(au=20, al=176), end=dict(au=172, al=176), fix="l_ankle"),
    "seated_press": dict(
        start=dict(t=-5, lu=90, ll=0, au=20, al=176),
        end=dict(t=-5, lu=90, ll=0, au=172, al=176),
        fix="hip",
        props=["seat_upright"],
    ),
    "front_press_view": dict(
        view="front", start=dict(au=88, al=178), end=dict(au=160, al=176), fix="hip_floor"
    ),
    "landmine_press": dict(
        start=dict(t=12, lu=12, ll=0, mu=-14, ml=-14, au=16, al=168),
        end=dict(t=12, lu=12, ll=0, mu=-14, ml=-14, au=130, al=136),
        fix="l_ankle",
        props=["landmine"],
    ),
    "handstand": dict(
        start=dict(t=180, au=0, al=0, lu=180, ll=180, lf=180, n=0),
        end=dict(t=180, au=0, al=0, lu=180, ll=180, lf=180, n=0),
        fix="l_hand",
        hold=True,
        props=["wall_back_tall"],
    ),
    "handstand_push_up": dict(
        start=dict(t=180, au=0, al=0, lu=180, ll=180, lf=180),
        end=dict(t=174, au=56, al=-4, lu=178, ll=178, lf=178),
        fix="l_hand",
        props=["wall_back_tall"],
    ),
    "wall_walk": dict(
        start=dict(t=84, au=0, al=0, lu=-84, ll=-84, lf=10, n=-15),
        end=dict(t=172, au=0, al=0, lu=172, ll=172, lf=180, n=-10),
        fix="l_hand",
        props=["wall_back_tall"],
    ),
    "fly": dict(
        view="front",
        start=dict(au=92, al=96),
        end=dict(au=28, al=-12),
        fix="hip_floor",
        cable=("sides", "mid"),
    ),
    "fly_lying": dict(
        view="front",
        start=dict(au=92, al=96, rot=-90, lu=4, ll=4),
        end=dict(au=26, al=-16, rot=-90, lu=4, ll=4),
        fix="hip",
        anchor="center",
        props=["bench_top"],
    ),
    "pullover": dict(
        start=dict(t=-90, lu=70, ll=0, au=175, al=180),
        end=dict(t=-90, lu=70, ll=0, au=262, al=268),
        fix="hip",
        props=["bench_flat"],
    ),
    # ---- pull
    "row_bent": dict(
        start=dict(t=62, lu=22, ll=-10, n=-20, au=0, al=0),
        end=dict(t=62, lu=22, ll=-10, n=-20, au=-62, al=8),
        fix="l_ankle",
    ),
    "row_one_arm": dict(
        start=dict(t=78, lu=-6, ll=-6, mu=60, ml=-88, mf=-110, au=0, al=0, bu=0, bl=0, n=-20),
        end=dict(t=78, lu=-6, ll=-6, mu=60, ml=-88, mf=-110, au=-58, al=6, bu=0, bl=0, n=-20),
        fix="l_ankle",
        props=["bench_knee"],
    ),
    "row_chest_supported": dict(
        start=dict(t=48, lu=60, ll=-10, au=0, al=0, n=-10),
        end=dict(t=48, lu=60, ll=-10, au=-60, al=6, n=-10),
        fix="l_ankle",
        props=["bench_chest"],
    ),
    "row_seated": dict(
        start=dict(t=10, lu=82, ll=70, lf=150, au=84, al=88),
        end=dict(t=-6, lu=82, ll=70, lf=150, au=-30, al=86),
        fix="hip",
        props=["row_seat"],
        cable=("front", "mid"),
    ),
    "row_inverted": dict(
        start=dict(t=-76, lu=-110, ll=-110, lf=-200, au=180, al=180, n=10),
        end=dict(t=-82, lu=-100, ll=-100, lf=-190, au=150, al=230, n=10),
        fix="l_heel",
        props=["bar_low"],
    ),
    "face_pull": dict(
        start=dict(t=-4, au=96, al=96),
        end=dict(t=-4, au=86, al=200),
        fix="l_ankle",
        cable=("front", "high"),
    ),
    "pull_apart": dict(
        view="front",
        start=dict(au=88, al=88),
        end=dict(au=92, al=96),
        fix="hip_floor",
        band="hands",
    ),
    "reverse_fly": dict(
        view="front",
        start=dict(au=8, al=4),
        end=dict(au=86, al=88),
        fix="hip_floor",
        cable=("sides", "mid"),
    ),
    "pulldown": dict(
        view="front",
        start=dict(au=158, al=178, lu=14, ll=-4, tf=0.25),
        end=dict(au=56, al=172, lu=14, ll=-4, tf=0.25),
        fix="hip_floor",
        props=["seat_front_low"],
        cable=("top", "high"),
    ),
    "pull_up": dict(
        view="front",
        start=dict(au=160, al=178, lu=4, ll=-10),
        end=dict(au=52, al=168, lu=4, ll=-10),
        fix="l_hand",
        anchor="hands",
        props=["pullup_bar"],
    ),
    "chin_up": dict(
        start=dict(au=180, al=180, lu=8, ll=-30, lf=60),
        end=dict(au=50, al=200, lu=14, ll=-30, lf=60, t=-8),
        fix="l_hand",
        anchor="hands",
        props=["pullup_bar_side"],
    ),
    "dead_hang": dict(
        view="front",
        start=dict(au=168, al=176, lu=4, ll=-6),
        end=dict(au=168, al=176, lu=4, ll=-6, sh=-4),
        fix="l_hand",
        anchor="hands",
        props=["pullup_bar"],
        hold=True,
    ),
    "straight_arm_pulldown": dict(
        start=dict(t=24, lu=14, ll=-2, au=136, al=136),
        end=dict(t=24, lu=14, ll=-2, au=-6, al=-6),
        fix="l_ankle",
        cable=("front", "high"),
    ),
    "shrug": dict(
        view="front", start=dict(au=4, al=4), end=dict(au=4, al=4, sh=8), fix="hip_floor"
    ),
    "upright_row": dict(
        view="front",
        start=dict(au=4, al=-8),
        end=dict(au=96, al=-70),
        fix="hip_floor",
        cable=("center", "low"),
    ),
    # ---- arms
    "curl": dict(
        start=dict(au=0, al=0), end=dict(au=12, al=150), fix="l_ankle", cable=("front", "low")
    ),
    "curl_incline": dict(
        start=dict(t=-36, lu=88, ll=0, au=-12, al=-12),
        end=dict(t=-36, lu=88, ll=0, au=-8, al=140),
        fix="hip",
        props=["bench_incline"],
    ),
    "preacher_curl": dict(
        start=dict(t=6, lu=90, ll=0, au=44, al=44),
        end=dict(t=6, lu=90, ll=0, au=44, al=170),
        fix="hip",
        props=["preacher"],
    ),
    "concentration_curl": dict(
        start=dict(t=40, lu=82, ll=-4, mu=70, ml=-14, au=4, al=4),
        end=dict(t=40, lu=82, ll=-4, mu=70, ml=-14, au=4, al=150),
        fix="hip",
        props=["seat"],
    ),
    "pushdown": dict(
        start=dict(t=8, au=4, al=110),
        end=dict(t=8, au=4, al=6),
        fix="l_ankle",
        cable=("front", "high"),
    ),
    "overhead_extension": dict(
        start=dict(au=168, al=10), end=dict(au=172, al=176), fix="l_ankle", cable=("behind", "mid")
    ),
    "skull_crusher": dict(
        start=dict(t=-90, lu=70, ll=0, au=170, al=170),
        end=dict(t=-90, lu=70, ll=0, au=160, al=36),
        fix="hip",
        props=["bench_flat"],
    ),
    "kickback": dict(
        start=dict(t=62, lu=22, ll=-10, n=-20, au=-62, al=8),
        end=dict(t=62, lu=22, ll=-10, n=-20, au=-62, al=-112),
        fix="l_ankle",
        cable=("front", "low"),
    ),
    "wrist_curl": dict(
        start=dict(t=30, lu=88, ll=-4, au=40, al=88),
        end=dict(t=30, lu=88, ll=-4, au=40, al=118),
        fix="hip",
        props=["seat"],
    ),
    "front_raise": dict(
        start=dict(au=4, al=4), end=dict(au=88, al=90), fix="l_ankle", cable=("behind", "low")
    ),
    "lateral_raise": dict(
        view="front",
        start=dict(au=8, al=10),
        end=dict(au=86, al=90),
        fix="hip_floor",
        cable=("front", "low"),
    ),
    "external_rotation": dict(
        view="front",
        start=dict(au=4, al=-80, bu=4, bl=4),
        end=dict(au=4, al=-10, bu=4, bl=4),
        fix="hip_floor",
        cable=("front", "mid"),
    ),
    "carry": dict(
        start=dict(lu=18, ll=0, mu=-14, ml=-24, mf=110),
        end=dict(lu=-14, ll=-24, lf=110, mu=18, ml=0),
        fix="hip_floor_side",
        arms={"*": dict(au=0, al=0, bu=0, bl=0)},
    ),
    "grip_hold": dict(start=dict(au=0, al=0), end=dict(au=0, al=0), fix="l_ankle", hold=True),
    # ---- core
    "plank": dict(
        start=dict(t=88, au=0, al=90, lu=-40, ll=-90, lf=-110, n=-10),
        end=dict(t=84, au=0, al=90, lu=-84, ll=-84, lf=10, n=-10),
        fix="l_elbow",
        hold=True,
        props=["mat"],
    ),
    "side_plank": dict(
        view="front",
        start=dict(rot=-74, au=-10, al=60, bu=170, bl=170, lu=2, ll=2),
        end=dict(rot=-74, au=-10, al=60, bu=10, bl=10, lu=2, ll=2),
        fix="l_elbow",
        hold=True,
        props=["mat"],
    ),
    "crunch": dict(
        start=dict(t=-90, lu=140, ll=-20, n=0, au=150, al=-20),
        end=dict(t=-58, lu=140, ll=-20, n=-6, au=180, al=-20),
        fix="hip",
        props=["mat"],
        arm_rel=True,
    ),
    "cable_crunch": dict(
        start=dict(t=12, lu=4, ll=-90, lf=-110, au=170, al=-30, n=-10),
        end=dict(t=72, lu=4, ll=-90, lf=-110, au=170, al=-30, n=-25),
        fix="l_knee",
        cable=("front", "high"),
        arm_rel=True,
    ),
    "leg_raise": dict(
        start=dict(t=-90, lu=86, ll=86, lf=180, au=-90, al=-90),
        end=dict(t=-90, lu=176, ll=176, lf=260, au=-90, al=-90),
        fix="hip",
        props=["mat"],
    ),
    "hanging_raise": dict(
        start=dict(au=180, al=180, lu=4, ll=4, lf=90),
        end=dict(au=180, al=180, lu=92, ll=92, lf=180, t=-6),
        fix="l_hand",
        anchor="hands",
        props=["pullup_bar_side"],
    ),
    "hanging_knee_raise": dict(
        start=dict(au=180, al=180, lu=4, ll=4, lf=90),
        end=dict(au=180, al=180, lu=96, ll=0, lf=90, t=-6),
        fix="l_hand",
        anchor="hands",
        props=["pullup_bar_side"],
    ),
    "v_up": dict(
        start=dict(t=-90, lu=90, ll=90, lf=170, au=-90, al=-90),
        end=dict(t=-40, lu=140, ll=140, lf=230, au=-40 + 180 - 30, al=150),
        fix="hip",
        props=["mat"],
    ),
    "dead_bug": dict(
        start=dict(t=-90, lu=180, ll=90, au=180, al=180, mu=180, ml=90),
        end=dict(t=-90, lu=180, ll=90, au=-96, al=-96, bu=180, bl=180, mu=94, ml=94, mf=180),
        fix="hip",
        props=["mat"],
    ),
    "russian_twist": dict(
        start=dict(t=-50, lu=140, ll=60, lf=150, au=60, al=100),
        end=dict(t=-50, lu=140, ll=60, lf=150, au=40, al=30),
        fix="hip",
        props=["mat"],
    ),
    "mountain_climber": dict(
        start=dict(t=78, au=0, al=0, lu=-78, ll=-78, lf=10, n=-15),
        end=dict(t=78, au=0, al=0, lu=48, ll=-34, mu=-78, ml=-78, mf=10, n=-15),
        fix="l_hand",
    ),
    "bird_dog": dict(
        start=dict(t=90, au=0, al=0, lu=0, ll=-90, lf=-110, n=-10),
        end=dict(
            t=90, au=90, al=90, bu=0, bl=0, lu=0, ll=-90, lf=-110, mu=-90, ml=-90, mf=-110, n=-10
        ),
        fix="l_knee",
        props=["mat"],
        hold=True,
    ),
    "hollow_hold": dict(
        start=dict(t=-90, lu=140, ll=-20, au=180, al=180),
        end=dict(t=-78, lu=102, ll=102, lf=190, au=-100, al=-100, n=-10),
        fix="hip",
        props=["mat"],
        hold=True,
    ),
    "rollout": dict(
        start=dict(t=55, au=0, al=0, lu=0, ll=-90, lf=-110, n=-20),
        end=dict(t=78, au=82, al=82, lu=-78, ll=-90, lf=-110, n=-15),
        fix="l_knee",
        props=["ab_wheel", "mat"],
    ),
    "pallof": dict(
        start=dict(au=0, al=110), end=dict(au=88, al=88), fix="l_ankle", cable=("side", "mid")
    ),
    "woodchop": dict(
        view="front",
        start=dict(au=150, al=160, bu=-150, bl=-160),
        end=dict(au=-30, al=-30, bu=30, bl=30),
        fix="hip_floor",
        cable=("side_high", "high"),
    ),
    "side_bend": dict(
        view="front", start=dict(au=4, al=4), end=dict(au=4, al=4, bend=-16), fix="hip_floor"
    ),
    "l_sit": dict(
        start=dict(t=0, lu=90, ll=90, lf=170, au=0, al=0),
        end=dict(t=0, lu=90, ll=90, lf=170, au=0, al=0),
        fix="l_hand",
        hold=True,
        props=["parallettes"],
        lift_start=16,
        lift_end=16,
    ),
    "dragon_flag": dict(
        start=dict(t=-90, lu=176, ll=176, lf=260, au=180, al=180),
        end=dict(t=-120, lu=110, ll=110, lf=200, au=210, al=210),
        fix="shoulder",
        props=["bench_flat"],
    ),
    "flutter": dict(
        start=dict(t=-90, lu=70, ll=70, lf=160, mu=110, ml=110, mf=200, au=-90, al=-90),
        end=dict(t=-90, lu=110, ll=110, lf=200, mu=70, ml=70, mf=160, au=-90, al=-90),
        fix="hip",
        props=["mat"],
    ),
    "copenhagen": dict(
        view="front",
        start=dict(rot=-74, au=-10, al=60, bu=170, bl=170, lu=2, ll=2),
        end=dict(rot=-74, au=-10, al=60, bu=170, bl=170, lu=2, ll=2, mu=-10, ml=-10),
        fix="l_elbow",
        hold=True,
        props=["bench_side"],
    ),
    # ---- full body / cardio
    "run": dict(
        start=dict(t=10, lu=60, ll=-20, mu=-30, ml=-80, mf=40, au=-40, al=60, bu=40, bl=130),
        end=dict(t=10, mu=60, ml=-20, lu=-30, ll=-80, lf=40, bu=-40, bl=60, au=40, al=130),
        fix="hip_floor_side",
    ),
    "walk": dict(
        start=dict(lu=22, ll=8, mu=-18, ml=-26, mf=110, au=-18, al=-10, bu=18, bl=30),
        end=dict(mu=22, ml=8, lu=-18, ll=-26, lf=110, bu=-18, bl=-10, au=18, al=30),
        fix="hip_floor_side",
    ),
    "treadmill": dict(
        start=dict(t=6, lu=40, ll=-10, mu=-24, ml=-60, mf=70, au=-30, al=60, bu=30, bl=110),
        end=dict(t=6, mu=40, ml=-10, lu=-24, ll=-60, lf=70, bu=-30, bl=60, au=30, al=110),
        fix="hip_floor_side",
        props=["treadmill"],
    ),
    "bike": dict(
        start=dict(t=30, lu=40, ll=-10, mu=100, ml=20, au=70, al=80),
        end=dict(t=30, lu=100, ll=20, mu=40, ml=-10, au=70, al=80),
        fix="hip",
        props=["bike"],
    ),
    "rower": dict(
        start=dict(t=24, lu=150, ll=-10, lf=120, au=88, al=88),
        end=dict(t=-20, lu=96, ll=86, lf=170, au=-20, al=90),
        fix="l_ankle",
        props=["rower"],
    ),
    "elliptical": dict(
        start=dict(t=4, lu=30, ll=-4, mu=-10, ml=-20, au=60, al=120, bu=20, bl=150),
        end=dict(t=4, mu=30, ml=-4, lu=-10, ll=-20, bu=60, bl=120, au=20, al=150),
        fix="hip_floor_side",
        props=["elliptical"],
    ),
    "stair_climber": dict(
        start=dict(t=10, lu=70, ll=-10, mu=0, ml=0, au=50, al=80),
        end=dict(t=10, mu=70, ml=-10, lu=0, ll=0, au=50, al=80),
        fix="hip_floor_side",
        props=["stairs"],
    ),
    "skierg": dict(
        start=dict(au=170, al=170),
        end=dict(t=50, lu=40, ll=-14, au=-20, al=-20, n=-20),
        fix="l_ankle",
        props=["skierg"],
    ),
    "jumping_jack": dict(
        view="front",
        start=dict(au=8, al=8, lu=4, ll=4),
        end=dict(au=160, al=170, lu=20, ll=18),
        fix="hip_floor",
    ),
    "jump_rope": dict(
        start=dict(au=20, al=60, lf=120),
        end=dict(au=20, al=60, lf=130),
        fix="l_toe",
        props=["rope"],
        lift=12,
    ),
    "high_knees": dict(
        start=dict(t=0, lu=90, ll=0, mu=0, ml=0, au=-40, al=60, bu=40, bl=120),
        end=dict(t=0, mu=90, ml=0, lu=0, ll=0, bu=-40, bl=60, au=40, al=120),
        fix="hip_floor_side",
    ),
    "butt_kicks": dict(
        start=dict(t=6, lu=-6, ll=-150, mu=0, ml=0, au=-30, al=60, bu=30, bl=110),
        end=dict(t=6, mu=-6, ml=-150, lu=0, ll=0, bu=-30, bl=60, au=30, al=110),
        fix="hip_floor_side",
    ),
    "burpee": dict(
        start=dict(t=40, lu=96, ll=-36, au=60, al=60, n=-10),
        end=dict(t=78, au=0, al=0, lu=-78, ll=-78, lf=10, n=-15),
        fix="l_hand",
    ),
    "jump_squat": dict(
        start=dict(t=38, lu=86, ll=-32, au=-30, al=-30, n=-12),
        end=dict(au=170, al=170, lf=150),
        fix="l_ankle",
        lift_end=26,
    ),
    "box_jump": dict(
        start=dict(t=40, lu=80, ll=-30, au=-40, al=-40, n=-12),
        end=dict(t=30, lu=86, ll=-30, au=80, al=90),
        fix="l_ankle",
        props=["box_front"],
        lift_end=44,
        end_dx=46,
    ),
    "bear_crawl": dict(
        start=dict(t=90, au=0, al=0, lu=10, ll=-70, lf=-120, n=-15),
        end=dict(
            t=90, au=24, al=24, bu=0, bl=0, lu=10, ll=-70, lf=-120, mu=34, ml=-50, mf=-120, n=-15
        ),
        fix="hip",
    ),
    "lateral_shuffle": dict(
        view="front",
        start=dict(lu=24, ll=4, au=30, al=-30),
        end=dict(lu=40, ll=10, mu=10, ml=0, au=30, al=-30),
        fix="hip_floor",
    ),
    "boxing": dict(
        start=dict(t=6, lu=14, ll=0, mu=-16, ml=-10, au=40, al=160, bu=30, bl=170),
        end=dict(t=10, lu=14, ll=0, mu=-16, ml=-10, au=88, al=88, bu=30, bl=170),
        fix="l_ankle",
    ),
    "battle_ropes": dict(
        start=dict(t=20, lu=40, ll=-16, au=40, al=60),
        end=dict(t=20, lu=40, ll=-16, au=70, al=100),
        fix="l_ankle",
        props=["ropes"],
    ),
    "sled_push": dict(
        start=dict(t=58, lu=50, ll=-20, mu=-40, ml=-60, mf=40, au=86, al=86),
        end=dict(t=58, mu=50, ml=-20, lu=-40, ll=-60, lf=40, au=86, al=86),
        fix="l_hand",
        props=["sled"],
    ),
    "skater": dict(
        view="front",
        start=dict(lu=30, ll=-10, mu=-30, ml=-60, au=-40, al=-40),
        end=dict(mu=30, ml=-10, lu=-30, ll=-60, bu=-40, bl=-40, au=60, al=60),
        fix="hip_floor",
    ),
    "get_up": dict(
        start=dict(t=-90, lu=140, ll=-20, au=180, al=180, bu=-60, bl=-60),
        end=dict(au=180, al=180),
        fix="l_ankle",
    ),
    # ---- mobility
    "stretch_quad": dict(
        start={},
        end=dict(mu=-6, ml=-160, mf=-100, bu=-20, bl=170),
        fix="l_ankle",
        hold=True,
        arms={"*": dict(au=70, al=70)},
    ),
    "stretch_hamstring": dict(
        start=dict(lu=20, ll=20, lf=150, mu=-6, ml=0),
        end=dict(t=50, lu=20, ll=20, lf=150, mu=-6, ml=0, au=30, al=30, n=-20),
        fix="m_ankle",
        hold=True,
    ),
    "stretch_calf": dict(
        start=dict(t=16, lu=20, ll=-8, mu=-24, ml=-24, au=90, al=90),
        end=dict(t=24, lu=30, ll=-14, mu=-30, ml=-30, au=80, al=80),
        fix="l_ankle",
        hold=True,
        props=["wall_front"],
    ),
    "hip_flexor": dict(
        start=dict(lu=90, ll=0, mu=0, ml=-90, mf=-110),
        end=dict(t=-6, lu=74, ll=-14, mu=-24, ml=-114, mf=-130, au=170, al=170),
        fix="m_knee",
        hold=True,
        props=["mat"],
    ),
    "lunge_stretch": dict(
        start=dict(t=40, lu=90, ll=-10, mu=-30, ml=-80, mf=50, au=0, al=0),
        end=dict(t=40, lu=90, ll=-10, mu=-30, ml=-80, mf=50, au=0, al=0, bu=150, bl=150),
        fix="l_ankle",
        hold=True,
    ),
    "pigeon": dict(
        start=dict(t=10, lu=90, ll=-80, lf=0, mu=-90, ml=-90, mf=-110, au=10, al=10),
        end=dict(t=60, lu=90, ll=-80, lf=0, mu=-90, ml=-90, mf=-110, au=60, al=60),
        fix="hip",
        hold=True,
        props=["mat"],
    ),
    "child_pose": dict(
        start=dict(t=90, lu=0, ll=-90, lf=-110, au=0, al=0, n=-10),
        end=dict(t=112, lu=62, ll=-90, lf=-110, au=96, al=96, n=-20),
        fix="l_knee",
        hold=True,
        props=["mat"],
    ),
    "cat_cow": dict(
        start=dict(t=96, lu=0, ll=-90, lf=-110, au=0, al=0, n=-30),
        end=dict(t=84, lu=0, ll=-90, lf=-110, au=0, al=0, n=20),
        fix="l_knee",
        props=["mat"],
    ),
    "down_dog": dict(
        start=dict(t=90, au=0, al=0, lu=0, ll=-90, lf=-110, n=-10),
        end=dict(t=140, au=-10, al=-10, lu=-40, ll=-40, lf=-110, n=-30),
        fix="l_hand",
        hold=True,
        props=["mat"],
    ),
    "seated_fold": dict(
        start=dict(t=0, lu=90, ll=90, lf=170, au=0, al=0),
        end=dict(t=60, lu=90, ll=90, lf=170, au=80, al=86, n=-20),
        fix="hip",
        hold=True,
        props=["mat"],
    ),
    "seated_twist": dict(
        start=dict(t=0, lu=150, ll=0, lf=90, mu=90, ml=90, mf=170, au=0, al=0),
        end=dict(t=0, lu=150, ll=0, lf=90, mu=90, ml=90, mf=170, au=-30, al=-10, bu=40, bl=140),
        fix="hip",
        hold=True,
        props=["mat"],
    ),
    "butterfly_stretch": dict(
        view="front",
        start=dict(lu=60, ll=-80, rot=0),
        end=dict(lu=80, ll=-80),
        fix="hip_floor_sit",
        hold=True,
        props=["mat"],
        arms={"*": dict(au=-10, al=-30)},
    ),
    "figure_four": dict(
        start=dict(t=-90, lu=140, ll=60, lf=150, mu=130, ml=-10, au=150, al=160),
        end=dict(t=-90, lu=160, ll=80, lf=170, mu=150, ml=10, au=170, al=170),
        fix="hip",
        hold=True,
        props=["mat"],
    ),
    "lying_twist": dict(
        start=dict(t=-90, lu=140, ll=-20, au=-90, al=-90),
        end=dict(t=-90, lu=100, ll=10, au=-90, al=-90),
        fix="hip",
        hold=True,
        props=["mat"],
    ),
    "arm_across": dict(
        view="front",
        start=dict(au=8, al=8),
        end=dict(au=-80, al=-86, bu=20, bl=-130),
        fix="hip_floor",
        hold=True,
    ),
    "doorway": dict(
        start=dict(au=90, al=180),
        end=dict(t=-10, au=80, al=170, lu=10, ll=0, mu=-20, ml=-20),
        fix="l_ankle",
        hold=True,
        props=["door"],
    ),
    "overhead_stretch": dict(
        view="front",
        start=dict(au=170, al=-10),
        end=dict(au=170, al=-10, bu=170, bl=150),
        fix="hip_floor",
        hold=True,
    ),
    "neck_stretch": dict(
        view="front", start=dict(n=0), end=dict(n=-26, bu=176, bl=-60), fix="hip_floor", hold=True
    ),
    "arm_circles": dict(
        view="front", start=dict(au=88, al=88), end=dict(au=100, al=100), fix="hip_floor"
    ),
    "wall_slide": dict(
        view="front",
        start=dict(au=88, al=178),
        end=dict(au=160, al=178),
        fix="hip_floor",
        props=["wall_behind"],
    ),
    "pass_through": dict(
        start=dict(au=150, al=150), end=dict(au=210, al=210), fix="l_ankle", band="hands"
    ),
    "leg_swing": dict(
        start=dict(mu=-30, ml=-30),
        end=dict(mu=70, ml=70),
        fix="l_ankle",
        arms={"*": dict(au=90, al=90)},
    ),
    "ankle_mob": dict(
        start=dict(t=6, lu=88, ll=0, mu=-4, ml=-90, mf=-110, au=88, al=88),
        end=dict(t=10, lu=78, ll=-32, mu=-4, ml=-90, mf=-110, au=80, al=80),
        fix="m_knee",
        props=["wall_front"],
    ),
    "quadruped_reach": dict(
        start=dict(t=90, au=0, al=0, lu=0, ll=-90, lf=-110, n=-10),
        end=dict(t=110, au=-20, al=60, bu=0, bl=0, lu=0, ll=-90, lf=-110, n=-10),
        fix="l_knee",
        hold=True,
        props=["mat"],
    ),
    "inchworm": dict(
        start=dict(t=150, au=-20, al=-20, lu=-4, ll=-4, lf=90, n=-30),
        end=dict(t=80, au=0, al=0, lu=-80, ll=-80, lf=10, n=-15),
        fix="l_toe",
    ),
    "wrist_stretch": dict(
        start=dict(au=88, al=88, bu=70, bl=90),
        end=dict(au=88, al=88, bu=70, bl=70),
        fix="l_ankle",
        hold=True,
    ),
    "ninety_ninety": dict(
        view="front",
        start=dict(lu=80, ll=-80, mu=80, ml=10),
        end=dict(lu=80, ll=10, mu=80, ml=-80),
        fix="hip_floor_sit",
        props=["mat"],
        arms={"*": dict(au=20, al=10)},
    ),
}

# Exercise -> family: keyword rules first (order matters), then the movement default.
KEYWORDS: List[Tuple[str, str]] = [
    ("turkish-get-up", "get_up"),
    ("world-s-greatest-stretch", "lunge_stretch"),
    ("90-90", "ninety_ninety"),
    ("inchworm", "inchworm"),
    ("thread-the-needle", "quadruped_reach"),
    ("knee-to-wall", "ankle_mob"),
    ("leg-swings", "leg_swing"),
    ("pass-through", "pass_through"),
    ("wall-slide", "wall_slide"),
    ("arm-circles", "arm_circles"),
    ("neck-side", "neck_stretch"),
    ("overhead-triceps-stretch", "overhead_stretch"),
    ("doorway", "doorway"),
    ("cross-body-shoulder", "arm_across"),
    ("figure-four", "figure_four"),
    ("butterfly-stretch", "butterfly_stretch"),
    ("seated-spinal-twist", "seated_twist"),
    ("downward-dog", "down_dog"),
    ("cat-cow", "cat_cow"),
    ("child-s-pose", "child_pose"),
    ("pigeon", "pigeon"),
    ("hip-flexor-stretch", "hip_flexor"),
    ("calf-stretch", "stretch_calf"),
    ("hamstring-stretch", "stretch_hamstring"),
    ("quad-stretch", "stretch_quad"),
    ("wrist-stretch", "wrist_stretch"),
    ("assault-bike", "bike"),
    ("stationary-bike", "bike"),
    ("elliptical", "elliptical"),
    ("rowing-machine", "rower"),
    ("stair-climber", "stair_climber"),
    ("skierg", "skierg"),
    ("treadmill", "treadmill"),
    ("jump-rope", "jump_rope"),
    ("jumping-jacks", "jumping_jack"),
    ("high-knees", "high_knees"),
    ("butt-kicks", "butt_kicks"),
    ("burpee", "burpee"),
    ("bear-crawl", "bear_crawl"),
    ("lateral-shuffle", "lateral_shuffle"),
    ("shadow-boxing", "boxing"),
    ("battle-rope", "battle_ropes"),
    ("sled", "sled_push"),
    ("skater", "skater"),
    ("brisk-walk", "walk"),
    ("walk", "walk"),
    ("run", "run"),
    ("sprint", "run"),
    ("box-jump", "box_jump"),
    ("jump-squat", "jump_squat"),
    ("carry", "carry"),
    ("hand-gripper", "grip_hold"),
    ("plate-pinch", "grip_hold"),
    ("wrist-roller", "grip_hold"),
    ("towel-dead-hang", "dead_hang"),
    ("dead-hang", "dead_hang"),
    ("scapular-pull-up", "dead_hang"),
    ("chin-up", "chin_up"),
    ("pull-up", "pull_up"),
    ("lat-pulldown", "pulldown"),
    ("straight-arm", "straight_arm_pulldown"),
    ("lying-towel-pull", "pulldown"),
    ("face-pull", "face_pull"),
    ("pull-apart", "pull_apart"),
    ("inverted-row", "row_inverted"),
    ("one-arm-dumbbell-row", "row_one_arm"),
    ("renegade-row", "row_one_arm"),
    ("chest-supported", "row_chest_supported"),
    ("seated-cable-row", "row_seated"),
    ("cable-row", "row_seated"),
    ("machine-row", "row_seated"),
    ("high-row", "row_seated"),
    ("smith-machine-row", "row_bent"),
    ("row", "row_bent"),
    ("upright-row", "upright_row"),
    ("shrug", "shrug"),
    ("chest-supported-dumbbell-reverse-fly", "reverse_fly"),
    ("reverse-fly", "reverse_fly"),
    ("rear-delt-fly", "reverse_fly"),
    ("reverse-pec-deck", "reverse_fly"),
    ("cable-x-fly", "reverse_fly"),
    ("butterfly", "reverse_fly"),
    ("iron-man", "reverse_fly"),
    ("prone-t", "reverse_fly"),
    ("prone-y", "reverse_fly"),
    ("snow-angel", "reverse_fly"),
    ("pullover", "pullover"),
    ("flat-dumbbell-fly", "fly_lying"),
    ("incline-dumbbell-fly", "fly_lying"),
    ("fly", "fly"),
    ("external-rotation", "external_rotation"),
    ("internal-rotation", "external_rotation"),
    ("lateral-raise", "lateral_raise"),
    ("front-raise", "front_raise"),
    ("preacher", "preacher_curl"),
    ("concentration", "concentration_curl"),
    ("incline-dumbbell-curl", "curl_incline"),
    ("spider-curl", "row_chest_supported"),
    ("wrist-curl", "wrist_curl"),
    ("pronation", "wrist_curl"),
    ("curl", "curl"),
    ("pushdown", "pushdown"),
    ("kickback", "kickback"),
    ("skull-crusher", "skull_crusher"),
    ("overhead-triceps-extension", "overhead_extension"),
    ("overhead-dumbbell-extension", "overhead_extension"),
    ("triceps-extension-machine", "pushdown"),
    ("kneeling-triceps-extension", "knee_push_up"),
    ("straight-leg-triceps-extension", "plank"),
    ("bench-dip", "bench_dip"),
    ("dip", "dip"),
    ("handstand-push-up", "handstand_push_up"),
    ("handstand", "handstand"),
    ("wall-walk", "wall_walk"),
    ("pike-push-up", "pike_push_up"),
    ("wall-push-up", "wall_push_up"),
    ("incline-push-up", "incline_push_up"),
    ("decline-push-up", "decline_push_up"),
    ("knee-push-up", "knee_push_up"),
    ("pseudo-planche-lean", "push_up"),
    ("scapular-push-up", "push_up"),
    ("cobra-push-up", "superman"),
    ("push-up", "push_up"),
    ("floor-press", "floor_press"),
    ("incline-dumbbell-press", "incline_press"),
    ("incline-barbell-bench", "incline_press"),
    ("incline-iso-lateral", "incline_press"),
    ("incline-press", "incline_press"),
    ("machine-chest-press", "machine_press"),
    ("machine-press", "machine_press"),
    ("chest-press", "machine_press"),
    ("standing-cable-chest-press", "standing_press_fwd"),
    ("band-chest-press", "standing_press_fwd"),
    ("bench-press", "bench_press"),
    ("close-grip-press", "bench_press"),
    ("squeeze-press", "bench_press"),
    ("landmine-press", "landmine_press"),
    ("seated-dumbbell-shoulder-press", "seated_press"),
    ("seated-barbell-shoulder-press", "seated_press"),
    ("seated-ez-bar-shoulder-press", "seated_press"),
    ("machine-shoulder-press", "seated_press"),
    ("arnold", "seated_press"),
    ("shoulder-press", "overhead_press"),
    ("overhead-press", "overhead_press"),
    ("hip-thrust", "hip_thrust"),
    ("glute-bridge", "bridge"),
    ("frog-pump", "bridge"),
    ("back-extension", "back_extension"),
    ("superman", "superman"),
    ("swing", "swing"),
    ("pull-through", "pull_through"),
    ("rack-pull", "rack_pull"),
    ("single-leg-romanian", "single_leg_rdl"),
    ("romanian", "rdl"),
    ("deadlift", "deadlift"),
    ("leg-press-calf", "calf_raise"),
    ("seated-calf", "seated_calf"),
    ("calf-raise", "calf_raise"),
    ("tibialis", "tibialis_raise"),
    ("leg-extension", "leg_extension"),
    ("lying-leg-curl", "leg_curl_lying"),
    ("seated-leg-curl", "leg_curl_seated"),
    ("sliding-hamstring", "bridge"),
    ("nordic-hamstring-curl", "nordic"),
    ("reverse-nordic", "nordic"),
    ("sissy", "sissy"),
    ("abduction", "abduction_machine"),
    ("adduction", "adduction_machine"),
    ("lateral-walk", "abduction_band"),
    ("clamshell", "clamshell"),
    ("fire-hydrant", "kickback_quad"),
    ("donkey-kick", "kickback_quad"),
    ("glute-kickback", "cable_kickback"),
    ("pistol", "pistol"),
    ("lateral-lunge", "lateral_lunge"),
    ("cossack", "lateral_lunge"),
    ("step-up", "step_up"),
    ("bulgarian-split-squat", "split_squat_rear"),
    ("lunge", "lunge"),
    ("split-squat", "lunge"),
    ("wall-sit", "wall_sit"),
    ("leg-press", "leg_press"),
    ("hack-squat", "hack_squat"),
    ("front-squat", "front_squat"),
    ("sumo", "sumo_squat"),
    ("squat", "squat"),
    ("side-plank", "side_plank"),
    ("copenhagen", "copenhagen"),
    ("shoulder-tap", "plank"),
    ("plank", "plank"),
    ("ab-wheel", "rollout"),
    ("rollout", "rollout"),
    ("cable-crunch", "cable_crunch"),
    ("ab-crunch-machine", "crunch"),
    ("reverse-crunch", "leg_raise"),
    ("crunch", "crunch"),
    ("hanging-knee", "hanging_knee_raise"),
    ("hanging-leg", "hanging_raise"),
    ("toes-to-bar", "hanging_raise"),
    ("lying-leg-raise", "leg_raise"),
    ("v-up", "v_up"),
    ("dead-bug", "dead_bug"),
    ("russian-twist", "russian_twist"),
    ("mountain-climber", "mountain_climber"),
    ("bird-dog", "bird_dog"),
    ("hollow", "hollow_hold"),
    ("pallof", "pallof"),
    ("woodchop", "woodchop"),
    ("side-bend", "side_bend"),
    ("l-sit", "l_sit"),
    ("dragon-flag", "dragon_flag"),
    ("flutter", "flutter"),
]

MOVEMENT_DEFAULT = {
    "squat": "squat",
    "hinge": "rdl",
    "lunge": "lunge",
    "horizontal_push": "push_up",
    "vertical_push": "overhead_press",
    "horizontal_pull": "row_bent",
    "vertical_pull": "pulldown",
    "core": "crunch",
    "carry": "carry",
    "cardio": "run",
    "mobility": "stretch_hamstring",
    "full_body": "burpee",
    "isolation": "curl",
}

LOWER = {"quads", "hamstrings", "glutes", "adductors", "abductors", "hip_flexors", "calves"}


def _has_tokens(slug_tokens: List[str], key_tokens: List[str]) -> bool:
    n = len(key_tokens)
    return any(slug_tokens[i : i + n] == key_tokens for i in range(len(slug_tokens) - n + 1))


# More specific keywords (more words) win; ties keep the list order.
_RULES = sorted(((k.split("-"), fam) for k, fam in KEYWORDS), key=lambda r: -len(r[0]))


def family_for(ex: dict) -> str:
    forced = (ex.get("form_pose") or "").strip()
    if forced in F:
        return forced
    tokens = (ex.get("slug") or "").split("-")
    for key_tokens, fam in _RULES:
        if _has_tokens(tokens, key_tokens):
            return fam
    return MOVEMENT_DEFAULT.get(ex.get("movement") or "", "curl")


def sex_for(ex: dict) -> str:
    primary = list((ex.get("muscles") or {}).get("primary") or [])
    lower = sum(1 for m in primary if m in LOWER)
    return "female" if lower and lower > len(primary) - lower else "male"


IMPLEMENT_ORDER = ("smith", "barbell", "dumbbell", "kettlebell", "cable", "bands")


def implement_for(ex: dict) -> Optional[str]:
    eq = ex.get("equipment_all")
    if eq is None:
        eq = ex.get("equipment")
    eq = list(eq) if isinstance(eq, (list, tuple)) else []
    for key in IMPLEMENT_ORDER:
        if key in eq:
            return key
    return None


# -----------------------------------------------------------------------------
# Placement
# -----------------------------------------------------------------------------
def _arms_for(spec: dict, implement: Optional[str]) -> dict:
    arms = spec.get("arms") or {}
    if "*" in arms:
        return dict(arms["*"])
    if implement in arms:
        return dict(arms[implement])
    if implement == "smith" and "barbell" in arms:
        return dict(arms["barbell"])
    if implement in ("bands", "cable") and "dumbbell" in arms:
        return dict(arms["dumbbell"])
    return dict(arms.get(None, {}))


def _pose_joints(spec: dict, which: str, sex: str, implement: Optional[str]) -> dict:
    arms = _arms_for(spec, implement)
    rel = bool(arms.pop("rel", False) or spec.get("arm_rel"))
    pose = dict(arms)
    pose.update(spec[which])
    if spec.get("view") == "front":
        return front_joints(pose, sex)
    return side_joints(pose, sex, arm_rel=rel)


def _points(j: dict, sex: str, view: str) -> List[Tuple[float, float, float]]:
    """(x, y, radius) samples that bound the drawn figure."""
    r = RIG[sex]
    pts = []
    for k, (x, y) in j.items():
        if k.startswith("_"):
            continue
        rad = 4.0
        if k == "head":
            rad = r["head"] + 1.5
        elif k in ("hip", "shoulder"):
            rad = r["w_torso"] / 2
        elif k.endswith("knee") or k.endswith("hipj"):
            rad = r["w_thigh"] / 2
        elif k.endswith("toe") or k.endswith("heel"):
            rad = r["w_foot"] / 2 + 1
        elif k.endswith("hand"):
            rad = 3.0
        pts.append((x, y, rad))
    return pts


def _shift(j: dict, dx: float, dy: float) -> dict:
    return {k: (v if k.startswith("_") else (v[0] + dx, v[1] + dy)) for k, v in j.items()}


def _scale(j: dict, s: float, ox: float, oy: float) -> dict:
    return {
        k: (v if k.startswith("_") else (ox + (v[0] - ox) * s, oy + (v[1] - oy) * s))
        for k, v in j.items()
    }


def _fix_x(j: dict, fix: str) -> float:
    if fix in ("hip_floor", "hip_floor_side", "hip_floor_sit"):
        return j["hip"][0]
    if fix in j:
        return j[fix][0]
    return j["hip"][0]


def _ik_far_leg(j: dict, target, sex: str, foot: float) -> dict:
    """Bend the far leg so its ankle lands on `target` (knee toward the floor)."""
    r = RIG[sex]
    a, b = r["thigh"], r["shin"]
    hx, hy = j["hip"]
    dx, dy = target[0] - hx, target[1] - hy
    d = max(abs(a - b) + 0.01, min(a + b - 0.01, math.hypot(dx, dy)))
    phi = math.degrees(math.atan2(dx, dy))
    alpha = math.degrees(math.acos((a * a + d * d - b * b) / (2 * a * d)))
    best = None
    for thigh in (phi + alpha, phi - alpha):
        knee = _add(j["hip"], _down(thigh), a)
        if best is None or knee[1] > best[1][1]:
            best = (thigh, knee)
    knee = best[1]
    shin = math.degrees(math.atan2(target[0] - knee[0], target[1] - knee[1]))
    ankle = _add(knee, _down(shin), b)
    out = dict(j)
    out["m_knee"], out["m_ankle"] = knee, ankle
    out["m_toe"] = _add(ankle, _down(foot), r["foot"])
    out["m_heel"] = _add(ankle, _down(foot), -r["foot"] * 0.28)
    return out


def place(spec: dict, sex: str, implement: Optional[str]) -> Tuple[dict, dict, float]:
    """Start and end joints in canvas coordinates, plus the scale used."""
    view = spec.get("view", "side")
    fix = spec.get("fix", "l_ankle")
    anchor = spec.get("anchor", "floor")
    raise_ = {"start": spec.get("lift_start", 0), "end": spec.get("lift_end", 0)}
    if spec.get("lift"):
        raise_["end"] = spec["lift"]
    out = {}
    for which in ("start", "end"):
        j = _pose_joints(spec, which, sex, implement)
        pts = _points(j, sex, view)
        if anchor == "hands":
            hy = min(j[k][1] for k in ("a_hand", "b_hand"))
            dy = BAR_Y + 4 - hy
        elif anchor == "dip":
            dy = FLOOR - 118 - j["a_hand"][1]
        elif spec.get("hip_y"):
            dy = FLOOR - spec["hip_y"] - j["hip"][1]
        elif anchor == "center":
            ys = [y for _, y, _ in pts]
            dy = H / 2 - (min(ys) + max(ys)) / 2
        else:
            low = max(y + rad for _, y, rad in pts)
            dy = FLOOR - raise_[which] - low
        dx = -_fix_x(j, fix)
        if which == "end":
            dx += spec.get("end_dx", 0)
        out[which] = _shift(j, dx, dy)
    pin = spec.get("pin")
    if pin and view == "side":
        # Keep the pinned far ankle where it was at the start (rear foot on a bench).
        j = out["end"]
        dx = out["start"][fix][0] - j[fix][0]
        dy = out["start"][fix][1] - j[fix][1]
        j = _shift(j, dx, dy)
        out["end"] = _ik_far_leg(j, out["start"]["m_ankle"], sex, spec["end"].get("mf", 90.0))
    # Centre both frames together and shrink if they don't fit.
    allp = _points(out["start"], sex, view) + _points(out["end"], sex, view)
    pad = 22 if spec.get("props") or implement else 8
    min_x = min(x - r for x, _, r in allp) - pad
    max_x = max(x + r for x, _, r in allp) + pad
    min_y = min(y - r for _, y, r in allp) - 6
    oy = {"hands": BAR_Y, "center": H / 2}.get(anchor, FLOOR)
    s = min(1.3, (W - 2 * MARGIN) / max(1.0, max_x - min_x))
    if anchor == "center":
        max_y = max(y + r for _, y, r in allp)
        s = min(s, (H - 2 * MARGIN) / max(1.0, max_y - min_y))
    elif anchor != "hands":
        s = min(s, (FLOOR - MARGIN) / max(1.0, FLOOR - min_y))
    else:
        max_y = max(y + r for _, y, r in allp)
        s = min(s, (FLOOR - BAR_Y - 6) / max(1.0, max_y - BAR_Y))
    cx = (min_x + max_x) / 2
    for which in out:
        j = _shift(out[which], W / 2 - cx, 0)
        out[which] = _scale(j, s, W / 2, oy)
    return out["start"], out["end"], s


# -----------------------------------------------------------------------------
# Drawing
# -----------------------------------------------------------------------------
def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _line(a, b, color: str, width: float, extra: str = "") -> str:
    return (
        f'<line x1="{_f(a[0])}" y1="{_f(a[1])}" x2="{_f(b[0])}" y2="{_f(b[1])}" '
        f'stroke="{color}" stroke-width="{_f(width)}"{extra}/>'
    )


def _lerp(a, b, k):
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)


def _head_axes(j: dict, view: str) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    if view == "front":
        rot, n = j["_rot"]
        a = math.radians(rot + n)
        u = (math.sin(a), -math.cos(a))
        f = (math.cos(a), math.sin(a))
        return u, f
    t, n = j["_t"]
    u = _up(t + n)
    f = (-u[1], u[0])
    return u, f


def _hair(j: dict, sex: str, view: str, s: float) -> str:
    r = RIG[sex]["head"] * s
    c = j["head"]
    u, f = _head_axes(j, view)

    def pt(phi, k=1.06):
        a = math.radians(phi)
        return (
            c[0] + r * k * (u[0] * math.cos(a) + f[0] * math.sin(a)),
            c[1] + r * k * (u[1] * math.cos(a) + f[1] * math.sin(a)),
        )

    if view == "front":
        arc = [pt(a) for a in range(-100, 101, 20)]
        inner = (c[0] + u[0] * r * 0.25, c[1] + u[1] * r * 0.25)
    else:
        arc = [pt(a) for a in range(-150, 71, 20)]
        inner = (c[0] + u[0] * r * 0.05 - f[0] * r * 0.1, c[1] + u[1] * r * 0.05 - f[1] * r * 0.1)
    d = "M" + " L".join(f"{_f(x)} {_f(y)}" for x, y in arc) + f" Q{_f(inner[0])} {_f(inner[1])} "
    d += f"{_f(arc[0][0])} {_f(arc[0][1])}Z"
    out = f'<path d="{d}" fill="{HAIR}"/>'
    if sex == "female" and view != "front":
        base = pt(-115, 0.92)
        tip = (base[0] - f[0] * 13 * s - u[0] * 9 * s, base[1] - f[1] * 13 * s - u[1] * 9 * s)
        side = (u[0] * 4 * s, u[1] * 4 * s)
        out += (
            f'<path d="M{_f(base[0] + side[0])} {_f(base[1] + side[1])} '
            f"Q{_f(tip[0] + side[0] * 1.5)} {_f(tip[1] + side[1] * 1.5)} {_f(tip[0])} {_f(tip[1])} "
            f"Q{_f(tip[0] - side[0])} {_f(tip[1] - side[1])} "
            f'{_f(base[0] - side[0])} {_f(base[1] - side[1])}Z" fill="{HAIR}"/>'
        )
    elif sex == "female":
        top = pt(0, 1.18)
        out += f'<circle cx="{_f(top[0])}" cy="{_f(top[1])}" r="{_f(4.2 * s)}" fill="{HAIR}"/>'
    return out


def _side_figure(j: dict, sex: str, s: float, ghost: bool = False) -> str:
    r = RIG[sex]
    cap = ' stroke-linecap="round"'
    parts: List[str] = []

    def col(near, far, is_far):
        if ghost:
            return GHOST
        return far if is_far else near

    def leg(side, is_far):
        hip, knee, ankle = j["hip"], j[f"{side}_knee"], j[f"{side}_ankle"]
        toe, heel = j[f"{side}_toe"], j[f"{side}_heel"]
        sk = col(SKIN, SKIN_FAR, is_far)
        pants = col(SHORTS, SHORTS_FAR, is_far)
        shoe = col(SHOE, SHOE_FAR, is_far)
        out = [
            _line(hip, knee, sk, r["w_thigh"] * s, cap),
            _line(knee, ankle, sk, r["w_shin"] * s, cap),
        ]
        if sex == "female":  # leggings to mid-calf
            out.append(_line(hip, knee, pants, (r["w_thigh"] + 0.6) * s, cap))
            out.append(_line(knee, _lerp(knee, ankle, 0.72), pants, (r["w_shin"] + 0.6) * s, cap))
        else:  # shorts
            out.append(_line(hip, _lerp(hip, knee, 0.58), pants, (r["w_thigh"] + 1.6) * s, cap))
        out.append(_line(heel, toe, shoe, r["w_foot"] * s, cap))
        return out

    def arm(side, is_far):
        sh, el, wr, hd = j["shoulder"], j[f"{side}_elbow"], j[f"{side}_wrist"], j[f"{side}_hand"]
        sk = col(SKIN, SKIN_FAR, is_far)
        out = [
            _line(sh, el, sk, r["w_ua"] * s, cap),
            _line(el, wr, sk, r["w_fa"] * s, cap),
            _line(wr, hd, sk, r["w_fa"] * 0.95 * s, cap),
        ]
        if sex == "male":
            out.append(
                _line(
                    sh,
                    _lerp(sh, el, 0.42),
                    col(SHIRT, SHIRT_FAR, is_far),
                    (r["w_ua"] + 1.8) * s,
                    cap,
                )
            )
        return out

    parts += leg("m", True)
    parts += arm("b", True)
    shirt = GHOST if ghost else SHIRT
    sk = GHOST if ghost else SKIN
    parts.append(_line(j["shoulder"], j["neck"], sk, 7.5 * s, cap))
    hip_low = _lerp(j["hip"], j["shoulder"], -0.04)
    parts.append(_line(hip_low, j["shoulder"], shirt, r["w_torso"] * s, cap))
    if not ghost:
        # Waistband: shorts/leggings over the hips.
        parts.append(
            _line(
                _lerp(j["hip"], j["shoulder"], -0.06),
                _lerp(j["hip"], j["shoulder"], 0.12),
                SHORTS,
                (r["w_torso"] + 0.5) * s,
                cap,
            )
        )
    parts.append(
        f'<circle cx="{_f(j["head"][0])}" cy="{_f(j["head"][1])}" r="{_f(r["head"] * s)}" '
        f'fill="{sk}"/>'
    )
    if not ghost:
        parts.append(_hair(j, sex, "side", s))
    parts += leg("l", False)
    parts += arm("a", False)
    return "".join(parts)


def _front_figure(j: dict, sex: str, s: float, ghost: bool = False) -> str:
    r = RIG[sex]
    cap = ' stroke-linecap="round" stroke-linejoin="round"'
    sk = GHOST if ghost else SKIN
    shirt = GHOST if ghost else SHIRT
    pants = GHOST if ghost else SHORTS
    shoe = GHOST if ghost else SHOE
    parts: List[str] = []
    for leg in ("l", "m"):
        hp, kn, an = j[f"{leg}_hipj"], j[f"{leg}_knee"], j[f"{leg}_ankle"]
        parts.append(_line(hp, kn, sk, r["w_thigh"] * s, cap))
        parts.append(_line(kn, an, sk, r["w_shin"] * s, cap))
        if sex == "female":
            parts.append(_line(hp, kn, pants, (r["w_thigh"] + 0.6) * s, cap))
            parts.append(_line(kn, _lerp(kn, an, 0.72), pants, (r["w_shin"] + 0.6) * s, cap))
        else:
            parts.append(_line(hp, _lerp(hp, kn, 0.5), pants, (r["w_thigh"] + 2) * s, cap))
        parts.append(_line(j[f"{leg}_heel"], j[f"{leg}_toe"], shoe, r["w_foot"] * s, cap))
    # Pelvis (shorts) and torso (shirt) as rounded polygons through the joints.
    ls, rs = j["a_shoulder"], j["b_shoulder"]
    lh, rh = j["l_hipj"], j["m_hipj"]
    waist_l, waist_r = _lerp(ls, lh, 0.78), _lerp(rs, rh, 0.78)
    waist_l = _lerp(waist_l, _lerp(waist_l, waist_r, 0.5), 0.12)
    waist_r = _lerp(waist_r, _lerp(waist_l, waist_r, 0.5), 0.12)
    hl = (lh[0] + (lh[0] - j["hip"][0]) * 0.55, lh[1])
    hr = (rh[0] + (rh[0] - j["hip"][0]) * 0.55, rh[1])
    pts = " ".join(f"{_f(x)},{_f(y)}" for x, y in (waist_l, waist_r, hr, rh, lh, hl))
    parts.append(
        f'<polygon points="{pts}" fill="{pants}" stroke="{pants}" '
        f'stroke-width="{_f(5 * s)}"{cap}/>'
    )
    parts.append(_line(j["neck"], _lerp(ls, rs, 0.5), sk, 7.5 * s, cap))
    pts = " ".join(f"{_f(x)},{_f(y)}" for x, y in (ls, rs, waist_r, waist_l))
    parts.append(
        f'<polygon points="{pts}" fill="{shirt}" stroke="{shirt}" '
        f'stroke-width="{_f(9 * s)}"{cap}/>'
    )
    for side in ("a", "b"):
        sh, el, wr, hd = (
            j[f"{side}_shoulder"],
            j[f"{side}_elbow"],
            j[f"{side}_wrist"],
            j[f"{side}_hand"],
        )
        parts.append(_line(sh, el, sk, r["w_ua"] * s, cap))
        parts.append(_line(el, wr, sk, r["w_fa"] * s, cap))
        parts.append(_line(wr, hd, sk, r["w_fa"] * 0.95 * s, cap))
        if sex == "male":
            parts.append(_line(sh, _lerp(sh, el, 0.42), shirt, (r["w_ua"] + 1.8) * s, cap))
    parts.append(
        f'<circle cx="{_f(j["head"][0])}" cy="{_f(j["head"][1])}" r="{_f(r["head"] * s)}" '
        f'fill="{sk}"/>'
    )
    if not ghost:
        parts.append(_hair(j, sex, "front", s))
    return "".join(parts)


# -----------------------------------------------------------------------------
# Equipment and props
# -----------------------------------------------------------------------------
def _rect(x, y, w, h, fill, rx=2.0, extra="") -> str:
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    return (
        f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" rx="{_f(rx)}" '
        f'fill="{fill}"{extra}/>'
    )


def _circle(c, r, fill, extra="") -> str:
    return f'<circle cx="{_f(c[0])}" cy="{_f(c[1])}" r="{_f(r)}" fill="{fill}"{extra}/>'


def _bench(x1, x2, top, s, legs=True) -> str:
    out = []
    if legs:
        for x in (x1 + 6 * s, x2 - 6 * s):
            out.append(_line((x, top), (x, FLOOR), FRAME, 3.2 * s))
        out.append(_line((x1 + 6 * s, FLOOR - 2), (x2 - 6 * s, FLOOR - 2), FRAME, 2.5 * s))
    out.append(_rect(x1, top, x2 - x1, 7 * s, PAD, 3 * s))
    return "".join(out)


def _pad_along(a, b, offset, s, width=7.0) -> str:
    """A pad beside the segment a-b, pushed `offset` along its normal."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1
    nx, ny = -dy / n * offset, dx / n * offset
    return _line(
        (a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), PAD, width * s, ' stroke-linecap="round"'
    )


def _barbell_side(c, s, smith=False) -> str:
    out = ""
    if smith:
        out += _line((c[0], 10), (c[0], FLOOR), FRAME, 2.4 * s)
    out += _circle(c, 14 * s, IRON, f' stroke="{IRON_RIM}" stroke-width="{_f(2 * s)}"')
    out += _circle(c, 3.2 * s, METAL)
    return out


def _dumbbell_side(c, s, far=False) -> str:
    return _circle(
        c,
        7 * s,
        IRON if not far else "#2B2F2D",
        f' stroke="{IRON_RIM}" stroke-width="{_f(1.6 * s)}"',
    ) + _circle(c, 2 * s, METAL)


def _kettlebell(c, s) -> str:
    body = (c[0], c[1] + 9 * s)
    return _line(
        (c[0] - 4 * s, c[1] + 2 * s),
        (c[0] + 4 * s, c[1] + 2 * s),
        IRON_RIM,
        2.6 * s,
        ' stroke-linecap="round"',
    ) + _circle(body, 8.5 * s, IRON, f' stroke="{IRON_RIM}" stroke-width="{_f(1.4 * s)}"')


def _dumbbell_front(c, s) -> str:
    return (
        _line((c[0], c[1] - 9 * s), (c[0], c[1] + 9 * s), METAL, 2.6 * s)
        + _rect(c[0] - 5 * s, c[1] - 11 * s, 10 * s, 5 * s, IRON, 1.5 * s)
        + _rect(c[0] - 5 * s, c[1] + 6 * s, 10 * s, 5 * s, IRON, 1.5 * s)
    )


def _barbell_front(a, b, s, smith=False) -> str:
    y = (a[1] + b[1]) / 2
    x1, x2 = min(a[0], b[0]) - 30 * s, max(a[0], b[0]) + 30 * s
    out = ""
    if smith:
        out += _line((x1 + 2, 10), (x1 + 2, FLOOR), FRAME, 2.4 * s)
        out += _line((x2 - 2, 10), (x2 - 2, FLOOR), FRAME, 2.4 * s)
    out += _line((x1, y), (x2, y), METAL, 2.6 * s)
    for x in (x1 + 4 * s, x2 - 4 * s):
        out += _rect(x - 3.5 * s, y - 15 * s, 7 * s, 30 * s, IRON, 2 * s)
    return out


def _pulley(where, height, j, view) -> Tuple[float, float]:
    sh = j.get("shoulder") or j.get("a_shoulder")
    y = {"high": 26, "mid": sh[1], "low": FLOOR - 14}.get(height, sh[1])
    if where == "front":
        x = W - 16
    elif where == "behind":
        x = 16
    elif where in ("side", "side_high"):
        x = W - 16
    elif where == "center":
        x = W / 2
    elif where == "top":
        x = (j["a_hand"][0] + j["b_hand"][0]) / 2
        y = 10
    else:
        x = W - 16
    return x, y


def equipment_svg(
    spec: dict,
    j: dict,
    view: str,
    s: float,
    implement: Optional[str],
    accent: str,
    sex: str = "male",
) -> Tuple[str, str]:
    """(behind, in front) markup for the props and the implement in the hands."""
    R = RIG[sex]  # noqa: N806
    behind: List[str] = []
    front: List[str] = []
    props = list(spec.get("props") or [])
    cap = ' stroke-linecap="round"'
    for name in props:
        if name == "mat":
            xs = [p[0] for k, p in j.items() if not k.startswith("_")]
            behind.append(
                _rect(min(xs) - 14, FLOOR - 1.5, max(xs) - min(xs) + 28, 4.5, "#2E3D38", 2)
            )
        elif name == "bench_flat":
            hip, sh = j["hip"], j["shoulder"]
            top = max(hip[1], sh[1]) + R["w_torso"] * s / 2
            behind.append(_bench(min(hip[0], sh[0]) - 16 * s, max(hip[0], sh[0]) + 14 * s, top, s))
        elif name == "bench_incline":
            hip, sh = j["hip"], j["shoulder"]
            behind.append(_pad_along(hip, sh, -R["w_torso"] * s / 2 - 3 * s, s))
            seat_top = hip[1] + 9 * s
            behind.append(_bench(hip[0] - 14 * s, hip[0] + 18 * s, seat_top, s))
            behind.append(
                _line((hip[0] - 18 * s, hip[1] - 20 * s), (hip[0] - 6 * s, FLOOR), FRAME, 3 * s)
            )
        elif name == "bench_behind":
            sh = j["shoulder"]
            behind.append(_bench(sh[0] - 44 * s, sh[0] + 6 * s, sh[1] + 6 * s, s))
        elif name == "bench_knee":
            kn, hd = j["m_knee"], j["b_hand"]
            top = max(kn[1] + 5 * s, hd[1])
            behind.append(_bench(min(kn[0], hd[0]) - 14 * s, max(kn[0], hd[0]) + 12 * s, top, s))
        elif name == "bench_chest":
            hip, sh = j["hip"], j["shoulder"]
            behind.append(_pad_along(_lerp(hip, sh, 0.15), sh, R["w_torso"] * s / 2 + 3 * s, s, 8))
            mid = _lerp(hip, sh, 0.45)
            behind.append(
                _line((mid[0] + 10 * s, mid[1] + 8 * s), (mid[0] + 10 * s, FLOOR), FRAME, 3 * s)
            )
        elif name == "bench_hands":
            hd = j["a_hand"]
            behind.append(_bench(hd[0] - 22 * s, hd[0] + 18 * s, hd[1] + 2 * s, s))
        elif name == "bench_feet":
            toe = j["l_toe"]
            behind.append(_bench(toe[0] - 20 * s, toe[0] + 16 * s, toe[1] + 3 * s, s))
        elif name == "bench_rear_foot":
            toe, ank = j["m_toe"], j["m_ankle"]
            top = max(toe[1], ank[1]) + 3 * s
            behind.append(
                _bench(min(toe[0], ank[0]) - 18 * s, max(toe[0], ank[0]) + 10 * s, top, s)
            )
        elif name == "bench_behind_hands":
            hd = j["a_hand"]
            behind.append(_bench(hd[0] - 30 * s, hd[0] + 8 * s, hd[1] + 1, s))
        elif name == "bench_side":
            an = j["m_ankle"]
            behind.append(_bench(an[0] - 18 * s, an[0] + 18 * s, an[1] + 5 * s, s))
        elif name == "bench_top":
            hip, nk = j["hip"], j["neck"]
            behind.append(_line(_lerp(hip, nk, -0.25), _lerp(hip, nk, 1.05), PAD, 26 * s, cap))
        elif name in ("seat", "seat_back", "seat_upright"):
            hip = j["hip"]
            top = hip[1] + R["w_thigh"] * s / 2
            behind.append(_line((hip[0], top), (hip[0], FLOOR), FRAME, 3.4 * s))
            behind.append(
                _line((hip[0] - 16 * s, FLOOR - 1), (hip[0] + 16 * s, FLOOR - 1), FRAME, 3 * s)
            )
            behind.append(_rect(hip[0] - 15 * s, top, 32 * s, 7 * s, PAD, 3 * s))
            if name in ("seat_back", "seat_upright"):
                sh = j["shoulder"]
                behind.append(
                    _pad_along(hip, _lerp(hip, sh, 1.05), -R["w_torso"] * s / 2 - 3 * s, s)
                )
                behind.append(
                    _line((hip[0] - 14 * s, hip[1]), (hip[0] - 14 * s, FLOOR), FRAME, 3 * s)
                )
        elif name in ("seat_front", "seat_front_low"):
            hip = j["hip"]
            behind.append(_rect(hip[0] - 26 * s, hip[1] + 4 * s, 52 * s, 8 * s, PAD, 3 * s))
            behind.append(_line((hip[0], hip[1] + 12 * s), (hip[0], FLOOR), FRAME, 3.4 * s))
            behind.append(
                _line((hip[0] - 22 * s, FLOOR - 1), (hip[0] + 22 * s, FLOOR - 1), FRAME, 3 * s)
            )
            if name == "seat_front_low":
                for leg in ("l", "m"):
                    kn = j[f"{leg}_knee"]
                    front.append(
                        _line(
                            (kn[0] - 8 * s, kn[1] - 8 * s),
                            (kn[0] + 8 * s, kn[1] - 8 * s),
                            PAD,
                            7 * s,
                            cap,
                        )
                    )
        elif name == "row_seat":
            hip, toe = j["hip"], j["l_toe"]
            behind.append(
                _rect(hip[0] - 40 * s, FLOOR - 6 * s, toe[0] - hip[0] + 70 * s, 6 * s, FRAME, 2)
            )
            behind.append(_rect(hip[0] - 14 * s, hip[1] + 6 * s, 28 * s, 7 * s, PAD, 3 * s))
            behind.append(_line((hip[0], hip[1] + 12 * s), (hip[0], FLOOR - 4), FRAME, 3 * s))
            behind.append(
                _line(
                    (toe[0] + 4 * s, toe[1] - 14 * s), (toe[0] + 4 * s, FLOOR - 4), PAD, 6 * s, cap
                )
            )
        elif name == "leg_press":
            hip, sh, toe, heel = j["hip"], j["shoulder"], j["l_toe"], j["l_heel"]
            behind.append(
                _pad_along(
                    _lerp(hip, sh, -0.2), _lerp(hip, sh, 1.05), -R["w_torso"] * s / 2 - 3 * s, s
                )
            )
            behind.append(
                _line((hip[0] - 6 * s, hip[1] + 14 * s), (hip[0] - 6 * s, FLOOR), FRAME, 3 * s)
            )
            plate = _lerp(heel, toe, 0.5)
            ux, uy = toe[0] - heel[0], toe[1] - heel[1]
            n = math.hypot(ux, uy) or 1
            ox, oy = ux / n * 4 * s, uy / n * 4 * s
            px, py = -uy / n * 22 * s, ux / n * 22 * s
            front.append(
                _line(
                    (plate[0] - px - oy, plate[1] - py + ox),
                    (plate[0] + px - oy, plate[1] + py + ox),
                    FRAME,
                    5 * s,
                    cap,
                )
            )
            behind.append(_line((plate[0], plate[1]), (plate[0] + 30 * s, FLOOR), FRAME, 3 * s))
        elif name == "hack":
            hip, sh = j["hip"], j["shoulder"]
            behind.append(
                _pad_along(
                    _lerp(hip, sh, -0.1), _lerp(hip, sh, 1.08), -R["w_torso"] * s / 2 - 3 * s, s
                )
            )
        elif name == "pad_lying":
            sh, kn = j["shoulder"], j["l_knee"]
            top = max(sh[1], j["hip"][1]) + R["w_torso"] * s / 2
            behind.append(_bench(min(sh[0], kn[0]) - 4 * s, max(sh[0], kn[0]) + 4 * s, top, s))
            an = j["l_ankle"]
            front.append(_circle((an[0], an[1] - 7 * s), 5 * s, PAD))
        elif name == "preacher":
            hip, sh, el = j["hip"], j["shoulder"], j["a_elbow"]
            behind.append(_line((hip[0], hip[1] + 8 * s), (hip[0], FLOOR), FRAME, 3.4 * s))
            behind.append(_rect(hip[0] - 14 * s, hip[1] + 6 * s, 28 * s, 7 * s, PAD, 3 * s))
            front.append(
                _pad_along(
                    _lerp(sh, el, 0.35), _lerp(sh, el, 1.15), R["w_ua"] * s / 2 + 3 * s, s, 7
                )
            )
            behind.append(_line((el[0], el[1] + 4 * s), (el[0] - 4 * s, FLOOR), FRAME, 3 * s))
        elif name == "hyper":
            hip, an = j["hip"], j["l_ankle"]
            front.append(_circle((hip[0] + 6 * s, hip[1] + 6 * s), 6 * s, PAD))
            front.append(_circle((an[0] + 5 * s, an[1] - 5 * s), 4.5 * s, PAD))
            behind.append(_line((hip[0] + 6 * s, hip[1] + 6 * s), (an[0], FLOOR), FRAME, 3.4 * s))
            behind.append(
                _line((an[0] - 18 * s, FLOOR - 1), (hip[0] + 24 * s, FLOOR - 1), FRAME, 3 * s)
            )
        elif name == "dip_bars":
            hd = j["a_hand"]
            y = hd[1] + 2
            x1, x2 = hd[0] - 34 * s, hd[0] + 30 * s
            for x in (x1 + 3, x2 - 3):
                behind.append(_line((x, y), (x, FLOOR), FRAME, 3.4 * s))
            behind.append(_line((x1 - 6, FLOOR - 1), (x2 + 6, FLOOR - 1), FRAME, 3 * s))
            front.append(_line((x1, y), (x2, y), METAL, 4 * s, cap))
        elif name == "pullup_bar":
            a, b = j["a_hand"], j["b_hand"]
            y = min(a[1], b[1]) + 1
            x1, x2 = min(a[0], b[0]) - 26 * s, max(a[0], b[0]) + 26 * s
            behind.append(_line((x1, y), (x1, 0), FRAME, 3 * s))
            behind.append(_line((x2, y), (x2, 0), FRAME, 3 * s))
            front.append(_line((x1, y), (x2, y), METAL, 3.6 * s, cap))
        elif name == "pullup_bar_side":
            hd = j["a_hand"]
            behind.append(_line((hd[0], hd[1]), (hd[0] - 14 * s, 0), FRAME, 3 * s))
            front.append(_circle((hd[0], hd[1] + 1), 3.6 * s, METAL))
        elif name == "bar_low":
            hd = j["a_hand"]
            behind.append(_line((hd[0], hd[1]), (hd[0], FLOOR), FRAME, 3 * s))
            front.append(_circle((hd[0], hd[1] + 1), 3.6 * s, METAL))
        elif name in ("wall_back", "wall_back_tall"):
            keys = ("hip", "shoulder", "l_heel", "m_heel", "head")
            x = min(j[k][0] for k in keys) - R["w_torso"] * s / 2 - 1
            if name == "wall_back_tall":
                x = max(j[k][0] for k in ("l_toe", "m_toe", "l_heel", "m_heel")) + 4 * s
            behind.append(_rect(x - 6, 0, 6, FLOOR, "#262A28", 0))
        elif name == "wall_front":
            x = max(j[k][0] for k in ("a_hand", "b_hand", "head", "l_toe")) + 2
            behind.append(_rect(x, 0, 6, FLOOR, "#262A28", 0))
        elif name == "wall_behind":
            behind.append(_rect(W * 0.18, 18, W * 0.64, FLOOR - 18, "#1E2220", 6))
        elif name == "door":
            hd = j["a_hand"]
            behind.append(_rect(hd[0] - 1, 10, 7 * s, FLOOR - 10, "#3A3530", 1))
        elif name == "landmine":
            hd = j["a_hand"]
            end = (hd[0] - 120 * s, FLOOR - 4)
            behind.append(_line(end, (hd[0] + 6 * s, hd[1] - 3 * s), METAL, 3 * s, cap))
            behind.append(_circle(end, 4 * s, FRAME))
        elif name == "ab_wheel":
            hd = j["a_hand"]
            front.append(
                _circle(
                    (hd[0], FLOOR - 9 * s),
                    9 * s,
                    IRON,
                    f' stroke="{IRON_RIM}" stroke-width="{_f(2 * s)}"',
                )
            )
        elif name == "parallettes":
            for side in ("b", "a"):
                hd = j[f"{side}_hand"]
                behind.append(_line((hd[0] - 8 * s, FLOOR - 1), (hd[0], hd[1] + 2), FRAME, 2.5 * s))
                behind.append(_line((hd[0] + 8 * s, FLOOR - 1), (hd[0], hd[1] + 2), FRAME, 2.5 * s))
        elif name == "treadmill":
            hip = j["hip"]
            behind.append(_rect(hip[0] - 62 * s, FLOOR - 10 * s, 120 * s, 10 * s, "#2B302D", 4))
            behind.append(
                _line(
                    (hip[0] + 54 * s, FLOOR - 8 * s),
                    (hip[0] + 40 * s, hip[1] - 20 * s),
                    FRAME,
                    3.4 * s,
                )
            )
            behind.append(
                _line(
                    (hip[0] + 40 * s, hip[1] - 20 * s),
                    (hip[0] + 24 * s, hip[1] - 14 * s),
                    FRAME,
                    3 * s,
                    cap,
                )
            )
        elif name == "bike":
            hip = j["hip"]
            crank = (
                (j["l_ankle"][0] + j["m_ankle"][0]) / 2,
                (j["l_ankle"][1] + j["m_ankle"][1]) / 2,
            )
            hd = j["a_hand"]
            behind.append(_rect(hip[0] - 12 * s, hip[1] + 5 * s, 22 * s, 6 * s, PAD, 3 * s))
            behind.append(_line((hip[0], hip[1] + 10 * s), crank, FRAME, 3.6 * s))
            behind.append(_line(crank, (hd[0], hd[1] + 6 * s), FRAME, 3.6 * s))
            behind.append(
                _line(
                    (crank[0] - 30 * s, FLOOR - 1), (crank[0] + 40 * s, FLOOR - 1), FRAME, 3.4 * s
                )
            )
            behind.append(_line(crank, (crank[0] - 20 * s, FLOOR), FRAME, 3 * s))
            behind.append(_line(crank, (crank[0] + 30 * s, FLOOR), FRAME, 3 * s))
            behind.append(_circle(crank, 7 * s, IRON, f' stroke="{IRON_RIM}" stroke-width="1.5"'))
        elif name == "rower":
            hip, toe = j["hip"], j["l_toe"]
            behind.append(
                _rect(hip[0] - 50 * s, FLOOR - 6 * s, toe[0] - hip[0] + 90 * s, 5 * s, FRAME, 2)
            )
            behind.append(_rect(hip[0] - 13 * s, hip[1] + 5 * s, 26 * s, 6 * s, PAD, 3 * s))
            fly = (toe[0] + 26 * s, FLOOR - 22 * s)
            behind.append(_circle(fly, 15 * s, "#2B302D", f' stroke="{FRAME}" stroke-width="2"'))
            hd = j["a_hand"]
            behind.append(_line(hd, fly, CABLE, 1.2 * s))
        elif name == "elliptical":
            hip = j["hip"]
            behind.append(_rect(hip[0] - 50 * s, FLOOR - 6 * s, 100 * s, 6 * s, FRAME, 2))
            behind.append(
                _line(
                    (hip[0] + 46 * s, FLOOR - 4), (hip[0] + 34 * s, hip[1] - 40 * s), FRAME, 3.4 * s
                )
            )
            for leg in ("m", "l"):
                ft = j[f"{leg}_heel"]
                behind.append(
                    _line(
                        (ft[0] - 6 * s, ft[1] + 4 * s),
                        (ft[0] + 18 * s, ft[1] + 4 * s),
                        FRAME,
                        3 * s,
                        cap,
                    )
                )
        elif name == "stairs":
            hip = j["hip"]
            for i in range(4):
                behind.append(
                    _rect(
                        hip[0] - 20 * s + i * 14 * s,
                        FLOOR - (i + 1) * 12 * s,
                        40 * s,
                        12 * s,
                        "#2B302D" if i % 2 else "#323834",
                        1,
                    )
                )
        elif name == "skierg":
            sh = j.get("shoulder")
            x = sh[0] + 46 * s
            behind.append(_line((x, FLOOR), (x, 14), FRAME, 4 * s))
            behind.append(_line((x - 16, FLOOR - 1), (x + 16, FLOOR - 1), FRAME, 3 * s))
            for side in ("a", "b"):
                behind.append(_line((x, 22), j[f"{side}_hand"], CABLE, 1.2 * s))
        elif name == "rope":
            a, b = j["a_hand"], j["b_hand"]
            mid = (a[0] - 10, FLOOR + 1)
            behind.append(
                f'<path d="M{_f(a[0])} {_f(a[1])} Q{_f(mid[0] - 40)} {_f(FLOOR + 40)} '
                f'{_f(b[0])} {_f(b[1])}" fill="none" stroke="{accent}" '
                f'stroke-width="{_f(1.6 * s)}"/>'
            )
        elif name == "ropes":
            hd = j["a_hand"]
            x2 = W - 10
            d = f"M{_f(hd[0])} {_f(hd[1])}"
            n = 5
            for i in range(1, n + 1):
                x = hd[0] + (x2 - hd[0]) * i / n
                y = FLOOR - 8 + (-1) ** i * (14 - i * 2)
                d += f" Q{_f(x - (x2 - hd[0]) / n / 2)} {_f(y)} {_f(x)} {_f(FLOOR - 6)}"
            front.append(
                f'<path d="{d}" fill="none" stroke="{IRON_RIM}" stroke-width="{_f(3 * s)}" '
                f'stroke-linecap="round"/>'
            )
        elif name == "sled":
            hd = j["a_hand"]
            front.append(_line((hd[0], hd[1]), (hd[0] + 8 * s, FLOOR - 20 * s), FRAME, 3 * s, cap))
            front.append(_rect(hd[0] + 2 * s, FLOOR - 22 * s, 40 * s, 20 * s, IRON, 3))
            front.append(
                _line((hd[0] - 4 * s, FLOOR - 1), (hd[0] + 50 * s, FLOOR - 1), FRAME, 3 * s)
            )
        elif name == "box_front":
            pass  # drawn by render(), which knows the frame
    # The implement in the hands.
    hands = [("b", True), ("a", False)]
    if implement in ("barbell", "smith"):
        if view == "front":
            front.append(_barbell_front(j["a_hand"], j["b_hand"], s, implement == "smith"))
        else:
            front.append(_barbell_side(j["a_hand"], s, implement == "smith"))
    elif implement == "dumbbell":
        for side, far in hands:
            (behind if far and view != "front" else front).append(
                _dumbbell_front(j[f"{side}_hand"], s)
                if view == "front"
                else _dumbbell_side(j[f"{side}_hand"], s, far)
            )
    elif implement == "kettlebell":
        front.append(_kettlebell(j["a_hand"], s))
    elif implement == "bands" and spec.get("band") in ("hands", "knees"):
        a, b = ("a_hand", "b_hand") if spec["band"] == "hands" else ("l_knee", "m_knee")
        front.append(_line(j[a], j[b], accent, 2.4 * s, cap))
    elif implement in ("cable", "bands"):
        where, height = spec.get("cable") or (
            ("front", "mid") if implement == "cable" else (None, None)
        )
        color = CABLE if implement == "cable" else accent
        src_keys = [spec["cable_from"]] if spec.get("cable_from") else ["b_hand", "a_hand"]
        if where:
            px, py = _pulley(where, height, j, view)
            if implement == "cable" and where != "center":
                behind.append(_line((px, FLOOR), (px, 8), FRAME, 4 * s))
                behind.append(_line((px - 14, FLOOR - 1), (px + 14, FLOOR - 1), FRAME, 3 * s))
                if where == "sides":
                    behind.append(_line((W - px, FLOOR), (W - px, 8), FRAME, 4 * s))
            for k in src_keys:
                target = (px, py)
                if where == "sides" and k == "b_hand":
                    target = (W - px, py)
                if where == "sides" and k == "a_hand":
                    target = (px, py)
                behind.append(_line(target, j[k], color, 1.4 * s))
            behind.append(_circle((px, py), 3.4 * s, METAL))
        else:  # band under the feet
            foot = j["l_ankle"]
            for k in src_keys:
                behind.append(_line((foot[0], foot[1] + 4), j[k], color, 2 * s, cap))
    return "".join(behind), "".join(front)


# -----------------------------------------------------------------------------
# Frames
# -----------------------------------------------------------------------------
STEP_TITLES = ("Start", "Movement", "Finish")


def _arrow(a, b, accent: str, s: float) -> str:
    dx, dy = b[0] - a[0], b[1] - a[1]
    dist = math.hypot(dx, dy)
    if dist < 14:
        return ""
    ux, uy = dx / dist, dy / dist
    # Start and stop a little away from the joints, bow the path slightly.
    a2 = (a[0] + ux * 7, a[1] + uy * 7)
    b2 = (b[0] - ux * 9, b[1] - uy * 9)
    bow = min(18.0, dist * 0.22)
    c = ((a2[0] + b2[0]) / 2 - uy * bow, (a2[1] + b2[1]) / 2 + ux * bow)
    # Arrowhead along the curve's end tangent.
    tx, ty = b2[0] - c[0], b2[1] - c[1]
    tl = math.hypot(tx, ty) or 1
    tx, ty = tx / tl, ty / tl
    hs = 7.5
    p1 = (b2[0] - tx * hs - ty * hs * 0.6, b2[1] - ty * hs + tx * hs * 0.6)
    p2 = (b2[0] - tx * hs + ty * hs * 0.6, b2[1] - ty * hs - tx * hs * 0.6)
    return (
        f'<path d="M{_f(a2[0])} {_f(a2[1])} Q{_f(c[0])} {_f(c[1])} {_f(b2[0])} {_f(b2[1])}" '
        f'fill="none" stroke="{accent}" stroke-width="2.6" stroke-linecap="round" '
        f'stroke-dasharray="1 5.5"/>'
        f'<path d="M{_f(p1[0])} {_f(p1[1])} L{_f(b2[0] + tx * 1.5)} {_f(b2[1] + ty * 1.5)} '
        f'L{_f(p2[0])} {_f(p2[1])}" fill="none" stroke="{accent}" stroke-width="2.6" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )


MOVERS = ("a_hand", "b_hand", "l_ankle", "m_ankle", "hip", "shoulder", "head", "l_knee", "m_knee")


def _main_mover(j0: dict, j1: dict) -> str:
    best, key = -1.0, "a_hand"
    for k in MOVERS:
        if k in j0 and k in j1:
            d = math.hypot(j1[k][0] - j0[k][0], j1[k][1] - j0[k][1])
            if d > best + 0.5:
                best, key = d, k
    return key


def _box(spec: dict, which: str, j: dict, s: float) -> str:
    """The plyo box / step: under the lead foot when raised, in front of it otherwise."""
    if "box_front" not in (spec.get("props") or []):
        return ""
    top = FLOOR - 44 * s
    an = j["l_ankle"]
    raised = (spec.get("lift_end", 0) and which == "end") or j["l_heel"][1] < FLOOR - 20
    if raised:
        x = an[0] - 14 * s
    else:
        x = j["l_toe"][0] + 14 * s
    return _rect(x, top, 40 * s, FLOOR - top, "#3B342D", 3) + _rect(
        x, top, 40 * s, 5 * s, "#4C443B", 2
    )


@lru_cache(maxsize=4096)
def frame_svg(
    family: str, sex: str, implement: Optional[str], step: int, accent: str, uid: str
) -> str:
    """One illustrated step (0 Start, 1 Movement, 2 Finish) as a standalone inline SVG."""
    spec = F[family]
    view = spec.get("view", "side")
    j0, j1, s = place(spec, sex, implement)
    hold = spec.get("hold", False)
    if step == 0:
        main, which, ghost = j0, "start", None
    elif step == 1:
        main, which, ghost = j1, "end", j0
    else:
        main, which, ghost = (j1, "end", None) if hold else (j0, "start", j1)
    draw = _front_figure if view == "front" else _side_figure
    behind, front = equipment_svg(spec, main, view, s, implement, accent, sex)
    ghost_svg = arrow = ""
    if ghost is not None:
        ghost_svg = f'<g opacity="0.16">{draw(ghost, sex, s, ghost=True)}</g>'
        k = _main_mover(ghost, main)
        arrow = _arrow(ghost[k], main[k], accent, s)
    floor = (
        ""
        if spec.get("anchor") in ("hands", "center")
        else (
            f'<line x1="0" y1="{FLOOR + 1}" x2="{W}" y2="{FLOOR + 1}" stroke="{FLOOR_LINE}" '
            f'stroke-width="1.5"/>'
        )
    )
    if spec.get("anchor") == "hands" and family not in ("dip",):
        floor = ""
    return (
        f'<svg class="nfg-form-figure" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-hidden="true">'
        f'<defs><radialGradient id="{uid}g" cx="50%" cy="46%" r="62%">'
        f'<stop offset="0" stop-color="{accent}" stop-opacity="0.20"/>'
        f'<stop offset="1" stop-color="{accent}" stop-opacity="0"/></radialGradient></defs>'
        f'<rect width="{W}" height="{H}" fill="#121514"/>'
        f'<rect width="{W}" height="{H}" fill="url(#{uid}g)"/>'
        f"{floor}{_box(spec, which, main, s)}{ghost_svg}{behind}"
        f"{draw(main, sex, s)}{front}{arrow}"
        f"</svg>"
    )


def figures_for(ex: dict, accent: str = "#F5B731") -> List[str]:
    """The three step figures for an exercise dict (content file or DB doc)."""
    fam = family_for(ex)
    sex = sex_for(ex)
    imp = implement_for(ex)
    if F[fam].get("no_implement"):
        imp = None
    base = (ex.get("slug") or "x").replace("-", "")[:10]
    return [frame_svg(fam, sex, imp, i, accent, f"{base}{i}") for i in range(3)]


# -----------------------------------------------------------------------------
# Step text: from the exercise's own instructions unless the file sets `form:`
# -----------------------------------------------------------------------------
def _first(items, i=0) -> str:
    items = [x for x in (items or []) if isinstance(x, str) and x.strip()]
    return items[i].strip() if len(items) > i else ""


def _avoid(mistake: str) -> str:
    if not mistake:
        return ""
    word = mistake.split(" ", 1)[0]
    if word.lower().endswith("ing"):
        return "Avoid " + mistake[0].lower() + mistake[1:]
    return "Watch for: " + mistake[0].lower() + mistake[1:]


PREP_WORDS = {"take", "brace", "breathe", "inhale", "exhale", "grip", "set", "hold", "keep"}


def step_texts(w: dict) -> List[dict]:
    """[{title, caption, tip}] x3. Works on a content file or a synced DB doc."""
    how = w.get("instructions") or {}
    setup = how.get("setup") if how else None
    setup = setup if setup is not None else w.get("setup")
    steps = (how.get("steps") if how else None) or w.get("steps") or []
    cues = w.get("tips") or w.get("cues") or []
    breathing = (how.get("breathing") if how else None) or w.get("breathing") or ""
    mistakes = w.get("mistakes") or []
    safety = w.get("safety") or []
    fam = family_for(w)
    hold = F[fam].get("hold", False)

    start = _first(setup, -1) if setup else _first(steps)
    body = [x for x in steps if isinstance(x, str) and x.strip()]
    pool = body[:-1] if len(body) > 2 else body
    if not setup:
        pool = pool[1:] or pool
    moving = [x for x in pool if x.split(" ", 1)[0].lower().strip(",.") not in PREP_WORDS]
    move = (moving or pool or [""])[0].strip()
    finish = _first(steps, len(steps) - 1) if len(steps) > 1 else ""
    if finish == move:
        finish = ""
    if not finish:
        finish = "Hold it, breathing steadily." if hold else "Return to the start under control."
    texts = [
        {"title": "Start", "caption": start, "tip": _first(cues, 0)},
        {"title": "Movement", "caption": move, "tip": _first(cues, 1) or breathing},
        {
            "title": "Finish",
            "caption": finish,
            "tip": _avoid(_first(mistakes)) or _first(safety) or _first(cues, 2),
        },
    ]
    custom = w.get("form") or w.get("form_steps") or []
    for i, item in enumerate(custom[:3]):
        if isinstance(item, dict):
            for key in ("caption", "tip"):
                if (item.get(key) or "").strip():
                    texts[i][key] = item[key].strip()
    return texts


def lighten(hex_color: str, k: float = 0.35) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "#F5B731"
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return "#{:02X}{:02X}{:02X}".format(*(int(c + (255 - c) * k) for c in (r, g, b)))
