"""
probe_m6_env.py — M6: what Atom offers for sky, skylight, fog and post-process.

The exporter has no `environment` block yet, so this runs FIRST: there is no
point exporting UE properties that nothing on the O3DE side can consume. It
answers, by measurement:

  1. which environment component NAMES resolve (candidates below, plus a dump
     of every component whose name mentions sky/fog/post/exposure/bloom/
     reflection so a wrong guess cannot hide a real component);
  2. the full property path + current value of each one that resolves;
  3. which of them require a PostFX Layer on the same entity to do anything
     (Atom's post-process components are layer members) -- probed by adding a
     PostFX Layer first and seeing whether the property surface changes;
  4. whether "Enabled"/"Enable" style toggles exist per component, since a
     post-process component with its override flags off is inert and would
     serialize into a prefab looking configured.

Nothing authored here survives: scratch entities in DefaultLevel, never saved.

Run:  run_o3de_python.bat probe_m6_env.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m6_env_result.txt')

CANDIDATES = [
    'Global Skylight (IBL)',
    'Physical Sky',
    'HDRi Skybox',
    'Sky Atmosphere',
    'PostFX Layer',
    'Deferred Fog',
    'Exposure Control',
    'Bloom',
    'Depth of Field',
    'Reflection Probe',
    'Light',
]
KEYWORDS = ('sky', 'fog', 'post', 'exposure', 'bloom', 'depth of field',
            'reflection', 'ibl', 'atmosphere', 'grading', 'vignette')

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
    from azlmbr.entity import EntityType

    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(30)

    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game

    available = editor.EditorComponentAPIBus(
        bus.Broadcast, 'BuildComponentTypeNameListByEntityType', game_type) or []
    log('=== 1. every component whose name mentions an M6 keyword ===')
    for name in sorted(available):
        if any(keyword in name.lower() for keyword in KEYWORDS):
            log('  ' + name)

    log('')
    log('=== 2. which candidates resolve ===')
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', CANDIDATES, game_type)
    resolved = {}
    for name, type_id in zip(CANDIDATES, type_ids or []):
        hit = type_id is not None and not type_id.IsNull()
        log('  %-24s %s' % (name, type_id.ToString() if hit else 'MISS'))
        if hit:
            resolved[name] = type_id

    def new_entity(label):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, label)
        return entity_id

    def add(entity_id, type_id):
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
        if not outcome or not outcome.IsSuccess():
            return None
        return editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()

    def dump(pair, label):
        paths = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentPropertyList', pair) or []
        log('  --- %s: %d properties ---' % (label, len(paths)))
        for path in sorted(paths):
            try:
                outcome = editor.EditorComponentAPIBus(
                    bus.Broadcast, 'GetComponentProperty', pair, path)
                value = outcome.GetValue() if outcome and outcome.IsSuccess() else '<unreadable>'
            except Exception as exc:
                value = 'RAISED %r' % (exc,)
            log('    %-62s %r' % (path, value))
        return paths

    log('')
    log('=== 3. property surface, each on its OWN entity ===')
    for name, type_id in resolved.items():
        if name == 'Light':
            continue
        entity_id = new_entity('Probe_' + name.replace(' ', '_').replace('(', '').replace(')', ''))
        pair = add(entity_id, type_id)
        if pair is None:
            log('  --- %s: ADD FAILED (needs a dependency?) ---' % name)
            continue
        general.idle_wait_frames(5)
        dump(pair, name)

    # --- 4. post-process components alongside a PostFX Layer ---------------
    log('')
    log('=== 4. the same components WITH a PostFX Layer on the entity ===')
    layer_type = resolved.get('PostFX Layer')
    if layer_type is None:
        log('  PostFX Layer did not resolve; skipping')
    else:
        for name in ('Deferred Fog', 'Exposure Control', 'Bloom', 'Depth of Field'):
            type_id = resolved.get(name)
            if type_id is None:
                continue
            entity_id = new_entity('ProbeLayer_' + name.replace(' ', '_'))
            layer_pair = add(entity_id, layer_type)
            pair = add(entity_id, type_id)
            general.idle_wait_frames(5)
            if pair is None:
                log('  --- %s WITH layer: ADD FAILED ---' % name)
                continue
            dump(pair, name + ' (with PostFX Layer)')
            if layer_pair is not None and name == 'Deferred Fog':
                dump(layer_pair, 'PostFX Layer itself')


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
