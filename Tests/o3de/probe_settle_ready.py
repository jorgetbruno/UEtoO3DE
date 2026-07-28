"""
probe_settle_ready.py — is a mesh collider's BAKE readable from Python?

The settle before serialization is the largest cost of a real import, and
measurement showed it is genuinely load-bearing: with it removed, 15 of 2501
mesh colliders reached the saved prefab with no `CookedData` at all, and the
import still reported PASS. So the wait cannot simply go; it has to become a
readiness check. This asks whether the readiness signal is reachable.

What it dumps:
  1. every property path on a Jolt Mesh Collider, so a cooked-data field can
     be found by name rather than guessed at
  2. whether that path READS back through GetComponentProperty (a path can
     exist in the reflection list and still refuse to be read)
  3. the same for the PhysX mesh collider name, which should not resolve in
     the Jolt project -- the control that proves 1 and 2 mean anything

Run:  run_o3de_python.bat probe_settle_ready.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_settle_ready_result.txt')

lines = []
failures = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def section(title):
    log('')
    log('=' * 68)
    log(title)
    log('=' * 68)


def main():
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    from azlmbr.entity import EntityType

    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(30)

    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game

    names = ['Jolt Mesh Collider', 'PhysX Mesh Collider']
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', names, game_type)
    resolved = {}
    for name, type_id in zip(names, type_ids or []):
        good = type_id is not None and not type_id.IsNull()
        log('  %-24s %s' % (name, type_id.ToString() if good else 'MISS'))
        if good:
            resolved[name] = type_id

    if 'Jolt Mesh Collider' not in resolved:
        fail('Jolt Mesh Collider did not resolve; this project is not the Jolt one')
        return
    if 'PhysX Mesh Collider' in resolved:
        fail('PhysX Mesh Collider resolved in the Jolt project -- the control '
             'that makes the positive result meaningful is broken')

    probe = editor.ToolsApplicationRequestBus(
        bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
    editor.EditorEntityAPIBus(bus.Event, 'SetName', probe, 'SettleReadyProbe')
    outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'AddComponentsOfType', probe, [resolved['Jolt Mesh Collider']])
    if not outcome or not outcome.IsSuccess():
        fail('could not add a Jolt Mesh Collider: %r'
             % (outcome.GetError() if outcome else None))
        return
    pair = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', probe,
        resolved['Jolt Mesh Collider']).GetValue()

    section('1. EVERY PROPERTY PATH ON A JOLT MESH COLLIDER')
    paths = editor.EditorComponentAPIBus(
        bus.Broadcast, 'BuildComponentPropertyList', pair) or []
    for path in sorted(paths):
        log('     %s' % path)
    log('  (%d paths)' % len(paths))

    section('2. ANYTHING THAT LOOKS LIKE THE BAKE RESULT')
    interesting = [p for p in paths
                   if any(word in p.lower() for word in
                          ('cook', 'bake', 'mesh asset', 'shape', 'ready',
                           'triangle', 'vertex', 'valid'))]
    if not interesting:
        log('  NONE -- no reflected property mentions cooking, baking or shape '
            'readiness')
    for path in sorted(interesting):
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', pair, path)
        if outcome is None:
            log('  %-52s no outcome' % path)
            continue
        if outcome.IsSuccess():
            value = outcome.GetValue()
            text = repr(value)
            log('  %-52s READS -> %s%s'
                % (path, type(value).__name__,
                   (' len=%d' % len(value)) if hasattr(value, '__len__') else
                   ' ' + text[:60]))
        else:
            log('  %-52s unreadable (%r)' % (path, outcome.GetError()))

    section('3. THE OTHER DIRECTION: WHAT THE COMPONENT REPORTS ABOUT ITSELF')
    # If no property exposes the bake, the remaining Python-visible signal is
    # whether the component considers itself valid/active. Try the buses that
    # a collider is likely to answer on, and say plainly if none exist.
    for module_name, bus_name, event in (
            ('azlmbr.physics', 'ColliderComponentRequestBus', 'GetShapeConfigurations'),
            ('azlmbr.physics', 'SimulatedBodyComponentRequestsBus', 'GetAabb'),
    ):
        try:
            module = __import__(module_name, fromlist=['*'])
            handler = getattr(module, bus_name, None)
            if handler is None:
                log('  %s.%s  not present' % (module_name, bus_name))
                continue
            value = handler(bus.Event, event, probe)
            log('  %s.%s(%s) -> %r' % (module_name, bus_name, event, value))
        except Exception as exc:
            log('  %s.%s(%s) raised %s: %s'
                % (module_name, bus_name, event, type(exc).__name__, exc))


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
