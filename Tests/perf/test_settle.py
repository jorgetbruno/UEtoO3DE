"""
test_settle.py — the settle constant and the post-save bake verification.

Pure: no editor, no O3DE. Everything here is file I/O and arithmetic, which is
the whole point -- the check it covers exists precisely because the editor
CANNOT see what it checks.

Background, all of it measured (PERFORMANCE.md carries the figures):

  A Jolt mesh collider bakes on its component's tick and the result lands in
  the prefab as `ShapeConfiguration.CookedData`. Serialize too early and the
  component is written out fully configured with no cooked data -- a collider
  that collides with nothing, in a file that saved without error, on an import
  that reported PASS. On L_Showcase with the settle removed, 15 of 2501 bakes
  were lost exactly this way and `mesh_colliders` still read 2501.

The property that matters and is easy to get wrong in a way no green suite
would notice: **a detector that reports every entity and one that reports none
both leave the import passing**. So every assertion below has a control that
would fail if the detector drifted to either extreme.

Run: python Tests/perf/test_settle.py       (exit code is the verdict)
"""

import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ueimporter import importer, prefab_build  # noqa: E402
import prefab_diff  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def write_prefab(entities):
    """`entities` is [(name, [component, ...])] -> path to a saved prefab."""
    document = {"ContainerEntity": {"Id": "ContainerEntity", "Name": "Root"},
                "Entities": {}}
    for index, (name, components) in enumerate(entities):
        document["Entities"]["Entity_[%d]" % index] = {
            "Id": "Entity_[%d]" % index,
            "Name": name,
            "Components": {("c%d" % i): c for i, c in enumerate(components)},
        }
    handle = tempfile.NamedTemporaryFile("w", suffix=".prefab", delete=False)
    json.dump(document, handle)
    handle.close()
    return handle.name


def collider(cooked="AAAA", kind="EditorJoltMeshColliderComponent",
             shape=True):
    component = {"$type": kind, "Id": 1}
    if shape:
        component["ShapeConfiguration"] = {}
        if cooked is not None:
            component["ShapeConfiguration"]["CookedData"] = cooked
    return component


MESH = {"$type": "AZ::Render::EditorMeshComponent", "Id": 2,
        "Controller": {"Configuration": {}}}
BOX = {"$type": "EditorJoltBoxColliderComponent", "Id": 3,
       "ShapeConfiguration": {"Dimensions": [1, 1, 1]}}


# ---------------------------------------------------------------------------
# unbaked_colliders: what reached the file
# ---------------------------------------------------------------------------

paths = []

path = write_prefab([("Baked", [collider("SFZDSgEA")])])
paths.append(path)
check(prefab_build.unbaked_colliders(path) == [],
      "a collider WITH cooked data was reported as unbaked")

path = write_prefab([("Empty", [collider("")])])
paths.append(path)
check(prefab_build.unbaked_colliders(path) == ["Empty"],
      "an empty CookedData string was not reported")

path = write_prefab([("NoKey", [collider(cooked=None)])])
paths.append(path)
check(prefab_build.unbaked_colliders(path) == ["NoKey"],
      "a missing CookedData key was not reported")

path = write_prefab([("NoShape", [collider(shape=False)])])
paths.append(path)
check(prefab_build.unbaked_colliders(path) == ["NoShape"],
      "a collider with no ShapeConfiguration at all was not reported")

# The control that stops the detector reporting everything: components that
# are not mesh colliders have no cooked data and must NOT be reported.
path = write_prefab([("PlainMesh", [MESH]),
                     ("BoxCollider", [BOX]),
                     ("Nothing", [])])
paths.append(path)
check(prefab_build.unbaked_colliders(path) == [],
      "non-mesh-collider components were reported as unbaked colliders "
      "(a detector that fires on everything passes just as quietly)")

# Both backends' names carry "MeshCollider"; M3b compares them on content, so
# the check cannot be Jolt-only.
path = write_prefab([("PhysXOne", [collider("", kind="EditorMeshColliderComponent")])])
paths.append(path)
check(prefab_build.unbaked_colliders(path) == ["PhysXOne"],
      "a PhysX-named mesh collider with no cooked data was not reported")

path = write_prefab([("Zebra", [collider("")]),
                     ("Alpha", [collider(cooked=None)]),
                     ("Fine", [collider("DATA")])])
paths.append(path)
check(prefab_build.unbaked_colliders(path) == ["Alpha", "Zebra"],
      "results are not sorted, or a good entity was swept in with the bad")

check(prefab_build.unbaked_colliders(os.path.join(tempfile.gettempdir(),
                                                  "no_such_prefab_xyz.prefab")) == [],
      "a missing prefab should answer [] rather than raising")

