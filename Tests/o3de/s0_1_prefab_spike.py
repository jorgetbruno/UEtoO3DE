"""S0.1 spike (M0): prove prefab authoring from Python in O3DE 26.05.

Headless run (see run_s0_1.bat):
  Editor.exe --project-path=<proj> -BatchMode -autotest_mode \
      --runpython <this script> --runpythonargs <result-file path>

What it does:
  1. Opens Levels/DefaultLevel.
  2. Creates two entities, one parented to the other
     (editor.ToolsApplicationRequestBus/CreateNewEntity with a parent id).
  3. Resolves the "Mesh" and "Jolt Box Collider" component type IDs *by name*
     at runtime (EditorComponentAPIBus/FindComponentTypeIdsByEntityType) and
     hard-fails if either lookup misses. "Jolt Box Collider" resolves to the
     *editor* component (EditorJoltBoxColliderComponent): the gem gives only
     the editor component the AppearsInAddComponentMenu("Game") attribute
     (verified in Gems/JoltPhysics/Code/Source/Editor/Components/
     EditorJoltBoxColliderComponent.cpp), so EntityType().Game selects it.
  4. Adds both components to the child entity (AddComponentsOfType) and sets
     the Mesh component's model asset to the PrimitiveAssets gem's
     objects/_primitives/_box_1x1.fbx.azmodel (asset id looked up through
     AssetCatalogRequestBus/GetAssetIdByPath; the source .fbx and the cached
     product were both verified to exist on disk before this spike was written).
  5. Authors the prefab with PrefabPublicRequestBus/CreatePrefabInMemory.

  On-disk save: in 26.05 the *reflected* API cannot flush a newly authored
  template to disk by itself:
    - CreatePrefabInMemory explicitly keeps the template in memory only
      (AzToolsFramework/Prefab/PrefabPublicHandler.cpp).
    - CreatePrefabAndSaveToDisk exists but is NOT reflected to the behavior
      context (absent from PrefabPublicRequestHandler.cpp's Reflect list).
    - Level save (general.save_level) serializes only the root template
      (PrefabEditorEntityOwnershipService::SaveToStream).
  What IS reflected is PrefabLoaderScriptingBus/SaveTemplateToString (module
  "prefab", reflected from PrefabSystemComponent.cpp), which returns the exact
  file-format JSON the prefab system would write. CreatePrefabInMemory returns
  a container EntityId, not a TemplateId, and no reflected event maps
  path->TemplateId, so the script scans the small template-id space, picks the
  template whose JSON contains the S0_1_Parent entity (the on-disk .prefab
  format carries no "Source" key - verified against the real prefab files in
  both local projects), and writes that JSON to the .prefab file itself.
  VERIFY AT RUNTIME: that this scan finds the template and that the written
  file instantiates cleanly (asserted below via InstantiatePrefab).

  6. Reopens the prefab (PrefabPublicRequestBus/InstantiatePrefab) and asserts
     the expected two-entity parent/child structure and both components.

  Exit-code contract (Global Constraint 10): PASS writes the result file and
  exits via general.exit_no_prompt() (clean teardown, editor exit code 0; if
  teardown itself aborts, the process code goes non-zero and CI catches it).
  FAIL writes the result file and then terminates the process immediately with
  os._exit(1). NOTE: the JoltPhysics gem's smoke_test.py (the reference for
  this pattern) always exits via exit_no_prompt(), so on FAIL it still returns
  exit code 0 -- the os._exit(1) branch is a deliberate extension so that
  pass/fail actually controls the editor process exit code, which is what
  Global Constraint 10's CI asserts on. Everything else (log capture, verdict
  file, exit_no_prompt on success) mirrors smoke_test.py exactly.

  The result path comes from sys.argv[1] (the editor maps --runpythonargs to
  sys.argv after the script path; verified in EditorPythonBindings'
  PythonSystemComponent::EvaluateFile, PySys_SetArgvEx). Fallback:
  Tests/o3de/results/s0_1_result.txt relative to this script.
"""

import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 's0_1_result.txt')

