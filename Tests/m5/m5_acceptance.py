"""
m5_acceptance.py — M5 acceptance, editor half.

Instantiates the SAVED prefab (fresh session; the file that ships is the
artifact, not the importing session's memory) and asserts, per light type and
per intensity-unit mode:

  * the right component is present ("Light" for local, "Directional Light"
    for the sun) and absent where the manifest has no light;
  * the intensity landed in CANDELA for local lights and LUX for the sun,
    with the value UE's own arithmetic implies -- 1256.637 lm becomes exactly
    100 cd, 5 EV becomes 32 cd, 10000 unitless becomes 16 cd, and a 1000 lm
    spot becomes 1000/(2*pi*(1-cos 20deg)) cd because the cone is used, not
    the sphere;
  * the spot's cone rides on the shutters and the shutters are enabled;
  * the explicit attenuation radius survived (Atom would otherwise derive it);
  * colour round-trips in LINEAR space;
  * the point lights carry NO shadow flag (Atom's SimplePoint cannot cast
    shadows and the flag would read back true while doing nothing).

The expected values are recomputed here from the manifest with independent
arithmetic rather than imported from `light_build`, so a wrong conversion
cannot agree with itself.

Run:  Tests/o3de/run_o3de_python.bat Tests/m5/m5_acceptance.py
"""

import json
import math
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm5_acceptance_result.txt')

MANIFEST_PATH = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
PREFAB_REL_PATH = "Prefabs/Fixture_01.prefab"

# Independent copies of the enum values (see light_build for provenance).
CANDELA, LUX = 1, 2
SIMPLE_POINT, SIMPLE_SPOT = 6, 7
RADIUS_EXPLICIT = 0

P = "Controller|Configuration|"
INTENSITY_TOLERANCE = 1e-3

lines = []
failures = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def check(condition, msg):
    if not condition:
        fail(msg)
    return condition


def expected_candela(light):
    """UE units -> candela, derived here from first principles."""
    units = light["intensity_units"]
    value = float(light["intensity"])
    if units == "candelas":
        return value
    if units == "lumens":
        if light["type"] == "spot":
            half = math.radians(float(light["outer_cone_angle_deg"]))
            return value / (2.0 * math.pi * (1.0 - math.cos(half)))
        return value / (4.0 * math.pi)
    if units == "ev":
        return 2.0 ** value
    if units == "unitless":
        return value * 16.0 / 10000.0
    raise AssertionError("no expectation for units %r" % units)


