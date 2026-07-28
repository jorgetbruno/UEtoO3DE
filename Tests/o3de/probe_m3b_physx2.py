"""
probe_m3b_physx2.py -- M3b round 2: the four things the adapter cannot guess.

Round 1 mapped the component surface. It left four questions whose wrong
answer fails SILENTLY (the class of bug this project keeps finding late):

  1. SHAPE ENUM. PhysX has ONE 'PhysX Primitive Collider' with
     'Shape Configuration|Shape' (default 7), not Jolt's component-per-shape.
     Which integer is Sphere/Box/Capsule/Cylinder? And round 1's dump showed
     Box/Capsule/Cylinder sub-groups but NO Sphere -- so where does a
     sphere's radius live? Setting the wrong enum gives a collider of the
     wrong shape that still simulates.
  2. KINEMATIC. There is no 'Kinematic' property; the likely flag is
     'Configuration|Type' (default False), reflected as a Simulated/Kinematic
     combo. Verify by readback.
  3. MASS WRITE ORDER. 'Configuration|Compute Mass' defaults True, so a Mass
     written while it is on is recomputed away -- the same trap as M5's light
     intensity-vs-mode. Measure whether order matters.
  4. TRIMESH. 'PhysX Mesh Collider' wants a COOKED asset
     ('Shape Configuration|Asset|PhysX Mesh'), unlike Jolt's bake-from-render
     -mesh. Confirm there is no render-mesh path, so the adapter can declare
     the capability honestly instead of authoring a collider that is empty.

Run: run_o3de_python.bat probe_m3b_physx2.py <result> <PhysX project path>
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m3b_physx2_result.txt')

os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
_handle = open(RESULT_PATH, 'w')


def log(msg=""):
    _handle.write(str(msg) + "\n")
    _handle.flush()
    print(msg)


def main():
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath
    from azlmbr.entity import EntityType

    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    def type_id(name):
        ids = editor.EditorComponentAPIBus(
            bus.Broadcast, 'FindComponentTypeIdsByEntityType', [name], game_type)
        return ids[0] if ids and ids[0] and not ids[0].IsNull() else None

    def new_entity(label):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, label)
        return entity_id

    def add(entity_id, name):
        tid = type_id(name)
        editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', entity_id, [tid])
        return editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, tid).GetValue()

    def get(pair, path):
        try:
            outcome = editor.EditorComponentAPIBus(
                bus.Broadcast, 'GetComponentProperty', pair, path)
            return outcome.GetValue() if outcome and outcome.IsSuccess() else None
        except Exception:
            return "<unmarshallable>"

    def put(pair, path, value):
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', pair, path, value)
        return bool(outcome and outcome.IsSuccess())

    log("=== 1. Shape enum on PhysX Primitive Collider ===")
    entity_id = new_entity('__shape_probe')
    pair = add(entity_id, "PhysX Primitive Collider")
    log("  default Shape = %r" % get(pair, "Shape Configuration|Shape"))
    for value in range(0, 10):
        ok = put(pair, "Shape Configuration|Shape", value)
        back = get(pair, "Shape Configuration|Shape")
        paths = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentPropertyList', pair) or []
        groups = sorted({p.split("|")[1] for p in paths
                         if p.startswith("Shape Configuration|")
                         and len(p.split("|")) > 1})
        log("  Shape=%d set=%-5s readback=%-4r sub-groups=%r"
            % (value, ok, back, groups))

    log("")
    log("=== 1b. where does a SPHERE radius live? ===")
    for candidate in ("Shape Configuration|Sphere|Radius",
                      "Shape Configuration|Radius",
                      "Shape Configuration|Sphere radius",
                      "Shape Configuration|Capsule|Radius"):
        ok = put(pair, candidate, 0.7)
        log("  set %-42s -> %s (readback %r)"
            % (candidate, ok, get(pair, candidate)))

    log("")
    log("=== 2. kinematic flag ===")
    body_entity = new_entity('__body_probe')
    body = add(body_entity, "PhysX Dynamic Rigid Body")
    log("  default Configuration|Type = %r" % get(body, "Configuration|Type"))
    for value in (True, False, 1, 0):
        ok = put(body, "Configuration|Type", value)
        log("  set Type=%-5r -> %-5s readback=%r"
            % (value, ok, get(body, "Configuration|Type")))
    for candidate in ("Configuration|Kinematic", "Configuration|kinematic"):
        log("  %s exists: %s" % (candidate, get(body, candidate) is not None))

    log("")
    log("=== 3. mass write order (Compute Mass vs Mass) ===")
    log("  -- order A: Mass first, then Compute Mass=False --")
    put(body, "Configuration|Compute Mass", True)
    put(body, "Configuration|Mass", 42.0)
    log("     after Mass=42 (Compute Mass still True): Mass=%r"
        % get(body, "Configuration|Mass"))
    put(body, "Configuration|Compute Mass", False)
    log("     after Compute Mass=False:                Mass=%r"
        % get(body, "Configuration|Mass"))

    log("  -- order B: Compute Mass=False first, then Mass --")
    put(body, "Configuration|Compute Mass", True)
    put(body, "Configuration|Mass", 1.0)
    put(body, "Configuration|Compute Mass", False)
    put(body, "Configuration|Mass", 42.0)
    log("     Mass=%r  (if this is 42 and order A is not, ORDER MATTERS)"
        % get(body, "Configuration|Mass"))

    log("")
    log("=== 4. trimesh: is there ANY render-mesh path? ===")
    mesh_entity = new_entity('__mesh_probe')
    mesh_pair = add(mesh_entity, "PhysX Mesh Collider")
    paths = editor.EditorComponentAPIBus(
        bus.Broadcast, 'BuildComponentPropertyList', mesh_pair) or []
    interesting = [p for p in sorted(paths) if any(
        k in p.lower() for k in ("asset", "mesh", "render", "visible", "type"))]
    for p in interesting:
        log("  %-58s = %r" % (p, get(mesh_pair, p)))
    log("  NOTE: an empty 'PhysX Mesh' asset id means the collider has NO "
        "geometry; unlike Jolt there is no bake-from-render-mesh fallback.")

    for e in (entity_id, body_entity, mesh_entity):
        editor.ToolsApplicationRequestBus(bus.Broadcast, 'DeleteEntityById', e)


status = "PASS"
try:
    main()
except Exception:
    log("FATAL: " + traceback.format_exc())
    status = "FAIL"

log("")
log("RESULT: " + status)
_handle.close()

import azlmbr.legacy.general as _general
if status == "PASS":
    _general.exit_no_prompt()
else:
    os._exit(1)
