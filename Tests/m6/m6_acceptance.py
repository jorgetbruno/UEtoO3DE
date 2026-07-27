"""
m6_acceptance.py — M6 acceptance, editor half.

Instantiates the SAVED prefab and asserts the environment actually landed:

  * the fog entity carries BOTH a PostFX Layer and a Deferred Fog (Atom's
    post-process components are inert without the layer), with fog enabled and
    a start < end ramp;
  * the post-process entity carries a layer plus exactly the components its
    overridden settings called for -- and nothing invented;
  * exactly ONE Physical Sky exists in the whole prefab (two fight);
  * every enable flag that makes a post-process component do anything is
    actually true, since a component with it off serializes looking configured.

The plan also asks for a headless-render luminance check ("catches the
everything-is-black failure"). What is asserted here instead, and why: a
render capture needs a live render pipeline and a camera, which this
-BatchMode editor does not have. The structural equivalent is asserted --
a sky component exists and is the only one -- and the black-void failure the
render check was aiming at is what that prevents. Recorded honestly rather
than claimed.

Run:  Tests/o3de/run_o3de_python.bat Tests/m6/m6_acceptance.py
"""

import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm6_acceptance_result.txt')

MANIFEST_PATH = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
PREFAB_REL_PATH = "Prefabs/Fixture_01.prefab"

P = "Controller|Configuration|"

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
    environments = {item["name"]: item["environment"]
                    for item in document["entities"] if "environment" in item}

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
    names = ['Physical Sky', 'PostFX Layer', 'Deferred Fog', 'Exposure Control', 'Bloom']
    type_ids = dict(zip(names, editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', names, game_type)))

    def count_of(entity_id, component):
        return editor.EditorComponentAPIBus(
            bus.Broadcast, 'CountComponentsOfType', entity_id, type_ids[component])

    def pair_of(entity_id, component):
        return editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_ids[component]).GetValue()

    def prop(pair, path):
        result = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', pair, path)
        if result and result.IsSuccess():
            return result.GetValue()
        return None

    log('== environment components in the saved prefab ==')
    sky_entities = []
    for name in sorted(environments):
        environment = environments[name]
        entity_id = by_name.get(name)
        if not check(entity_id is not None, '%s missing from prefab' % name):
            continue
        present = [component for component in names if count_of(entity_id, component)]
        log('  %-22s %-16s %r' % (name, environment["type"], present))

        if environment["type"] in ("skylight", "sky_atmosphere"):
            if 'Physical Sky' in present:
                sky_entities.append(name)
            continue

        if environment["type"] == "fog":
            check('PostFX Layer' in present,
                  '%s: Deferred Fog does nothing without a PostFX Layer' % name)
            if not check('Deferred Fog' in present, '%s: no Deferred Fog' % name):
                continue
            fog_pair = pair_of(entity_id, 'Deferred Fog')
            check(prop(fog_pair, P + 'Enable Deferred Fog') is True,
                  '%s: the fog component is present but disabled' % name)
            start = prop(fog_pair, P + 'Distance|Fog Start Distance')
            end = prop(fog_pair, P + 'Distance|Fog End Distance')
            check(start is not None and end is not None and start < end,
                  '%s: fog ramp is not ordered (start %r, end %r)' % (name, start, end))
            density = prop(fog_pair, P + 'Density Control|Fog Density')
            check(density is not None and 0.0 < density <= 1.0,
                  '%s: fog density %r is outside Atom range' % (name, density))
            continue

        if environment["type"] == "post_process":
            check('PostFX Layer' in present, '%s: no PostFX Layer' % name)
            overrides = environment.get("overrides") or {}
            wants_exposure = 'auto_exposure_bias' in overrides
            wants_bloom = ('bloom_intensity' in overrides
                           or 'bloom_threshold' in overrides)
            check(('Exposure Control' in present) == wants_exposure,
                  '%s: Exposure Control presence %r does not match the '
                  'overridden settings %r' % (name, 'Exposure Control' in present,
                                              sorted(overrides)))
            check(('Bloom' in present) == wants_bloom,
                  '%s: Bloom presence %r does not match the overridden settings'
                  % (name, 'Bloom' in present))
            if wants_bloom:
                bloom_pair = pair_of(entity_id, 'Bloom')
                check(prop(bloom_pair, P + 'Enable Bloom') is True,
                      '%s: Bloom is present but disabled, so it renders nothing'
                      % name)
                if 'bloom_intensity' in overrides:
                    got = prop(bloom_pair, P + 'Intensity')
                    check(abs(got - float(overrides['bloom_intensity'])) <= 1e-4,
                          '%s: bloom intensity is %r, UE has %r'
                          % (name, got, overrides['bloom_intensity']))
            if wants_exposure:
                exposure_pair = pair_of(entity_id, 'Exposure Control')
                check(prop(exposure_pair, P + 'Enable') is True,
                      '%s: Exposure Control is present but disabled' % name)
                got = prop(exposure_pair, P + 'Manual Compensation')
                check(abs(got - float(overrides['auto_exposure_bias'])) <= 1e-4,
                      '%s: exposure compensation is %r, UE has %r'
                      % (name, got, overrides['auto_exposure_bias']))

    log('')
    log('== exactly one sky, and the SkyLight owns it ==')
    log('  sky authored on: %r' % sky_entities)
    check(len(sky_entities) == 1,
          'the prefab has %d Physical Sky components (%r); two fight over the '
          'same sky and the level is not lit as authored'
          % (len(sky_entities), sky_entities))

    # Which actor wins matters: the SkyLight carries the artist's intensity,
    # the SkyAtmosphere carries scattering parameters Atom cannot represent.
    # If the atmosphere wins, the authored intensity is silently replaced by
    # a default -- a level that is subtly the wrong brightness with nothing
    # in the report to explain it.
    skylights = [name for name, block in environments.items()
                 if block["type"] == "skylight"]
    if skylights and sky_entities:
        check(sky_entities[0] in skylights,
              'the sky was authored on %r, but the level has a SkyLight (%r) '
              'whose intensity should have won' % (sky_entities[0], skylights))
        sky_pair = pair_of(by_name[sky_entities[0]], 'Physical Sky')
        intensity = prop(sky_pair, P + 'Sky Intensity')
        authored = float(environments[sky_entities[0]].get("intensity", 1.0))
        log('  sky intensity %r (UE skylight intensity %r)' % (intensity, authored))
        check(intensity is not None and intensity > 0.0,
              'the sky intensity is %r; the level would be lit by nothing' % intensity)
        # The exact value matters, not just that one is set: writing Sky
        # Intensity without writing Intensity Mode first stores 1.0 for ANY
        # input while reporting success (probe_m6_sky_intensity.py). Asserting
        # only "> 0" would pass on that silently-wrong sky.
        expected = authored * 4.0
        check(abs(intensity - expected) <= max(1e-3, expected * 1e-5),
              'sky intensity is %r, expected %r (UE %r x Atom default 4.0). A '
              'value of exactly 1.0 means the intensity write was discarded '
              'because the intensity mode was not written first.'
              % (intensity, expected, authored))


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
