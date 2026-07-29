"""
test_convex.py — collapsing N identical convex hulls into one.

Pure: no editor. Run: python Tests/perf/test_convex.py  (exit code is the verdict)

WHY. `_author_shape` answers a `convex` element with
`add_mesh_collider(convex=True)`, which hulls the entity's WHOLE RENDER MESH and
ignores the element -- its offset, its rotation, which part of the model it
covers. N convex elements therefore author N byte-identical colliders, and the
union of N identical hulls is one hull.

Measured on a 4.27-era siege map: one `Scaf_Tower` carries 340 convex elements,
12,147 mesh colliders across the level where 3,425 do the same job, and the
import peaked at 24 GB for 3,677 entities before dying inside
`idle_wait_frames`.

THE PROPERTY THAT MATTERS, and the reason this is not simply "dedupe":
collapsing must not touch anything else. A filter that dropped boxes, or
reordered shapes, or collapsed convex elements belonging to DIFFERENT entities
into one, would also make the import cheap -- and would silently change the
level's collision. So every assertion below pins what survives, not just what
goes.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import physics_build  # noqa: E402
from ueimporter.report import Report  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def shape(kind, tag=None):
    out = {"type": kind}
    if tag is not None:
        out["tag"] = tag
    return out


class FakeAdapter(object):
    """Just enough adapter to answer the one question the collapse asks.

    CAP_SHAPE_CONVEX is the whole decision: a backend that advertises it hulls
    the render mesh and ignores the element, so N elements are interchangeable;
    a backend that does not gives each element its own AABB box, so they are
    not. Jolt is the first, PhysX the second.
    """

    def __init__(self, convex):
        self._convex = convex

    def name(self):
        return "jolt" if self._convex else "physx"

    def capabilities(self):
        from ueimporter.adapters import base
        return {base.CAP_SHAPE_CONVEX} if self._convex else set()


HULLING = FakeAdapter(True)      # Jolt: render-mesh hull, element ignored
BOXING = FakeAdapter(False)      # PhysX: per-element AABB box


def collapse(shapes, subject="Thing", adapter=HULLING):
    report = Report()
    kept = physics_build._collapse_convex(shapes, subject, report, adapter)
    codes = [r["code"] for r in report.records()]
    return kept, codes, report


# --- THE BACKEND THAT MUST NOT COLLAPSE ------------------------------------
# On a backend without CAP_SHAPE_CONVEX every element becomes a box over ITS
# OWN aabb, at its own centre -- 340 boxes tracing the shape of the
# decomposition. Collapsing them keeps one small box and throws the tower away.
# This is not hypothetical: the first version of _collapse_convex did exactly
# that, shipped, and every Jolt suite stayed green because the fixture has no
# multi-convex asset.
many = [shape("convex", i) for i in range(340)]
kept, codes, _ = collapse(many, adapter=BOXING)
check(kept == many,
      "a backend that boxes each convex element had %d of 340 elements "
      "collapsed away -- its collision would be one small box" % (340 - len(kept)))
check(codes == [],
      "collapsing nothing should report nothing on the boxing backend, got %r"
      % codes)

# Mixed shapes on the boxing backend: still untouched, order still intact.
mixed = [shape("box", "b"), shape("convex", 1), shape("sphere", "s"),
         shape("convex", 2)]
kept, _codes, _ = collapse(mixed, adapter=BOXING)
check(kept == mixed,
      "the boxing backend's shape list was altered: %r" % kept)

# --- the pathological case -------------------------------------------------
kept, codes, _ = collapse([shape("convex", i) for i in range(340)])
check(len(kept) == 1, "340 convex elements collapsed to %d, expected 1" % len(kept))
check(codes == ["PHYS_SHAPE_APPROXIMATED"],
      "collapsing 340 hulls should report exactly one approximation, got %r" % codes)

# --- one convex is left completely alone, and reports nothing ---------------
one = [shape("convex", "only")]
kept, codes, _ = collapse(one)
check(kept == one, "a single convex element was altered: %r" % kept)
check(codes == [],
      "a single convex element must not report an approximation, got %r" % codes)

check(collapse([])[0] == [], "an empty shape list should stay empty")

# --- THE CONTROL: everything that is not convex must survive untouched ------
# add_box_collider/add_sphere_collider DO use the element's dimensions and
# offset, so those elements are all distinct and dropping any is a real loss.
mixed = [shape("box", 1), shape("convex", "a"), shape("sphere", 2),
         shape("convex", "b"), shape("capsule", 3), shape("convex", "c")]
kept, codes, _ = collapse(mixed)
check([s["type"] for s in kept] == ["box", "convex", "sphere", "capsule"],
      "collapsing changed the non-convex shapes or their order: %r"
      % [s["type"] for s in kept])
check([s.get("tag") for s in kept if s["type"] != "convex"] == [1, 2, 3],
      "a non-convex element lost its data: %r" % kept)
check(kept[1]["tag"] == "a",
      "the FIRST convex element should be the survivor, got %r" % kept[1])

# Many primitives and no convex at all: nothing to do, nothing to report.
prims = [shape("box", i) for i in range(50)]
kept, codes, _ = collapse(prims)
check(kept == prims and codes == [],
      "a list with no convex elements was altered or reported: %d kept, %r"
      % (len(kept), codes))

# Two convex among many primitives still collapses, and still keeps every prim.
many = [shape("box", i) for i in range(10)] + [shape("convex", "x"),
                                               shape("convex", "y")]
kept, codes, _ = collapse(many)
check(len(kept) == 11 and sum(1 for s in kept if s["type"] == "convex") == 1,
      "expected 10 primitives + 1 convex, got %r" % [s["type"] for s in kept])
check(codes == ["PHYS_SHAPE_APPROXIMATED"], "two hulls should report once, got %r" % codes)

# --- the caller's list must not be mutated ---------------------------------
original = [shape("convex", i) for i in range(5)]
before = list(original)
collapse(original)
check(original == before,
      "_collapse_convex mutated the shape list it was given (%d -> %d)"
      % (len(before), len(original)))

# --- the warning has to name the real count --------------------------------
_kept, _codes, report = collapse([shape("convex", i) for i in range(26)])
detail = report.records()[0]["detail"]
check("26" in detail,
      "the warning does not say how many pieces were collapsed: %r" % detail)

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
