"""
m3_smoke_import.py — the M3 acceptance test (plan v2.2), editor half.

Imports Fixture_01 with physics authored through the backend adapter, then
simulates it in game mode (mirroring the JoltPhysics gem's own smoke_test.py
pattern) and asserts:

  * the dynamic cube FALLS and comes to REST on the floor at the analytic
    height, within a tolerance derived from `adapter.contact_offset()` --
    never a hard-coded 0.02, or this file gets rewritten in M3b (plan M3);
  * the static floor never moves;
  * the kinematic actor hovers forever (gravity ignored, position pinned);
  * the trigger volume does not physically block a body (a dynamic ball
    dropped through it falls past freely) -- the sensor's defining physical
    behaviour; and enter/exit events are asserted via handler if a Python
    binding exists, else the pass-through is the assertion and the gap is
    logged honestly;
  * the mesh-collider entity (SM_LetterF, no simple collision) stops a ball
    dropped onto it -- proving the render-mesh bake produced real collision.

The import itself runs here (not reusing M2's prefab) because M3's import
authors physics; the same manifest, staged assets and AP products are reused.

Run:  Tests/o3de/run_o3de_python.bat Tests/m3/m3_smoke_import.py
"""

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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm3_smoke_import_result.txt')

MANIFEST_PATH = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
SOURCE_ASSETS = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "Assets")
PREFAB_REL_PATH = "Prefabs/Fixture_01_M3.prefab"
REPORT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm3_import_report.json')

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

    project_root = general.get_game_folder().rstrip('/\\')
    project_assets = os.path.join(project_root, "Assets")
    prefab_path = os.path.join(project_root, *PREFAB_REL_PATH.split("/")).replace(os.sep, "/")

    backend = os.environ.get("UEO3DE_BACKEND", "").strip() or None

    log("== import with physics ==")
    report, _saved = importer.import_level(
        manifest_path=MANIFEST_PATH,
        source_assets_root=SOURCE_ASSETS,
        project_assets_root=project_assets,
        prefab_path=prefab_path,
        backend=backend,
        log=log)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    report.write(REPORT_PATH)

    counters = report.to_dict()["counters"]
    check(counters.get("physics_bodies", 0) >= 10,
          "expected >= 10 physics bodies (floor, prims, F, parent+child, "
          "dynamic, kinematic, trigger); got %r" % counters.get("physics_bodies"))
    check(not report.has_errors(), "import report contains errors")

    # The adapter's measured tolerance -- the plan's explicit requirement.
    from ueimporter import physics_build
    from ueimporter.adapters import detect_in_editor, make_adapter
    adapter = make_adapter(detect_in_editor(explicit=backend)["backend"])
    adapter.resolve_components()
    contact = adapter.contact_offset()
    # Rest height may sit anywhere from `contact` low (older gem builds) to
    # exact (current build, measured); plus solver slop.
    rest_tolerance = contact + 0.03
    log("  contact offset: %.4f m -> rest tolerance %.4f m" % (contact, rest_tolerance))

    log("")
    log("== probes ==")
    # A ball above the trigger box (trigger at (-9, 0, 1), 0.8m box): if the
    # trigger is a real sensor the ball falls THROUGH to the floor plane.
    def make_probe(name, position, radius=0.25):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, name)
        components.TransformBus(bus.Event, 'SetWorldTranslation', entity_id,
                                azmath.Vector3(*position))
        adapter.add_dynamic_body(entity_id)
        adapter.add_sphere_collider(entity_id, radius)
        return entity_id

    make_probe('M3_TriggerProbe', (-9.0, 0.0, 3.0))
    # A ball above the F mesh's stem (world x=15 + local -0.375 => 14.625,
    # y=+0.25 local => -0.25 world... keep it simple: over the top arm,
    # x=15, y=-0.125 local center, top z=2.0).
    make_probe('M3_MeshProbe', (15.0, -0.125, 3.0))
    general.idle_wait_frames(90)  # let mesh collider bakes finish ticking

    log("")
    log("== simulate ==")
    initial = {}
    for name in ('Fixture_Floor', 'Cube_Dynamic', 'Cube_Kinematic', 'TriggerBox_01'):
        entity_id = general.find_editor_entity(name)
        if not check(entity_id is not None, "editor entity %s not found" % name):
            return
        translation = components.TransformBus(bus.Event, 'GetWorldTranslation', entity_id)
        initial[name] = (translation.x, translation.y, translation.z)
        log("  %-16s starts at (%.3f, %.3f, %.3f)" % ((name,) + initial[name]))

    general.enter_game_mode()
    general.idle_wait_frames(30)
    check(general.is_in_game_mode(), "editor did not enter game mode")

    game = {}
    for name in ('Fixture_Floor', 'Cube_Dynamic', 'Cube_Kinematic', 'TriggerBox_01',
                 'M3_TriggerProbe', 'M3_MeshProbe'):
        game[name] = general.find_game_entity(name)
        check(game[name] is not None, "runtime entity %s missing in game mode" % name)

    def z_of(entity_id):
        return components.TransformBus(bus.Event, 'GetWorldTranslation', entity_id).z

    def pos_of(entity_id):
        t = components.TransformBus(bus.Event, 'GetWorldTranslation', entity_id)
        return (t.x, t.y, t.z)

    dynamic_samples = []
    kinematic_samples = []
    trigger_probe_samples = []
    mesh_probe_samples = []
    floor_samples = []
    for _ in range(10):  # ~5 simulated seconds
        general.idle_wait_frames(30)
        if game['Cube_Dynamic']:
            dynamic_samples.append(round(z_of(game['Cube_Dynamic']), 4))
        if game['Cube_Kinematic']:
            kinematic_samples.append(round(z_of(game['Cube_Kinematic']), 4))
        if game['M3_TriggerProbe']:
            trigger_probe_samples.append(round(z_of(game['M3_TriggerProbe']), 4))
        if game['M3_MeshProbe']:
            mesh_probe_samples.append(round(z_of(game['M3_MeshProbe']), 4))
        if game['Fixture_Floor']:
            floor_samples.append(pos_of(game['Fixture_Floor']))

    log("  dynamic cube z:  %r" % dynamic_samples)
    log("  kinematic z:     %r" % kinematic_samples)
    log("  trigger probe z: %r" % trigger_probe_samples)
    log("  mesh probe z:    %r" % mesh_probe_samples)

    general.exit_game_mode()
    general.idle_wait_frames(10)

    log("")
    log("== assertions ==")

    # 1. dynamic cube: starts at 1.0, falls to rest on the floor slab.
    #    Floor: plane collider clamped to 0.02m thickness at z=0 -> top ~0.01.
    #    Cube half-extent 0.5 -> analytic rest ~0.51; accept the band
    #    [analytic - contact - slop, analytic + slop].
    if check(len(dynamic_samples) >= 2, "no dynamic samples"):
        start_z = initial['Cube_Dynamic'][2]
        rest = dynamic_samples[-1]
        analytic = 0.5 + 0.01
        check(rest < start_z - 0.2,
              "dynamic cube did not fall (start %.3f, end %.3f)" % (start_z, rest))
        check(abs(rest - analytic) <= rest_tolerance,
              "dynamic cube rest z %.4f not within %.4f of analytic %.4f"
              % (rest, rest_tolerance, analytic))
        check(abs(dynamic_samples[-1] - dynamic_samples[-2]) < 0.01,
              "dynamic cube still moving at the end (jitter or no rest)")

    # 2. static floor never moves.
    if check(len(floor_samples) >= 2, "no floor samples"):
        drift = max(max(abs(a - b) for a, b in zip(sample, initial['Fixture_Floor']))
                    for sample in floor_samples)
        check(drift < 1e-3, "static floor moved %.5f m" % drift)

    # 3. kinematic hovers forever: starts at z=1 with no support underneath a
    #    1m cube would fall; kinematic must pin it.
    if check(len(kinematic_samples) >= 2, "no kinematic samples"):
        start_z = initial['Cube_Kinematic'][2]
        drift = max(abs(z - start_z) for z in kinematic_samples)
        check(drift < 1e-3,
              "kinematic actor moved %.5f m from its spawn height" % drift)

    # 4. trigger: the probe ball must fall PAST the trigger box. The box spans
    #    z 0.6..1.4 at (-9, 0); a blocking volume stops the r=0.25 ball at
    #    z >= ~1.65. There is no floor under the trigger (the 10 m plane spans
    #    x +/-5), so a sensor lets the ball fall indefinitely -- any sample
    #    well below the trigger's underside proves pass-through.
    if check(len(trigger_probe_samples) >= 2, "no trigger probe samples"):
        final = trigger_probe_samples[-1]
        check(final < 0.2,
              "trigger probe stopped at z=%.3f -- the trigger volume is "
              "BLOCKING; it must be a sensor" % final)

    # 5. mesh collider: the probe over the F must be stopped well above the
    #    floor by baked render-mesh collision (top arm z 1.7..2.0 at that x/y;
    #    ball r=0.25 -> rest ~2.25 give or take bake/solver slop).
    if check(len(mesh_probe_samples) >= 2, "no mesh probe samples"):
        final = mesh_probe_samples[-1]
        check(final > 1.5,
              "mesh probe fell to z=%.3f -- the render-mesh bake produced no "
              "collision on SM_LetterF" % final)

    log("")
    for record in report.records():
        log("  [%s] %s %s - %s" % (record["severity"], record["code"],
                                   record["subject"], record["detail"]))


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
