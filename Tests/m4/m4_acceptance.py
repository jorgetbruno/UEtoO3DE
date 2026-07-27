"""
m4_acceptance.py — M4 acceptance, editor half.

Instantiates the SAVED prefab (fresh session, same reasoning as M2: the file
that ships is the artifact, not the importing session's memory) and asserts:

  * every entity whose slot-0 material was converted carries a Material
    component whose default slot resolves to the expected .azmaterial product;
  * entities on unmapped materials (the deliberately unsupported one, and the
    engine's WorldGrid) have NO Material component -- the backend default, by
    design, visibly grey rather than silently wrong.

Run:  Tests/o3de/run_o3de_python.bat Tests/m4/m4_acceptance.py
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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm4_acceptance_result.txt')

MANIFEST_PATH = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
PREFAB_REL_PATH = "Prefabs/Fixture_01.prefab"

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
    import azlmbr.asset as asset
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath
    import azlmbr.prefab as prefab
    from azlmbr.entity import EntityType

    with open(MANIFEST_PATH) as handle:
        document = json.load(handle)
    assets_by_guid = {a["guid"]: a for a in document["assets"]}

    # entity name -> expected azmaterial product path (or None for default)
    expected = {}
    for item in document["entities"]:
        mesh = item.get("mesh")
        if mesh is None:
            continue
        slots = mesh.get("material_slots") or []
        guid = slots[0].get("material_guid") if slots else None
        material = assets_by_guid.get(guid) if guid else None
        if material and material.get("material_data"):
            product = "assets/%s.azmaterial" % material["o3de_relative_path"].rsplit(".", 1)[0]
            expected[item["name"]] = product.lower()
        else:
            expected[item["name"]] = None

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
    material_type = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', ['Material'], game_type)[0]

    log('== material assignments in the saved prefab ==')
    for name in sorted(expected):
        entity_id = by_name.get(name)
        if not check(entity_id is not None, '%s missing from prefab' % name):
            continue
        count = editor.EditorComponentAPIBus(
            bus.Broadcast, 'CountComponentsOfType', entity_id, material_type)
        want = expected[name]
        if want is None:
            log('  %-22s default material (unmapped)  components=%d' % (name, count))
            check(count == 0,
                  '%s is on an unmapped material and must have NO Material '
                  'component (backend default), found %d' % (name, count))
            continue
        log('  %-22s expect %s' % (name, want))
        if not check(count == 1, '%s has %d Material components, expected 1' % (name, count)):
            continue
        pair = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, material_type).GetValue()
        value = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', pair, 'Default Material|Material Asset')
        asset_id = value.GetValue() if value and value.IsSuccess() else None
        back = asset.AssetCatalogRequestBus(bus.Broadcast, 'GetAssetPathById', asset_id) if asset_id else ''
        check(back == want,
              '%s default material is %r, expected %r' % (name, back, want))


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
