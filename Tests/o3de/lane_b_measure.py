"""S0.2 (Lane B) measurement: load the SceneAPI product of SM_LetterF.fbx into an
entity and measure its local bounds in the O3DE editor, then compare against the
UE-side reference (Exports/LaneB/SM_LetterF.ue_reference.json).

This is the authoritative end-to-end answer for Lane B: what unit scale and axis
mapping SceneAPI actually applied under default settings.

Run (headless):
  Editor.exe --project-path=C:/Users/jorge/O3DE/Projects/UEtoO3DETest-Jolt -BatchMode -autotest_mode \
      --runpython Tests/o3de/lane_b_measure.py --runpythonargs <result-file>

Writes <result-file> (RESULT: PASS/FAIL + measurements) and
Tests/o3de/results/lane_b_measure.json (machine-readable numbers for LANE_B.md).
Exit-code contract identical to s0_1_prefab_spike.py (exit_no_prompt on PASS,
os._exit(1) on FAIL).
"""

import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'lane_b_result.txt')

JSON_OUT = os.path.join(SCRIPT_DIR, 'results', 'lane_b_measure.json')
UE_REF_PATH = r'D:/Gamedev/UEtoO3DE/Exports/LaneB/SM_LetterF.ue_reference.json'
MODEL_PRODUCT_PATH = 'assets/uetoo3de/sm_letterf.fbx.azmodel'

lines = []
ok = True


def log(msg):
    lines.append(str(msg))
    print(msg)


class SpikeFailure(Exception):
    pass


