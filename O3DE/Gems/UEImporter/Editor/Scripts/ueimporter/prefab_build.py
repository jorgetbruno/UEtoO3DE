"""
prefab_build.py — turn a verified manifest into a saved `.prefab` (plan M2).

Needs the editor (`azlmbr`). Every API here was established by M0 spike S0.1 or
by the M2 probes under `Tests/o3de/`; the two findings the code depends on most:

  * **Saving a prefab.** In 26.05 the reflected API cannot flush a newly
    authored template to disk: `CreatePrefabInMemory` keeps it in memory by
    design, `CreatePrefabAndSaveToDisk` is not reflected, and level save only
    serializes the root template. What *is* reflected is
    `PrefabLoaderScriptingBus/SaveTemplateToString`, which returns exactly the
    on-disk JSON -- but it needs a TemplateId and nothing maps a path to one.
    So the template id space is scanned and the template is identified by
    content. S0.1 proved the file this produces reopens correctly.

  * **Scale.** `AZ::Transform` holds one uniform scale float; `SetLocalScale`
    is a no-op stub that reports (1,1,1) back whatever you pass it. Non-uniform
    scale needs `EditorNonUniformScaleComponent`, which appears in no Add
    Component list (it is added through the Transform component's UI) and whose
    `azlmbr.editor.AddNonUniformScaleComponent` helper does nothing here. It is
    added by type id, read from the SDK header rather than guessed, and
    `Tests/o3de/probe_m2_nonuniform.py` confirms it survives a save/reload.
"""

# AzToolsFramework::Components::EditorNonUniformScaleComponent, from
# Code/Framework/AzToolsFramework/AzToolsFramework/ToolsComponents/
# EditorNonUniformScaleComponent.h in the 26.05 SDK.
NON_UNIFORM_SCALE_TYPE_ID = "{2933FB4F-B3DA-4CD1-8106-F37300730777}"

MESH_COMPONENT_NAME = "Mesh"
MODEL_ASSET_PROPERTY = "Controller|Configuration|Model Asset"
MATERIAL_COMPONENT_NAME = "Material"
# Verified live: probe_m4_material — assigns and reads back an azmaterial.
MATERIAL_ASSET_PROPERTY = "Default Material|Material Asset"

# Below this, a scale is treated as uniform and goes in the transform.
UNIFORM_SCALE_EPSILON = 1e-6

TEMPLATE_ID_SCAN_LIMIT = 2048


class PrefabBuildError(Exception):
    pass


def _uuid_from_string(text):
    import azlmbr.math as math
    if hasattr(math, "Uuid_CreateString"):
        return math.Uuid_CreateString(text, 0)
    return math.Uuid().CreateString(text)


def resolve_component_type(name):
    """Type id for an editor component, by display name. Misses are fatal.

    A silent miss would produce entities with no Mesh component -- a level that
    imports "successfully" and renders nothing.
    """
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    from azlmbr.entity import EntityType

    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', [name], game_type)
    if not type_ids or len(type_ids) != 1 or type_ids[0].IsNull():
        available = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentTypeNameListByEntityType', game_type)
        raise PrefabBuildError(
            "component %r did not resolve to a type id. Available: %r"
            % (name, sorted(available or [])))
    return type_ids[0]


def _is_uniform(scale):
    return (abs(scale[0] - scale[1]) < UNIFORM_SCALE_EPSILON
            and abs(scale[1] - scale[2]) < UNIFORM_SCALE_EPSILON)


