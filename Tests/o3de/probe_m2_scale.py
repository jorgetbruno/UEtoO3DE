"""
probe_m2_scale.py — M2 reconnaissance: can an O3DE entity hold a non-uniform scale?

Round 1 was inconclusive. `TransformBus` accepted both `SetLocalUniformScale`
and `SetLocalScale(Vector3)` without raising, and `GetLocalScale` returned a
Vector3 -- but the values were never printed, and "the call did not raise" is
not the same as "the scale was stored". `AZ::Transform` holds a single uniform
scale float, so the likely answer is that `SetLocalScale` quietly collapses the
vector; this measures exactly what it does.

Fixture_01 contains an actor scaled (2, 1, 0.5) on purpose (plan M0), and M2's
acceptance test compares world transforms to 1 cm, so the answer decides
whether M2 can place instances directly or has to bake per-instance scale into
geometry.

Also sweeps every entity type for a scale-related component, since round 1 only
looked at Game (where `Transform` itself does not appear either, so absence
there proves nothing).

Run:  run_o3de_python.bat probe_m2_scale.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m2_scale_result.txt')

lines = []
ok = True


def log(msg):
    lines.append(str(msg))
    print(msg)


def v3(vector):
    try:
        return '(%.4f, %.4f, %.4f)' % (vector.x, vector.y, vector.z)
    except Exception as exc:
        return '<unreadable %r>' % (exc,)


def main():
    import azlmbr.legacy.general as general
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity
    import azlmbr.math as math
    from azlmbr.entity import EntityType

    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(30)

    log('=== 1. does SetLocalScale actually store a non-uniform scale? ===')
    probe = editor.ToolsApplicationRequestBus(bus.Broadcast, 'CreateNewEntity', entity.EntityId())
    editor.EditorEntityAPIBus(bus.Event, 'SetName', probe, 'M2_ScaleProbe')

    components.TransformBus(bus.Event, 'SetLocalScale', probe, math.Vector3(2.0, 1.0, 0.5))
    general.idle_wait_frames(5)
    log('  after SetLocalScale(2, 1, 0.5):')
    log('    GetLocalScale        = ' + v3(components.TransformBus(bus.Event, 'GetLocalScale', probe)))
    log('    GetLocalUniformScale = %r' % components.TransformBus(bus.Event, 'GetLocalUniformScale', probe))
    world = components.TransformBus(bus.Event, 'GetWorldTM', probe)
    try:
        log('    GetWorldTM scale     = %r' % world.GetUniformScale())
    except Exception as exc:
        log('    GetWorldTM scale     : %r' % exc)

    components.TransformBus(bus.Event, 'SetLocalUniformScale', probe, 3.0)
    general.idle_wait_frames(5)
    log('  after SetLocalUniformScale(3.0):')
    log('    GetLocalScale        = ' + v3(components.TransformBus(bus.Event, 'GetLocalScale', probe)))
    log('    GetLocalUniformScale = %r' % components.TransformBus(bus.Event, 'GetLocalUniformScale', probe))

    log('')
    log('=== 2. scale-related components across every entity type ===')
    instance = EntityType()
    for type_name in ('Game', 'System', 'Layer', 'Level'):
        try:
            attribute = getattr(instance, type_name)
            entity_type = attribute() if callable(attribute) else attribute
        except Exception as exc:
            log('  %s: unavailable (%r)' % (type_name, exc))
            continue
        names = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentTypeNameListByEntityType', entity_type)
        names = list(names or [])
        matches = sorted(n for n in names if 'cale' in n or 'ransform' in n)
        log('  %-7s %4d components; scale/transform related: %r'
            % (type_name, len(names), matches))

    log('')
    log('=== 3. azlmbr surface for non-uniform scale ===')
    import azlmbr
    for module_name in sorted(dir(azlmbr)):
        try:
            module = getattr(azlmbr, module_name)
            for attribute in dir(module):
                if 'NonUniform' in attribute or 'NonUniformScale' in attribute:
                    log('  azlmbr.%s.%s' % (module_name, attribute))
        except Exception:
            pass
    log('  azlmbr.components entries containing "Scale": %r'
        % [n for n in dir(components) if 'Scale' in n])

    log('')
    log('=== 4. what the Transform component itself exposes ===')
    pairs = editor.EditorComponentAPIBus(bus.Broadcast, 'GetComponents', probe)
    log('  GetComponents -> %r' % (pairs,))
    if pairs and pairs.IsSuccess():
        for pair in pairs.GetValue():
            paths = editor.EditorComponentAPIBus(
                bus.Broadcast, 'BuildComponentPropertyList', pair)
            scaleish = [p for p in (paths or []) if 'cale' in p]
            log('    component property paths containing "cale": %r' % scaleish)


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
