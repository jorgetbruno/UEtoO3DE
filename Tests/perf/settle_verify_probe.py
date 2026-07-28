"""
settle_verify_probe.py — can a late collider bake be RECOVERED after the save?

Established by measurement before this probe was written:

  * The settle is load-bearing. Importing L_Showcase with UEO3DE_SETTLE_FRAMES=0
    produced a prefab with 2486 cooked mesh colliders where the control had
    2501 -- and the import reported PASS. The 15 missing ones are the biggest
    meshes in the level (Landscape's cooked data is 3 MB, SM_Mountain_3's is
    262 KB), which is what a bake that has not finished looks like.
  * The report's `mesh_colliders` counter said 2501 in BOTH runs. It counts
    what was authored, and nothing checks what was serialized, so the loss is
    invisible from inside the importer.
  * The bake result is not reflected: `probe_settle_ready` dumped all 17
    property paths on a Jolt Mesh Collider and none mentions cooking. So the
    readiness signal cannot be polled per component.

That leaves one place the truth is visible: the file. This probe asks whether
the file can be re-derived cheaply once the bakes finish -- `flush_template_to_disk`
writes the IN-MEMORY template via SaveTemplateToString, and the question is
whether that template picks up a bake that completed after CreatePrefabInMemory
snapshotted it.

  yes -> the fix is save, verify, settle+reflush until complete. Cheap: no
         entities are rebuilt, only the template is re-serialized.
  no  -> the settle has to stay in front of the save, and the fix is a
         verified escalation rather than a fixed guess.

It also records HOW LONG the bakes actually need, which is the number the
current 41,040-frame formula is standing in for.

Env:  UEO3DE_EXPORT = export directory (default Exports/L_Showcase)
Run:  Tests/o3de/run_o3de_python.bat Tests/perf/settle_verify_probe.py
"""

import json
import os
import sys
import time
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'settle_verify_probe_result.txt')

EXPORT_DIR = os.environ.get("UEO3DE_EXPORT", "").strip() or \
    os.path.join(REPO_ROOT, "Exports", "L_Showcase")
LEVEL_NAME = os.path.basename(os.path.normpath(EXPORT_DIR))

# Frames to idle between re-flushes. Deliberately coarse: this is measuring a
# trajectory, not tuning a constant.
STEP_FRAMES = int(os.environ.get("UEO3DE_PROBE_STEP", "600"))
STEPS = int(os.environ.get("UEO3DE_PROBE_STEPS", "12"))

lines = []
failures = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def cooked_census(path):
    """(entities, colliders_with_data, colliders_missing_data) in a prefab."""
    with open(path, "r") as handle:
        document = json.load(handle)
    entities = document.get("Entities") or {}
    with_data = 0
    empty = 0
    collider_components = 0
    for entity in entities.values():
        for component in (entity.get("Components") or {}).values():
            if not isinstance(component, dict):
                continue
            kind = str(component.get("$type", ""))
            if "MeshCollider" not in kind:
                continue
            collider_components += 1
            shape = component.get("ShapeConfiguration")
            cooked = shape.get("CookedData") if isinstance(shape, dict) else None
            if isinstance(cooked, str) and cooked:
                with_data += 1
            else:
                empty += 1
    return len(entities), collider_components, with_data, empty


def missing_names(path):
    with open(path, "r") as handle:
        document = json.load(handle)
    out = []
    for entity in (document.get("Entities") or {}).values():
        for component in (entity.get("Components") or {}).values():
            if not isinstance(component, dict):
                continue
            if "MeshCollider" not in str(component.get("$type", "")):
                continue
            shape = component.get("ShapeConfiguration")
            cooked = shape.get("CookedData") if isinstance(shape, dict) else None
            if not (isinstance(cooked, str) and cooked):
                out.append(entity.get("Name"))
    return sorted(out)


