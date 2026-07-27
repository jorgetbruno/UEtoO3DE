"""
probe_m5_lights2.py — M5: verify the exact write sequences the importer uses.

probe_m5_lights.py found the components, the property paths and the enum
integers. This one answers the questions that decide the AUTHORING ORDER, on
the precise sequences `light_build` will emit:

  1. Does writing "Intensity mode" CONVERT the stored intensity? (The first
     probe's directional readings said yes for that component and no for the
     local one -- if it converts, mode must be written BEFORE intensity, and
     the reverse order silently rescales every light in the level.)
  2. Do the local light's per-type properties round-trip: light type,
     shutters (spot cone), attenuation radius in Explicit mode, shadows?
  3. Does Color round-trip component-wise (linear values, not sRGB)?
  4. Does the directional light's shadow toggle live at a different path than
     the local one's (probe 1 says "Shadow|Enable Shadow" vs
     "Shadows|Enable shadow" -- one capital letter apart, so it is worth an
     explicit assertion rather than a squint).

Run:  run_o3de_python.bat probe_m5_lights2.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m5_lights2_result.txt')

LIGHT = 'Light'
DIRECTIONAL = 'Directional Light'

# From the SDK headers (Atom/Feature/CoreLights/PhotometricValue.h and
# AtomLyIntegration/.../AreaLightComponentConfig.h), confirmed by probe 1.
LUMEN, CANDELA, LUX, NIT, EV100_LUM, EV100_ILL = 0, 1, 2, 3, 4, 5
TYPE_SPHERE, TYPE_SPOT_DISK, TYPE_SIMPLE_POINT, TYPE_SIMPLE_SPOT = 1, 2, 6, 7
RADIUS_EXPLICIT, RADIUS_AUTOMATIC = 0, 1

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
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', [LIGHT, DIRECTIONAL], game_type)
    light_type_id, directional_type_id = type_ids

    def fresh(type_id, label):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, label)
        editor.EditorComponentAPIBus(bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
        general.idle_wait_frames(5)
        return editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()

    def put(pair, path, value):
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', pair, path, value)
        return bool(outcome and outcome.IsSuccess())

    def get(pair, path):
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', pair, path)
        if outcome and outcome.IsSuccess():
            return outcome.GetValue()
        return None

    def check(condition, message):
        global ok
        if not condition:
            ok = False
            log('  FAIL: ' + message)
        return condition

    # --- 1. does writing the mode convert the intensity? -------------------
    log('=== 1. mode-then-intensity vs intensity-then-mode ===')
    for name, type_id, mode_a, mode_b, value in (
            (LIGHT, light_type_id, LUMEN, CANDELA, 12.5),
            (DIRECTIONAL, directional_type_id, EV100_ILL, LUX, 5.0)):
        pair = fresh(type_id, 'Probe_order_' + name.replace(' ', '_'))
        # (a) mode first, then intensity -- the order light_build will use.
        put(pair, 'Controller|Configuration|Intensity mode', mode_b)
        put(pair, 'Controller|Configuration|Intensity', value)
        general.idle_wait_frames(3)
        after_correct = get(pair, 'Controller|Configuration|Intensity')
        mode_after = get(pair, 'Controller|Configuration|Intensity mode')

        # (b) intensity first, then mode -- the order that would rescale it.
        pair2 = fresh(type_id, 'Probe_order2_' + name.replace(' ', '_'))
        put(pair2, 'Controller|Configuration|Intensity mode', mode_a)
        put(pair2, 'Controller|Configuration|Intensity', value)
        put(pair2, 'Controller|Configuration|Intensity mode', mode_b)
        general.idle_wait_frames(3)
        after_wrong = get(pair2, 'Controller|Configuration|Intensity')

        log('  %-18s mode-then-intensity -> %r (mode %r);  intensity-then-mode -> %r'
            % (name, after_correct, mode_after, after_wrong))
        check(after_correct == value,
              '%s: writing mode then intensity did not store %r' % (name, value))
        if after_wrong != value:
            log('    NOTE: %s CONVERTS on mode change (%r -> %r); order is load-bearing'
                % (name, value, after_wrong))

    # --- 2. local light: per-type properties -------------------------------
    log('')
    log('=== 2. local light property round-trips ===')
    for label, light_type, shutters in (('SimplePoint', TYPE_SIMPLE_POINT, False),
                                        ('SimpleSpot', TYPE_SIMPLE_SPOT, True)):
        pair = fresh(light_type_id, 'Probe_' + label)
        put(pair, 'Controller|Configuration|Light type', light_type)
        put(pair, 'Controller|Configuration|Intensity mode', CANDELA)
        put(pair, 'Controller|Configuration|Intensity', 12.5)
        put(pair, 'Controller|Configuration|Color', azmath.Color(1.0, 0.6, 0.3, 1.0))
        put(pair, 'Controller|Configuration|Attenuation radius|Mode', RADIUS_EXPLICIT)
        put(pair, 'Controller|Configuration|Attenuation radius|Radius', 6.0)
        put(pair, 'Controller|Configuration|Shadows|Enable shadow', True)
        if shutters:
            put(pair, 'Controller|Configuration|Shutters|Enable shutters', True)
            put(pair, 'Controller|Configuration|Shutters|Inner angle', 15.0)
            put(pair, 'Controller|Configuration|Shutters|Outer angle', 30.0)
        general.idle_wait_frames(5)

        readings = {}
        for path in ('Controller|Configuration|Light type',
                     'Controller|Configuration|Intensity mode',
                     'Controller|Configuration|Intensity',
                     'Controller|Configuration|Attenuation radius|Mode',
                     'Controller|Configuration|Attenuation radius|Radius',
                     'Controller|Configuration|Shadows|Enable shadow',
                     'Controller|Configuration|Shutters|Enable shutters',
                     'Controller|Configuration|Shutters|Inner angle',
                     'Controller|Configuration|Shutters|Outer angle'):
            readings[path.rsplit('|', 1)[-1]] = get(pair, path)
        log('  %-12s %r' % (label, readings))
        check(readings['Light type'] == light_type, '%s: light type did not stick' % label)
        check(readings['Intensity'] == 12.5, '%s: intensity did not stick' % label)
        check(readings['Radius'] == 6.0, '%s: explicit radius did not stick' % label)
        check(readings['Mode'] == RADIUS_EXPLICIT, '%s: radius mode did not stick' % label)
        if shutters:
            check(readings['Inner angle'] == 15.0, '%s: inner angle did not stick' % label)
            check(readings['Outer angle'] == 30.0, '%s: outer angle did not stick' % label)

        colour = get(pair, 'Controller|Configuration|Color')
        parts = [getattr(colour, axis, None) for axis in ('r', 'g', 'b')]
        log('    color reads %r' % (parts,))
        if parts[0] is not None:
            check(abs(parts[0] - 1.0) < 1e-4 and abs(parts[1] - 0.6) < 1e-3,
                  '%s: colour did not round-trip (%r)' % (label, parts))

    # --- 3. directional light ---------------------------------------------
    log('')
    log('=== 3. directional light round-trips ===')
    pair = fresh(directional_type_id, 'Probe_Directional')
    put(pair, 'Controller|Configuration|Intensity mode', LUX)
    put(pair, 'Controller|Configuration|Intensity', 5.0)
    put(pair, 'Controller|Configuration|Color', azmath.Color(1.0, 0.95, 0.85, 1.0))
    wrote_shadow = put(pair, 'Controller|Configuration|Shadow|Enable Shadow', True)
    wrong_path = put(pair, 'Controller|Configuration|Shadows|Enable shadow', True)
    general.idle_wait_frames(5)
    log('  intensity=%r mode=%r shadow_write=%s wrong_path_write=%s'
        % (get(pair, 'Controller|Configuration|Intensity'),
           get(pair, 'Controller|Configuration|Intensity mode'),
           wrote_shadow, wrong_path))
    check(get(pair, 'Controller|Configuration|Intensity') == 5.0,
          'directional intensity did not stick')
    check(get(pair, 'Controller|Configuration|Intensity mode') == LUX,
          'directional mode did not stick')
    check(wrote_shadow, 'directional shadow path "Shadow|Enable Shadow" was rejected')
    check(not wrong_path,
          'the local light shadow path also worked on the directional light; '
          'the two paths are NOT distinct after all')


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