def _apply_transform(entity_id, transform, report, entity_name, has_children):
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.math as math

    translation = transform["translation"]
    rotation = transform["rotation"]
    scale = transform["scale"]

    components.TransformBus(bus.Event, 'SetLocalTranslation', entity_id,
                            math.Vector3(float(translation[0]), float(translation[1]),
                                         float(translation[2])))
    components.TransformBus(bus.Event, 'SetLocalRotationQuaternion', entity_id,
                            math.Quaternion(float(rotation[0]), float(rotation[1]),
                                            float(rotation[2]), float(rotation[3])))

    if _is_uniform(scale):
        components.TransformBus(bus.Event, 'SetLocalUniformScale', entity_id, float(scale[0]))
        return

    # Non-uniform: keep the transform at 1.0 and carry the whole scale on the
    # dedicated component, so the two never multiply together by accident.
    components.TransformBus(bus.Event, 'SetLocalUniformScale', entity_id, 1.0)
    type_id = _uuid_from_string(NON_UNIFORM_SCALE_TYPE_ID)
    outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
    if not outcome or not outcome.IsSuccess():
        raise PrefabBuildError(
            "%s needs a non-uniform scale %r but EditorNonUniformScaleComponent "
            "could not be added: %s"
            % (entity_name, scale, outcome.GetError() if outcome else "no outcome"))

    import azlmbr.entity as entity_module
    entity_module.NonUniformScaleRequestBus(
        bus.Event, 'SetScale', entity_id,
        math.Vector3(float(scale[0]), float(scale[1]), float(scale[2])))

    read_back = entity_module.NonUniformScaleRequestBus(bus.Event, 'GetScale', entity_id)
    if read_back is None or any(abs(getattr(read_back, axis) - float(value)) > 1e-4
                                for axis, value in zip("xyz", scale)):
        raise PrefabBuildError(
            "%s: non-uniform scale did not round-trip (wrote %r)" % (entity_name, scale))

    report.warn("XFORM_NONUNIFORM_SCALE_COMPONENT", entity_name,
                "scale %r carried on an EditorNonUniformScaleComponent because "
                "AZ::Transform is uniform-scale only" % (scale,))
    if has_children:
        # O3DE applies non-uniform scale at the component, not in the transform
        # hierarchy, so it does not reach descendants the way UE's does.
        report.warn("XFORM_NONUNIFORM_SCALE_NOT_INHERITED", entity_name,
                    "entity has children; O3DE does not propagate non-uniform "
                    "scale to them, UE does")


def _add_mesh_component(entity_id, mesh_type, asset_id, entity_name):
    import azlmbr.bus as bus
    import azlmbr.editor as editor

    outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'AddComponentsOfType', entity_id, [mesh_type])
    if not outcome or not outcome.IsSuccess():
        raise PrefabBuildError(
            "%s: AddComponentsOfType(Mesh) failed: %s"
            % (entity_name, outcome.GetError() if outcome else "no outcome"))

    pair_outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', entity_id, mesh_type)
    if not pair_outcome or not pair_outcome.IsSuccess():
        raise PrefabBuildError("%s: Mesh component vanished after being added" % entity_name)
    pair = pair_outcome.GetValue()

    set_outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'SetComponentProperty', pair, MODEL_ASSET_PROPERTY, asset_id)
    if not set_outcome or not set_outcome.IsSuccess():
        raise PrefabBuildError(
            "%s: could not set %s: %s"
            % (entity_name, MODEL_ASSET_PROPERTY,
               set_outcome.GetError() if set_outcome else "no outcome"))


def create_level_root(name):
    """A single identity-transform entity that every imported root hangs off.

    Two reasons, one of them load-bearing:

    * `CreatePrefabInMemory` places the container entity at the centroid of the
      entities it is given, and rewrites their transforms to be relative to it.
      Handing it one entity at the origin puts the container at the origin, so
      instantiating the prefab at the origin reproduces the level exactly.
      (Anchoring the container afterwards does not work: transform changes made
      after the call do not reach the serialized template.)
    * A single root is what makes the imported level movable as a unit, and
      gives M10's re-import something stable to match against.
    """
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.math as math

    root_id = editor.ToolsApplicationRequestBus(
        bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
    if not root_id or not root_id.IsValid():
        raise PrefabBuildError("could not create the level root entity")
    editor.EditorEntityAPIBus(bus.Event, 'SetName', root_id, name)
    components.TransformBus(bus.Event, 'SetLocalTranslation', root_id,
                            math.Vector3(0.0, 0.0, 0.0))
    components.TransformBus(bus.Event, 'SetLocalRotationQuaternion', root_id,
                            math.Quaternion(0.0, 0.0, 0.0, 1.0))
    components.TransformBus(bus.Event, 'SetLocalUniformScale', root_id, 1.0)
    return root_id


def assign_material(entity_id, material_asset_id, entity_name):
    """Add a Material component and set the default slot's material (M4).

    The default slot covers the whole model, which matches what the export
    ships today: the baked FBX carries a single material slot (mesh_export's
    known M4 limitation), so per-slot assignment beyond slot 0 would target
    slots that do not exist.
    """
    import azlmbr.bus as bus
    import azlmbr.editor as editor

    material_type = resolve_component_type(MATERIAL_COMPONENT_NAME)
    outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'AddComponentsOfType', entity_id, [material_type])
    if not outcome or not outcome.IsSuccess():
        raise PrefabBuildError("%s: AddComponentsOfType(Material) failed: %s"
                               % (entity_name, outcome.GetError() if outcome else "?"))
    pair = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', entity_id, material_type).GetValue()
    set_outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'SetComponentProperty', pair,
        MATERIAL_ASSET_PROPERTY, material_asset_id)
    if not set_outcome or not set_outcome.IsSuccess():
        raise PrefabBuildError("%s: setting %s failed"
                               % (entity_name, MATERIAL_ASSET_PROPERTY))


