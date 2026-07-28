"""
perf_bakes.py — every mesh collider that was authored reached the prefab with
baked geometry.

This is the live half of the settle guard. The unit tests prove the detector
tells a baked collider from an unbaked one; this proves the SETTLE is still
long enough on real content, which no amount of pure testing can establish.

The failure it exists to catch is silent by construction:

  * the bake runs on the component's tick and is serialized into the prefab as
    `ShapeConfiguration.CookedData`
  * serialize early and the component is written out fully configured with no
    cooked data -- a collider that collides with nothing
  * `CreatePrefabInMemory` does not complain, the import reports PASS, and
    `mesh_colliders` counts the collider as authored either way
  * it cannot be repaired afterwards: the in-memory template is a snapshot
    that does not track late bakes, and O3DE refuses to re-create a prefab in
    the same session ("Creating prefab as an override edit is currently not
    supported"). Both measured -- see PERFORMANCE.md.

Measured on L_Showcase: with the settle removed, 15 of 2501 bakes were lost
this way and every existing suite stayed green.

Env:  UEO3DE_EXPORT = export directory (default Exports/L_Showcase)
Run:  Tests/perf/run_perf.bat
"""

import json
import os
import shutil
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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'perf_bakes_result.txt')

EXPORT_DIR = os.environ.get("UEO3DE_EXPORT", "").strip() or \
    os.path.join(REPO_ROOT, "Exports", "L_Showcase")
LEVEL_NAME = os.path.basename(os.path.normpath(EXPORT_DIR))

lines = []
failures = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def check(condition, message):
    if not condition:
        failures.append(str(message))
        log('FAIL: ' + str(message))
    return condition


def main():
    import azlmbr.legacy.general as general
    from ueimporter import importer, prefab_build

    # An inherited UEO3DE_SETTLE_FRAMES would mean this guard tested a settle
    # nobody ships. The shipped constant is the thing under test.
    if os.environ.pop("UEO3DE_SETTLE_FRAMES", None):
        log("  ignoring an inherited UEO3DE_SETTLE_FRAMES: this guard tests "
            "the SHIPPED settle, not an experimental one")

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = "%s/Prefabs/%s_bakes.prefab" % (project_root, LEVEL_NAME)
    for stale in (prefab_path, prefab_path[:-len(".prefab")] + ".ueimport.json"):
        if os.path.exists(stale):
            os.remove(stale)

    log("importing %s at the shipped settle" % EXPORT_DIR)
    report, _saved = importer.import_level(
        manifest_path=os.path.join(EXPORT_DIR, "manifest.json"),
        source_assets_root=os.path.join(EXPORT_DIR, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        log=lambda m: None)

    authored = report.counters.get("mesh_colliders", 0)
    cooked = report.counters.get("colliders_cooked", None)
    settle = report.counters.get("settle_frames", None)
    log("  settled %r frames; authored %d mesh colliders" % (settle, authored))

    check(authored > 0,
          "the level authored no mesh colliders at all, so this guard tested "
          "nothing -- point it at content with baked collision")

    unbaked = prefab_build.unbaked_colliders(prefab_path)
    log("  %d authored, %d reached the prefab with baked geometry, %d without"
        % (authored, authored - len(unbaked), len(unbaked)))
    check(not unbaked,
          "%d mesh collider(s) reached the prefab with no baked geometry: %s"
          % (len(unbaked), ", ".join(unbaked[:10])))

    # The importer must have noticed by itself, not just this script.
    check(cooked == authored - len(unbaked),
          "counter colliders_cooked=%r does not match the file (%d authored, "
          "%d unbaked)" % (cooked, authored, len(unbaked)))
    reported = [r for r in report.records() if r["code"] == "PHYS_COLLIDER_NOT_BAKED"]
    check(len(reported) == len(unbaked),
          "the file has %d unbaked collider(s) but the report carries %d "
          "PHYS_COLLIDER_NOT_BAKED record(s) -- the importer is not seeing "
          "what the file says" % (len(unbaked), len(reported)))

    # THE CONTROL. Everything above passes just as happily if the detector has
    # quietly stopped detecting. Plant one blanked bake in a copy and require
    # it to be found -- an assertion with no failing counterpart is decoration.
    scratch = prefab_path[:-len(".prefab")] + "_control.prefab"
    shutil.copyfile(prefab_path, scratch)
    with open(scratch, "r") as handle:
        document = json.load(handle)
    planted = None
    for entity in (document.get("Entities") or {}).values():
        for component in (entity.get("Components") or {}).values():
            if not isinstance(component, dict):
                continue
            if "MeshCollider" not in str(component.get("$type", "")):
                continue
            shape = component.get("ShapeConfiguration")
            if isinstance(shape, dict) and shape.get("CookedData"):
                shape["CookedData"] = ""
                planted = entity.get("Name")
                break
        if planted:
            break
    if check(planted is not None,
             "could not plant a blanked bake, so the control proves nothing"):
        with open(scratch, "w") as handle:
            json.dump(document, handle)
        found = prefab_build.unbaked_colliders(scratch)
        check(found == [planted],
              "the control failed: planted a blanked bake on %r and the "
              "detector answered %r" % (planted, found[:5]))
        log("  control: planted a blanked bake on %r and it was caught" % planted)
    os.remove(scratch)

    if report.has_errors():
        errors = [r["code"] for r in report.records() if r["severity"] == "error"]
        check(False, "import reported errors: %s" % ", ".join(sorted(set(errors))))


try:
    main()
except Exception:
    failures.append('EXCEPTION')
    log('FAIL: EXCEPTION: ' + traceback.format_exc())

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
