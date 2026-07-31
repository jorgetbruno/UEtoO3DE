"""
m3b_backend_smoke.py — the SAME physics contract, asserted on either backend.

Runs against whichever project it is launched in (UEtoO3DETest-Jolt or
UEtoO3DETest-PhysX) and drives the adapter DIRECTLY rather than importing a
manifest: the contract under test is `PhysicsBackendAdapter`, and building
entities in-session keeps this suite independent of staged assets and AP
products in a freshly created project.

`UEO3DE_EXPECT_BACKEND` names the backend the caller believes this project
provides. Detection must agree -- a project that silently resolved the OTHER
backend would otherwise pass every assertion below while proving nothing
about the backend anyone thinks is under test (constraint 5, "available !=
active").

Assertions, in the order a failure is most diagnostic:

  1. detection agrees with the caller, and the adapter resolves;
  2. `contact_offset()` is readable and sane -- tolerances derive from it,
     never a hard-coded 0.02;
  3. capability honesty: the advertised set matches what the backend can
     really do. Jolt bakes trimesh collision from the render mesh; PhysX
     needs a cooked .pxmesh and therefore must NOT advertise it (measured,
     probe_m3b_physx2). Advertising a capability the backend lacks is how a
     level ends up with colliders that have no geometry;
  4. SHAPE SELECTION, PINNED BY BEHAVIOUR. PhysX selects shapes with an
     integer enum that ACCEPTS AND ECHOES every value 0..9, so no readback
     can prove the sphere constant is really a sphere. Instead a sphere
     (r=0.30), a box (half 0.50) and a capsule (r=0.20, h=1.20) are dropped
     onto a floor: each must rest at ITS OWN analytic height. A wrong enum
     lands the body at a different, and detectably wrong, height;
  4b. SHAPE DIMENSIONS, all three axes. A resting height reads ONE axis, so
     the box's deliberately unequal half extents did not actually constrain
     the dimension ORDER: swap X and Y and every assertion above still
     passes. Three STATIC subjects (no falling, so no settling rotation to
     skew the reading) have their world AABB measured against the authored
     size, which is what makes the ordering claim true rather than intended;
  5. dynamic bodies fall and rest; static bodies never move;
  6. kinematic bodies ignore gravity;
  7. a trigger does not physically block a falling body.

Run: Tests/o3de/run_o3de_python.bat Tests/m3b/m3b_backend_smoke.py <result> <project>
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

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm3b_backend_smoke_result.txt')

EXPECT_BACKEND = os.environ.get("UEO3DE_EXPECT_BACKEND", "").strip().lower()

FLOOR_TOP_Z = 0.0
FLOOR_HALF = 0.5
DROP_Z = 3.0
SETTLE_TICKS = 12          # x30 frames
SIM_FRAMES = 30

# Distinct analytic rest heights so a wrong shape cannot coincidentally pass.
# NONE of these may equal a PhysX DEFAULT rest height, or the leg is vacuous:
# the default Box Dimensions are (1,1,1) and the default Capsule is r=0.25
# h=1.0, so BOTH default to a 0.50 rest height. An earlier revision used
# BOX_HALF = 0.50 and could not have distinguished a correct box from a
# collider that silently kept its defaults.
SPHERE_RADIUS = 0.30       # rest 0.30
BOX_HALF_Z = 0.45          # rest 0.45 -- not 0.50
BOX_HALF_X = 0.80          # X/Y deliberately != Z so the dimension ORDER is
BOX_HALF_Y = 0.60          # constrained, not just the height
CAPSULE_RADIUS = 0.20
CAPSULE_HEIGHT = 1.20      # O3DE capsule height includes the caps -> rest 0.60

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


def main():
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath

    from ueimporter.adapters import base, detect_in_editor, make_adapter

    log("=== 1. detection agrees with the caller ===")
    if not check(EXPECT_BACKEND in ("jolt", "physx"),
                 "UEO3DE_EXPECT_BACKEND must be jolt or physx, got %r"
                 % EXPECT_BACKEND):
        return
    detection = detect_in_editor(explicit=None)
    log("  detected %r (source %s, settings hint %r, resolved %r)"
        % (detection["backend"], detection["source"],
           detection["settings_hint"], detection["resolved"]))
    if not check(detection["backend"] == EXPECT_BACKEND,
                 "this project resolved %r but the caller expected %r -- the "
                 "assertions below would test the wrong backend"
                 % (detection["backend"], EXPECT_BACKEND)):
        return
    check(not detection["resolved"].get(
              "physx" if EXPECT_BACKEND == "jolt" else "jolt", False),
          "the OTHER backend also resolves here; these projects are meant to "
          "carry exactly one, and ambiguity must never be resolved silently")

    # A level must be open BEFORE resolve_components(): it reads the backend's
    # contact offset off a live collider, and creating that scratch entity
    # without a level throws. `importer.import_level` opens the level first
    # for the same reason, so this mirrors production order rather than
    # working around it.
    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    adapter = make_adapter(detection["backend"])
    adapter.resolve_components()
    log("  adapter %r resolved" % adapter.name())

    log("")
    log("=== 2. contact offset (tolerances derive from it) ===")
    contact = adapter.contact_offset()
    log("  contact_offset = %.6f m" % contact)
    check(0.0 <= contact < 0.5, "contact offset %r is not sane" % contact)
    tolerance = contact + 0.05

    log("")
    log("=== 3. capability honesty ===")
    caps = adapter.capabilities()
    log("  advertises: %s" % sorted(caps))
    for required in (base.CAP_SHAPE_BOX, base.CAP_SHAPE_SPHERE,
                     base.CAP_SHAPE_CAPSULE, base.CAP_TRIGGER,
                     base.CAP_KINEMATIC):
        check(required in caps, "%s must advertise %s" % (adapter.name(), required))
    if adapter.name() == "physx":
        # Measured: PhysX mesh colliders need a cooked .pxmesh asset and have
        # no render-mesh fallback. Advertising trimesh would let the importer
        # author colliders with no geometry -- silent, and indistinguishable
        # from a physics bug.
        check(base.CAP_SHAPE_TRIMESH not in caps,
              "PhysX advertises trimesh, but it has no render-mesh bake; the "
              "importer would author empty mesh colliders")
        # Convex matters just as much: physics_build picks convex for
        # dynamic/kinematic bodies, so advertising it would send those down
        # the same dead path.
        check(base.CAP_SHAPE_CONVEX not in caps,
              "PhysX advertises convex, but convex hulls come from the same "
              "cooked-asset path it cannot build")
        # What it CAN do is author from a cooked .pxmesh the caller resolved;
        # losing this capability silently reverts every convex asset to AABB
        # boxes and every no-simple-collision static mesh to no collider.
        check(base.CAP_SHAPE_MESH_COOKED in caps,
              "PhysX must advertise %s -- the cooked-asset mesh collider "
              "route" % base.CAP_SHAPE_MESH_COOKED)
        raised = False
        try:
            scratch = editor.ToolsApplicationRequestBus(
                bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
            adapter.add_mesh_collider(scratch, convex=False)
        except Exception:
            raised = True
        check(raised, "PhysX add_mesh_collider must refuse loudly rather than "
                      "author a geometry-less collider")
    else:
        check(base.CAP_SHAPE_TRIMESH in caps,
              "Jolt must advertise trimesh (it bakes from the render mesh)")

    log("")
    log("=== 3b. mass actually takes (PhysX write-order regression) ===")
    # THE milestone's headline PhysX finding: 'Compute Mass' defaults ON and
    # recomputes the value, so a Mass written before disabling it is silently
    # discarded (measured: 42 kg reads back 1.0). The adapter writes
    # Compute Mass=False first -- assert the value survives, or a reordering
    # would sail through every other check here.
    mass_entity = editor.ToolsApplicationRequestBus(
        bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
    editor.EditorEntityAPIBus(bus.Event, 'SetName', mass_entity, 'M3B_MassProbe')
    adapter.add_dynamic_body(mass_entity, mass=42.0)
    mass_paths = {"jolt": "Configuration|Mass", "physx": "Configuration|Mass"}
    body_names = {"jolt": "Jolt Rigid Body", "physx": "PhysX Dynamic Rigid Body"}
    from ueimporter import prefab_build
    body_type = prefab_build.resolve_component_type(body_names[adapter.name()])
    got = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', mass_entity, body_type)
    read_mass = None
    if got and got.IsSuccess():
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', got.GetValue(),
            mass_paths[adapter.name()])
        if outcome and outcome.IsSuccess():
            read_mass = outcome.GetValue()
    log("  requested mass 42.0 -> reads back %r" % (read_mass,))
    check(read_mass is not None and abs(float(read_mass) - 42.0) < 1e-3,
          "mass read back as %r, not 42.0 -- on PhysX this means the write "
          "order regressed and 'Compute Mass' silently recomputed it away"
          % (read_mass,))
    editor.ToolsApplicationRequestBus(bus.Broadcast, 'DeleteEntityById', mass_entity)

    log("")
    log("=== 4-7. simulation ===")

    def spawn(name, position):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, name)
        components.TransformBus(bus.Event, 'SetWorldTranslation', entity_id,
                                azmath.Vector3(position[0], position[1], position[2]))
        return entity_id

    # Floor: top face at z = 0.
    floor = spawn('M3B_Floor', (0.0, 0.0, FLOOR_TOP_Z - FLOOR_HALF))
    adapter.add_static_body(floor)
    adapter.add_box_collider(floor, [10.0, 10.0, FLOOR_HALF])

    # One body per primitive shape, each at its own analytic rest height.
    subjects = []

    sphere = spawn('M3B_Sphere', (0.0, 0.0, DROP_Z))
    adapter.add_dynamic_body(sphere)
    adapter.add_sphere_collider(sphere, SPHERE_RADIUS)
    subjects.append(('M3B_Sphere', SPHERE_RADIUS, 'sphere'))

    box = spawn('M3B_Box', (3.0, 0.0, DROP_Z))
    adapter.add_dynamic_body(box)
    adapter.add_box_collider(box, [BOX_HALF_X, BOX_HALF_Y, BOX_HALF_Z])
    subjects.append(('M3B_Box', BOX_HALF_Z, 'box'))

    capsule = spawn('M3B_Capsule', (6.0, 0.0, DROP_Z))
    adapter.add_dynamic_body(capsule)
    adapter.add_capsule_collider(capsule, CAPSULE_RADIUS, CAPSULE_HEIGHT)
    subjects.append(('M3B_Capsule', CAPSULE_HEIGHT / 2.0, 'capsule'))

    # Kinematic: must ignore gravity entirely.
    kinematic = spawn('M3B_Kinematic', (-3.0, 0.0, DROP_Z))
    adapter.add_dynamic_body(kinematic, kinematic=True)
    adapter.add_box_collider(kinematic, [0.5, 0.5, 0.5])

    # Trigger: a sensor must not physically block the ball dropped through it.
    trigger = spawn('M3B_Trigger', (-6.0, 0.0, 1.5))
    adapter.add_static_body(trigger)
    adapter.add_box_collider(trigger, [1.0, 1.0, 0.25])
    adapter.make_trigger(trigger)
    faller = spawn('M3B_Faller', (-6.0, 0.0, DROP_Z))
    adapter.add_dynamic_body(faller)
    adapter.add_sphere_collider(faller, 0.2)

    # Static twins of the three shapes, for the DIMENSION reading. Static
    # because a dropped body can settle a degree off level, and an AABB is
    # axis-aligned in WORLD space -- a tilted box reports a bigger box, which
    # would turn an exact assertion into a fuzzy one for no reason. Parked far
    # from the drop subjects so nothing can rest on them.
    sizes = [('M3B_SizeSphere', 'sphere',
              lambda e: adapter.add_sphere_collider(e, SPHERE_RADIUS),
              [2.0 * SPHERE_RADIUS] * 3),
             ('M3B_SizeBox', 'box',
              lambda e: adapter.add_box_collider(
                  e, [BOX_HALF_X, BOX_HALF_Y, BOX_HALF_Z]),
              [2.0 * BOX_HALF_X, 2.0 * BOX_HALF_Y, 2.0 * BOX_HALF_Z]),
             ('M3B_SizeCapsule', 'capsule',
              lambda e: adapter.add_capsule_collider(
                  e, CAPSULE_RADIUS, CAPSULE_HEIGHT),
              [2.0 * CAPSULE_RADIUS, 2.0 * CAPSULE_RADIUS, CAPSULE_HEIGHT])]
    for index, (name, _label, author, _expected) in enumerate(sizes):
        entity_id = spawn(name, (0.0, 20.0 + 5.0 * index, 5.0))
        adapter.add_static_body(entity_id)
        author(entity_id)

    general.idle_wait_frames(60)
    general.enter_game_mode()
    general.idle_wait_frames(30)
    if not check(general.is_in_game_mode(), "editor did not enter game mode"):
        return
    for _ in range(SETTLE_TICKS):
        general.idle_wait_frames(SIM_FRAMES)

    def world_z(name):
        game_id = general.find_game_entity(name)
        if game_id is None or not game_id.IsValid():
            return None
        return components.TransformBus(bus.Event, 'GetWorldTranslation', game_id).z

    log("  shape rest heights (a wrong shape selector lands elsewhere):")
    for name, expected, label in subjects:
        z = world_z(name)
        if not check(z is not None, "%s missing in game mode" % name):
            continue
        log("    %-14s %-8s rest z=%.4f expected %.4f (delta %+.4f)"
            % (name, label, z, expected, z - expected))
        check(abs(z - expected) <= tolerance,
              "%s rested at %.4f, not its analytic %.4f (tolerance %.4f). On "
              "PhysX this is also the SHAPE ENUM assertion: the collider is "
              "not the %s it was asked for" % (name, z, expected, tolerance, label))

    log("")
    log("  shape dimensions, all three axes (a rest height reads only one):")
    for name, label, _author, expected in sizes:
        game_id = general.find_game_entity(name)
        if not check(game_id is not None and game_id.IsValid(),
                     "%s missing in game mode" % name):
            continue
        measured = editor_physics.body_extents(game_id)
        if not check(measured is not None,
                     "%s has no simulated body, so its %s collider carries no "
                     "geometry" % (name, label)):
            continue
        size = measured[0]
        log("    %-16s %-8s AABB (%.3f, %.3f, %.3f) expected (%.3f, %.3f, %.3f)"
            % (name, label, size[0], size[1], size[2],
               expected[0], expected[1], expected[2]))
        check(max(abs(size[i] - expected[i]) for i in range(3)) <= 0.02,
              "%s: the %s collider measures %r, not the authored %r. Every "
              "axis is constrained here, so this also catches a dimension "
              "ORDER swap that a resting height cannot see"
              % (name, label, [round(v, 3) for v in size],
                 [round(v, 3) for v in expected]))

    floor_z = world_z('M3B_Floor')
    check(floor_z is not None and abs(floor_z - (FLOOR_TOP_Z - FLOOR_HALF)) < 1e-3,
          "the static floor moved (z=%r)" % floor_z)

    kin_z = world_z('M3B_Kinematic')
    log("    %-14s %-8s rest z=%.4f (dropped from %.1f)"
        % ('M3B_Kinematic', 'kinematic', kin_z if kin_z is not None else -999, DROP_Z))
    check(kin_z is not None and abs(kin_z - DROP_Z) < 1e-2,
          "the kinematic body fell to %r; it must ignore gravity" % kin_z)

    fall_z = world_z('M3B_Faller')
    log("    %-14s %-8s rest z=%.4f (must be BELOW the trigger at 1.5)"
        % ('M3B_Faller', 'trigger', fall_z if fall_z is not None else -999))
    check(fall_z is not None and fall_z < 1.0,
          "the ball stopped at %r instead of passing through the trigger; a "
          "sensor must be physically transparent" % fall_z)

    general.exit_game_mode()
    general.idle_wait_frames(10)


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