# An entity carrying several colliders: one bad is enough to name it, and the
# name must not appear twice per bad collider.
path = write_prefab([("Two", [collider("DATA"), collider("")])])
paths.append(path)
check(prefab_build.unbaked_colliders(path) == ["Two"],
      "an entity with one good and one bad collider was misreported")


# ---------------------------------------------------------------------------
# settle_frames: the constant, and the override that measures it
# ---------------------------------------------------------------------------

os.environ.pop("UEO3DE_SETTLE_FRAMES", None)
default = importer.settle_frames(bake_count=100, skeletal_authored=3)
check(default == 300 + 100 // 2 + 10 * 3,
      "the default settle formula changed without its callers being told: %r"
      % default)

# The margin is the point, not the arithmetic. L_Showcase needs somewhere
# under 30 frames and this must stay far above that -- it is insurance against
# heavier geometry on slower machines, and the failure it insures against
# cannot be repaired once the prefab is written.
showcase = importer.settle_frames(bake_count=2501, skeletal_authored=0)
check(showcase >= 30 * 20,
      "the settle for a 2501-collider level fell to %d frames, under 20x the "
      "measured need -- if that is deliberate, move this test with it" % showcase)

os.environ["UEO3DE_SETTLE_FRAMES"] = "4321"
check(importer.settle_frames(100, 3) == 4321,
      "UEO3DE_SETTLE_FRAMES did not override the formula")

# The trap: "0" is a perfectly good override and must not be read as "unset".
# Every measurement in PERFORMANCE.md that establishes the settle is
# load-bearing was taken at settle=0; if this silently fell back to the
# formula, those runs measured nothing.
os.environ["UEO3DE_SETTLE_FRAMES"] = "0"
check(importer.settle_frames(100, 3) == 0,
      "UEO3DE_SETTLE_FRAMES=0 fell back to the formula, so a settle=0 "
      "measurement would silently have been a full-settle run")

os.environ["UEO3DE_SETTLE_FRAMES"] = "   "
check(importer.settle_frames(100, 3) == default,
      "a blank override should mean 'unset', not crash or mean zero")
os.environ.pop("UEO3DE_SETTLE_FRAMES", None)


# ---------------------------------------------------------------------------
# prefab_diff: the comparator that judged the shorter settle
# ---------------------------------------------------------------------------

good = write_prefab([("A", [collider("LONGDATA")]), ("B", [MESH])])
same = write_prefab([("A", [collider("LONGDATA")]), ("B", [MESH])])
lost = write_prefab([("A", [collider("")]), ("B", [MESH])])
paths.extend([good, same, lost])

result = prefab_diff.compare(prefab_diff.summarize(good), prefab_diff.summarize(same))
check(prefab_diff.total_differences(result) == 0,
      "two identical prefabs did not compare equal: %r" % result)

result = prefab_diff.compare(prefab_diff.summarize(good), prefab_diff.summarize(lost))
check([n for n, _a, _b in result["cooked_differs"]] == ["A"],
      "a blanked CookedData was not caught by the comparator: %r" % result)

# Duplicate names are normal in a saved prefab (a level root named after the
# level sits beside the actors), so they must be compared as a multiset rather
# than collapsed -- collapsing would hide a real difference behind a name.
dup_good = write_prefab([("Same", [collider("DATA")]), ("Same", [collider("DATA")])])
dup_bad = write_prefab([("Same", [collider("DATA")]), ("Same", [collider("")])])
paths.extend([dup_good, dup_bad])
result = prefab_diff.compare(prefab_diff.summarize(dup_good),
                             prefab_diff.summarize(dup_good))
check(prefab_diff.total_differences(result) == 0,
      "duplicate names broke the identity comparison")
result = prefab_diff.compare(prefab_diff.summarize(dup_good),
                             prefab_diff.summarize(dup_bad))
check(prefab_diff.total_differences(result) > 0,
      "one of two identically-named entities lost its bake and the comparator "
      "collapsed them and saw nothing")

# The one direction that must never happen: a false "EQUIVALENT". Reporting a
# difference badly is a diagnostic annoyance; reporting none when the content
# differs is what would have let a shortened settle look safe. Same entity
# count, same names, everything else different.
left = write_prefab([("A", [collider("AAAA")]), ("B", [collider("BBBB")])])
right = write_prefab([("A", [collider("")]), ("B", [MESH])])
paths.extend([left, right])
result = prefab_diff.compare(prefab_diff.summarize(left), prefab_diff.summarize(right))
check(prefab_diff.total_differences(result) > 0,
      "two prefabs with the same names and entirely different contents "
      "compared EQUIVALENT -- the comparator's one fatal failure mode")

for path in paths:
    try:
        os.remove(path)
    except OSError:
        pass

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
