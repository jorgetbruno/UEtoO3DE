"""
probe_m2_nonuniform.py — M2 reconnaissance: the Non-uniform Scale component.

Established so far: `AZ::Transform` holds one uniform scale float
(`SetLocalScale` is a no-op stub -- it reports (1,1,1) back), and
"Non-uniform Scale" does not appear in any Add Component list. But
`azlmbr.editor.AddNonUniformScaleComponent` and
`azlmbr.entity.NonUniformScaleRequestBus` both exist.

Fixture_01 has an actor scaled (2, 1, 0.5) and M2 asserts world transforms to
1 cm, so this has to work or M2 must bake per-instance scale into geometry.
Two questions, and the second is the one that actually matters:

  1. what is `AddNonUniformScaleComponent`'s signature, and does
     `NonUniformScaleRequestBus` read the value back?
  2. **does it survive a prefab save and reload?** A scale that is set
     correctly in memory and lost on serialization would pass every in-session
     assertion and ship a wrong prefab.

Run:  run_o3de_python.bat probe_m2_nonuniform.py
"""

import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m2_nonuniform_result.txt')

ENTITY_NAME = 'M2_NonUniformProbe'
# AzToolsFramework::Components::EditorNonUniformScaleComponent, read from
# EditorNonUniformScaleComponent.h in the 26.05 SDK.
NON_UNIFORM_SCALE_TYPE_ID = '{2933FB4F-B3DA-4CD1-8106-F37300730777}'
PREFAB_REL_PATH = 'Tests/M2_NonUniform_Probe.prefab'
TARGET_SCALE = (2.0, 1.0, 0.5)

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
    global ok
    import azlmbr.legacy.general as general
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity
    import azlmbr.math as math
    import azlmbr.prefab as prefab

    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(30)

    log('=== 1. AddNonUniformScaleComponent signature ===')
    probe = editor.ToolsApplicationRequestBus(bus.Broadcast, 'CreateNewEntity', entity.EntityId())
    editor.EditorEntityAPIBus(bus.Event, 'SetName', probe, ENTITY_NAME)
    components.TransformBus(bus.Event, 'SetLocalTranslation', probe, math.Vector3(1.0, 2.0, 3.0))

    # editor.AddNonUniformScaleComponent(entity_id) is a no-op here: it returns
    # None and leaves the request bus without a handler (measured in
    # probe_m2_nonuniform2.py). Add the component by type id instead. The id is
    # read from the SDK's own header rather than guessed --
    # Code/Framework/AzToolsFramework/AzToolsFramework/ToolsComponents/
    # EditorNonUniformScaleComponent.h: AZ_EDITOR_COMPONENT(..., "{2933FB4F-...}").
    # It does not appear in any Add Component list because it is added through
    # the Transform component's UI, not the menu.
    type_id = math.Uuid_CreateString(NON_UNIFORM_SCALE_TYPE_ID, 0) \
        if hasattr(math, 'Uuid_CreateString') else math.Uuid().CreateString(NON_UNIFORM_SCALE_TYPE_ID)
    log('  resolved type id: %s' % type_id.ToString())
    add = editor.EditorComponentAPIBus(bus.Broadcast, 'AddComponentsOfType', probe, [type_id])
    log('  AddComponentsOfType -> success=%s' % (add.IsSuccess() if add else None))
    if not add or not add.IsSuccess():
        log('  FAIL: %r' % (add.GetError() if add else None))
        ok = False
        return
    general.idle_wait_frames(5)

    log('')
    log('=== 2. set and read back through NonUniformScaleRequestBus ===')
    target = math.Vector3(*TARGET_SCALE)
    try:
        entity.NonUniformScaleRequestBus(bus.Event, 'SetScale', probe, target)
        log('  SetScale accepted')
    except Exception as exc:
        log('  SetScale raised: %r' % (exc,))
        ok = False
    general.idle_wait_frames(5)
    try:
        read_back = entity.NonUniformScaleRequestBus(bus.Event, 'GetScale', probe)
        log('  GetScale -> ' + v3(read_back))
        if abs(read_back.x - TARGET_SCALE[0]) > 1e-4 or \
           abs(read_back.y - TARGET_SCALE[1]) > 1e-4 or \
           abs(read_back.z - TARGET_SCALE[2]) > 1e-4:
            log('  FAIL: scale did not round-trip in memory')
            ok = False
    except Exception as exc:
        log('  GetScale raised: %r' % (exc,))
        ok = False

    log('')
    log('=== 3. does it survive a prefab save + reload? ===')
    project_root = general.get_game_folder().rstrip('/\\')
    prefab_abs = os.path.join(project_root, *PREFAB_REL_PATH.split('/')).replace(os.sep, '/')
    os.makedirs(os.path.dirname(prefab_abs), exist_ok=True)

    create = prefab.PrefabPublicRequestBus(bus.Broadcast, 'CreatePrefabInMemory', [probe], prefab_abs)
    if not create or not create.IsSuccess():
        log('  FAIL: CreatePrefabInMemory: %r' % (create.GetError() if create else None))
        ok = False
        return
    general.idle_wait_frames(30)

    # Same template scan S0.1 established: no reflected event maps path -> id.
    template_json = None
    for template_id in range(1, 1025):
        outcome = prefab.PrefabLoaderScriptingBus(bus.Broadcast, 'SaveTemplateToString', template_id)
        if not outcome or not outcome.IsSuccess():
            continue
        text = outcome.GetValue()
        if '"%s"' % ENTITY_NAME in text:
            template_json = text
            break
    if template_json is None:
        log('  FAIL: no template containing the probe entity')
        ok = False
        return

    with open(prefab_abs, 'w') as handle:
        handle.write(template_json)
    log('  wrote %s (%d bytes)' % (prefab_abs, len(template_json)))

    has_nus = 'NonUniformScale' in template_json
    log('  serialized JSON mentions NonUniformScale: %s' % has_nus)
    if not has_nus:
        log('  FAIL: the component did not serialize into the prefab')
        ok = False

    instantiate = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'InstantiatePrefab', prefab_abs, entity.EntityId(),
        math.Vector3(10.0, 0.0, 0.0))
    if not instantiate or not instantiate.IsSuccess():
        log('  FAIL: InstantiatePrefab: %r' % (instantiate.GetError() if instantiate else None))
        ok = False
        return
    general.idle_wait_frames(60)

    container = instantiate.GetValue()
    children = editor.EditorEntityInfoRequestBus(bus.Event, 'GetChildren', container) or []
    log('  instantiated container children: %d' % len(children))
    for child in children:
        name = editor.EditorEntityInfoRequestBus(bus.Event, 'GetName', child)
        if name != ENTITY_NAME:
            continue
        reloaded = entity.NonUniformScaleRequestBus(bus.Event, 'GetScale', child)
        log('  reloaded %s GetScale -> %s' % (name, v3(reloaded)))
        if abs(reloaded.x - TARGET_SCALE[0]) > 1e-4 or \
           abs(reloaded.y - TARGET_SCALE[1]) > 1e-4 or \
           abs(reloaded.z - TARGET_SCALE[2]) > 1e-4:
            log('  FAIL: non-uniform scale was lost or changed by serialization')
            ok = False
        else:
            log('  ok: non-uniform scale survives the prefab round trip')
        break
    else:
        log('  FAIL: reloaded prefab has no entity named ' + ENTITY_NAME)
        ok = False


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