LEVEL_NAME = 'DefaultLevel'
PARENT_NAME = 'S0_1_Parent'
CHILD_NAME = 'S0_1_Child'
MESH_COMPONENT_NAME = 'Mesh'
JOLT_COMPONENT_NAME = 'Jolt Box Collider'
MODEL_PRODUCT_PATH = 'objects/_primitives/_box_1x1.fbx.azmodel'  # PrimitiveAssets gem product
PREFAB_REL_PATH = 'Tests/S0_1_Spike.prefab'  # project-relative

lines = []
ok = True


def log(msg):
    lines.append(str(msg))
    print(msg)


class SpikeFailure(Exception):
    pass


def fail(msg):
    raise SpikeFailure(msg)


def check(cond, msg):
    global ok
    if cond:
        log('ok: ' + msg)
    else:
        ok = False
        log('FAIL: ' + msg)


def main():
    import azlmbr.legacy.general as general
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity
    import azlmbr.math as math
    import azlmbr.asset as asset
    import azlmbr.prefab as prefab
    from azlmbr.entity import EntityType

    # --- open a level (prefab authoring requires a root prefab instance) ---
    general.idle_enable(True)
    general.open_level_no_prompt(LEVEL_NAME)
    general.idle_wait_frames(30)
    current_level = general.get_current_level_name()
    log(f'current level: {current_level}')
    if not current_level or current_level.lower() != LEVEL_NAME.lower():
        fail(f'level "{LEVEL_NAME}" did not open (get_current_level_name="{current_level}")')

    # --- resolve component type ids by name; a miss is a hard failure ---
    # EditorEntityType is reflected as azlmbr.entity.EntityType with property
    # constants Game/System/Layer/Level (see the docstring in o3de's
    # EditorComponentAPIComponent.cpp: "EntityType().Game"). Access it as a
    # property, with a method-call fallback in case the binding differs.
    entity_type_instance = EntityType()
    game_entity_type = entity_type_instance.Game() if callable(entity_type_instance.Game) \
        else entity_type_instance.Game
    names = [MESH_COMPONENT_NAME, JOLT_COMPONENT_NAME]
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', names, game_entity_type)
    log(f'resolved type ids: {type_ids}')
    if not type_ids or len(type_ids) != len(names) or any(t.IsNull() for t in type_ids):
        available = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentTypeNameListByEntityType', game_entity_type)
        log(f'available editor component names: {available}')
        fail(f'component type lookup missed one of {names}')
    mesh_type, jolt_type = type_ids

    # --- create parent + child entities ---
    parent_id = editor.ToolsApplicationRequestBus(bus.Broadcast, 'CreateNewEntity', entity.EntityId())
    child_id = editor.ToolsApplicationRequestBus(bus.Broadcast, 'CreateNewEntity', parent_id)
    if not parent_id or not child_id or not parent_id.IsValid() or not child_id.IsValid():
        fail(f'entity creation failed (parent={parent_id}, child={child_id})')
    editor.EditorEntityAPIBus(bus.Event, 'SetName', parent_id, PARENT_NAME)
    editor.EditorEntityAPIBus(bus.Event, 'SetName', child_id, CHILD_NAME)
    components.TransformBus(bus.Event, 'SetWorldTranslation', child_id, math.Vector3(0.0, 0.0, 1.0))

    parent_of_child = editor.EditorEntityInfoRequestBus(bus.Event, 'GetParent', child_id)
    check(parent_of_child.ToString() == parent_id.ToString(),
          f'child is parented to parent (parent id {parent_id.ToString()})')

    # --- add Mesh + Jolt Box Collider to the child ---
    add_outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'AddComponentsOfType', child_id, [mesh_type, jolt_type])
    if not add_outcome or not add_outcome.IsSuccess():
        err = add_outcome.GetError() if add_outcome else 'no outcome returned'
        fail(f'AddComponentsOfType failed: {err}')
    check(editor.EditorComponentAPIBus(bus.Broadcast, 'CountComponentsOfType', child_id, mesh_type) == 1,
          f'child has one "{MESH_COMPONENT_NAME}" component')
    check(editor.EditorComponentAPIBus(bus.Broadcast, 'CountComponentsOfType', child_id, jolt_type) == 1,
          f'child has one "{JOLT_COMPONENT_NAME}" component')

    # --- point the Mesh component at the primitive box model ---
    asset_id = asset.AssetCatalogRequestBus(
        bus.Broadcast, 'GetAssetIdByPath', MODEL_PRODUCT_PATH, math.Uuid(), False)
    asset_path_back = asset.AssetCatalogRequestBus(bus.Broadcast, 'GetAssetPathById', asset_id)
    log(f'model asset id path resolves to: {asset_path_back!r}')
    if not asset_path_back:
        fail(f'model product "{MODEL_PRODUCT_PATH}" is not in the asset catalog '
             '(run the Asset Processor for the project first)')

    mesh_pair_outcome = editor.EditorComponentAPIBus(bus.Broadcast, 'GetComponentOfType', child_id, mesh_type)
    if not mesh_pair_outcome or not mesh_pair_outcome.IsSuccess():
        fail('GetComponentOfType(Mesh) failed right after adding it')
    mesh_pair = mesh_pair_outcome.GetValue()
    prop_paths = editor.EditorComponentAPIBus(bus.Broadcast, 'BuildComponentPropertyList', mesh_pair)
    log(f'mesh component property paths: {prop_paths}')
    model_prop = [p for p in prop_paths if 'ModelAsset' in p or 'Model Asset' in p]
    if not model_prop:
        fail('no model-asset property found on the Mesh component (see path list above)')
    set_outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'SetComponentProperty', mesh_pair, model_prop[0], asset_id)
    if not set_outcome or not set_outcome.IsSuccess():
        err = set_outcome.GetError() if set_outcome else 'no outcome returned'
        fail(f'SetComponentProperty({model_prop[0]}) failed: {err}')
    log(f'model asset set via property "{model_prop[0]}"')

    # --- author the prefab (in-memory) ---
    project_root = general.get_game_folder().rstrip('/\\')
    prefab_abs = os.path.join(project_root, *PREFAB_REL_PATH.split('/')).replace(os.sep, '/')
    os.makedirs(os.path.dirname(prefab_abs), exist_ok=True)
    create_result = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'CreatePrefabInMemory', [parent_id], prefab_abs)
    if not create_result or not create_result.IsSuccess():
        err = create_result.GetError() if create_result else 'no outcome returned'
        fail(f'CreatePrefabInMemory failed: {err}')
    container_id = create_result.GetValue()
    log(f'prefab instance created, container entity: {container_id.ToString()}')
    general.idle_wait_frames(30)

    owning_path = prefab.PrefabPublicRequestBus(bus.Broadcast, 'GetOwningInstancePrefabPath', parent_id)
    log(f'parent now owned by prefab instance at: {owning_path!r}')

    # --- flush the template to disk via the prefab system's own serializer ---
    # SaveTemplateToString needs a TemplateId; none of the reflected events map
    # path->TemplateId, so scan the (small) id space and identify our template
    # by content: it is the only one containing the PARENT_NAME entity (the
    # on-disk .prefab format has no "Source" key - checked against the real
    # Levels/*/*.prefab files in both local projects).
    template_json = None
    for template_id in range(1, 1025):
        outcome = prefab.PrefabLoaderScriptingBus(bus.Broadcast, 'SaveTemplateToString', template_id)
        if not outcome or not outcome.IsSuccess():
            continue
        text = outcome.GetValue()
        try:
            dom = json.loads(text)
            keys = sorted(dom.keys())
        except Exception:
            keys = '<unparseable>'
        log(f'template id {template_id}: top-level keys={keys}')
        if f'"{PARENT_NAME}"' in text or f"'{PARENT_NAME}'" in text:
            template_json = text
            break
    if template_json is None:
        fail(f'no in-memory template containing entity "{PARENT_NAME}" found after CreatePrefabInMemory')
    dom = json.loads(template_json)
    if 'ContainerEntity' not in dom or 'Entities' not in dom:
        fail('serialized template does not have the prefab file format '
             f'(top-level keys: {sorted(dom.keys())})')
    with open(prefab_abs, 'w') as f:
        f.write(template_json)
    check(os.path.exists(prefab_abs), f'prefab file exists on disk: {prefab_abs}')
    if '_box_1x1' not in template_json and 'c15cd465-9589-56ed-945f-8416fa4798a3' not in template_json.lower():
        fail('saved prefab does not reference the primitive box model asset '
             '(Mesh component model asset did not serialize)')
    log('saved prefab references the primitive box model asset')

    # --- reopen: instantiate the prefab and assert the two-entity structure ---
    instantiate_result = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'InstantiatePrefab', prefab_abs, entity.EntityId(), math.Vector3(5.0, 0.0, 0.0))
    if not instantiate_result or not instantiate_result.IsSuccess():
        err = instantiate_result.GetError() if instantiate_result else 'no outcome returned'
        fail(f'InstantiatePrefab failed: {err}')
    inst_container_id = instantiate_result.GetValue()
    general.idle_wait_frames(60)  # let the instance-propagation queue drain

    def children_of(entity_id):
        children = editor.EditorEntityInfoRequestBus(bus.Event, 'GetChildren', entity_id)
        return list(children) if children else []

    def name_of(entity_id):
        return editor.EditorEntityInfoRequestBus(bus.Event, 'GetName', entity_id)

    inst_container_children = children_of(inst_container_id)
    inst_parents = [c for c in inst_container_children if name_of(c) == PARENT_NAME]
    check(len(inst_container_children) == 1 and len(inst_parents) == 1,
          'instantiated prefab contains exactly one top-level entity named '
          f'{PARENT_NAME} (container children: {[name_of(c) for c in inst_container_children]})')
    if not inst_parents:
        fail('reopened prefab is missing the parent entity')
    inst_parent = inst_parents[0]

    inst_parent_children = children_of(inst_parent)
    inst_children = [c for c in inst_parent_children if name_of(c) == CHILD_NAME]
    check(len(inst_parent_children) == 1 and len(inst_children) == 1,
          f'{PARENT_NAME} has exactly one child named {CHILD_NAME} '
          f'(children: {[name_of(c) for c in inst_parent_children]})')
    if not inst_children:
        fail('reopened prefab is missing the child entity')
    inst_child = inst_children[0]

    childs_parent = editor.EditorEntityInfoRequestBus(bus.Event, 'GetParent', inst_child)
    check(childs_parent.ToString() == inst_parent.ToString(),
          f'{CHILD_NAME} is parented to {PARENT_NAME} in the reopened prefab')
    check(editor.EditorComponentAPIBus(bus.Broadcast, 'CountComponentsOfType', inst_child, mesh_type) == 1,
          f'reopened {CHILD_NAME} still has the "{MESH_COMPONENT_NAME}" component')
    check(editor.EditorComponentAPIBus(bus.Broadcast, 'CountComponentsOfType', inst_child, jolt_type) == 1,
          f'reopened {CHILD_NAME} still has the "{JOLT_COMPONENT_NAME}" component')
    pos = components.TransformBus(bus.Event, 'GetWorldTranslation', inst_child)
    log(f'reopened child world position: ({pos.x:.3f}, {pos.y:.3f}, {pos.z:.3f})')

    # original instance from CreatePrefabInMemory should hold the same structure
    orig_children = children_of(container_id)
    check(len(orig_children) == 1 and name_of(orig_children[0]) == PARENT_NAME,
          f'original prefab instance holds {PARENT_NAME} under its container')

    # log-only sanity: entity lookup by name (same API smoke_test.py uses)
    found = general.find_editor_entity(CHILD_NAME)
    log(f'general.find_editor_entity("{CHILD_NAME}") found an entity: {found is not None}')


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
try:
    _exit_fn = _general_for_exit.exit_no_prompt
except Exception:
    _exit_fn = None

if ok and _exit_fn is not None:
    # Clean editor shutdown, mirroring the JoltPhysics smoke_test.py: exit code
    # 0 unless teardown itself aborts (which CI must catch as a failure).
    _exit_fn()
else:
    # Deliberate extension over smoke_test.py (which exits 0 even on FAIL):
    # make the editor process exit code reflect the failure, per Global
    # Constraint 10. Result file is already written and closed above.
    os._exit(0 if ok else 1)
