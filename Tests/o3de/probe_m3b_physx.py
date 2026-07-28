"""
probe_m3b_physx.py -- M3b reconnaissance: the PhysX editor component surface.

Runs against UEtoO3DETest-PhysX (PhysX5 enabled, JoltPhysics ABSENT). Same
discipline as probe_m3_jolt: resolve-or-fail, measure every property path
before a line of the adapter is written (constraint 5).

The structural question this answers: Jolt ships ONE COMPONENT PER SHAPE
(Jolt Box Collider, Jolt Sphere Collider, ...), while PhysX is believed to
ship a single collider with a shape SELECTOR. The adapter's shape methods
depend on which it is, so the catalogue is dumped rather than assumed.

  1. every physics-ish component name the catalogue offers;
  2. resolve the names detection.PROBE_NAMES uses for 'physx' -- these MUST
     resolve here, and the 'jolt' ones must NOT (the negative half of the
     ambiguity contract, mirrored from the Jolt project's probe);
  3. property paths of every collider/body component, dumped in full;
  4. shape selector: if a single collider component exists, what enum values
     does its shape property take, and where do dimensions/radius/height
     live for each;
  5. contact offset: read from a live collider default (tests derive their
     tolerances from it, never a hard-coded 0.02);
  6. Settings Registry /O3DE/Physics/DefaultBackend -- the Jolt gem ships it,
     PhysX is expected NOT to, which is what makes it only a weak hint;
  7. end-to-end: floor + falling box in game mode, so the resting height is
     measured on THIS backend rather than assumed equal to Jolt's.

Run: run_o3de_python.bat probe_m3b_physx.py <result> <PhysX project path>
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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m3b_physx_result.txt')

os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
_handle = open(RESULT_PATH, 'w')
_failures = []


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

    from ueimporter.adapters import detection

    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game

    log("=== 1. physics components in the catalogue ===")
    names = editor.EditorComponentAPIBus(
        bus.Broadcast, 'BuildComponentTypeNameListByEntityType', game_type) or []
    physics_like = sorted(n for n in names if any(
        k in n.lower() for k in ("physx", "jolt", "collider", "rigid body",
                                 "force", "joint", "character")))
    for n in physics_like:
        log("  " + n)

    log("")
    log("=== 2. detection contract: physx resolves, jolt does NOT ===")

    def resolves(name_list):
        ids = editor.EditorComponentAPIBus(
            bus.Broadcast, 'FindComponentTypeIdsByEntityType', name_list, game_type)
        return [bool(i) and not i.IsNull() for i in (ids or [])]

    for backend, probe_names in sorted(detection.PROBE_NAMES.items()):
        got = resolves(probe_names)
        log("  %-6s %r -> %r" % (backend, probe_names, got))
        if backend == "physx" and not all(got):
            _failures.append("PhysX probe names did not resolve in the PhysX project")
        if backend == "jolt" and any(got):
            _failures.append("Jolt names resolve in the PhysX project -- the "
                             "ambiguity contract's negative half is broken")

    log("")
    log("=== 3+4. property paths per physics component (+ shape selectors) ===")
    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    def dump(component_name):
        ids = editor.EditorComponentAPIBus(
            bus.Broadcast, 'FindComponentTypeIdsByEntityType', [component_name],
            game_type)
        if not ids or not ids[0] or ids[0].IsNull():
            log("  %s: DOES NOT RESOLVE" % component_name)
            return None, None
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id,
                                  '__probe_' + component_name.replace(" ", "_"))
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', entity_id, [ids[0]])
        if not outcome or not outcome.IsSuccess():
            log("  %s: ADD FAILED" % component_name)
            return entity_id, None
        pair = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, ids[0]).GetValue()
        paths = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentPropertyList', pair) or []
        log("")
        log("  --- %s (%d properties) ---" % (component_name, len(paths)))
        for p in sorted(paths):
            # Reading the VALUE is best-effort: some reflected types cannot be
            # marshalled to Python and raise out of GetValue() ("Failed to
            # create proxy object by type name"), which killed an earlier run
            # of this probe before it reached the collider components. The
            # PATH is the thing the adapter needs; the value is a nicety.
            shown = "<unreadable>"
            try:
                value = editor.EditorComponentAPIBus(
                    bus.Broadcast, 'GetComponentProperty', pair, p)
                if value and value.IsSuccess():
                    shown = str(value.GetValue())[:60]
            except Exception as exc:
                shown = "<unmarshallable: %s>" % type(exc).__name__
            log("      %-56s = %s" % (p, shown))
        return entity_id, pair

    candidates = [n for n in physics_like if n.lower().startswith("physx")]
    probes = []
    for component_name in candidates:
        entity_id, pair = dump(component_name)
        probes.append((component_name, entity_id, pair))

    log("")
    log("=== 5. contact offset from a live collider default ===")
    for component_name, _entity_id, pair in probes:
        if pair is None or "collider" not in component_name.lower():
            continue
        for path in ("Collider Configuration|Contact offset",
                     "Collider configuration|Contact offset",
                     "Configuration|Contact offset"):
            outcome = editor.EditorComponentAPIBus(
                bus.Broadcast, 'GetComponentProperty', pair, path)
            if outcome and outcome.IsSuccess():
                log("  %s -> %s = %r" % (component_name, path, outcome.GetValue()))
                break

    for _component_name, entity_id, _pair in probes:
        if entity_id is not None:
            editor.ToolsApplicationRequestBus(
                bus.Broadcast, 'DeleteEntityById', entity_id)

    log("")
    log("=== 6. settings registry hint ===")
    try:
        value = detection.editor_settings_reader()
        log("  /O3DE/Physics/DefaultBackend = %r (PhysX is expected to ship "
            "no value, which is why it is only a hint)" % (value,))
    except Exception as exc:
        log("  read failed: %s" % str(exc)[:120])

    log("")
    log("=== 7. end-to-end resting height on THIS backend ===")
    log("  (deferred to the adapter's own acceptance; component surface first)")


try:
    main()
    log("")
    log("RESULT: " + ("PASS" if not _failures else "FAIL"))
    for f in _failures:
        log("  FAILURE: " + f)
except Exception:
    log("FATAL: " + traceback.format_exc())
    log("")
    log("RESULT: FAIL")
    _failures.append("fatal")

_handle.close()

import azlmbr.legacy.general as _general
if not _failures:
    _general.exit_no_prompt()
else:
    os._exit(1)
