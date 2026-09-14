"""
test_render_framing.py — the render check aims at the level and can fail.

Pure: no editor. Run: python Tests/perf/test_render_framing.py

WHY THIS EXISTS. The level-render check passed on NYC1950 while its "level"
capture was byte-identical to its empty control: the camera never looked at
the city. The check now aims from the level's own extent and compares the
same pose with and without the prefab. These tests pin the aim and the verdict.
"""

import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "Tests", "lib"))

import render_framing as rf  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# A block of city 300 m across, far from the origin, plus one stray entity at
# the origin (a container, a sky sphere) that must not drag the camera home.
city = [(-600.0 + x * 10.0, 400.0 + y * 10.0, 0.0 + (x % 5) * 4.0)
        for x in range(31) for y in range(31)]
pose = rf.framing(city + [(0.0, 0.0, 0.0)])
cx, cy, cz = pose["target"]
check(abs(cx - (-450.0)) < 20.0 and abs(cy - 550.0) < 20.0,
      "the camera must aim at the city's centre, not at the origin; target %r" % (pose["target"],))
px, py, pz = pose["position"]
check(py < cy and abs(px - cx) < 1e-6,
      "the camera must stand back along -Y and look +Y at the centre; position %r" % (pose["position"],))
check(pz > cz and pose["pitch"] < 0.0,
      "the camera must be above the level and pitched down; z %.1f pitch %.1f" % (pz, pose["pitch"]))
back, up = cy - py, pz - cz
check(abs(pose["pitch"] - (-math.degrees(math.atan2(up, back)))) < 1e-6,
      "the pitch must point exactly at the target")
check(back > 300.0 * 0.8,
      "the camera must stand back far enough to hold a 300 m extent; back %.1f" % back)

tiny = rf.framing([(1.0, 2.0, 0.0)])
check(tiny["size"] >= 10.0 and tiny["position"][1] < 2.0,
      "a single-entity level still gets a sensible framing (minimum size)")

try:
    rf.framing([])
    check(False, "framing nothing must raise, not aim at an arbitrary place")
except ValueError:
    pass

# --- the verdict -------------------------------------------------------------
check(rf.verdict_changed(0.0) is not None,
      "byte-identical captures (the NYC failure) must fail")
check(rf.verdict_changed(0.004) is not None,
      "capture noise alone must fail")
check(rf.verdict_changed(0.35) is None, "a level that fills its framing must pass")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