def main():
    import azlmbr.legacy.general as general
    from ueimporter import importer, manifest_io, prefab_build

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = "%s/Prefabs/%s_settleprobe.prefab" % (project_root, LEVEL_NAME)
    ledger = prefab_path[:-len(".prefab")] + ".ueimport.json"
    for stale in (prefab_path, ledger):
        if os.path.exists(stale):
            os.remove(stale)

    manifest_path = os.path.join(EXPORT_DIR, "manifest.json")
    level_root_name = manifest_io.load(manifest_path)["level"]["name"]

    # The whole point: import with NO settle, so the bakes are demonstrably
    # unfinished when the template is snapshotted.
    os.environ["UEO3DE_SETTLE_FRAMES"] = "0"
    log("importing %s with settle=0 (root %r)" % (EXPORT_DIR, level_root_name))
    started = time.perf_counter()
    report, _saved = importer.import_level(
        manifest_path=manifest_path,
        source_assets_root=os.path.join(EXPORT_DIR, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        log=lambda m: None)
    log("import took %.1f s" % (time.perf_counter() - started))
    authored = report.counters.get("mesh_colliders", 0)
    log("report says mesh_colliders = %d" % authored)

    entities, components, with_data, empty = cooked_census(prefab_path)
    log("")
    log("  after save: %d entities, %d mesh-collider components, "
        "%d with cooked data, %d WITHOUT"
        % (entities, components, with_data, empty))
    if components != authored:
        log("  NOTE: %d collider components in the file vs %d authored -- "
            "%d never reached the prefab at all"
            % (components, authored, authored - components))

    baseline_missing = authored - with_data
    if baseline_missing <= 0:
        fail("settle=0 produced a COMPLETE prefab (%d/%d cooked), so this probe "
             "has nothing to recover and proves nothing. Re-run on a level "
             "whose bakes are slow enough to lose." % (with_data, authored))
        return

    log("  %d bakes are missing -- the thing to recover" % baseline_missing)
    log("  first few: %s" % ", ".join(missing_names(prefab_path)[:8]))

    # ESTABLISHED, first run of this probe: re-flushing the in-memory template
    # recovers NOTHING. Twelve re-flushes over 3600 further frames left the
    # count at 2486/2501, unchanged at every step, while the same level with
    # the full settle in front of the save reaches 2501. So the template is a
    # snapshot taken at CreatePrefabInMemory and does not track a bake that
    # finishes afterwards -- the settle has to precede the snapshot, and the
    # only way to re-test readiness is to take the snapshot again.
    #
    # So this measures the thing that matters instead: settle a step, RE-CREATE
    # the prefab, count. That answers both "is re-creating even possible?" and
    # "how many frames do these bakes actually need?", which is the number the
    # 41,040-frame formula is standing in for.
    log("")
    log("=== re-creating the prefab after further settling ===")

    def find_entity(name):
        import azlmbr.bus as bus
        import azlmbr.entity as entity_module
        search = entity_module.SearchFilter()
        search.names = [name]
        found = entity_module.SearchBus(bus.Broadcast, 'SearchEntities', search)
        return found[0] if found else None

    root = find_entity(level_root_name)
    if root is None:
        fail("could not find the level root %r after the save, so the prefab "
             "cannot be re-created" % level_root_name)
        return
    log("  level root %r found after the save" % level_root_name)

    log("  step  frames   cooked/authored   gained   snapshot")
    total_frames = 0
    previous = with_data
    for step in range(1, STEPS + 1):
        general.idle_wait_frames(STEP_FRAMES)
        total_frames += STEP_FRAMES
        probe_path = prefab_path[:-len(".prefab")] + ("_step%02d.prefab" % step)
        snap_started = time.perf_counter()
        try:
            prefab_build.create_prefab_in_memory([root], probe_path)
            prefab_build.flush_template_to_disk(probe_path, level_root_name)
        except Exception as exc:
            fail("re-CREATE failed at step %d (%d frames in): %s: %s"
                 % (step, total_frames, type(exc).__name__, exc))
            return
        snap_s = time.perf_counter() - snap_started
        _e, _c, now, _empty = cooked_census(probe_path)
        log("  %4d  %6d   %5d/%-5d        %+d       %.1f s"
            % (step, total_frames, now, authored, now - previous, snap_s))
        previous = now
        os.remove(probe_path)
        if now >= authored:
            log("")
            log("  COMPLETE after %d further frames. Re-creating the prefab "
                "DOES pick up late bakes, so save/verify/settle/re-save is a "
                "real feedback loop and the blind formula can go."
                % total_frames)
            return
    log("")
    log("  still %d/%d after %d further frames" % (previous, authored, total_frames))
    log("  still missing: %s" % ", ".join(missing_names(prefab_path)[:8]))


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
