"""
probe_cooked_shape.py -- WHERE is a cooked mesh collider's geometry, if it is
anywhere at all?

`probe_scale_matrix` has now failed its cooked-mesh control on both backends,
across seven runs: the product is on disk, `asset_wait.resolve` returns an id,
the gem logs no complaint (its own "no .joltmesh asset assigned" warning never
fires) -- and a ball dropped from directly above falls straight through to the
floor. Two very different explanations survive that evidence and they need
different fixes:

  * the collider produces NO shape (the asset id reaches the component but the
    asset never loads, or loads empty), or
  * the collider produces a shape SOMEWHERE ELSE -- cooked geometry sits in the
    source scene's coordinates, so a mesh authored 100 m from its FBX origin
    collides 100 m from the entity. A ball dropped on the entity would miss it
    completely, which looks exactly like "no collision" from a drop test.

A drop test cannot tell those apart, so this probe asks the physics system for
the body's world AABB instead of inferring it from a falling ball:

    box control    -> AABB must match the authored half extents (proves the
                      query itself works and is reported in world space)
    cooked mesh    -> AABB is the answer. Empty/absent -> no shape. Present but
                      far from the entity -> the geometry is offset, and the
                      offset is printed so it can be corrected at author time.

The AABB bus name differs across O3DE versions, so every candidate is tried and
the one that answered is logged; if none answer, that is reported as a probe
limitation rather than a finding about the collider.

Env: UEO3DE_COOKED_MESH  product path to test (default: per-backend barrel)
Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_cooked_shape.py \
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
               else os.path.join(SCRIPT_DIR, 'results', 'probe_cooked_shape_result.txt'))

BOX_HALF = [1.0, 1.0, 0.45]
ASSET_LOAD_FRAMES = int(os.environ.get('UEO3DE_PROBE_LOAD_FRAMES', '300'))

lines = []
failures = []


def log(message):
    lines.append(str(message))
    print(message)


def fail(message):
    failures.append(str(message))
    log('FAIL: ' + str(message))


def aabb_of(entity_id):
    """(min, max, bus_name) for a game entity's simulated body, or (None, None, tried)."""
    import azlmbr.bus as bus

    tried = []
    try:
        import azlmbr.physics as physics
    except ImportError:
        return None, None, 'azlmbr.physics not importable'

    for name in ('SimulatedBodyComponentRequestBus',
                 'SimulatedBodyComponentRequestsBus',
                 'RigidBodyRequestBus',
                 'StaticRigidBodyRequestBus'):
        handler = getattr(physics, name, None)
        if handler is None:
            continue
        tried.append(name)
        try:
            aabb = handler(bus.Event, 'GetAabb', entity_id)
        except Exception as error:  # noqa: BLE001 - any bus failure means "try the next"
            tried[-1] += '(raised %s)' % type(error).__name__
            continue
        if aabb is None:
            tried[-1] += '(None)'
            continue
        minimum, maximum = _corners(aabb)
        if minimum is None:
            # A bus that answers with something this probe cannot read is a probe
            # limitation, and the only way to fix it is to see what came back.
            tried[-1] += '(unreadable %s: %s)' % (
                type(aabb).__name__,
                ','.join(sorted(a for a in dir(aabb) if not a.startswith('_')))[:200])
            continue
        return minimum, maximum, name
    return None, None, ', '.join(tried) or 'no candidate bus exists'


def _corners(aabb):
    """(min, max) out of whatever shape the Aabb binding takes, else (None, None)."""
    if all(callable(getattr(aabb, getter, None)) for getter in ('GetMin', 'GetMax')):
        return aabb.GetMin(), aabb.GetMax()
    minimum = getattr(aabb, 'min', None)
    maximum = getattr(aabb, 'max', None)
    if minimum is not None and maximum is not None and hasattr(minimum, 'x'):
        return minimum, maximum
    return None, None


