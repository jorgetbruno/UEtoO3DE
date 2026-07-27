"""
probe_m5_lights.py — M5: Atom's light components and their property surface.

Everything M5 needs to author, measured rather than guessed (the same
discipline the Mesh/Material/Jolt components got):

  1. which component NAMES resolve for lights, out of the plausible candidates
     ("Light", "Directional Light", "Area Light", ...);
  2. the full property path list of each, with current values and types;
  3. the intensity-MODE enum: which integer means candela / lumen / lux / EV100
     / nit, by writing each value and reading back the resulting intensity;
  4. the light-TYPE enum on the general Light component (point vs spot vs
     capsule ...), same way;
  5. whether cone angles, attenuation radius (and its "automatic" toggle),
     shadows and colour round-trip through SetComponentProperty.

Nothing here authors anything that survives: it runs on scratch entities in
DefaultLevel and never saves.

Run:  run_o3de_python.bat probe_m5_lights.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m5_lights_result.txt')

CANDIDATE_NAMES = [
    'Light',
    'Directional Light',
    'Area Light',
    'Point Light',
    'Spot Light',
    'Global Skylight (IBL)',
]

lines = []
ok = True


def log(msg):
    lines.append(str(msg))
    print(msg)


def main():
    global ok
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

    log('=== 1. which light component names resolve? ===')
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', CANDIDATE_NAMES, game_type)
    resolved = {}
    for name, type_id in zip(CANDIDATE_NAMES, type_ids or []):
        hit = type_id is not None and not type_id.IsNull()
        log('  %-24s %s' % (name, type_id.ToString() if hit else 'MISS'))
        if hit:
            resolved[name] = type_id

    available = editor.EditorComponentAPIBus(
        bus.Broadcast, 'BuildComponentTypeNameListByEntityType', game_type)
    lightish = sorted(n for n in (available or []) if 'light' in n.lower())
    log('  every component with "light" in its name: %r' % lightish)

    def new_entity(label):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, label)
        return entity_id

    def add(entity_id, type_id, label):
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
        if not outcome or not outcome.IsSuccess():
            log('  ADD FAILED for ' + label)
            return None
        return editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()

    def get(pair, path):
        try:
            outcome = editor.EditorComponentAPIBus(
                bus.Broadcast, 'GetComponentProperty', pair, path)
            if outcome and outcome.IsSuccess():
                return True, outcome.GetValue()
        except Exception as exc:
            return False, 'RAISED %r' % (exc,)
        return False, None

    def put(pair, path, value):
        try:
            outcome = editor.EditorComponentAPIBus(
                bus.Broadcast, 'SetComponentProperty', pair, path, value)
            return bool(outcome and outcome.IsSuccess())
        except Exception as exc:
            log('    SET %r raised %r' % (path, exc))
            return False

    pairs = {}
    log('')
    log('=== 2. property surface of each resolved light component ===')
    for name, type_id in resolved.items():
        entity_id = new_entity('Probe_' + name.replace(' ', '_'))
        pair = add(entity_id, type_id, name)
        if pair is None:
            continue
        pairs[name] = pair
        general.idle_wait_frames(10)
        paths = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentPropertyList', pair)
        log('  --- %s: %d properties ---' % (name, len(paths or [])))
        for path in sorted(paths or []):
            found, value = get(pair, path)
            log('    %-58s %s %r' % (path, 'ok ' if found else 'ERR', value))

    # --- 3. intensity mode enum ------------------------------------------
    log('')
    log('=== 3. intensity MODE enum (write each int, read back) ===')
    for name in ('Light', 'Directional Light'):
        pair = pairs.get(name)
        if pair is None:
            continue
        paths = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentPropertyList', pair) or []
        mode_paths = [p for p in paths if 'mode' in p.lower() or 'unit' in p.lower()]
        log('  %s candidate mode paths: %r' % (name, mode_paths))
        for mode_path in mode_paths:
            for value in range(0, 8):
                if not put(pair, mode_path, value):
                    log('    %s = %d  REJECTED' % (mode_path, value))
                    continue
                general.idle_wait_frames(2)
                _found, read_back = get(pair, mode_path)
                intensity_paths = [p for p in paths if p.lower().endswith('intensity')]
                readings = {}
                for intensity_path in intensity_paths:
                    _f, intensity_value = get(pair, intensity_path)
                    readings[intensity_path] = intensity_value
                log('    %s = %d -> reads %r, intensity %r'
                    % (mode_path, value, read_back, readings))

    # --- 4. light TYPE enum on the general Light component ---------------
    log('')
    log('=== 4. light TYPE enum on "Light" ===')
    pair = pairs.get('Light')
    if pair is not None:
        paths = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentPropertyList', pair) or []
        type_paths = [p for p in paths if p.lower().endswith('light type')]
        log('  candidate type paths: %r' % type_paths)
        for type_path in type_paths:
            for value in range(0, 10):
                if not put(pair, type_path, value):
                    log('    %s = %d  REJECTED' % (type_path, value))
                    continue
                general.idle_wait_frames(2)
                _found, read_back = get(pair, type_path)
                # Which properties EXIST at this type: the visible set changes.
                now = editor.EditorComponentAPIBus(
                    bus.Broadcast, 'BuildComponentPropertyList', pair) or []
                new_paths = sorted(set(now) - set(paths))
                log('    %s = %d -> reads %r, extra props %r'
                    % (type_path, value, read_back, new_paths[:12]))

    # --- 5. round-trip the properties M5 actually writes -------------------
    log('')
    log('=== 5. round-trip writes ===')
    pair = pairs.get('Light')
    if pair is not None:
        colour = azmath.Color(1.0, 0.6, 0.3, 1.0)
        for path, value in (('Controller|Configuration|Color', colour),
                            ('Controller|Configuration|Intensity', 12.5),
                            ('Controller|Configuration|Enable Shadow', True),
                            ('Controller|Configuration|Inner Cone Angle', 15.0),
                            ('Controller|Configuration|Outer Cone Angle', 30.0),
                            ('Controller|Configuration|Attenuation Radius', 6.0),
                            ('Controller|Configuration|Attenuation Radius Mode', 0),
                            ('Controller|Configuration|Attenuation Radius Mode', 1)):
            wrote = put(pair, path, value)
            general.idle_wait_frames(2)
            found, read_back = get(pair, path)
            log('  %-56s write=%s read=%s %r' % (path, wrote, found, read_back))


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
