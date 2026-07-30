"""
probe_scale_matrix.py — does the ENGINE already scale colliders, on either
backend, for either kind of scale, through either collider route?

`physics_build` multiplies every collider dimension by the entity's world
scale, on the premise that "collider components live outside the transform's
scale". Two earlier probes measured that premise false on PhysX (primitive box
ratio 1.97 under transform scale, 2.00 under a non-uniform scale component;
cooked mesh 2.00), which would mean a 2x-scaled actor gets 4x collision. Two
cells were missing and both matter before touching a shipped constant:

  * ALL OF JOLT -- its probe run crashed, so the "both backends" claim in
    DIVERGENCES.md rests on PhysX alone;
  * COOKED MESH UNDER NON-UNIFORM SCALE, on either backend -- the earlier
    reading failed the probe's own settle check and was discarded.

METHOD, unchanged where it worked: ratios, not absolutes. The same collider at
scale 1 and at scale 2 under a dropped ball, resting height read in game mode,
so the geometry cancels and only the ratio is interpreted.

  rest(scale 2) / rest(scale 1) ~= 2  -> the engine scales it (so baking the
                                        scale in as well squares it)
                                ~= 1  -> it does not (so baking is correct)

WHAT MAKES THIS RUN TRUSTWORTHY, after the last one produced numbers it could
not stand behind:
  * the unscaled BOX is an analytic control -- it must rest at exactly
    half-extent + ball radius, and if it does not the whole run is refused;
  * the unscaled COOKED MESH is a second control -- the ball must come to rest
    ABOVE the floor, or the asset never loaded and every mesh ratio is noise;
  * subjects sit 200 m apart and every entity name is unique per run, so
    nothing can rest on another subject or be read from a leftover entity;
  * the settle loop reports whether the balls actually stopped, and a run
    where they did not is a FAIL rather than a table.

Env: UEO3DE_COOKED_MESH  product path to use (default: per-backend barrel)
Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_scale_matrix.py \
         <result> <project>
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
               else os.path.join(SCRIPT_DIR, 'results', 'probe_scale_matrix_result.txt'))

BOX_HALF_Z = 0.45
BALL_RADIUS = 0.2
DROP_Z = 6.0
SPACING = float(os.environ.get('UEO3DE_PROBE_SPACING', '12'))
ASSET_LOAD_FRAMES = int(os.environ.get('UEO3DE_PROBE_LOAD_FRAMES', '300'))

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

    detection = detect_in_editor(explicit=os.environ.get("UEO3DE_BACKEND") or None)
    adapter = make_adapter(detection["backend"])
    adapter.resolve_components()
    backend = adapter.name()
    log("backend: %s" % backend)

    cooked_capable = base.CAP_SHAPE_MESH_COOKED in adapter.capabilities()
    default_product = ("assets/uetoo3de/game/siegeofponthus/meshes/sm_barrel.fbx."
                       + ("pxmesh" if backend == "physx" else "joltmesh"))
    product = os.environ.get("UEO3DE_COOKED_MESH", "").strip() or default_product
    asset_id = asset_wait.resolve(product) if cooked_capable else None
    log("cooked-mesh capable: %s | %s -> %s"
        % (cooked_capable, product,
           "resolved" if asset_id is not None else "NOT IN CATALOG"))

    prefix = "SM%d_" % (len(lines) + 1)

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
                fail("could not add the non-uniform scale component to " + name)
                return entity_id
            entity_module.NonUniformScaleRequestBus(
                bus.Event, 'SetScale', entity_id,
                azmath.Vector3(*[float(v) for v in nonuniform]))
        return entity_id

    plan = [("box_plain", {}, "box"),
            ("box_uniform", {"uniform": 2.0}, "box"),
            ("box_nonuni", {"nonuniform": (1.0, 1.0, 2.0)}, "box")]
    if asset_id is not None:
        plan += [("mesh_plain", {}, "mesh"),
                 ("mesh_uniform", {"uniform": 2.0}, "mesh"),
                 ("mesh_nonuni", {"nonuniform": (1.0, 1.0, 2.0)}, "mesh")]

    floor = spawn("Floor", 2.5 * SPACING)
    components.TransformBus(bus.Event, 'SetWorldTranslation', floor,
                            azmath.Vector3(2.5 * SPACING, 0.0, -0.5))
    adapter.add_static_body(floor)
    adapter.add_box_collider(floor, [4.0 * SPACING, 20.0, 0.5])

    log("")
    log("=== subjects ===")
    for index, (label, scaling, kind) in enumerate(plan):
        x = index * SPACING
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

    # Cooked assets load asynchronously; entering game mode before the shape
    # exists is what made the previous run read "resting on the floor".
    general.idle_wait_frames(ASSET_LOAD_FRAMES)
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

    previous, settled = {}, False
    for attempt in range(15):
        general.idle_wait_frames(60)
        current = {label: ball_z(label) for label, _s, _k in plan}
        if previous and all(
                current[k] is not None and previous[k] is not None
                and abs(current[k] - previous[k]) < 1e-4 for k in current):
            settled = True
            log("  balls stopped moving after %d rounds" % (attempt + 1))
            break
        previous = current
    heights = {label: ball_z(label) for label, _s, _k in plan}
    general.exit_game_mode()
    general.idle_wait_frames(10)

    log("")
    log("=== raw resting heights (floor top z=0, ball radius %.2f) ===" % BALL_RADIUS)
    for label, _s, _k in plan:
        log("  %-14s %s" % (label, "None" if heights[label] is None
                            else "%.4f" % heights[label]))

    if not settled:
        fail("balls never stopped moving; these readings are not evidence")
        return

    # CONTROL 1: the unscaled box has an analytic answer.
    expected = BOX_HALF_Z + BALL_RADIUS
    got = heights.get("box_plain")
    if not (got is not None and abs(got - expected) < 0.05):
        fail("the unscaled box rested at %r, not its analytic %.4f; nothing "
             "in this run can be trusted" % (got, expected))
        return
    log("")
    log("  control: unscaled box rested at %.4f (analytic %.4f) OK"
        % (got, expected))

    # CONTROL 2: the unscaled cooked mesh must actually stop the ball.
    if asset_id is not None:
        mesh_plain = heights.get("mesh_plain")
        if not (mesh_plain is not None and mesh_plain > BALL_RADIUS + 0.1):
            fail("the unscaled cooked mesh let the ball fall to %r -- the "
                 "asset never produced a shape, so the mesh rows below are "
                 "noise" % (mesh_plain,))
            return
        log("  control: unscaled cooked mesh stopped the ball at %.4f OK"
            % mesh_plain)

    def surface(label):
        value = heights.get(label)
        return None if value is None else value - BALL_RADIUS

    log("")
    log("=== verdicts (ratio vs the same collider unscaled) ===")
    pairs = [("box_uniform", "box_plain", "primitive box, transform scale"),
             ("box_nonuni", "box_plain", "primitive box, non-uniform component")]
    if asset_id is not None:
        pairs += [("mesh_uniform", "mesh_plain", "cooked mesh, transform scale"),
                  ("mesh_nonuni", "mesh_plain", "cooked mesh, non-uniform component")]
    for label, base_label, what in pairs:
        top, base_top = surface(label), surface(base_label)
        if top is None or base_top is None or abs(base_top) < 1e-6:
            log("  %-38s NO READING" % what)
            continue
        ratio = top / base_top
        verdict = ("engine SCALES it (~2)" if abs(ratio - 2.0) < 0.15
                   else "engine IGNORES scale (~1)" if abs(ratio - 1.0) < 0.15
                   else "neither 1 nor 2 -- look closer")
        log("  %-38s %.4f / %.4f = %.3f  %s"
            % (what, top, base_top, ratio, verdict))

    log("")
    log("Reading it: a ratio of ~2 means the engine already applies that scale, "
        "so physics_build multiplying the dimensions by it as well squares the "
        "collision. A ratio of ~1 means the baking is what makes it correct.")


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
