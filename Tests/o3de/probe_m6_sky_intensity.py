"""
probe_m6_sky_intensity.py — M6: what the Physical Sky actually stores.

The M6 acceptance caught the sky intensity reading back 1.0 after 3.2 was
written. `PhysicalSkyComponentConfig` defaults to `PhotometricUnit::Ev100Luminance`
and has per-mode Get*IntensityMin/Max clamps, so either the value is being
clamped or -- as on the Directional Light (M5) -- the component CONVERTS on a
mode change and the write order matters. Measured rather than guessed.

Run:  run_o3de_python.bat probe_m6_sky_intensity.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m6_sky_intensity_result.txt')

P = "Controller|Configuration|"
LUMEN, CANDELA, LUX, NIT, EV100_LUM, EV100_ILL = 0, 1, 2, 3, 4, 5

lines = []
ok = True


def log(msg):
    lines.append(str(msg))
    print(msg)


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
    type_id = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', ['Physical Sky'], game_type)[0]

    def fresh(label):
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
        return outcome.GetValue() if outcome and outcome.IsSuccess() else None

    log('=== defaults ===')
    pair = fresh('Probe_sky_defaults')
    for path in ('Intensity Mode', 'Sky Intensity', 'Sun Intensity'):
        log('  %-16s %r' % (path, get(pair, P + path)))

    log('')
    log('=== write Sky Intensity in the DEFAULT mode (Ev100Luminance) ===')
    for value in (0.5, 1.0, 3.2, 4.0, 8.0, 20.0, 50.0):
        pair = fresh('Probe_sky_%s' % value)
        wrote = put(pair, P + 'Sky Intensity', value)
        general.idle_wait_frames(3)
        log('  wrote %-6r ok=%-5s reads %r' % (value, wrote, get(pair, P + 'Sky Intensity')))

    log('')
    log('=== mode first, then intensity (the M5 lesson) ===')
    for mode, label in ((NIT, 'Nit'), (EV100_LUM, 'Ev100Luminance')):
        for value in (1.0, 3.2, 100.0, 1000.0):
            pair = fresh('Probe_sky_%s_%s' % (label, value))
            put(pair, P + 'Intensity Mode', mode)
            wrote = put(pair, P + 'Sky Intensity', value)
            general.idle_wait_frames(3)
            log('  mode=%-14s wrote %-8r ok=%-5s reads %r (mode reads %r)'
                % (label, value, wrote, get(pair, P + 'Sky Intensity'),
                   get(pair, P + 'Intensity Mode')))


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
