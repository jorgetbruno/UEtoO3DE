"""
probe_m2_nonuniform2.py — M2: why did AddNonUniformScaleComponent do nothing?

`editor.AddNonUniformScaleComponent(entity_id)` returned None, the request bus
read back (0,0,0) (the default-constructed value a bus with no handler
returns), and the serialized prefab contained no NonUniformScale at all -- so
the component was never added.

This dumps the binding's own documentation and tries the plausible variants,
because the alternative (baking per-instance scale into geometry in the UE
exporter) does not scale: a real level uses non-uniform scale on thousands of
props, and each distinct scale value would need its own FBX. Worth one focused
probe before accepting that cost.

Run:  run_o3de_python.bat probe_m2_nonuniform2.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m2_nonuniform2_result.txt')

lines = []
ok = True


def log(msg):
    lines.append(str(msg))
    print(msg)


def main():
    import azlmbr.legacy.general as general
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.entity as entity
    import azlmbr.math as math

    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(30)

    log('=== 1. binding documentation ===')
    fn = getattr(editor, 'AddNonUniformScaleComponent', None)
    log('  editor.AddNonUniformScaleComponent = %r' % (fn,))
    log('  __doc__: %r' % (getattr(fn, '__doc__', None),))
    bus_obj = getattr(entity, 'NonUniformScaleRequestBus', None)
    log('  entity.NonUniformScaleRequestBus = %r' % (bus_obj,))
    log('  __doc__: %r' % (getattr(bus_obj, '__doc__', None),))

    log('')
    log('=== 2. component introspection API ===')
    probe = editor.ToolsApplicationRequestBus(bus.Broadcast, 'CreateNewEntity', entity.EntityId())
    editor.EditorEntityAPIBus(bus.Event, 'SetName', probe, 'M2_NUS2')
    general.idle_wait_frames(5)

    for event in ('GetComponents', 'GetComponentsOfType', 'CountComponents'):
        try:
            outcome = editor.EditorComponentAPIBus(bus.Broadcast, event, probe)
            log('  %-22s -> %r' % (event, outcome))
            if outcome is not None and hasattr(outcome, 'IsSuccess'):
                log('     IsSuccess=%s value=%r' % (outcome.IsSuccess(),
                                                    outcome.GetValue() if outcome.IsSuccess() else None))
        except Exception as exc:
            log('  %-22s raised %r' % (event, exc))

    log('')
    log('=== 3. call AddNonUniformScaleComponent and re-inspect ===')
    try:
        result = editor.AddNonUniformScaleComponent(probe)
        log('  returned %r' % (result,))
    except Exception as exc:
        log('  raised %r' % (exc,))
    general.idle_wait_frames(10)

    try:
        outcome = editor.EditorComponentAPIBus(bus.Broadcast, 'GetComponents', probe)
        if outcome is not None and hasattr(outcome, 'IsSuccess') and outcome.IsSuccess():
            pairs = outcome.GetValue()
            log('  entity now has %d components' % len(pairs))
            for pair in pairs:
                try:
                    name = editor.EditorComponentAPIBus(
                        bus.Broadcast, 'GetComponentName', pair)
                    log('    - %r' % (name,))
                except Exception as exc:
                    log('    - <name unavailable: %r>' % (exc,))
        else:
            log('  GetComponents outcome: %r' % (outcome,))
    except Exception as exc:
        log('  GetComponents raised %r' % (exc,))

    log('')
    log('=== 4. set/get through the bus after the add ===')
    try:
        entity.NonUniformScaleRequestBus(bus.Event, 'SetScale', probe, math.Vector3(2.0, 1.0, 0.5))
        general.idle_wait_frames(5)
        got = entity.NonUniformScaleRequestBus(bus.Event, 'GetScale', probe)
        log('  GetScale -> (%.4f, %.4f, %.4f)' % (got.x, got.y, got.z))
    except Exception as exc:
        log('  raised %r' % (exc,))

    log('')
    log('=== 5. does the entity need to be selected / in an undo batch? ===')
    try:
        editor.ToolsApplicationRequestBus(bus.Broadcast, 'SetSelectedEntities', [probe])
        general.idle_wait_frames(5)
        editor.AddNonUniformScaleComponent(probe)
        general.idle_wait_frames(10)
        got = entity.NonUniformScaleRequestBus(bus.Event, 'GetScale', probe)
        log('  after selecting: GetScale -> (%.4f, %.4f, %.4f)' % (got.x, got.y, got.z))
    except Exception as exc:
        log('  raised %r' % (exc,))

    log('')
    log('=== 6. every azlmbr.editor free function mentioning component ===')
    for name in sorted(dir(editor)):
        if 'Component' in name and not name.endswith('Bus'):
            log('  editor.%s' % name)


try:
    main()
except Exception:
    ok = False
    log('EXCEPTION: ' + traceback.format_exc())

log('RESULT: ' + ('PASS' if ok else 'FAIL'))
os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
if ok:
    _general.exit_no_prompt()
else:
    os._exit(1)