def main():
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath
    import azlmbr.prefab as prefab
    from azlmbr.entity import EntityType

    with open(MANIFEST_PATH) as handle:
        document = json.load(handle)
    lights = {item["name"]: item["light"] for item in document["entities"]
              if "light" in item}
    non_lights = [item["name"] for item in document["entities"]
                  if "light" not in item]

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = os.path.join(project_root, *PREFAB_REL_PATH.split('/')).replace(os.sep, '/')

    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(30)

    outcome = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'InstantiatePrefab', prefab_path, entity_module.EntityId(),
        azmath.Vector3(0.0, 0.0, 0.0))
    if outcome is None or not outcome.IsSuccess():
        fail('InstantiatePrefab failed')
        return
    container = outcome.GetValue()
    general.idle_wait_frames(60)

    def children_of(entity_id):
        found = editor.EditorEntityInfoRequestBus(bus.Event, 'GetChildren', entity_id)
        return list(found) if found else []

    by_name = {}
    stack = children_of(container)
    while stack:
        entity_id = stack.pop()
        by_name[editor.EditorEntityInfoRequestBus(bus.Event, 'GetName', entity_id)] = entity_id
        stack.extend(children_of(entity_id))

    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game
    light_type_id, directional_type_id = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType',
        ['Light', 'Directional Light'], game_type)

    def count_of(entity_id, type_id):
        return editor.EditorComponentAPIBus(
            bus.Broadcast, 'CountComponentsOfType', entity_id, type_id)

    def pair_of(entity_id, type_id):
        return editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()

    def prop(pair, path):
        result = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', pair, path)
        if result and result.IsSuccess():
            return result.GetValue()
        return None

    log('== lights in the saved prefab ==')
    for name in sorted(lights):
        light = lights[name]
        entity_id = by_name.get(name)
        if not check(entity_id is not None, '%s missing from prefab' % name):
            continue

        if light["type"] == "directional":
            if not check(count_of(entity_id, directional_type_id) == 1,
                         '%s should carry exactly one Directional Light' % name):
                continue
            pair = pair_of(entity_id, directional_type_id)
            mode = prop(pair, P + 'Intensity mode')
            intensity = prop(pair, P + 'Intensity')
            log('  %-22s directional  mode=%r intensity=%r' % (name, mode, intensity))
            check(mode == LUX, '%s: intensity mode is %r, expected Lux (%d)'
                  % (name, mode, LUX))
            check(abs(intensity - float(light["intensity"])) <= INTENSITY_TOLERANCE,
                  '%s: intensity is %r, UE has %r lux'
                  % (name, intensity, light["intensity"]))
            check(prop(pair, P + 'Shadow|Enable Shadow') == bool(light["cast_shadows"]),
                  '%s: shadow flag does not match UE' % name)
            check(count_of(entity_id, light_type_id) == 0,
                  '%s carries a local Light component as well' % name)
            continue

        if not check(count_of(entity_id, light_type_id) == 1,
                     '%s should carry exactly one Light component' % name):
            continue
        pair = pair_of(entity_id, light_type_id)
        want_type = SIMPLE_SPOT if light["type"] == "spot" else SIMPLE_POINT
        want_intensity = expected_candela(light)

        mode = prop(pair, P + 'Intensity mode')
        intensity = prop(pair, P + 'Intensity')
        light_type = prop(pair, P + 'Light type')
        log('  %-22s %-5s %-9s type=%r mode=%r intensity=%r (expect %.4f cd)'
            % (name, light["type"], light["intensity_units"], light_type, mode,
               intensity, want_intensity))

        check(light_type == want_type,
              '%s: light type is %r, expected %r' % (name, light_type, want_type))
        check(mode == CANDELA,
              '%s: intensity mode is %r; Atom local lights take Candela (%d)'
              % (name, mode, CANDELA))
        check(abs(intensity - want_intensity) <= max(INTENSITY_TOLERANCE,
                                                     want_intensity * 1e-5),
              '%s: intensity is %r cd, %r %s converts to %r cd'
              % (name, intensity, light["intensity"], light["intensity_units"],
                 want_intensity))

        # Explicit attenuation radius (Atom would derive it otherwise).
        if "attenuation_radius" in light:
            check(prop(pair, P + 'Attenuation radius|Mode') == RADIUS_EXPLICIT,
                  '%s: attenuation radius mode is not Explicit' % name)
            radius = prop(pair, P + 'Attenuation radius|Radius')
            check(abs(radius - float(light["attenuation_radius"])) <= 1e-3,
                  '%s: attenuation radius is %r, UE has %r'
                  % (name, radius, light["attenuation_radius"]))

        # Colour, in linear space.
        colour = prop(pair, P + 'Color')
        expected_colour = light["color_linear"]
        actual = [getattr(colour, axis, None) for axis in ('r', 'g', 'b')]
        if actual[0] is not None:
            for index, axis in enumerate('rgb'):
                check(abs(actual[index] - expected_colour[index]) <= 2e-3,
                      '%s: colour.%s is %r, manifest has %r (linear)'
                      % (name, axis, actual[index], expected_colour[index]))

        if light["type"] == "spot":
            check(prop(pair, P + 'Shutters|Enable shutters') is True,
                  '%s: the spot cone rides on the shutters; they must be on' % name)
            inner = prop(pair, P + 'Shutters|Inner angle')
            outer = prop(pair, P + 'Shutters|Outer angle')
            check(abs(inner - float(light["inner_cone_angle_deg"])) <= 1e-3,
                  '%s: inner angle is %r, UE has %r'
                  % (name, inner, light["inner_cone_angle_deg"]))
            check(abs(outer - float(light["outer_cone_angle_deg"])) <= 1e-3,
                  '%s: outer angle is %r, UE has %r'
                  % (name, outer, light["outer_cone_angle_deg"]))
            check(prop(pair, P + 'Shadows|Enable shadow') == bool(light["cast_shadows"]),
                  '%s: SimpleSpot supports shadows; the flag should match UE' % name)
        else:
            # SimplePoint cannot cast shadows: the importer must NOT have
            # written the flag, so it stays at the component default (False).
            check(prop(pair, P + 'Shadows|Enable shadow') is False,
                  '%s: a shadow flag was written on a SimplePoint light, which '
                  'reads back true while casting nothing' % name)

    log('')
    log('== non-light entities carry no light components ==')
    stray = [name for name in non_lights
             if by_name.get(name) is not None
             and (count_of(by_name[name], light_type_id)
                  or count_of(by_name[name], directional_type_id))]
    check(not stray, 'these non-light entities got a light component: %r' % stray)
    log('  checked %d non-light entities' % len(non_lights))


try:
    main()
except Exception:
    fail('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: ' + ('PASS' if not failures else 'FAIL (%d)' % len(failures)))
os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
if not failures:
    _general.exit_no_prompt()
else:
    os._exit(1)
