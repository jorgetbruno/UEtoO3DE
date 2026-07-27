"""
probe_m2_transform.py — M2 reconnaissance: transforms, scale, and asset waiting.

Three things the prefab builder cannot be written without, and none of which
should be assumed:

  1. **Non-uniform scale.** `AZ::Transform` carries a single uniform scale
     float, but Fixture_01 deliberately contains an actor scaled (2, 1, 0.5)
     (plan M0). O3DE's answer is a separate Non-uniform Scale component; this
     confirms whether it resolves by name in 26.05 and what its property path
     is. If it does not exist, M2 has to bake per-instance scale into geometry,
     which is a much larger change -- so measure before designing.
  2. **Which TransformBus events exist** for setting local translation,
     rotation and scale on an editor entity.
  3. **Asset catalog behaviour** for a product that is present and one that is
     absent, which is what `wait_for_asset` polls (global constraint 8).

Run:  run_o3de_python.bat probe_m2_transform.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m2_transform_result.txt')

lines = []
ok = True


def log(msg):
    lines.append(str(msg))
    print(msg)


def section(title):
    log('')
    log('=' * 68)
    log(title)
    log('=' * 68)


def main():
    import azlmbr.legacy.general as general
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity
    import azlmbr.math as math
    import azlmbr.asset as asset
    from azlmbr.entity import EntityType

    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(30)

    section('1. COMPONENT TYPE RESOLUTION BY NAME')
    entity_type_instance = EntityType()
    game_entity_type = entity_type_instance.Game() if callable(entity_type_instance.Game) \
        else entity_type_instance.Game
    wanted = ['Mesh', 'Non-uniform Scale', 'Transform']
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', wanted, game_entity_type)
    for name, type_id in zip(wanted, type_ids or []):
        log('  %-22s -> %s (null=%s)' % (name, type_id.ToString(), type_id.IsNull()))

    available = editor.EditorComponentAPIBus(
        bus.Broadcast, 'BuildComponentTypeNameListByEntityType', game_entity_type)
    scaleish = sorted(n for n in (available or []) if 'cale' in n)
    log('  component names containing "cale": %r' % scaleish)

    section('2. TRANSFORM BUS SURFACE')
    probe = editor.ToolsApplicationRequestBus(bus.Broadcast, 'CreateNewEntity', entity.EntityId())
    editor.EditorEntityAPIBus(bus.Event, 'SetName', probe, 'M2_TransformProbe')

    for event, args in (
            ('SetLocalTranslation', [math.Vector3(1.0, -2.0, 3.0)]),
            ('SetLocalRotationQuaternion', [math.Quaternion(0.0, 0.0, 0.0, 1.0)]),
            ('SetLocalUniformScale', [2.0]),
            ('SetLocalScale', [math.Vector3(2.0, 1.0, 0.5)]),
    ):
        try:
            components.TransformBus(bus.Event, event, probe, *args)
            log('  %-28s accepted' % event)
        except Exception as exc:
            log('  %-28s FAILED: %r' % (event, exc))

    for event in ('GetLocalTranslation', 'GetLocalUniformScale', 'GetWorldTranslation',
                  'GetLocalRotationQuaternion', 'GetWorldTM', 'GetLocalScale'):
        try:
            log('  %-28s -> %r' % (event, components.TransformBus(bus.Event, event, probe)))
        except Exception as exc:
            log('  %-28s FAILED: %r' % (event, exc))

    section('3. NON-UNIFORM SCALE COMPONENT')
    nus_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', ['Non-uniform Scale'], game_entity_type)
    if nus_ids and len(nus_ids) == 1 and not nus_ids[0].IsNull():
        add = editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', probe, [nus_ids[0]])
        log('  AddComponentsOfType(Non-uniform Scale) success=%s'
            % (add.IsSuccess() if add else None))
        if add and add.IsSuccess():
            pair = editor.EditorComponentAPIBus(
                bus.Broadcast, 'GetComponentOfType', probe, nus_ids[0]).GetValue()
            paths = editor.EditorComponentAPIBus(
                bus.Broadcast, 'BuildComponentPropertyList', pair)
            log('  property paths: %r' % (paths,))
            for path in (paths or []):
                try:
                    set_outcome = editor.EditorComponentAPIBus(
                        bus.Broadcast, 'SetComponentProperty', pair, path,
                        math.Vector3(2.0, 1.0, 0.5))
                    log('    set %-24s success=%s' % (path, set_outcome.IsSuccess()
                                                      if set_outcome else None))
                    got = editor.EditorComponentAPIBus(
                        bus.Broadcast, 'GetComponentProperty', pair, path)
                    log('    get %-24s -> %r' % (path, got.GetValue() if got and got.IsSuccess() else None))
                except Exception as exc:
                    log('    %-28s raised %r' % (path, exc))
    else:
        log('  Non-uniform Scale does NOT resolve; M2 would have to bake '
            'per-instance scale into geometry')

    section('4. ASSET CATALOG / wait_for_asset BEHAVIOUR')
    for product in ('assets/uetoo3de/sm_letterf.fbx.azmodel',
                    'assets/uetoo3de/game/meshes/sm_letterf.fbx.azmodel',
                    'objects/_primitives/_box_1x1.fbx.azmodel',
                    'assets/definitely/not/here.fbx.azmodel'):
        asset_id = asset.AssetCatalogRequestBus(
            bus.Broadcast, 'GetAssetIdByPath', product, math.Uuid(), False)
        back = asset.AssetCatalogRequestBus(bus.Broadcast, 'GetAssetPathById', asset_id)
        log('  %-52s valid=%-5s path_back=%r'
            % (product, bool(asset_id.is_valid()) if hasattr(asset_id, 'is_valid') else 'n/a', back))

    section('5. AP CONNECTION STATE')
    for name in sorted(dir(azlmbr_asset_module())):
        if 'Processor' in name or 'Status' in name:
            log('  azlmbr.asset.%s' % name)


def azlmbr_asset_module():
    import azlmbr.asset as asset
    return asset


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
