"""
test_lod_reduce.py — UEO3DE_LOD_REDUCE: scaling a mesh's AUTHORED LOD ladder.

Pure: no editor. `unreal` is stubbed, so only the parsing and the triangle
arithmetic are exercised -- the bake itself needs UE.
Run: python Tests/perf/test_lod_reduce.py

WHY THIS EXISTS. The Nanite budgets (UEO3DE_LOD0_RATIO, UEO3DE_LOD_RATIOS)
reach only meshes with no authored chain, which is why halving them did
nothing at all to NYC_Level_WC: every NYC mesh carries artist LODs
(SM_Umbrella01 ships 4569/3424/2298/1160/591 vertices) and took the verbatim
path. Asking for "lower polycount" there has to scale the ladder instead.
The failure mode this pins is the quiet one: a knob that parses, reports
nothing, and exports the same 19 GB.
"""

import os
import sys
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The exporter runs inside UE; stub the module so the parser can be imported.
if "unreal" not in sys.modules:
    sys.modules["unreal"] = types.ModuleType("unreal")

sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

from ueo3de import mesh_export  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def reduce_with(value):
    """_lod_reduce() under UEO3DE_LOD_REDUCE=value, cache cleared."""
    del mesh_export._LOD_REDUCE_CACHE[:]
    if value is None:
        os.environ.pop("UEO3DE_LOD_REDUCE", None)
    else:
        os.environ["UEO3DE_LOD_REDUCE"] = value
    try:
        return mesh_export._lod_reduce()
    finally:
        os.environ.pop("UEO3DE_LOD_REDUCE", None)
        del mesh_export._LOD_REDUCE_CACHE[:]


# --- the default is the old behaviour ----------------------------------------
# Every export before this knob existed wrote authored LODs verbatim, and a
# default below 1.0 would silently re-cut every artist ladder in every level.
check(reduce_with(None) == 1.0,
      "unset UEO3DE_LOD_REDUCE must keep authored LODs verbatim (1.0), got %r"
      % (reduce_with(None),))
check(mesh_export._LOD_REDUCE_DEFAULT == 1.0,
      "the documented default must be 1.0")

# --- parsing ------------------------------------------------------------------
check(reduce_with(" 0.5 ") == 0.5, "the ratio must parse with whitespace")
check(reduce_with("1.0") == 1.0, "1.0 must parse as verbatim")
for garbage in ("half", "0", "-0.5", "1.5", ""):
    if garbage == "":
        continue                      # empty is "unset", covered above
    try:
        reduce_with(garbage)
        check(False, "UEO3DE_LOD_REDUCE=%r must raise, not fall back" % garbage)
    except mesh_export.MeshExportError:
        pass

# --- the triangle arithmetic --------------------------------------------------
# The ladder keeps its proportions: each authored step is scaled by the same
# factor, so LOD1 stays lighter than LOD0 and no step collapses to nothing.
ladder = (26055, 19542, 13026, 6516, 3258)      # SM_Umbrella01, polygon indices
halved = [mesh_export._reduce_target(n, 0.5) for n in ladder]
check(halved == [13027, 9771, 6513, 3258, 1629],
      "0.5 must halve every authored step; got %r" % (halved,))
check(all(a > b for a, b in zip(halved, halved[1:])),
      "a scaled ladder must stay monotonic; got %r" % (halved,))

check(mesh_export._reduce_target(12345, 1.0) == 12345,
      "1.0 must keep every triangle")
check(mesh_export._reduce_target(3, 0.01) == 1,
      "a tiny mesh must keep at least one triangle, never zero")
check(mesh_export._reduce_target(0, 0.5) == 0,
      "an empty mesh must stay empty rather than gain a triangle")


# --- _reduce_by_ratio hands the reducer a smaller target, or nothing at all ---
class FakeDyn(object):
    def __init__(self, triangles):
        self.triangles = triangles


simplified = []
mesh_export._simplify_to_triangle_count = (
    lambda dyn, target: simplified.append((dyn.triangles, target)) or "reduced")

verbatim = FakeDyn(1000)
check(mesh_export._reduce_by_ratio(verbatim, 1.0) is verbatim,
      "at 1.0 the mesh must pass through untouched, with no reducer call")
check(simplified == [], "1.0 must not call the reducer at all")

FakeDyn.get_triangle_count = lambda self: self.triangles
check(mesh_export._reduce_by_ratio(FakeDyn(1000), 0.5) == "reduced",
      "below 1.0 the reducer's result must be returned")
check(simplified == [(1000, 500)],
      "the reducer must be asked for the scaled count; got %r" % (simplified,))

del simplified[:]
tiny = FakeDyn(1)
check(mesh_export._reduce_by_ratio(tiny, 0.5) is tiny,
      "a one-triangle mesh must pass through rather than be 'reduced' to itself")
check(simplified == [], "a target that saves nothing must not call the reducer")

# --- the bounds expectation must describe the REDUCED file --------------------
# The export verifier compares the written FBX against a recorded expectation.
# For a single-LOD non-Nanite mesh that expectation is the source asset's
# bounds -- which stops being true the moment the ladder is scaled: NYC's
# EditorSkySphere bulges to Z=+-4124.6 against the asset's +-4096 and failed
# a completed three-hour export. Reduced files must take the chain-derived
# expectation, the same one multi-LOD and Nanite meshes already use.
check(mesh_export._expectation_from_chain(1, False, True, 0.5) is True,
      "a REDUCED single-LOD FBX must take the chain-derived expectation")
check(mesh_export._expectation_from_chain(1, False, True, 1.0) is False,
      "an unreduced single-LOD FBX must keep the asset-bounds expectation")
check(mesh_export._expectation_from_chain(4, False, True, 1.0) is True,
      "a multi-LOD mesh takes the chain-derived expectation, as before")
check(mesh_export._expectation_from_chain(1, True, True, 1.0) is True,
      "a Nanite source read takes the chain-derived expectation, as before")
check(mesh_export._expectation_from_chain(1, False, False, 0.5) is False,
      "a glTF writes an unreduced flattened mesh and keeps asset bounds")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
