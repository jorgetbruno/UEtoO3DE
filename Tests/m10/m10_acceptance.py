"""
m10_acceptance.py — the plan's M10 test, on the O3DE side.

    "move one actor in UE, re-export, re-import -- assert entity count
     unchanged and exactly one transform differs"

`m10_export_two_passes.py` produced the two manifests (Exports/M10_pass1 and
M10_pass2, identical but for `Prim_Box` moved 3 m). This imports the first,
re-imports the second into the SAME prefab, and measures the difference in the
saved file.

Beyond the plan's letter, two things it would be easy to ship broken:

  * a hand edit in O3DE must SURVIVE a re-import and be reported. Tested by
    editing the saved prefab and re-importing on top of it.
  * that preservation must be doing something. The control is the same
    re-import with `reimport=False`, which must overwrite the edit. Without
    it, "the value is still there" is equally consistent with an import that
    never writes transforms at all.

Run: Tests/o3de/run_o3de_python.bat Tests/m10/m10_acceptance.py <result> <project>
"""

import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm10_acceptance_result.txt')

PASS1 = os.path.join(REPO_ROOT, "Exports", "M10_pass1")
PASS2 = os.path.join(REPO_ROOT, "Exports", "M10_pass2")
MOVED_ENTITY = "Prim_Box"
HAND_EDITED_ENTITY = "Prim_Sphere"
HAND_EDIT_DELTA_Z = 2.0

lines = []
failures = []


def log(msg=""):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def check(condition, msg):
    if not condition:
        fail(msg)
    return condition


def diff_transforms(before, after):
    """Names whose transform differs between two read_prefab() snapshots."""
    from ueimporter import reimport
    names = set(before) | set(after)
    changed = []
    for name in sorted(names):
        left, right = before.get(name), after.get(name)
        if left is None or right is None:
            changed.append(name)
        elif not reimport.transforms_equal(left, right):
            changed.append(name)
    return changed