def main():
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath

    from ueimporter import asset_wait
    from ueimporter.adapters import base, detect_in_editor, make_adapter

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    detection = detect_in_editor(explicit=os.environ.get("UEO3DE_BACKEND") or None)
    adapter = make_adapter(detection["backend"])
    adapter.resolve_components()
    backend = adapter.name()
    log("backend: %s" % backend)

    if base.CAP_SHAPE_MESH_COOKED not in adapter.capabilities():
        fail("%s does not advertise cooked mesh colliders; nothing to probe" % backend)
        return

    default_product = ("assets/uetoo3de/game/siegeofponthus/meshes/sm_barrel.fbx."
                       + ("pxmesh" if backend == "physx" else "joltmesh"))
    product = os.environ.get("UEO3DE_COOKED_MESH", "").strip() or default_product
    asset_id = asset_wait.resolve(product)
    log("product: %s -> %s" % (product, "resolved" if asset_id else "NOT IN CATALOG"))
    if asset_id is None:
        fail("the product is not in the catalog; run AssetProcessorBatch first")
        return

    prefix = "CS_"

    def spawn(name, x):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, prefix + name)
        components.TransformBus(bus.Event, 'SetWorldTranslation', entity_id,
                                azmath.Vector3(float(x), 0.0, 0.0))
        return entity_id

    control = spawn("box", 0.0)
    adapter.add_static_body(control)
    adapter.add_box_collider(control, BOX_HALF)

    subject = spawn("mesh", 40.0)
    adapter.add_static_body(subject)
    adapter.add_mesh_collider(subject, convex=True, asset_id=asset_id)

    general.idle_wait_frames(ASSET_LOAD_FRAMES)
    general.enter_game_mode()
    general.idle_wait_frames(60)
    if not general.is_in_game_mode():
        fail("editor did not enter game mode")
        return

    readings = {}
    for label, origin_x in (("box", 0.0), ("mesh", 40.0)):
        game_id = general.find_game_entity(prefix + label)
        if game_id is None or not game_id.IsValid():
            fail("no game entity for %s" % label)
            continue
        minimum, maximum, how = aabb_of(game_id)
        readings[label] = (minimum, maximum, origin_x)
        log("")
        log("=== %s (entity at x=%.1f) ===" % (label, origin_x))
        log("  bus: %s" % how)
        if minimum is None:
            log("  AABB: none")
        else:
            log("  AABB min (%.3f, %.3f, %.3f)" % (minimum.x, minimum.y, minimum.z))
            log("  AABB max (%.3f, %.3f, %.3f)" % (maximum.x, maximum.y, maximum.z))
            log("  size    (%.3f, %.3f, %.3f)"
                % (maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z))
            log("  center offset from the entity: (%.3f, %.3f, %.3f)"
                % ((maximum.x + minimum.x) * 0.5 - origin_x,
                   (maximum.y + minimum.y) * 0.5,
                   (maximum.z + minimum.z) * 0.5))

    general.exit_game_mode()
    general.idle_wait_frames(10)

    # CONTROL: the box's AABB is analytic, so a wrong reading here means the
    # query is the problem and the mesh reading below says nothing.
    box = readings.get("box")
    if not box or box[0] is None:
        fail("the box control returned no AABB -- this probe cannot measure "
             "anything on this build; the mesh reading above is not evidence")
        return
    minimum, maximum, _origin = box
    size = [maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z]
    expected = [2.0 * half for half in BOX_HALF]
    if max(abs(size[i] - expected[i]) for i in range(3)) > 0.15:
        fail("the box control's AABB is %r, not its authored %r; the query is "
             "not measuring what it claims" % ([round(s, 3) for s in size], expected))
        return
    log("")
    log("  control: box AABB size %r matches the authored %r OK"
        % ([round(s, 3) for s in size], expected))

    mesh = readings.get("mesh")
    if not mesh or mesh[0] is None:
        fail("the cooked mesh collider has NO AABB while the box control has "
             "one: the asset id reaches the component but no shape is created")
        return
    minimum, maximum, origin_x = mesh
    size = [maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z]
    center = [(maximum.x + minimum.x) * 0.5 - origin_x,
              (maximum.y + minimum.y) * 0.5,
              (maximum.z + minimum.z) * 0.5]
    log("")
    if max(size) < 1e-3:
        fail("the cooked mesh collider's AABB is degenerate %r: a shape exists "
             "but carries no geometry" % [round(s, 4) for s in size])
        return
    drift = max(abs(component) for component in center)
    if drift > max(size) :
        fail("the cooked geometry sits %.2f m from its entity (center offset "
             "%r, size %r): the shape is real but nowhere near the entity, "
             "which is why drop tests miss it"
             % (drift, [round(c, 3) for c in center], [round(s, 3) for s in size]))
        return
    log("  the cooked mesh collider HAS geometry: size %r centered %r on the "
        "entity -- so a drop test missing it is the drop test's fault"
        % ([round(s, 3) for s in size], [round(c, 3) for c in center]))


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
