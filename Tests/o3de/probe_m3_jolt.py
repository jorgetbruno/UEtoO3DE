"""
probe_m3_jolt.py — M3 reconnaissance: the Jolt editor component surface.

Everything the adapter will call, verified before the adapter is written
(plan constraint 5: resolve-or-fail, never assume):

  1. resolve every component name the plan lists -> type IDs
  2. property paths of each (half extents, radius, height, kinematic flag,
     mass, damping, gravity, CCD, trigger flag, offsets, layer)
  3. Settings Registry: can Python read /O3DE/Physics/DefaultBackend?
  4. PhysX names must NOT resolve here (Jolt-only project) — that is the
     both-resolve ambiguity test's negative half
  5. a minimal end-to-end: create floor + falling box + kinematic + trigger
     entities with Jolt components, enter game mode, sample transforms —
     proving the enter_game_mode/find_game_entity pattern works on entities
     created in the SAME session (the gem's smoke test uses a saved level)

Run:  run_o3de_python.bat probe_m3_jolt.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m3_jolt_result.txt')

JOLT_NAMES = [
    'Jolt Rigid Body', 'Jolt Static Rigid Body',
    'Jolt Box Collider', 'Jolt Sphere Collider', 'Jolt Capsule Collider',
    'Jolt Cylinder Collider', 'Jolt Mesh Collider',
    'Jolt Static Compound Collider', 'Jolt Character Controller',
]
PHYSX_NAMES = ['PhysX Dynamic Rigid Body', 'PhysX Static Rigid Body',
               'PhysX Primitive Collider', 'PhysX Shape Collider', 'PhysX Mesh Collider']

lines = []
ok = True


def log(msg):
    lines.append(str(msg))
    print(msg)


def section(t):
    log('')
    log('=' * 68)
    log(t)
    log('=' * 68)


def main():
    global ok
    import azlmbr.bus as bus
    import azlmbr.components as components
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

    section('1. JOLT COMPONENT NAME -> TYPE ID RESOLUTION')
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', JOLT_NAMES, game_type)
    resolved = {}
    for name, type_id in zip(JOLT_NAMES, type_ids or []):
        good = type_id is not None and not type_id.IsNull()
        log('  %-32s %s' % (name, type_id.ToString() if good else 'MISS'))
        if good:
            resolved[name] = type_id
        else:
            ok = False

    section('2. PHYSX NAMES MUST NOT RESOLVE (negative half of detection)')
    physx_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', PHYSX_NAMES, game_type)
    for name, type_id in zip(PHYSX_NAMES, physx_ids or []):
        null = type_id is None or type_id.IsNull()
        log('  %-32s %s' % (name, 'not present (good)' if null else 'RESOLVES: ' + type_id.ToString()))

    section('3. PROPERTY PATHS PER COMPONENT')
    probe = editor.ToolsApplicationRequestBus(bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
    editor.EditorEntityAPIBus(bus.Event, 'SetName', probe, 'M3_PropProbe')
    for name in ('Jolt Rigid Body', 'Jolt Box Collider'):
        type_id = resolved.get(name)
        if type_id is None:
            continue
        outcome = editor.EditorComponentAPIBus(bus.Broadcast, 'AddComponentsOfType', probe, [type_id])
        if not outcome or not outcome.IsSuccess():
            log('  %s: could not add (%r)' % (name, outcome.GetError() if outcome else None))
            ok = False
            continue
        pair = editor.EditorComponentAPIBus(bus.Broadcast, 'GetComponentOfType', probe, type_id).GetValue()
        paths = editor.EditorComponentAPIBus(bus.Broadcast, 'BuildComponentPropertyList', pair)
        log('  -- %s --' % name)
        for path in sorted(paths or []):
            log('     %s' % path)
        # remove so the next add starts clean
        editor.EditorComponentAPIBus(bus.Broadcast, 'RemoveComponents', [pair])

    # sphere/capsule/cylinder differ only in dimension fields; dump one
    for name in ('Jolt Sphere Collider', 'Jolt Capsule Collider', 'Jolt Cylinder Collider',
                 'Jolt Mesh Collider'):
        type_id = resolved.get(name)
        if type_id is None:
            continue
        outcome = editor.EditorComponentAPIBus(bus.Broadcast, 'AddComponentsOfType', probe, [type_id])
        if not outcome or not outcome.IsSuccess():
            log('  %s: could not add' % name)
            continue
        pair = editor.EditorComponentAPIBus(bus.Broadcast, 'GetComponentOfType', probe, type_id).GetValue()
        paths = editor.EditorComponentAPIBus(bus.Broadcast, 'BuildComponentPropertyList', pair)
        dims = [p for p in (paths or [])
                if any(k in p for k in ('adius', 'eight', 'xtent', 'ffset', 'rigger',
                                        'sset', 'ayer', 'aterial'))]
        log('  -- %s (dimension/flag paths only) --' % name)
        for path in sorted(dims):
            log('     %s' % path)
        editor.EditorComponentAPIBus(bus.Broadcast, 'RemoveComponents', [pair])

    section('4. SETTINGS REGISTRY FROM PYTHON')
    import azlmbr
    hits = []
    for module_name in sorted(dir(azlmbr)):
        try:
            module = getattr(azlmbr, module_name)
        except Exception:
            continue
        for attr in dir(module):
            if 'SettingsRegistry' in attr or 'settings_registry' in attr:
                hits.append('azlmbr.%s.%s' % (module_name, attr))
    log('  bindings: %r' % (hits or 'none'))
    try:
        value = general.get_cvar('physics_defaultBackend')
        log('  get_cvar(physics_defaultBackend) = %r' % (value,))
    except Exception as exc:
        log('  get_cvar raised %r' % (exc,))

    section('5. MINIMAL SIMULATION ON SESSION-CREATED ENTITIES')
    def make(name, position):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, name)
        components.TransformBus(bus.Event, 'SetWorldTranslation', entity_id,
                                azmath.Vector3(*position))
        return entity_id

    def add(entity_id, component_name):
        type_id = resolved[component_name]
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
        if not outcome or not outcome.IsSuccess():
            raise RuntimeError('add %s failed' % component_name)
        return editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()

    floor = make('M3_Floor', (0.0, 0.0, 0.0))
    add(floor, 'Jolt Static Rigid Body')
    floor_col = add(floor, 'Jolt Box Collider')
    # widen the floor: find the dimensions property from the earlier dump
    paths = editor.EditorComponentAPIBus(bus.Broadcast, 'BuildComponentPropertyList', floor_col)
    dim_path = next((p for p in paths if 'imension' in p or 'xtent' in p), None)
    log('  floor box dimensions property: %r' % dim_path)
    if dim_path:
        editor.EditorComponentAPIBus(bus.Broadcast, 'SetComponentProperty', floor_col,
                                     dim_path, azmath.Vector3(20.0, 20.0, 1.0))

    box = make('M3_FallingBox', (0.0, 0.0, 3.0))
    add(box, 'Jolt Rigid Body')
    add(box, 'Jolt Box Collider')

    general.idle_wait_frames(10)
    general.enter_game_mode()
    general.idle_wait_frames(30)
    log('  in game mode: %r' % general.is_in_game_mode())

    game_box = general.find_game_entity('M3_FallingBox')
    game_floor = general.find_game_entity('M3_Floor')
    log('  runtime entities: box=%s floor=%s' % (game_box is not None, game_floor is not None))
    if not game_box:
        ok = False
    else:
        samples = []
        for _ in range(8):
            general.idle_wait_frames(30)
            z = components.TransformBus(bus.Event, 'GetWorldTranslation', game_box).z
            samples.append(round(z, 4))
        log('  box z over time: %r' % samples)
        rest = samples[-1]
        # floor top at z=0.5, box half-extent 0.5, minus ~2cm contact offset
        log('  final z: %.4f (expect ~0.98 = 0.5 + 0.5 - 0.02)' % rest)
        if not (0.8 < rest < 1.05):
            log('  WARNING: resting z outside expectation')

    general.exit_game_mode()
    general.idle_wait_frames(10)


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
