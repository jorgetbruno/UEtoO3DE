"""
probe_scale_matrix.py — does the ENGINE already scale colliders, on either
backend, for either kind of scale, through either collider route — and does it
scale collider OFFSETS as well as dimensions?

`physics_build` multiplies every collider dimension AND offset by the entity's
world scale, on the premise that "collider components live outside the
transform's scale". If a backend applies that scale itself, a 2x-scaled actor
gets 4x collision.

MEASURED BY AABB, NOT BY DROPPING A BALL. The first version of this probe
dropped a ball on each subject and read its resting height. That works for a
box and is useless for a cooked mesh: the barrel product's top is round, a
sphere landing dead centre on it sits in unstable equilibrium, and within the
settle window it rolls off and lands on the floor — indistinguishable from "the
collider does not exist". Seven runs across both backends were discarded that
way. Asking the simulated body for its world AABB answers the same question
deterministically, in one frame, for any shape:

  size(scale 2) / size(scale 1) ~= 2  -> the engine scales it (so baking the
                                        scale in as well squares it)
                                ~= 1  -> it does not (so baking is correct)

and the same ratio on an offset collider's CENTRE answers the second question,
which the resting-height method could not reach at all.

CONTROL: the unscaled plain box has an analytic AABB — exactly its authored
half extents, centred on its entity. If that reading is wrong the query is not
measuring what it claims and the whole run is refused before any verdict.

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
for _path in (os.path.join(REPO_ROOT, "Tests", "lib"), GEM_SCRIPTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import editor_physics  # noqa: E402

RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'probe_scale_matrix_result.txt'))

BOX_HALF = [1.0, 0.75, 0.45]     # deliberately unequal: a per-axis error shows up
OFFSET_HALF = [0.5, 0.5, 0.5]
COLLIDER_OFFSET = [0.0, 0.0, 1.0]
# 90 degrees about X, xyzw. An axis-PERMUTING rotation, so a non-uniform scale
# composes with it exactly in either convention -- and the two conventions give
# different answers, which is what makes it a discriminator rather than a
# tolerance question.
ROT_X90 = [0.7071067811865476, 0.0, 0.0, 0.7071067811865476]
ROT_SCALE = [1.0, 1.0, 3.0]
SPACING = float(os.environ.get('UEO3DE_PROBE_SPACING', '40'))
ASSET_LOAD_FRAMES = int(os.environ.get('UEO3DE_PROBE_LOAD_FRAMES', '300'))

lines = []
failures = []


def log(message):
    lines.append(str(message))
    print(message)


def fail(message):
    failures.append(str(message))
    log('FAIL: ' + str(message))


# Shared with the acceptance tests (Tests/lib/editor_physics.py) so a probe
# and the test it justifies cannot disagree about how to read an AABB.
_quat_matrix = editor_physics.quaternion_matrix
_aabb_size = editor_physics.transformed_aabb_size


def aabb_of(entity_id):
    """(size, centre, bus name) for a game entity's simulated body."""
    minimum, maximum, source = editor_physics.body_aabb_with_source(entity_id)
    if minimum is None:
        return None, None, source
    return ([maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z],
            [(maximum.x + minimum.x) * 0.5, (maximum.y + minimum.y) * 0.5,
             (maximum.z + minimum.z) * 0.5],
            source)


def _predicted_rotated(half, quat, scale):
    """(entity-space prediction, shape-space prediction) for a rotated box."""
    return (editor_physics.scaled_rotated_aabb(half, quat, scale),
            editor_physics.scaled_rotated_aabb(half, quat, scale,
                                               scale_in_shape_frame=True))


