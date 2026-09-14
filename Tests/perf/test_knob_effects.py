"""
test_knob_effects.py — a knob that had nothing to act on says so.

Pure: no editor. Run: python Tests/perf/test_knob_effects.py

WHY THIS EXISTS. On NYC1950 (no Nanite meshes) halving UEO3DE_LOD0_RATIO and
UEO3DE_LOD_RATIOS changed nothing, and a three-hour export wrote a
byte-identical copy of the previous one without a word. The export now counts
what it baked and names every knob that found nothing to act on.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

from ueo3de import knob_effects as ke  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def knobs(stats, **environ):
    return [k for k, _message in ke.no_effect_warnings(stats, environ)]


NYC = {"nanite": 0, "authored": 380, "single": 88}       # authored LODs, no Nanite
RETRO = {"nanite": 63, "authored": 0, "single": 0}      # all Nanite (RetroCars)

# --- the NYC case, exactly --------------------------------------------------------
check(knobs(NYC, UEO3DE_LOD0_RATIO="0.125", UEO3DE_LOD_RATIOS="0.05,0.02")
      == ["UEO3DE_LOD0_RATIO", "UEO3DE_LOD_RATIOS"],
      "both Nanite ratios on a level with no Nanite mesh must be named")
message = dict(ke.no_effect_warnings(NYC, {"UEO3DE_LOD0_RATIO": "0.125"}))["UEO3DE_LOD0_RATIO"]
check("UEO3DE_LOD_REDUCE" in message and "380" in message,
      "the warning must point at the knob that WOULD act, with the counts: %s" % message)

# --- knobs that did act stay quiet -----------------------------------------------
check(knobs(NYC, UEO3DE_LOD_REDUCE="0.5") == [],
      "LOD_REDUCE on authored meshes acted and must stay quiet")
check(knobs(RETRO, UEO3DE_LOD0_RATIO="0.25", UEO3DE_LOD_RATIOS="0.1") == [],
      "Nanite ratios on Nanite meshes acted and must stay quiet")
check(knobs(NYC) == [], "no knobs set, nothing to say")

# --- the mirror case and the rest -------------------------------------------------
check(knobs(RETRO, UEO3DE_LOD_REDUCE="0.5") == ["UEO3DE_LOD_REDUCE"],
      "LOD_REDUCE on an all-Nanite level must be named")
check(knobs(RETRO, UEO3DE_LOD_REDUCE="1.0") == [],
      "LOD_REDUCE=1.0 is the default and asks for nothing")
check(knobs(RETRO, UEO3DE_LOD_RATIOS="0.1", UEO3DE_LOD_CHAIN="0") == ["UEO3DE_LOD_RATIOS"],
      "far-LOD ratios with the chain off have no far LODs to budget")
check(knobs(NYC, UEO3DE_NANITE_FALLBACK="1") == ["UEO3DE_NANITE_FALLBACK"],
      "the Nanite fallback read on a level with no Nanite must be named")
check(knobs(NYC, UEO3DE_DEFER_BUILD="1", UEO3DE_LOD_CHAIN="0") == ["UEO3DE_DEFER_BUILD"],
      "rebuild deferral with no LOD chains written must be named")
check(knobs({"nanite": 0, "authored": 0, "single": 0}, UEO3DE_LOD0_RATIO="0.1") == [],
      "an export that baked nothing (reused meshes) has no evidence either way")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
