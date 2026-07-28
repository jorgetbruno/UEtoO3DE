"""
m7_acceptance.py — M7 acceptance, editor half: spheres rest ON the terrain.

The plan's test: a sphere dropped at 5 points on imported terrain rests on
the surface within tolerance (the documented contact offset). The points are
the exporter's `terrain_samples.json` -- grid-node surface heights in O3DE
metres, so the baked mesh's height there is exact, and the tolerance is the
adapter's measured contact offset plus solver slop, not a slope allowance.

Two-sided honesty about scenery: a probe can legitimately land ON A PROP
standing on the terrain (the samples avoid nothing). So:
  * the load-bearing assertion, per probe: it must NOT end below
    surface - (radius + tolerance) -- falling THROUGH the terrain is the
    failure this test exists for;
  * at least 3 of 5 probes must rest within the tight tolerance of the
    surface; the others must be explainable as scenery (they rest ABOVE).

Imports the level fresh (the M3 pattern): physics authoring is what is under
test, and the import also proves the terrain survives the full chain.

Env: UEO3DE_M7_EXPORT = export dir (default Exports/L_Showcase).
Run:  Tests/o3de/run_o3de_python.bat Tests/m7/m7_acceptance.py
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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm7_acceptance_result.txt')

EXPORT_DIR = os.environ.get("UEO3DE_M7_EXPORT", "").strip() or \
    os.path.join(REPO_ROOT, "Exports", "L_Showcase")
LEVEL_NAME = os.path.basename(os.path.normpath(EXPORT_DIR))
PROBE_RADIUS = 0.25
DROP_HEIGHT = 1.0

lines = []
failures = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def check(condition, msg):
    if not condition:
        fail(msg)
    return condition


def main():
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath

    from ueimporter import importer
    from ueimporter.adapters import detect_in_editor, make_adapter

    samples_path = os.path.join(EXPORT_DIR, "terrain_samples.json")
    if not os.path.exists(samples_path):
        fail("terrain_samples.json missing at %s -- export a level with a "
             "Landscape first" % samples_path)
        return
    with open(samples_path) as handle:
        samples = json.load(handle)["samples"]
    if not check(len(samples) >= 5, "expected >= 5 terrain samples"):
        return

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = "%s/Prefabs/%s.prefab" % (project_root, LEVEL_NAME)

    log("importing %s (fresh, physics under test)" % LEVEL_NAME)
    report, _saved = importer.import_level(
        manifest_path=os.path.join(EXPORT_DIR, "manifest.json"),
        source_assets_root=os.path.join(EXPORT_DIR, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        log=log)
    check(not report.has_errors(), "import report contains errors")

    adapter = make_adapter(detect_in_editor(explicit=None)["backend"])
    adapter.resolve_components()
    contact = adapter.contact_offset()
    tolerance = contact + 0.05
    log("contact offset %.4f m -> rest tolerance %.4f m" % (contact, tolerance))

    probes = []
    for index, (x, y, z) in enumerate(samples[:5]):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        name = 'M7_Probe_%d' % index
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, name)
        components.TransformBus(bus.Event, 'SetWorldTranslation', entity_id,
                                azmath.Vector3(x, y, z + DROP_HEIGHT))
        adapter.add_dynamic_body(entity_id)
        adapter.add_sphere_collider(entity_id, PROBE_RADIUS)
        probes.append((name, x, y, z))
        log("  %s above (%.2f, %.2f) surface z=%.3f" % (name, x, y, z))

    general.idle_wait_frames(120)   # let the terrain trimesh collider bake

    general.enter_game_mode()
    general.idle_wait_frames(30)
    if not check(general.is_in_game_mode(), "editor did not enter game mode"):
        return
    game_ids = {name: general.find_game_entity(name) for name, _x, _y, _z in probes}
    for _ in range(10):     # ~5 simulated seconds
        general.idle_wait_frames(30)

    resting = 0
    for name, x, y, z in probes:
        game_id = game_ids.get(name)
        if not check(game_id is not None and game_id.IsValid(),
                     "%s missing in game mode" % name):
            continue
        position = components.TransformBus(bus.Event, 'GetWorldTranslation', game_id)
        expected = z + PROBE_RADIUS
        delta = position.z - expected
        log("  %-12s rest z=%.3f (surface %.3f + r) delta=%+.3f"
            % (name, position.z, expected, delta))
        # The failure M7 exists to catch: falling THROUGH the terrain.
        check(delta > -(PROBE_RADIUS + tolerance),
              "%s ended %.3f m BELOW the terrain surface: the collider has a "
              "hole or never baked" % (name, -delta))
        # Sitting far above the surface = landed on scenery; allowed for a
        # minority of probes, never counted as resting.
        if abs(delta) <= tolerance:
            resting += 1
        elif delta > 0.3:
            log("    (rests above the surface -- scenery at this point, "
                "tolerated)")
        else:
            fail("%s rests %.3f m off the surface, outside tolerance %.3f and "
                 "not explainable as scenery" % (name, delta, tolerance))

    general.exit_game_mode()
    check(resting >= 3,
          "only %d of 5 probes rest on the terrain within tolerance; the "
          "surface is not where the samples say it is" % resting)


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
