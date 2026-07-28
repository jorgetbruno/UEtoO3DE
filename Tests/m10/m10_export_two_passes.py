"""
m10_export_two_passes.py — the UE half of the re-import acceptance.

The plan's test is "move one actor in UE, re-export, re-import; assert entity
count unchanged and exactly one transform differs". This produces the two
manifests that make that checkable:

    Exports/M10_pass1/manifest.json     Fixture_01 as committed
    Exports/M10_pass2/manifest.json     identical but for ONE actor, moved

`Prim_Box` is the actor: a leaf static mesh with no children, so moving it
changes exactly one entity's transform. Moving a parent would move its
children too and the "exactly one" assertion would be measuring the wrong
thing -- it would still pass or fail for reasons unrelated to re-import.

The level is NEVER saved. The move happens in memory, both exports read it,
and the .umap on disk is untouched -- otherwise this test would quietly
rewrite the fixture that eight other suites depend on.

Run: Tests/ue/run_ue_editor_python.bat Tests/m10/m10_export_two_passes.py <result>
"""

import os
import sys
import traceback

import unreal

# Derived from this file, never configured: a value that can be
# computed cannot be configured WRONG, and 40 files hardcoding one
# machine's drive letters is what that mistake looked like here.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))).replace("\\", "/")
PACKAGE_ROOT = REPO_ROOT + "/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python"
MAP_PATH = "/Game/Maps/Fixture_01"
PASS1_DIR = REPO_ROOT + "/Exports/M10_pass1"
PASS2_DIR = REPO_ROOT + "/Exports/M10_pass2"
RESULT_PATH = REPO_ROOT + "/Tests/m10/results/m10_export_two_passes_result.txt"

# The actor to move, and by how much. 3 m is far larger than any float noise
# and far smaller than the level, so the moved entity is unmistakable.
TARGET_LABEL = "Prim_Box"
DELTA_CM = unreal.Vector(300.0, 0.0, 0.0)

if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

lines = []
failures = []


def log(msg=""):
    lines.append(str(msg))
    unreal.log("[m10-export] " + str(msg))


def fail(msg):
    failures.append(str(msg))
    log("FAIL: " + str(msg))


def check(condition, msg):
    if not condition:
        fail(msg)
    return condition


def find_actor(label):
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in subsystem.get_all_level_actors():
        if actor.get_actor_label() == label:
            return actor
    return None


def main():
    from ueo3de import export_api

    unreal.EditorLoadingAndSavingUtils.load_map(MAP_PATH)

    log("=== pass 1: Fixture_01 as it stands ===")
    first = export_api.export_level(MAP_PATH, PASS1_DIR, log=lambda m: None)
    log("  entities=%d assets=%d" % (first["counts"]["entities"],
                                     first["counts"]["assets"]))

    log("")
    log("=== move %s by %s cm ===" % (TARGET_LABEL, DELTA_CM))
    actor = find_actor(TARGET_LABEL)
    if not check(actor is not None,
                 "no actor labelled %r in %s" % (TARGET_LABEL, MAP_PATH)):
        return
    before = actor.get_actor_location()
    actor.set_actor_location(before + DELTA_CM, False, False)
    after = actor.get_actor_location()
    log("  %s: %s -> %s" % (TARGET_LABEL, before, after))
    check(abs((after - before).x - DELTA_CM.x) < 1e-3,
          "the actor did not move: %s -> %s" % (before, after))

    log("")
    log("=== pass 2: the same level, one actor moved ===")
    # load=False is essential and not an optimisation: the default reloads the
    # map from disk, which would revert the move (and invalidate `actor`) so
    # the two passes came out identical. That reload is also why the menu item
    # passes load=False -- it would discard a user's unsaved edits.
    second = export_api.export_level(MAP_PATH, PASS2_DIR, log=lambda m: None,
                                     load=False)
    log("  entities=%d assets=%d" % (second["counts"]["entities"],
                                     second["counts"]["assets"]))

    check(first["counts"]["entities"] == second["counts"]["entities"],
          "moving an actor changed the entity count: %d -> %d"
          % (first["counts"]["entities"], second["counts"]["entities"]))

    log("")
    log("=== the two manifests differ in exactly one transform ===")
    # Asserted here as well as on the O3DE side, because a failure here means
    # the EXPORT is wrong, and a failure there means the IMPORT is -- and
    # telling those apart after the fact is most of the debugging.
    ids_first = {e["id"]: e for e in first["document"]["entities"]}
    ids_second = {e["id"]: e for e in second["document"]["entities"]}
    check(set(ids_first) == set(ids_second),
          "entity ids changed between passes; re-import matches on these")
    moved = []
    for entity_id, entity in ids_first.items():
        other = ids_second.get(entity_id)
        if other is None:
            continue
        if entity["transform"]["world"] != other["transform"]["world"]:
            moved.append(entity["name"])
    log("  entities whose world transform changed: %r" % (moved,))
    check(moved == [TARGET_LABEL],
          "expected exactly [%r] to move, got %r" % (TARGET_LABEL, moved))

    # Put it back in memory, purely so an interactive session is not left with
    # a modified level. Nothing is saved either way. The actor is looked up
    # again rather than reused: any reload in between invalidates the handle,
    # and a stale one raises "ObjectInstance is null".
    restored = find_actor(TARGET_LABEL)
    if restored is not None:
        restored.set_actor_location(before, False, False)
        log("  restored %s to %s (level never saved)" % (TARGET_LABEL, before))


try:
    main()
except Exception:
    fail("EXCEPTION: " + traceback.format_exc())

log("")
log("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
with open(RESULT_PATH, "w") as handle:
    handle.write("\n".join(lines) + "\n")

print("RESULT: " + ("PASS" if not failures else "FAIL"))
try:
    unreal.SystemLibrary.quit_editor()
except Exception:
    pass
if failures:
    raise SystemExit(1)
