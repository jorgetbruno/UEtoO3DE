"""
settle_signal_probe.py — is a FINISHED bake distinguishable from an unfinished
one, on a real level, from Python?

Everything else has been ruled out by measurement:

  * the settle is load-bearing -- settle=0 loses 15 of 2501 bakes, silently
  * no reflected property on a Jolt Mesh Collider mentions cooking or shape
    readiness (probe_settle_ready dumped all 17)
  * re-flushing the in-memory template recovers nothing -- 12 flushes over
    3600 frames stayed at 2486/2501
  * the prefab cannot be re-created in the same session: O3DE answers
    "Creating prefab as an override edit is currently not supported"

So a feedback loop after the save is impossible, and the settle must be right
BEFORE the one snapshot the session gets. That makes this the deciding
question: with the level still live and 15 bakes known to be unfinished, does
any Python-visible signal tell an unfinished collider from a finished one?

If yes, the settle becomes a readiness poll like the model wait already is.
If no, the settle stays a constant, and the honest fix is to VERIFY the saved
file and report the loss instead of continuing not to notice it.

The design that makes the answer trustworthy: the same call is made against
the entities that DID bake and the ones that did not, in the same session,
milliseconds apart. A signal that cannot separate two groups it is being shown
side by side is not a signal.

Env:  UEO3DE_EXPORT = export directory (default Exports/L_Showcase)
Run:  Tests/o3de/run_o3de_python.bat Tests/perf/settle_signal_probe.py
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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'settle_signal_probe_result.txt')

EXPORT_DIR = os.environ.get("UEO3DE_EXPORT", "").strip() or \
    os.path.join(REPO_ROOT, "Exports", "L_Showcase")
LEVEL_NAME = os.path.basename(os.path.normpath(EXPORT_DIR))

lines = []
failures = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def split_by_bake(path):
    """(names that baked, names that did not) from a saved prefab."""
    with open(path, "r") as handle:
        document = json.load(handle)
    baked, unbaked = [], []
    for entity in (document.get("Entities") or {}).values():
        for component in (entity.get("Components") or {}).values():
            if not isinstance(component, dict):
                continue
            if "MeshCollider" not in str(component.get("$type", "")):
                continue
            shape = component.get("ShapeConfiguration")
            cooked = shape.get("CookedData") if isinstance(shape, dict) else None
            (baked if (isinstance(cooked, str) and cooked) else unbaked).append(
                entity.get("Name"))
    return baked, unbaked


def main():
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    from ueimporter import importer, prefab_build

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = "%s/Prefabs/%s_signalprobe.prefab" % (project_root, LEVEL_NAME)
    for stale in (prefab_path, prefab_path[:-len(".prefab")] + ".ueimport.json"):
        if os.path.exists(stale):
            os.remove(stale)

    os.environ["UEO3DE_SETTLE_FRAMES"] = "0"
    log("importing %s with settle=0 to manufacture unfinished bakes" % EXPORT_DIR)
    report, _saved = importer.import_level(
        manifest_path=os.path.join(EXPORT_DIR, "manifest.json"),
        source_assets_root=os.path.join(EXPORT_DIR, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        log=lambda m: None)

    baked, unbaked = split_by_bake(prefab_path)
    log("  %d baked, %d UNBAKED" % (len(baked), len(unbaked)))
    if not unbaked:
        fail("settle=0 baked everything, so there is no unfinished group to "
             "compare against and this probe proves nothing")
        return
    if not baked:
        fail("nothing baked at all -- the comparison has no positive group")
        return

    sample_baked = sorted(baked)[:len(unbaked)]
    log("  unbaked: %s" % ", ".join(sorted(unbaked)))
    log("  control (baked): %s" % ", ".join(sample_baked[:6]) + " ...")

    def find_entity(name):
        search = entity_module.SearchFilter()
        search.names = [name]
        found = entity_module.SearchBus(bus.Broadcast, 'SearchEntities', search)
        return found[0] if found else None

    collider_type = prefab_build.resolve_component_type('Jolt Mesh Collider')

    def signals(name):
        """Every candidate readiness reading for one entity."""
        out = {}
        entity_id = find_entity(name)
        if entity_id is None:
            return {"found": False}
        out["found"] = True

        # 1. the collider component's own reflected properties -- all 17 of
        #    them, in case one moves when the bake lands even though none is
        #    NAMED for it.
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, collider_type)
        if outcome and outcome.IsSuccess():
            pair = outcome.GetValue()
            paths = editor.EditorComponentAPIBus(
                bus.Broadcast, 'BuildComponentPropertyList', pair) or []
            props = {}
            for path in paths:
                got = editor.EditorComponentAPIBus(
                    bus.Broadcast, 'GetComponentProperty', pair, path)
                if got and got.IsSuccess():
                    try:
                        props[path] = repr(got.GetValue())[:40]
                    except Exception:
                        props[path] = "<unreadable value>"
            out["props"] = props

        # 2. the physics buses, which a bare entity could not answer but a
        #    fully authored one might.
        try:
            import azlmbr.physics as physics
            for bus_name, event in (('SimulatedBodyComponentRequestsBus', 'GetAabb'),
                                    ('ColliderComponentRequestBus', 'GetColliderShapeAabb')):
                handler = getattr(physics, bus_name, None)
                if handler is None:
                    out[bus_name] = "bus absent"
                    continue
                try:
                    value = handler(bus.Event, event, entity_id)
                    out[bus_name] = repr(value)[:70]
                except Exception as exc:
                    out[bus_name] = "%s: %s" % (type(exc).__name__, str(exc)[:40])
        except ImportError:
            out["physics"] = "azlmbr.physics absent"
        return out

    log("")
    log("=== UNBAKED group ===")
    unbaked_props = {}
    for name in sorted(unbaked)[:3]:
        data = signals(name)
        unbaked_props[name] = data
        log("  %s: found=%s" % (name, data.get("found")))
        for key in sorted(k for k in data if k not in ("found", "props")):
            log("      %-34s %s" % (key, data[key]))
        for path, value in sorted((data.get("props") or {}).items()):
            log("      prop %-30s %s" % (path, value))

    log("")
    log("=== BAKED control group ===")
    for name in sample_baked[:3]:
        data = signals(name)
        log("  %s: found=%s" % (name, data.get("found")))
        for key in sorted(k for k in data if k not in ("found", "props")):
            log("      %-34s %s" % (key, data[key]))
        for path, value in sorted((data.get("props") or {}).items()):
            log("      prop %-30s %s" % (path, value))

    log("")
    log("=== VERDICT ===")
    log("  Compare the two blocks above. Any line that differs consistently "
        "between them is a readiness signal; if every line matches, the bake "
        "is invisible from Python and the settle cannot become a poll.")


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
