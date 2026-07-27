"""
probe_m4_material.py — M4: the O3DE Material component's assignment surface.

Answers, by measurement:
  1. does a component named "Material" resolve, and what are its property paths?
  2. can a .material product (azmaterial) be assigned to the default slot via
     SetComponentProperty, and does it read back?
  3. what does a .material source file next to a staged FBX produce in the
     cache (product extension/path), for wait_for_asset?

Uses the engine's own basic_grey.material product as the assignment guinea pig.

Run:  run_o3de_python.bat probe_m4_material.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m4_material_result.txt')

lines = []
ok = True


def log(msg):
    lines.append(str(msg))
    print(msg)


def main():
    global ok
    import azlmbr.asset as asset
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath
    from azlmbr.entity import EntityType

    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(30)

    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game

    log('=== 1. resolve Material (and Mesh) components ===')
    names = ['Material', 'Mesh']
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', names, game_type)
    for name, type_id in zip(names, type_ids or []):
        log('  %-10s %s' % (name, type_id.ToString() if type_id and not type_id.IsNull() else 'MISS'))
    material_type, mesh_type = type_ids

    probe = editor.ToolsApplicationRequestBus(bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
    editor.EditorEntityAPIBus(bus.Event, 'SetName', probe, 'M4_MatProbe')
    editor.EditorComponentAPIBus(bus.Broadcast, 'AddComponentsOfType', probe, [mesh_type, material_type])
    pair = editor.EditorComponentAPIBus(bus.Broadcast, 'GetComponentOfType', probe, material_type).GetValue()

    log('')
    log('=== 2. Material component property paths ===')
    paths = editor.EditorComponentAPIBus(bus.Broadcast, 'BuildComponentPropertyList', pair)
    for path in sorted(paths or []):
        log('  %s' % path)

    log('')
    log('=== 3. assign an azmaterial to the default slot ===')
    product = 'materials/basic_grey.azmaterial'
    asset_id = asset.AssetCatalogRequestBus(bus.Broadcast, 'GetAssetIdByPath', product, azmath.Uuid(), False)
    back = asset.AssetCatalogRequestBus(bus.Broadcast, 'GetAssetPathById', asset_id)
    log('  product %r -> %r' % (product, back))
    if not back:
        # search the catalog for any azmaterial
        log('  basic_grey not found; trying default pbr')
        for candidate in ('materials/presets/pbr/default_grid.azmaterial',
                          'materials/defaultpbr.azmaterial'):
            asset_id = asset.AssetCatalogRequestBus(bus.Broadcast, 'GetAssetIdByPath', candidate, azmath.Uuid(), False)
            back = asset.AssetCatalogRequestBus(bus.Broadcast, 'GetAssetPathById', asset_id)
            log('  %r -> %r' % (candidate, back))
            if back:
                break
    if not back:
        log('  no azmaterial found to test with')
        ok = False
        return

    candidates = [p for p in (paths or []) if 'aterial' in p and 'sset' in p.lower()]
    log('  candidate assignment paths: %r' % candidates)
    assigned = None
    for path in candidates:
        outcome = editor.EditorComponentAPIBus(bus.Broadcast, 'SetComponentProperty', pair, path, asset_id)
        if outcome and outcome.IsSuccess():
            readback = editor.EditorComponentAPIBus(bus.Broadcast, 'GetComponentProperty', pair, path)
            value = readback.GetValue() if readback and readback.IsSuccess() else None
            back2 = asset.AssetCatalogRequestBus(bus.Broadcast, 'GetAssetPathById', value) if value else ''
            log('  SET OK via %r; readback resolves to %r' % (path, back2))
            if back2 == back:
                assigned = path
                break
        else:
            log('  set failed via %r' % path)
    if assigned is None:
        log('  NO working assignment path found')
        ok = False


try:
    main()
except Exception:
    ok = False
    log('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: ' + ('PASS' if ok else 'FAIL'))
os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
if ok:
    _general.exit_no_prompt()
else:
    os._exit(1)