def fail(msg):
    raise SpikeFailure(msg)


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

    entity_type_instance = EntityType()
    game_entity_type = entity_type_instance.Game() if callable(entity_type_instance.Game) \
        else entity_type_instance.Game
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', ['Mesh'], game_entity_type)
    if not type_ids or len(type_ids) != 1 or type_ids[0].IsNull():
        fail('Mesh component type id lookup failed')
    mesh_type = type_ids[0]

    probe_id = editor.ToolsApplicationRequestBus(bus.Broadcast, 'CreateNewEntity', entity.EntityId())
    if not probe_id or not probe_id.IsValid():
        fail('entity creation failed')
    editor.EditorEntityAPIBus(bus.Event, 'SetName', probe_id, 'LaneB_Probe')

    add_outcome = editor.EditorComponentAPIBus(bus.Broadcast, 'AddComponentsOfType', probe_id, [mesh_type])
    if not add_outcome or not add_outcome.IsSuccess():
        fail('AddComponentsOfType(Mesh) failed')

    asset_id = asset.AssetCatalogRequestBus(
        bus.Broadcast, 'GetAssetIdByPath', MODEL_PRODUCT_PATH, math.Uuid(), False)
    asset_path_back = asset.AssetCatalogRequestBus(bus.Broadcast, 'GetAssetPathById', asset_id)
    log(f'model asset resolves to: {asset_path_back!r}')
    if not asset_path_back:
        fail(f'model product "{MODEL_PRODUCT_PATH}" not in asset catalog (run AP first)')

    mesh_pair = editor.EditorComponentAPIBus(bus.Broadcast, 'GetComponentOfType', probe_id, mesh_type).GetValue()
    set_outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'SetComponentProperty', mesh_pair, 'Controller|Configuration|Model Asset', asset_id)
    if not set_outcome or not set_outcome.IsSuccess():
        fail('SetComponentProperty(Model Asset) failed')

    # --- bounds, with a load-retry loop (the model streams in asynchronously) ---
    # Discover where BoundsRequestBus actually lives in this build.
    import azlmbr
    for mod_name in sorted(dir(azlmbr)):
        try:
            mod = getattr(azlmbr, mod_name)
            for attr in dir(mod):
                if 'Bounds' in attr:
                    log(f'discovered: azlmbr.{mod_name}.{attr} = {getattr(mod, attr)!r}')
        except Exception:
            pass

    bounds_bus = None
    for module_name in ('azlmbr.entity', 'azlmbr.components', 'azlmbr.framework'):
        try:
            module = __import__(module_name, fromlist=['BoundsRequestBus'])
            candidate_bus = getattr(module, 'BoundsRequestBus', None)
            log(f'{module_name}.BoundsRequestBus = {candidate_bus!r}')
            if candidate_bus is not None and bounds_bus is None:
                bounds_bus = candidate_bus
                log(f'using {module_name}.BoundsRequestBus')
        except (ImportError, AttributeError) as e:
            log(f'{module_name}: {e!r}')
            continue
    if bounds_bus is None:
        fail('no BoundsRequestBus binding found')

    aabb = None
    for attempt in range(120):  # up to ~120 frames for the model to stream in
        general.idle_wait_frames(1)
        try:
            candidate = bounds_bus(bus.Event, 'GetEntityLocalBoundsUnion', probe_id)
        except Exception as e:
            if attempt == 0:
                log(f'GetEntityLocalBoundsUnion raised: {e!r}')
            candidate = None
        if candidate is not None:
            aabb = candidate
            break
    if aabb is None:
        fail('bounds never became available (model failed to load?)')

    log(f'aabb type: {type(aabb)} attrs: {[a for a in dir(aabb) if not a.startswith("_")]}')
    try:
        bmin, bmax = aabb.min, aabb.max
    except AttributeError:
        try:
            bmin, bmax = aabb.GetMin(), aabb.GetMax()
        except AttributeError:
            fail('cannot read min/max from the returned Aabb')

    lo = (bmin.x, bmin.y, bmin.z)
    hi = (bmax.x, bmax.y, bmax.z)
    dims = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    log(f'product local bounds: min=({lo[0]:.4f}, {lo[1]:.4f}, {lo[2]:.4f}) '
        f'max=({hi[0]:.4f}, {hi[1]:.4f}, {hi[2]:.4f})')
    log(f'product dimensions: ({dims[0]:.4f}, {dims[1]:.4f}, {dims[2]:.4f})')

    ue_ref = json.load(open(UE_REF_PATH))
    ue_min, ue_max = ue_ref['bounds_min'], ue_ref['bounds_max']  # cm, UE asset space
    log(f'UE reference (cm): min={ue_min} max={ue_max}')

    def close(a, b, tol):
        return all(abs(x - y) <= tol for x, y in zip(a, b))

    hypothesis_no_scale = close(lo, ue_min, 0.5) and close(hi, ue_max, 0.5)
    hypothesis_meters = close(lo, [v / 100.0 for v in ue_min], 0.005) and \
                        close(hi, [v / 100.0 for v in ue_max], 0.005)

    if hypothesis_no_scale:
        verdict = 'NO_UNIT_CONVERSION: SceneAPI kept FBX centimeter values 1:1 (mesh is 100x too large); axis order preserved'
    elif hypothesis_meters:
        verdict = 'CM_TO_M_CONVERTED: SceneAPI scaled cm -> m; axis order preserved'
    else:
        verdict = ('UNEXPECTED: bounds match neither hypothesis - axis swap or sign flip present; '
                   'see lane_b_measure.json')
    log('verdict: ' + verdict)

    out = {
        'o3de_bounds_min': list(lo), 'o3de_bounds_max': list(hi), 'o3de_dims': list(dims),
        'ue_ref_min_cm': ue_min, 'ue_ref_max_cm': ue_max,
        'hypothesis_no_unit_conversion': hypothesis_no_scale,
        'hypothesis_cm_to_m': hypothesis_meters,
        'verdict': verdict,
    }
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, 'w') as f:
        json.dump(out, f, indent=2)

    if not (hypothesis_no_scale or hypothesis_meters):
        fail('bounds matched neither hypothesis')


try:
    main()
except SpikeFailure as e:
    ok = False
    log('FAIL: ' + str(e))
except Exception:
    ok = False
    log('EXCEPTION: ' + traceback.format_exc())

log('RESULT: ' + ('PASS' if ok else 'FAIL'))

os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as f:
    f.write('\n'.join(lines))

import azlmbr.legacy.general as _general_for_exit

if ok:
    _general_for_exit.exit_no_prompt()
else:
    os._exit(1)