def create_entities(document, asset_ids_by_guid, report, level_root_id, log=None):
    """Create one editor entity per manifest entity. Returns {manifest id: EntityId}."""
    import azlmbr.bus as bus
    import azlmbr.editor as editor

    from . import manifest_io

    def emit(message):
        if log is not None:
            log(message)

    mesh_type = resolve_component_type(MESH_COMPONENT_NAME)
    ordered = manifest_io.entities_parents_first(document)
    children_count = {}
    for item in document["entities"]:
        if item["parent_id"]:
            children_count[item["parent_id"]] = children_count.get(item["parent_id"], 0) + 1

    created = {}
    for item in ordered:
        parent_entity_id = created.get(item["parent_id"]) if item["parent_id"] else None
        if parent_entity_id is None:
            # Manifest roots hang off the level root, which is at identity, so
            # their local transform is also their world transform.
            parent_entity_id = level_root_id

        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', parent_entity_id)
        if not entity_id or not entity_id.IsValid():
            raise PrefabBuildError("CreateNewEntity failed for " + item["name"])
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, item["name"])
        created[item["id"]] = entity_id

        _apply_transform(entity_id, item["transform"]["local"], report, item["name"],
                         children_count.get(item["id"], 0) > 0)

        mesh = item.get("mesh")
        if mesh is not None:
            asset_id = asset_ids_by_guid.get(mesh["asset_guid"])
            if asset_id is None:
                raise PrefabBuildError(
                    "%s references mesh asset %s, which was never waited for"
                    % (item["name"], mesh["asset_guid"]))
            _add_mesh_component(entity_id, mesh_type, asset_id, item["name"])
        elif item["kind"] == "static_mesh":
            report.warn("MESH_MISSING", item["name"],
                        "entity is a static mesh actor but carries no mesh reference")

        emit("  %-22s kind=%-12s parent=%s"
             % (item["name"], item["kind"], item["parent_id"] and "yes" or "-"))

    return created


def create_prefab_in_memory(root_entity_ids, prefab_path):
    """Author the prefab in memory. Returns the container EntityId."""
    import os

    import azlmbr.bus as bus
    import azlmbr.legacy.general as general
    import azlmbr.prefab as prefab

    os.makedirs(os.path.dirname(prefab_path), exist_ok=True)
    # CreatePrefabInMemory can throw an opaque "unknown exception" when a file
    # with a different template already exists at the target path. Re-import
    # over an old prefab is the normal workflow, so clear it first. M10's
    # incremental re-import replaces this with matched updates.
    if os.path.exists(prefab_path):
        os.remove(prefab_path)
    create = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'CreatePrefabInMemory', root_entity_ids, prefab_path)
    if create is None or not create.IsSuccess():
        reason = "no outcome returned"
        if create is not None:
            try:
                reason = repr(create.GetError())
            except Exception:
                reason = "outcome reported failure with no readable error"
        raise PrefabBuildError("CreatePrefabInMemory failed: " + reason)
    general.idle_wait_frames(30)
    return create.GetValue()


def flush_template_to_disk(prefab_path, marker_entity_name, log=None):
    """Write the in-memory template to `prefab_path`.

    See the module docstring: the scan is not a shortcut, it is the only
    reflected route to the on-disk JSON in 26.05.
    """
    import json

    import azlmbr.bus as bus
    import azlmbr.prefab as prefab

    def emit(message):
        if log is not None:
            log(message)

    template_json = None
    for template_id in range(1, TEMPLATE_ID_SCAN_LIMIT):
        outcome = prefab.PrefabLoaderScriptingBus(
            bus.Broadcast, 'SaveTemplateToString', template_id)
        if not outcome or not outcome.IsSuccess():
            continue
        text = outcome.GetValue()
        if '"%s"' % marker_entity_name in text:
            template_json = text
            break
    if template_json is None:
        raise PrefabBuildError(
            "no in-memory template contained entity %r after CreatePrefabInMemory"
            % marker_entity_name)

    document = json.loads(template_json)
    if "ContainerEntity" not in document or "Entities" not in document:
        raise PrefabBuildError(
            "serialized template is not in the prefab file format (keys: %r)"
            % sorted(document.keys()))

    with open(prefab_path, "w") as handle:
        handle.write(template_json)
    emit("  wrote %s (%d bytes, %d entities)"
         % (prefab_path, len(template_json), len(document.get("Entities", {}))))
    return prefab_path
