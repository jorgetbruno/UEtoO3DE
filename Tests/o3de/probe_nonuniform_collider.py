"""
probe_nonuniform_collider.py — does NON-uniform scale reach a collider?

The importer splits scale in two (prefab_build): a uniform scale goes on the
transform, a non-uniform one goes on `EditorNonUniformScaleComponent` with the
transform left at 1.0. `probe_collider_scale.py` established that a cooked
`.pxmesh` collider DOES follow the transform's uniform scale (ratio 2.002,
reproduced to four decimals across two runs), which is why the cooked path
passes no dimensions and must not also set Asset Scale.

That leaves the other half, and it decides real collision on 849 of one real
level's 3,290 collidable entities: a cooked collider on an entity whose scale
lives on the component, not the transform. DIVERGENCES.md records the
interaction as uncontracted. If the component does not reach the collider, a
non-uniformly scaled instance collides at 1x and the fix is an explicit Asset
Scale write.

WHY A SEPARATE FILE. The first probe's primitive readings drifted between two
runs of the same fixture (box_s1 rested at 0.6562 then 0.7736) while its
cooked-mesh readings were stable to four decimals. An unexplained reading is
not evidence, so this probe is built to be trustworthy instead of broad:

  * ONE subject per run-unique name prefix, so nothing can be inherited from
    an earlier run's level state;
  * subjects 200 m apart, so no body can interact with another;
  * every raw height reported, plus the ANALYTIC expectation for the box
    controls -- a control whose absolute value is wrong invalidates the run
    rather than quietly shifting a ratio;
  * a settle loop that reports whether the ball actually stopped moving.

Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_nonuniform_collider.py \
         <result> C:/Users/jorge/O3DE/Projects/UEtoO3DETest-PhysX
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'probe_nonuniform_result.txt'))

PXMESH = (os.environ.get("UEO3DE_PXMESH", "").strip()
          or "assets/uetoo3de/game/meshes/cylinder.fbx.pxmesh")
BOX_HALF_Z = 0.45
BALL_RADIUS = 0.2
DROP_Z = 6.0
SPACING = 200.0

lines = []
failures = []


def log(message):
    lines.append(str(message))
    print(message)


def fail(message):
    failures.append(str(message))
    log('FAIL: ' + str(message))


def main():
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath

    from ueimporter import asset_wait, prefab_build
    from ueimporter.adapters import base, detect_in_editor, make_adapter

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    adapter = make_adapter(detect_in_editor(explicit="physx")["backend"])
    adapter.resolve_components()
    if base.CAP_SHAPE_MESH_COOKED not in adapter.capabilities():
        fail("this backend has no cooked-mesh route; run the probe on PhysX")
        return
    asset_id = asset_wait.resolve(PXMESH)
    if asset_id is None:
        fail("cooked mesh %s not in the catalog" % PXMESH)
        return

    # A prefix nothing earlier can have used. Entity names are the only handle
    # `find_game_entity` has, so a collision with a leftover entity would read
    # the WRONG body's height -- the most likely explanation for the first
    # probe's drifting primitive numbers.
    prefix = "NU%d_" % len(lines)

    def spawn(name, x, uniform=None, nonuniform=None):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, prefix + name)
        components.TransformBus(bus.Event, 'SetWorldTranslation', entity_id,
                                azmath.Vector3(float(x), 0.0, 0.0))
        if uniform is not None:
            components.TransformBus(bus.Event, 'SetLocalUniformScale',
                                    entity_id, float(uniform))
        if nonuniform is not None:
            outcome = editor.EditorComponentAPIBus(
                bus.Broadcast, 'AddComponentsOfType', entity_id,
                [prefab_build._uuid_from_string(prefab_build.NON_UNIFORM_SCALE_TYPE_ID)])
            if not (outcome and outcome.IsSuccess()):
                fail("could not add EditorNonUniformScaleComponent to " + name)
                return entity_id
            entity_module.NonUniformScaleRequestBus(
                bus.Event, 'SetScale', entity_id,
                azmath.Vector3(*[float(v) for v in nonuniform]))
            read = entity_module.NonUniformScaleRequestBus(
                bus.Event, 'GetScale', entity_id)
            log("    %s non-uniform scale reads back (%.2f, %.2f, %.2f)"
                % (name, read.x, read.y, read.z))
        return entity_id

    # Each subject: (label, x, how the target is scaled, what collider).
    plan = [
        ("box_plain",    0.0 * SPACING, {}, "box"),
        ("box_nonuni",   1.0 * SPACING, {"nonuniform": (1.0, 1.0, 2.0)}, "box"),
        ("mesh_plain",   2.0 * SPACING, {}, "mesh"),
        ("mesh_nonuni",  3.0 * SPACING, {"nonuniform": (1.0, 1.0, 2.0)}, "mesh"),
        ("mesh_uniform", 4.0 * SPACING, {"uniform": 2.0}, "mesh"),
    ]

    floor = spawn("Floor", 2.0 * SPACING)
    components.TransformBus(bus.Event, 'SetWorldTranslation', floor,
                           azmath.Vector3(2.0 * SPACING, 0.0, -0.5))
    adapter.add_static_body(floor)
    adapter.add_box_collider(floor, [600.0, 50.0, 0.5])

    log("")
    log("=== subjects ===")
    for label, x, scaling, kind in plan:
        target = spawn(label, x, **scaling)
        adapter.add_static_body(target)
        if kind == "box":
            adapter.add_box_collider(target, [1.0, 1.0, BOX_HALF_Z])
        else:
            adapter.add_mesh_collider(target, convex=True, asset_id=asset_id)
        ball = spawn(label + "_ball", x)
        components.TransformBus(bus.Event, 'SetWorldTranslation', ball,
                               azmath.Vector3(float(x), 0.0, DROP_Z))
        adapter.add_dynamic_body(ball)
        adapter.add_sphere_collider(ball, BALL_RADIUS)
        log("  %-14s x=%-7.0f %-5s %s" % (label, x, kind, scaling or "scale 1"))

    general.idle_wait_frames(60)
    general.enter_game_mode()
    general.idle_wait_frames(30)
    if not general.is_in_game_mode():
        fail("editor did not enter game mode")
        return

    def ball_z(label):
        game_id = general.find_game_entity(prefix + label + "_ball")
        if game_id is None or not game_id.IsValid():
            return None
        return components.TransformBus(bus.Event, 'GetWorldTranslation', game_id).z

    # Settle until every ball stops moving, and SAY whether it did -- an
    # unsettled reading is what makes a ratio meaningless.
    previous = {}
    settled = False
    for attempt in range(12):
        general.idle_wait_frames(60)
        current = {label: ball_z(label) for label, _x, _s, _k in plan}
        if previous and all(
                current[k] is not None and previous[k] is not None
                and abs(current[k] - previous[k]) < 1e-4 for k in current):
            settled = True
            log("  all balls stopped moving after %d settle rounds" % (attempt + 1))
            break
        previous = current
    heights = {label: ball_z(label) for label, _x, _s, _k in plan}
    general.exit_game_mode()
    general.idle_wait_frames(10)

    if not settled:
        fail("balls never stopped moving; the readings below are not evidence")

    log("")
    log("=== raw resting heights (floor top z=0, ball radius %.2f) ===" % BALL_RADIUS)
    for label, _x, _s, _k in plan:
        value = heights[label]
        log("  %-14s %s" % (label, "None" if value is None else "%.4f" % value))

    # Controls first: the box cases have ANALYTIC answers, so a wrong absolute
    # value invalidates the run instead of silently skewing the mesh verdict.
    log("")
    log("=== controls (analytic) ===")
    expected_plain = BOX_HALF_Z + BALL_RADIUS
    got_plain = heights["box_plain"]
    ok_plain = got_plain is not None and abs(got_plain - expected_plain) < 0.05
    log("  box_plain  expected %.4f  got %s  %s"
        % (expected_plain, "None" if got_plain is None else "%.4f" % got_plain,
           "OK" if ok_plain else "OFF -- run is suspect"))
    if not ok_plain:
        fail("the unscaled box control did not rest at its analytic height; "
             "nothing else in this run can be trusted")

    def surface(label):
        value = heights.get(label)
        return None if value is None else value - BALL_RADIUS

    log("")
    log("=== verdicts ===")
    for label, base_label in (("box_nonuni", "box_plain"),
                              ("mesh_nonuni", "mesh_plain"),
                              ("mesh_uniform", "mesh_plain")):
        top, base_top = surface(label), surface(base_label)
        if top is None or base_top is None or abs(base_top) < 1e-6:
            log("  %-14s NO READING" % label)
            continue
        ratio = top / base_top
        verdict = ("FOLLOWS the scale (~2)" if abs(ratio - 2.0) < 0.15
                   else "IGNORES the scale (~1)" if abs(ratio - 1.0) < 0.15
                   else "neither 1 nor 2 -- look closer")
        log("  %-14s surface %.4f vs %.4f -> ratio %.3f  %s"
            % (label, top, base_top, ratio, verdict))

    log("")
    log("What it decides: if mesh_nonuni IGNORES the scale while mesh_uniform "
        "follows it, then cooked colliders need an explicit Asset Scale write "
        "for non-uniformly scaled entities (and only those).")


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
