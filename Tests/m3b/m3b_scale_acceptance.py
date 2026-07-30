"""
m3b_scale_acceptance.py — a SCALED entity, authored through the real adapter,
measured by the real physics engine.

`physics_build` multiplied collider dimensions and offsets by the entity's
world scale while both backends were already applying that scale themselves,
so every scaled entity collided at scale² — 4x for a 2x actor — and every
suite stayed green. This test is the one that fails when that comes back.

The repo does have scaled fixtures; what it had no assertion for was a scaled
collider's SIZE. Fixture_01's floor is scaled (10, 10, 1) and its collision
was ten times too wide in X and Y, which nothing noticed because every
assertion about it reads the height a ball rests at.

It authors the same synthetic manifest entity at three scales through
`physics_build.author_entity_physics` (so the adapter, the capability
negotiation and the whole authoring path are under test, not a mock), enters
game mode, and reads each collider's WORLD AABB from the simulated body. The
expected size is the manifest size times the scale, ONCE.

Why the AABB and not a dropped ball: a resting height measures one axis of one
shape and cannot see an offset error at all, and on a rounded cooked mesh the
ball rolls off during the settle window and reports "no collision" for a
collider that is demonstrably there (measured — that mistake cost seven probe
runs). The AABB is exact, immediate, and reads all three axes plus the centre.

CONTROL: the unscaled subject must read its authored size and offset exactly.
If it does not, the query is not measuring what it claims and the run is
refused before any verdict — a scaled reading is only meaningful against a
trustworthy unscaled one.

Run: Tests/o3de/run_o3de_python.bat Tests/m3b/m3b_scale_acceptance.py \
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
               else os.path.join(SCRIPT_DIR, 'results', 'm3b_scale_acceptance_result.txt'))

HALF_EXTENTS = [1.0, 0.5, 0.25]   # unequal on purpose: a per-axis error shows
COLLIDER_OFFSET = [0.0, 0.0, 2.0]
# 90 degrees about X, xyzw. UE collision elements carry rotations, so this is
# the shape the importer actually authors -- and it is the case where "who
# applies the scale" splits into "in which FRAME": entity space (outside the
# rotation, the way the render mesh transforms) or the shape's own frame. The
# two disagree unless the scale is uniform. Measured on both backends: entity
# space, exactly.
ROTATION = [0.7071067811865476, 0.0, 0.0, 0.7071067811865476]
ROTATED_SCALE = [1.0, 1.0, 3.0]
SPACING = 40.0
TOLERANCE = 0.02                  # meters; the readings are exact to 1e-3

lines = []
failures = []


def log(message):
    lines.append(str(message))
    print(message)


def fail(message):
    failures.append(str(message))
    log('FAIL: ' + str(message))


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def rotation_matrix(quat):
    """xyzw -> 3x3."""
    x, y, z, w = quat
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def rotated_aabb(half, quat, scale, half_already_scaled=False):
    """World AABB size of a box rotated by `quat` on an entity scaled `scale`.

    The scale multiplies in ENTITY space, outside the rotation: M = diag(s).R,
    and the AABB of a box under M is sum_j |M[i][j]| * half[j]. Passing
    `half_already_scaled` models the defect -- the importer having multiplied
    the half extents in the shape's own frame before the engine scales again.
    """
    matrix = rotation_matrix(quat)
    extents = [half[j] * (scale[j] if half_already_scaled else 1.0)
               for j in range(3)]
    return [2.0 * sum(abs(scale[i] * matrix[i][j]) * extents[j] for j in range(3))
            for i in range(3)]


def _corners(aabb):
    if all(callable(getattr(aabb, getter, None)) for getter in ('GetMin', 'GetMax')):
        return aabb.GetMin(), aabb.GetMax()
    minimum = getattr(aabb, 'min', None)
    maximum = getattr(aabb, 'max', None)
    if minimum is not None and maximum is not None and hasattr(minimum, 'x'):
        return minimum, maximum
    return None, None


def aabb_of(entity_id):
    """(size, centre) of a game entity's simulated body, or (None, None)."""
    import azlmbr.bus as bus

    try:
        import azlmbr.physics as physics
    except ImportError:
        return None, None

    for name in ('SimulatedBodyComponentRequestBus',
                 'SimulatedBodyComponentRequestsBus',
                 'RigidBodyRequestBus'):
        handler = getattr(physics, name, None)
        if handler is None:
            continue
        try:
            aabb = handler(bus.Event, 'GetAabb', entity_id)
        except Exception:  # noqa: BLE001 - any bus failure means "try the next"
            continue
        if aabb is None:
            continue
        minimum, maximum = _corners(aabb)
        if minimum is None:
            continue
        return ([maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z],
                [(maximum.x + minimum.x) * 0.5,
                 (maximum.y + minimum.y) * 0.5,
                 (maximum.z + minimum.z) * 0.5])
    return None, None