def _ratio_verdict(value, base_value):
    if abs(base_value) < 1e-6:
        return None, 'base reading is zero'
    ratio = value / base_value
    if abs(ratio - 2.0) < 0.1:
        return ratio, 'engine SCALES it (~2)'
    if abs(ratio - 1.0) < 0.1:
        return ratio, 'engine IGNORES scale (~1)'
    return ratio, 'neither 1 nor 2 -- look closer'


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

    prefix = "SM_"

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
            ("box_nonuni", {"nonuniform": (1.0, 1.0, 2.0)}, "box"),
            ("off_plain", {}, "offset"),
            ("off_uniform", {"uniform": 2.0}, "offset"),
            ("rot_plain", {}, "rotated"),
            ("rot_nonuni", {"nonuniform": tuple(ROT_SCALE)}, "rotated")]
    if asset_id is not None:
        plan += [("mesh_plain", {}, "mesh"),
                 ("mesh_uniform", {"uniform": 2.0}, "mesh"),
                 ("mesh_nonuni", {"nonuniform": (1.0, 1.0, 2.0)}, "mesh")]

    log("")
    log("=== subjects ===")
    origins = {}
    for index, (label, scaling, kind) in enumerate(plan):
        x = index * SPACING
        origins[label] = x
        target = spawn(label, x, **scaling)
        adapter.add_static_body(target)
        if kind == "box":
            adapter.add_box_collider(target, BOX_HALF)
        elif kind == "offset":
            adapter.add_box_collider(target, OFFSET_HALF, COLLIDER_OFFSET, None)
        elif kind == "rotated":
            adapter.add_box_collider(target, BOX_HALF, None, ROT_X90)
        else:
            adapter.add_mesh_collider(target, convex=True, asset_id=asset_id)
        log("  %-14s x=%-7.0f %-7s %s" % (label, x, kind, scaling or "scale 1"))

    # Cooked assets load asynchronously; a collider whose asset has not arrived
    # reports no body at all, which would read as "the engine ignores scale".
    general.idle_wait_frames(ASSET_LOAD_FRAMES)
    general.enter_game_mode()
    general.idle_wait_frames(60)
    if not general.is_in_game_mode():
        fail("editor did not enter game mode")
        return

    readings, buses = {}, set()
    for label, _scaling, _kind in plan:
        game_id = general.find_game_entity(prefix + label)
        if game_id is None or not game_id.IsValid():
            readings[label] = None
            continue
        size, centre, how = aabb_of(game_id)
        buses.add(how)
        readings[label] = None if size is None else (size, centre)
    general.exit_game_mode()
    general.idle_wait_frames(10)

    log("")
    log("=== world AABBs (via %s) ===" % ', '.join(sorted(buses)))
    for label, _scaling, _kind in plan:
        reading = readings.get(label)
        if reading is None:
            log("  %-14s NO BODY" % label)
            continue
        size, centre = reading
        log("  %-14s size (%.3f, %.3f, %.3f)  centre offset from entity (%.3f, %.3f, %.3f)"
            % (label, size[0], size[1], size[2],
               centre[0] - origins[label], centre[1], centre[2]))

    # CONTROL: the unscaled plain box has an analytic AABB.
    control = readings.get("box_plain")
    expected = [2.0 * half for half in BOX_HALF]
    if control is None:
        fail("the unscaled box reported no body; nothing in this run can be trusted")
        return
    if max(abs(control[0][i] - expected[i]) for i in range(3)) > 0.05:
        fail("the unscaled box's AABB is %r, not its authored %r; the query is "
             "not measuring what it claims"
             % ([round(s, 3) for s in control[0]], expected))
        return
    log("")
    log("  control: unscaled box AABB %r matches the authored %r OK"
        % ([round(s, 3) for s in control[0]], expected))

    log("")
    log("=== verdicts: collider DIMENSIONS ===")
    dimension_pairs = [("box_uniform", "box_plain", 0, "primitive box, transform scale"),
                       ("box_nonuni", "box_plain", 2, "primitive box, non-uniform component (z)")]
    if asset_id is not None:
        dimension_pairs += [
            ("mesh_uniform", "mesh_plain", 0, "cooked mesh, transform scale"),
            ("mesh_nonuni", "mesh_plain", 2, "cooked mesh, non-uniform component (z)")]
    for label, base_label, axis, what in dimension_pairs:
        this, that = readings.get(label), readings.get(base_label)
        if this is None or that is None:
            log("  %-42s NO READING" % what)
            continue
        ratio, verdict = _ratio_verdict(this[0][axis], that[0][axis])
        if ratio is None:
            log("  %-42s %s" % (what, verdict))
            continue
        log("  %-42s %.3f / %.3f = %.3f  %s"
            % (what, this[0][axis], that[0][axis], ratio, verdict))

    log("")
    log("=== verdicts: collider OFFSETS ===")
    this, that = readings.get("off_uniform"), readings.get("off_plain")
    if this is None or that is None:
        log("  offset box, transform scale               NO READING")
    else:
        ratio, verdict = _ratio_verdict(this[1][2], that[1][2])
        if ratio is None:
            log("  offset box, transform scale               %s" % verdict)
        else:
            log("  %-42s %.3f / %.3f = %.3f  %s"
                % ("offset box centre, transform scale", this[1][2], that[1][2],
                   ratio, verdict))

    log("")
    log("=== verdicts: a ROTATED collider under NON-UNIFORM scale ===")
    log("  (which frame does the engine apply the scale in? the two predictions")
    log("   below differ, so the reading picks one -- it is not a tolerance call)")
    rot_plain, rot_nonuni = readings.get("rot_plain"), readings.get("rot_nonuni")
    if rot_plain is None or rot_nonuni is None:
        log("  NO READING")
    else:
        unrotated = [2.0 * half for half in BOX_HALF]
        turned = _aabb_size(BOX_HALF, _quat_matrix(ROT_X90))
        log("  unscaled+rotated  measured %r vs predicted %r"
            % ([round(v, 3) for v in rot_plain[0]], [round(v, 3) for v in turned]))
        if max(abs(rot_plain[0][i] - turned[i]) for i in range(3)) > 0.05:
            log("    the rotation itself did not take; the scaled reading below "
                "says nothing (unrotated would be %r)"
                % [round(v, 3) for v in unrotated])
        outside, inside = _predicted_rotated(BOX_HALF, ROT_X90, ROT_SCALE)
        measured = rot_nonuni[0]
        matches_outside = max(abs(measured[i] - outside[i]) for i in range(3)) < 0.05
        matches_inside = max(abs(measured[i] - inside[i]) for i in range(3)) < 0.05
        log("  scaled+rotated    measured %r" % [round(v, 3) for v in measured])
        log("    ENTITY space (scale outside the rotation) predicts %r  %s"
            % ([round(v, 3) for v in outside], "<== MATCH" if matches_outside else ""))
        log("    SHAPE space  (scale inside the rotation)  predicts %r  %s"
            % ([round(v, 3) for v in inside], "<== MATCH" if matches_inside else ""))
        if not (matches_outside or matches_inside):
            log("    neither -- the engine is doing something this probe does "
                "not model, and that is the interesting result")

    log("")
    log("Reading it: a ratio of ~2 means the engine already applies that scale, "
        "so physics_build multiplying dimensions (or offsets) by it as well "
        "squares the collision. A ratio of ~1 means the baking is what makes it "
        "correct. Dimensions and offsets are separate questions and a backend "
        "can answer them differently.")


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