def main():
    import azlmbr.legacy.general as general

    from ueimporter import importer, reimport

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = "%s/Prefabs/Fixture_01_M10.prefab" % project_root
    project_assets = os.path.join(project_root, "Assets")

    for export_dir in (PASS1, PASS2):
        if not check(os.path.isfile(os.path.join(export_dir, "manifest.json")),
                     "missing %s -- run Tests/m10/m10_export_two_passes.py first"
                     % export_dir):
            return

    # A clean slate: this suite is about what a SECOND import does, so a
    # ledger left by a previous run would make the first import incremental
    # and the whole measurement meaningless.
    for stale in (prefab_path, reimport.ledger_path_for(prefab_path)):
        if os.path.exists(stale):
            os.remove(stale)
            log("removed stale " + os.path.basename(stale))

    def do_import(export_dir, reimport_mode=True):
        return importer.import_level(
            manifest_path=os.path.join(export_dir, "manifest.json"),
            source_assets_root=os.path.join(export_dir, "Assets"),
            project_assets_root=project_assets,
            prefab_path=prefab_path,
            restage=True,
            reimport=reimport_mode,
            log=lambda m: None)

    log("=== 1. first import (pass 1) ===")
    report1, _saved = do_import(PASS1)
    with open(os.path.join(PASS1, "manifest.json"), "r") as handle:
        manifest1 = json.load(handle)
    log("  entities_created = %d (manifest has %d)"
        % (report1.counters.get("entities_created", 0), len(manifest1["entities"])))
    check(report1.counters.get("entities_created") == len(manifest1["entities"]),
          "first import created %r of %d entities"
          % (report1.counters.get("entities_created"), len(manifest1["entities"])))
    check(os.path.isfile(prefab_path), "no prefab written")
    ledger = reimport.load_ledger(prefab_path)
    check(ledger is not None,
          "no import ledger beside the prefab; a re-import has nothing to "
          "match against")
    check(report1.counters.get("reimport_conflicts", 0) == 0,
          "a first import cannot have conflicts")
    before = reimport.read_prefab(prefab_path)
    log("  %d entities in the saved prefab" % len(before))

    log("")
    log("=== 2. re-import (pass 2: one actor moved 3 m) ===")
    report2, _saved = do_import(PASS2)
    after = reimport.read_prefab(prefab_path)
    log("  counters: created=%s added=%s removed=%s conflicts=%s"
        % (report2.counters.get("entities_created"),
           report2.counters.get("reimport_added"),
           report2.counters.get("reimport_removed"),
           report2.counters.get("reimport_conflicts")))

    check(report2.counters.get("entities_created")
          == report1.counters.get("entities_created"),
          "entity count changed across the re-import: %r -> %r"
          % (report1.counters.get("entities_created"),
             report2.counters.get("entities_created")))
    check(len(after) == len(before),
          "the saved prefab's entity count changed: %d -> %d (entities were "
          "duplicated rather than matched)" % (len(before), len(after)))
    check(report2.counters.get("reimport_added", -1) == 0
          and report2.counters.get("reimport_removed", -1) == 0,
          "moving an actor must not read as add/remove: added=%r removed=%r"
          % (report2.counters.get("reimport_added"),
             report2.counters.get("reimport_removed")))
    check(report2.counters.get("reimport_conflicts", -1) == 0,
          "nothing was hand-edited, but %r conflicts were reported"
          % report2.counters.get("reimport_conflicts"))

    changed = diff_transforms(before, after)
    log("  transforms that differ: %r" % (changed,))
    check(changed == [MOVED_ENTITY],
          "expected exactly [%r] to differ, got %r" % (MOVED_ENTITY, changed))
    if changed == [MOVED_ENTITY]:
        delta = [round(a - b, 3) for a, b in zip(after[MOVED_ENTITY]["translate"],
                                                 before[MOVED_ENTITY]["translate"])]
        log("  %s moved by %r m in O3DE" % (MOVED_ENTITY, delta))
        # UE +X 3 m maps to O3DE +X 3 m under Lane A (x, -y, z)/100.
        check(abs(delta[0] - 3.0) < 1e-3 and abs(delta[1]) < 1e-3
              and abs(delta[2]) < 1e-3,
              "the move did not survive Lane A intact: %r" % (delta,))

    log("")
    log("=== 3. a hand edit in O3DE survives a re-import, and is reported ===")
    with open(prefab_path, "r") as handle:
        document = json.load(handle)
    edited_value = None
    for entity in (document.get("Entities") or {}).values():
        if entity.get("Name") != HAND_EDITED_ENTITY:
            continue
        for component in (entity.get("Components") or {}).values():
            if "TransformComponent" not in str(component.get("$type", "")):
                continue
            data = component.setdefault("Transform Data", {})
            translate = list(data.get("Translate", [0.0, 0.0, 0.0]))
            translate[2] += HAND_EDIT_DELTA_Z
            data["Translate"] = translate
            edited_value = translate
    check(edited_value is not None,
          "could not find %r in the prefab to edit" % HAND_EDITED_ENTITY)
    with open(prefab_path, "w") as handle:
        json.dump(document, handle, indent=4)
    log("  hand-edited %s to translate=%r" % (HAND_EDITED_ENTITY, edited_value))

    report3, _saved = do_import(PASS2)
    preserved = reimport.read_prefab(prefab_path)
    codes = [r for r in report3.records() if r["code"] == "REIMPORT_ENTITY_CONFLICT"]
    log("  conflicts reported: %r" % ([r["subject"] for r in codes],))
    check(len(codes) == 1 and codes[0]["subject"] == HAND_EDITED_ENTITY,
          "expected exactly one REIMPORT_ENTITY_CONFLICT for %r, got %r"
          % (HAND_EDITED_ENTITY, [r["subject"] for r in codes]))
    check(report3.counters.get("reimport_preserved", 0) == 1,
          "the conflict was reported but %r transform(s) were preserved"
          % report3.counters.get("reimport_preserved"))
    check(preserved[HAND_EDITED_ENTITY]["translate"] == edited_value,
          "the hand edit was overwritten: %r, expected %r"
          % (preserved[HAND_EDITED_ENTITY]["translate"], edited_value))
    check(reimport.transforms_equal(preserved[MOVED_ENTITY], after[MOVED_ENTITY]),
          "preserving one entity disturbed another (%r)" % MOVED_ENTITY)

    log("")
    log("=== 3c. the edit survives a SECOND re-import ===")
    # Preservation that works once and then loses the edit is worse than none,
    # because the user has learned to trust it. This failed until the ledger
    # was written from what the import AUTHORED rather than from the patched
    # file: run 3 then saw file == ledger, reported no conflict, and the
    # rebuild replaced the edit without a word.
    report5, _saved = do_import(PASS2)
    still = reimport.read_prefab(prefab_path)
    codes5 = [r for r in report5.records() if r["code"] == "REIMPORT_ENTITY_CONFLICT"]
    log("  conflicts on the second re-import: %r" % ([r["subject"] for r in codes5],))
    log("  %s translate: %r" % (HAND_EDITED_ENTITY,
                                still[HAND_EDITED_ENTITY]["translate"]))
    check(len(codes5) == 1 and codes5[0]["subject"] == HAND_EDITED_ENTITY,
          "the second re-import stopped reporting the hand edit, so it is "
          "about to be overwritten silently: %r"
          % ([r["subject"] for r in codes5],))
    check(still[HAND_EDITED_ENTITY]["translate"] == edited_value,
          "the hand edit was lost on the SECOND re-import: %r, expected %r"
          % (still[HAND_EDITED_ENTITY]["translate"], edited_value))
    check(not report5.has_errors(),
          "the re-import reported errors (a conflict that could not be "
          "preserved is one)")

    log("")
    log("=== 3b. CONTROL: reimport=False must overwrite the same edit ===")
    # Without this, "the value survived" is also what an importer that never
    # writes transforms at all would produce.
    report4, _saved = do_import(PASS2, reimport_mode=False)
    overwritten = reimport.read_prefab(prefab_path)
    log("  %s translate after reimport=False: %r"
        % (HAND_EDITED_ENTITY, overwritten[HAND_EDITED_ENTITY]["translate"]))
    check(overwritten[HAND_EDITED_ENTITY]["translate"] != edited_value,
          "reimport=False left the hand edit in place, so preservation in "
          "step 3 proves nothing about the re-import path")
    check(report4.counters.get("reimport_conflicts", 0) == 0,
          "reimport=False must not report conflicts it is not honouring")

    log("")
    log("=== 4. the result loads and simulates ===")
    import azlmbr.bus as bus
    import azlmbr.entity as entity_module
    import azlmbr.math as azmath
    import azlmbr.prefab as prefab

    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(30)
    outcome = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'InstantiatePrefab', prefab_path, entity_module.EntityId(),
        azmath.Vector3(0.0, 0.0, 0.0))
    if not check(outcome is not None and outcome.IsSuccess(),
                 "InstantiatePrefab failed for the re-imported prefab"):
        return
    general.idle_wait_frames(60)
    general.enter_game_mode()
    general.idle_wait_frames(120)
    general.exit_game_mode()
    general.idle_wait_frames(30)
    log("  entered and left game mode with the re-imported prefab loaded")


try:
    main()
except Exception:
    fail('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: ' + ('PASS' if not failures else 'FAIL (%d)' % len(failures)))
os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
if not failures:
    _general.exit_no_prompt()
else:
    os._exit(1)