def main():
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath

    from ueimporter import physics_build, prefab_build
    from ueimporter.adapters import detect_in_editor, make_adapter
    from ueimporter.report import Report

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    expected_backend = os.environ.get("UEO3DE_EXPECT_BACKEND", "").strip() or None
    detection = detect_in_editor(explicit=os.environ.get("UEO3DE_BACKEND") or None)
    adapter = make_adapter(detection["backend"])
    adapter.resolve_components()
    backend = adapter.name()
    log("backend: %s" % backend)
    if expected_backend and backend != expected_backend:
        fail("this project resolved %r, not the expected %r; every assertion "
             "below would be about the wrong backend" % (backend, expected_backend))
        return

    report = Report()
    prefix = "SA_"

    # (label, world scale, non-uniform component or None, collider rotation)
    subjects = [("plain", [1.0, 1.0, 1.0], None, None),
                ("uniform", [2.0, 2.0, 2.0], None, None),
                ("nonuni", [1.0, 1.0, 3.0], (1.0, 1.0, 3.0), None),
                ("rot_plain", [1.0, 1.0, 1.0], None, ROTATION),
                ("rot_nonuni", list(ROTATED_SCALE), tuple(ROTATED_SCALE), ROTATION)]

    log("")
    log("=== authored subjects (manifest half extents %r, collider offset %r) ==="
        % (HALF_EXTENTS, COLLIDER_OFFSET))
    for index, (label, scale, nonuniform, rotation) in enumerate(subjects):
        x = index * SPACING
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, prefix + label)
        components.TransformBus(bus.Event, 'SetWorldTranslation', entity_id,
                                azmath.Vector3(float(x), 0.0, 0.0))
        if nonuniform is not None:
            outcome = editor.EditorComponentAPIBus(
                bus.Broadcast, 'AddComponentsOfType', entity_id,
                [prefab_build._uuid_from_string(prefab_build.NON_UNIFORM_SCALE_TYPE_ID)])
            if not check(outcome and outcome.IsSuccess(),
                         "could not add the non-uniform scale component to " + label):
                return
            entity_module.NonUniformScaleRequestBus(
                bus.Event, 'SetScale', entity_id,
                azmath.Vector3(*[float(v) for v in nonuniform]))
        elif scale[0] != 1.0:
            components.TransformBus(bus.Event, 'SetLocalUniformScale',
                                    entity_id, float(scale[0]))

        # The manifest the importer would have produced for this entity, with
        # the SAME world scale the transform above carries. A rotated subject
        # carries no collider offset: the offset and the rotation are separate
        # questions and mixing them would make a failure ambiguous.
        shape = {"type": "box", "half_extents": list(HALF_EXTENTS)}
        if rotation is None:
            shape["offset"] = list(COLLIDER_OFFSET)
        else:
            shape["rotation"] = list(rotation)
        item = {"name": prefix + label,
                "transform": {"world": {"scale": list(scale)}},
                "physics": {"has_collision": True, "is_trigger": False,
                            "simulates_physics": False, "kinematic": False,
                            "collision_profile": "", "ccd": False,
                            "enable_gravity": True, "linear_damping": 0.0,
                            "angular_damping": 0.0, "mass_override": False,
                            "mass_kg": None, "shapes_from_asset": None,
                            "shapes": [shape]}}
        physics_build.author_entity_physics(adapter, entity_id, item, {}, report, {})
        log("  %-11s world scale %-16r %s"
            % (label, scale, "rotated" if rotation else "axis-aligned"))

    general.idle_wait_frames(30)
    general.enter_game_mode()
    general.idle_wait_frames(60)
    if not check(general.is_in_game_mode(), "editor did not enter game mode"):
        return

    readings = {}
    for index, (label, _scale, _nonuniform, _rotation) in enumerate(subjects):
        game_id = general.find_game_entity(prefix + label)
        if game_id is None or not game_id.IsValid():
            readings[label] = None
            continue
        size, centre = aabb_of(game_id)
        readings[label] = None if size is None else (size, centre, index * SPACING)
    general.exit_game_mode()
    general.idle_wait_frames(10)

    log("")
    log("=== measured world AABBs ===")
    for label, _scale, _nonuniform, _rotation in subjects:
        reading = readings.get(label)
        if reading is None:
            log("  %-11s NO BODY" % label)
            continue
        size, centre, origin_x = reading
        log("  %-11s size (%.3f, %.3f, %.3f)  centre (%.3f, %.3f, %.3f)"
            % (label, size[0], size[1], size[2],
               centre[0] - origin_x, centre[1], centre[2]))

    # CONTROL first: the unscaled subject is analytic.
    plain = readings.get("plain")
    if not check(plain is not None,
                 "the unscaled subject produced no simulated body; nothing in "
                 "this run can be trusted"):
        return
    size, centre, origin_x = plain
    expected_size = [2.0 * half for half in HALF_EXTENTS]
    if not check(max(abs(size[i] - expected_size[i]) for i in range(3)) <= TOLERANCE,
                 "the UNSCALED subject's AABB is %r, not its authored %r; the "
                 "measurement is wrong, so the scaled verdicts below say nothing"
                 % ([round(s, 3) for s in size], expected_size)):
        return
    if not check(abs(centre[2] - COLLIDER_OFFSET[2]) <= TOLERANCE,
                 "the UNSCALED subject's collider centre is z=%.3f, not its "
                 "authored %.3f" % (centre[2], COLLIDER_OFFSET[2])):
        return
    log("")
    log("  control: unscaled AABB %r and centre z=%.3f match the manifest OK"
        % ([round(s, 3) for s in size], centre[2]))

    log("")
    log("=== verdicts (expected = manifest x scale, applied ONCE) ===")
    for label, scale, _nonuniform, rotation in subjects[1:]:
        reading = readings.get(label)
        if not check(reading is not None,
                     "%s produced no simulated body" % label):
            continue
        size, centre, origin_x = reading

        if rotation is not None:
            # A rotated collider answers a second question: in WHICH FRAME does
            # the scale multiply. Entity space (outside the rotation) is what
            # both backends do -- measured, and the two conventions disagree by
            # more than any tolerance, so this reading picks one.
            want = rotated_aabb(HALF_EXTENTS, rotation, scale)
            baked = rotated_aabb(HALF_EXTENTS, rotation, scale,
                                 half_already_scaled=True)
            looks_baked = max(abs(size[i] - baked[i]) for i in range(3)) <= TOLERANCE
            if check(max(abs(size[i] - want[i]) for i in range(3)) <= TOLERANCE,
                     "%s: rotated collider size %r, expected %r%s"
                     % (label, [round(s, 3) for s in size],
                        [round(s, 3) for s in want],
                        " -- this is what you get when the importer scales the "
                        "half extents in the SHAPE's frame and the engine then "
                        "scales again in entity space (%r)"
                        % [round(s, 3) for s in baked] if looks_baked else "")):
                log("  %-11s size %r (rotated)  OK"
                    % (label, [round(s, 3) for s in size]))
            continue

        want_size = [2.0 * HALF_EXTENTS[i] * scale[i] for i in range(3)]
        squared = [2.0 * HALF_EXTENTS[i] * scale[i] * scale[i] for i in range(3)]
        want_centre = COLLIDER_OFFSET[2] * scale[2]
        squared_centre = COLLIDER_OFFSET[2] * scale[2] * scale[2]

        looks_squared = max(abs(size[i] - squared[i]) for i in range(3)) <= TOLERANCE
        sized = check(
            max(abs(size[i] - want_size[i]) for i in range(3)) <= TOLERANCE,
            "%s: collider size %r, expected %r%s"
            % (label, [round(s, 3) for s in size], [round(s, 3) for s in want_size],
               " -- this is the manifest size times the scale SQUARED (%r): "
               "the importer is baking a scale the engine also applies"
               % [round(s, 3) for s in squared] if looks_squared else ""))
        centred = check(
            abs(centre[2] - want_centre) <= TOLERANCE,
            "%s: collider centre z=%.3f, expected %.3f%s"
            % (label, centre[2], want_centre,
               " -- that is the offset scaled TWICE (%.3f)" % squared_centre
               if abs(centre[2] - squared_centre) <= TOLERANCE else ""))
        if sized and centred:
            log("  %-11s size %r centre z=%.3f  OK"
                % (label, [round(s, 3) for s in size], centre[2]))

    # Nothing above was approximated: the engine applies non-uniform scale to a
    # box natively, so a warning here would be a false alarm the report cannot
    # afford (it is how a real approximation gets ignored).
    codes = [record["code"] for record in report.records()]
    check("PHYS_SHAPE_APPROXIMATED" not in codes,
          "authoring reported PHYS_SHAPE_APPROXIMATED for boxes that needed no "
          "approximation; codes were %r" % (codes,))


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
