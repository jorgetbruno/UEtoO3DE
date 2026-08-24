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

import os

# AzToolsFramework::Components::EditorNonUniformScaleComponent, from
# Code/Framework/AzToolsFramework/AzToolsFramework/ToolsComponents/
# EditorNonUniformScaleComponent.h in the 26.05 SDK.
NON_UNIFORM_SCALE_TYPE_ID = "{2933FB4F-B3DA-4CD1-8106-F37300730777}"

MESH_COMPONENT_NAME = "Mesh"
MODEL_ASSET_PROPERTY = "Controller|Configuration|Model Asset"
MATERIAL_COMPONENT_NAME = "Material"
# Verified live: probe_m4_material — assigns and reads back an azmaterial.
MATERIAL_ASSET_PROPERTY = "Default Material|Material Asset"
# Per-slot rows of the Material component. The technique is o3dimport's
# (lumbermixalot): FindMaterialAssignmentId maps a slot label to a stable id,
# and the row whose "Material Slot Stable Id" matches is the one to set.
MODEL_SLOT_STABLE_ID = "Model Materials|[%d]|Material Slot Stable Id"
MODEL_SLOT_ASSET = "Model Materials|[%d]|Material Asset"
# LOD wildcard for FindMaterialAssignmentId (u32 -1).
NO_LOD = 0xFFFFFFFF
# The Model Materials rows exist only once the entity's model asset has
# streamed in; how long wait_for_model_rows waits for that, total frames.
MODEL_READY_WAIT_FRAMES = 600
# Poll GRANULARITY, not the budget, and now the granularity of ONE shared wait
# rather than of 1217 private ones.
#
# Its history is the shape of both mistakes this file has made. It was 30, and
# because every multi-material entity was unready on its first check and ready
# well inside one quantum, each was charged the whole 30 -- 1217 x 30 = 36,510
# frames, ~95% of the materials phase and the largest single cost in the whole
# import. Dropping it to 2 removed the rounding-up and left 1217 x 2 = 2434.
# What it did NOT remove was the 1217: the loop still paid a fresh wait per
# entity for a tick that advances every entity at once. `wait_for_model_rows`
# does the waiting once for the level, so this number is now multiplied by the
# number of ROUNDS (typically one) rather than by the number of entities.
# Overridable to re-measure without editing code.
MODEL_READY_POLL_FRAMES = int(os.environ.get("UEO3DE_MODEL_POLL_FRAMES", "2"))

# Sub-phase accounting for material assignment, which the M11 phase timings
# showed to be HALF the wall clock of a real import (408.8 s of 806 s on a
# 2905-entity level) -- against a committed document that had attributed the
# cost to the collider bakes, which turned out to be 1.3%. These figures exist
# so the next attribution is measured too, rather than being the next guess.
# Reset per import by `reset_material_stats()`.
MATERIAL_STATS = {
    "material_add_component_s": 0.0,   # resolve type + AddComponentsOfType
    "material_model_wait_s": 0.0,      # polling for the model's slot rows
    "material_slot_set_s": 0.0,        # FindMaterialAssignmentId + SetProperty
    "material_default_set_s": 0.0,     # the single-material fast path
    "material_wait_frames": 0,         # frames burned polling
    "material_entities_that_waited": 0,
    # The wait loop splits two ways, and the split decides what to fix. Frames
    # are shared -- idling for entity A advances every other entity's stream
    # too -- but the PROBE calls are strictly per entity and buy nothing for
    # anyone else. If the probes dominate, batching the wait is the fix; if the
    # frames dominate, the quantum is. Guessing between those two is what cost
    # this project its last two performance rounds.
    "material_wait_probe_s": 0.0,      # GetComponentProperty inside the loop
    "material_wait_idle_s": 0.0,       # idle_wait_frames inside the loop
    "material_wait_probes": 0,         # how many probe calls, total
}


def reset_material_stats():
    # Reset each key to a zero of its OWN type rather than by guessing from the
    # name: the previous version keyed off a suffix list ("frames", "waited"),
    # so the first counter added that did not end in one of those came back as
    # 0.0 and reported itself as a float count.
    for key, value in MATERIAL_STATS.items():
        MATERIAL_STATS[key] = type(value)()

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


def detach_conflicting_instances(project_root, level_name, prefab_path, log=None):
    """Remove instances of `prefab_path` from the level FILE. Returns a count.

    Pure file I/O, and it must run BEFORE the level is opened.

    The scratch level is a prefab like any other, so instantiating an imported
    level into it and saving leaves a nested instance behind:

        DefaultLevel.prefab -> Instances -> {Source: "Prefabs/L_Overview.prefab"}

    Re-importing that same level then opens a level that already contains the
    previous import (its entities stream in, all of them), and
    `create_prefab_in_memory` deletes and recreates the very file that
    instance points at. `CreatePrefabInMemory` answers with an opaque "unknown
    exception".

    That is what this project spent three rounds of settle-tuning on. The
    failure *looked* like an asset-streaming race -- it tracked scene size, and
    adding one directional light was enough to tip a level that had been
    passing -- so it was diagnosed as one twice. It is not: with the stale
    instance gone the save succeeds immediately, and with it present no amount
    of settling helps (measured: 900, 1800 and 3600 frames all fail).

    Only instances of THIS prefab are removed; anything else in the level is
    left alone, because `CreatePrefabInMemory` serializes just the root
    entities it is handed and unrelated level content cannot reach the file.
    """
    import json

    def emit(message):
        if log is not None:
            log(message)

    level_file = os.path.join(project_root, "Levels", level_name,
                              level_name + ".prefab")
    if not os.path.exists(level_file):
        return 0

    # The instance's Source is project-relative with forward slashes.
    relative = os.path.relpath(prefab_path, project_root).replace("\\", "/")

    try:
        with open(level_file, "r") as handle:
            document = json.load(handle)
    except (ValueError, IOError) as exc:
        emit("  could not read the level file (%r); continuing" % (exc,))
        return 0

    instances = document.get("Instances") or {}
    doomed = [key for key, value in instances.items()
              if str(value.get("Source", "")).lower() == relative.lower()]
    if not doomed:
        return 0

    for key in doomed:
        del instances[key]
    if instances:
        document["Instances"] = instances
    else:
        document.pop("Instances", None)

    with open(level_file, "w") as handle:
        json.dump(document, handle, indent=4)
    emit("  removed %d stale instance(s) of %s from %s"
         % (len(doomed), relative, level_name))
    return len(doomed)


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


def _add_material_component(entity_id, entity_name):
    """Add (or fetch) the Material component; returns its component pair."""
    import time

    import azlmbr.bus as bus
    import azlmbr.editor as editor

    started = time.perf_counter()
    material_type = resolve_component_type(MATERIAL_COMPONENT_NAME)
    outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'AddComponentsOfType', entity_id, [material_type])
    if not outcome or not outcome.IsSuccess():
        raise PrefabBuildError("%s: AddComponentsOfType(Material) failed: %s"
                               % (entity_name, outcome.GetError() if outcome else "?"))
    pair = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', entity_id, material_type).GetValue()
    MATERIAL_STATS["material_add_component_s"] += time.perf_counter() - started
    return pair


def _get_property(pair, path):
    """(found, value) for a component property; found=False for missing rows."""
    import azlmbr.bus as bus
    import azlmbr.editor as editor

    outcome = editor.EditorComponentAPIBus(bus.Broadcast, 'GetComponentProperty',
                                           pair, path)
    if outcome and outcome.IsSuccess():
        return True, outcome.GetValue()
    return False, None


def assign_material(entity_id, material_asset_id, entity_name):
    """Add a Material component and set the DEFAULT slot's material (M4).

    Right for the entities that use it: every model slot without an explicit
    per-slot override inherits the default slot, so a model whose mapped slots
    all share one material is fully covered without touching the (asset-load-
    dependent) Model Materials rows. Multi-material models go through
    `begin_material_slots` / `finish_material_slots` instead.
    """
    import time

    import azlmbr.bus as bus
    import azlmbr.editor as editor

    pair = _add_material_component(entity_id, entity_name)
    started = time.perf_counter()
    set_outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'SetComponentProperty', pair,
        MATERIAL_ASSET_PROPERTY, material_asset_id)
    if not set_outcome or not set_outcome.IsSuccess():
        raise PrefabBuildError("%s: setting %s failed"
                               % (entity_name, MATERIAL_ASSET_PROPERTY))
    MATERIAL_STATS["material_default_set_s"] += time.perf_counter() - started


def begin_material_slots(entity_id, entity_name):
    """Add the Material component for a per-slot assignment. Returns its pair.

    Half of a two-pass protocol; `wait_for_model_rows` and
    `finish_material_slots` are the other half. See `wait_for_model_rows` for
    why the passes are split.
    """
    return _add_material_component(entity_id, entity_name)


def wait_for_model_rows(pairs):
    """Idle until every pair's Model Materials rows exist. Returns the stragglers.

    ONE wait for the whole level, not one per entity, and that is the entire
    point. Idling is shared: a frame spent waiting for entity A advances every
    other entity's component too. Waiting per entity buys the same frame over
    and over.

    The old per-entity loop made that concrete. On L_Showcase every one of the
    1217 multi-slot entities was unready on its first probe and ready after
    exactly one quantum -- never two, never zero, including the last entity
    processed, long after every model had finished streaming. That is not a
    stream to wait out; it is a tick that has to elapse between adding the
    component and its rows appearing. One tick, shared, is enough for all of
    them, so the cost should be one wait rather than 1217 x 2 = 2434 frames.

    The probes are free (measured: 0.0 s against 1.1 s of idling on a
    400-entity sample), so probing every pair on every round costs nothing and
    keeps the bound honest per entity rather than in aggregate.

    Returns the set of INDICES into `pairs` still not ready when
    MODEL_READY_WAIT_FRAMES ran out -- each is an entity whose caller must fall
    back. Indices rather than the pairs themselves: an EntityComponentIdPair is
    an engine proxy object with no promised hashing or equality, so a caller
    matching stragglers by identity would be relying on something the binding
    never offered.
    """
    import time

    import azlmbr.legacy.general as general

    def ready(pair):
        probe_started = time.perf_counter()
        found, _value = _get_property(pair, MODEL_SLOT_STABLE_ID % 0)
        MATERIAL_STATS["material_wait_probe_s"] += time.perf_counter() - probe_started
        MATERIAL_STATS["material_wait_probes"] += 1
        return found

    wait_started = time.perf_counter()
    pending = [index for index, pair in enumerate(pairs) if not ready(pair)]
    MATERIAL_STATS["material_entities_that_waited"] += len(pending)

    waited = 0
    while pending and waited < MODEL_READY_WAIT_FRAMES:
        idle_started = time.perf_counter()
        general.idle_wait_frames(MODEL_READY_POLL_FRAMES)
        MATERIAL_STATS["material_wait_idle_s"] += time.perf_counter() - idle_started
        waited += MODEL_READY_POLL_FRAMES
        pending = [index for index in pending if not ready(pairs[index])]

    MATERIAL_STATS["material_model_wait_s"] += time.perf_counter() - wait_started
    MATERIAL_STATS["material_wait_frames"] += waited
    return set(pending)


def finish_material_slots(pair, entity_id, assignments, entity_name, report,
                          ready=True):
    """Per-slot assignment by label, o3dimport's technique (M4 slot fidelity).

    `assignments` is an ordered list of (label, material_asset_id) with unique
    labels; the label is the UE material asset name, which is the FBX material
    name, which is the azmodel slot label (`mesh_export` docstring).

    `ready=False` means `wait_for_model_rows` gave up on this entity: the first
    material goes on the default slot so it is never worse off than the
    flattened behaviour this replaces. Returns the number of slots set.
    """
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.render as render

    import time

    if not ready:
        report.warn("MAT_MODEL_NOT_READY", entity_name,
                    "Model Materials rows never appeared within %d frames; "
                    "assigned %r on the default slot instead"
                    % (MODEL_READY_WAIT_FRAMES, assignments[0][0]))
        editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', pair,
            MATERIAL_ASSET_PROPERTY, assignments[0][1])
        return 0

    slot_started = time.perf_counter()
    # Stable id per row, once; rows are as many as the model has unique slots.
    row_stable_ids = []
    for row in range(len(assignments) + 8):
        found, value = _get_property(pair, MODEL_SLOT_STABLE_ID % row)
        if not found:
            break
        row_stable_ids.append(value)

    assigned = 0
    used_rows = set()
    unmatched = []
    for label, asset_id in assignments:
        assignment_id = render.MaterialComponentRequestBus(
            bus.Event, 'FindMaterialAssignmentId', entity_id, NO_LOD, label)
        stable_id = getattr(assignment_id, "materialSlotStableId", None)
        row = None
        if stable_id is not None:
            for index, row_stable in enumerate(row_stable_ids):
                if row_stable == stable_id:
                    row = index
                    break
        if row is None:
            unmatched.append((label, asset_id))
            continue
        set_outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', pair,
            MODEL_SLOT_ASSET % row, asset_id)
        if not set_outcome or not set_outcome.IsSuccess():
            raise PrefabBuildError("%s: setting %s failed"
                                   % (entity_name, MODEL_SLOT_ASSET % row))
        used_rows.add(row)
        assigned += 1

    # A mesh asset slot with NO default material exports with no material
    # name, so its azmodel slot label is unknowable from the manifest. When
    # exactly one assignment failed to match and exactly one row is
    # unclaimed, the pairing is forced -- assign by elimination rather than
    # leaving the section on the model default (measured: temple-roof
    # undersides whose asset slot is empty and the actor overrides it).
    free_rows = [index for index in range(len(row_stable_ids))
                 if index not in used_rows]
    if len(unmatched) == 1 and len(free_rows) == 1:
        label, asset_id = unmatched.pop()
        row = free_rows[0]
        set_outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', pair,
            MODEL_SLOT_ASSET % row, asset_id)
        if not set_outcome or not set_outcome.IsSuccess():
            raise PrefabBuildError("%s: setting %s failed"
                                   % (entity_name, MODEL_SLOT_ASSET % row))
        report.warn("MAT_SLOT_BY_ELIMINATION", entity_name,
                    "material %r matched no slot label; assigned to the only "
                    "unclaimed model slot (row %d)" % (label, row))
        assigned += 1

    for label, _asset_id in unmatched:
        if not free_rows:
            # Every model slot is claimed and this material still matched
            # nothing: the mesh asset lists the slot, but no LOD0 triangle
            # uses it, so the bake dropped it (measured: temple roofs whose
            # 'Wall' slot owns no visible geometry). Nothing to assign; the
            # level looks exactly as it should.
            report.warn("MAT_SLOT_UNUSED", entity_name,
                        "material %r maps to a slot no render triangle uses; "
                        "the model's %d slots are all assigned"
                        % (label, len(row_stable_ids)))
        else:
            report.warn("MAT_SLOT_UNMATCHED", entity_name,
                        "model has no slot labelled %r (rows: %d)"
                        % (label, len(row_stable_ids)))
    MATERIAL_STATS["material_slot_set_s"] += time.perf_counter() - slot_started
    return assigned


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
    # A frame correction (skeletal Rz180, decal Ry(-90)) compensates for how
    # ONE entity's product geometry was baked, but O3DE composes
    # child_world = parent_world * child_local, so it would also swing every
    # descendant around that entity. Corrections are recorded here and
    # undone on each child's own local transform (skel_build.
    # counter_correct_child) -- the same hazard the exporter already guards
    # for mirror folds (DIVERGENCES.md: "folding rewrites the parent frame
    # out from under its children").
    corrections = {}
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

        from . import skel_build
        local = item["transform"]["local"]
        parent_correction = corrections.get(item["parent_id"])
        if parent_correction is not None:
            local = skel_build.counter_correct_child(
                local, parent_correction["quat"], parent_correction["ratio"])

        if item.get("skeletal") is not None:
            # Skeletal products carry LaneA * Rz180 (native FBX export, no
            # bake stage); the compensation is one local yaw-180 composed
            # into the rotation (skel_build, LANE_B.md M8).
            local = skel_build.corrected_local_transform(local)
            corrections[item["id"]] = {"quat": skel_build.RZ180, "ratio": 1.0}
        if item.get("decal") is not None:
            # Atom decals project along local -Z over a scaled unit box; UE
            # projected along local +X with half-extent sizes. One local
            # Ry(-90) plus the extent scale remaps the volume (decal_build).
            from . import decal_build
            before = local
            local = decal_build.corrected_local_transform(
                local, item["decal"]["half_extents_m"])
            # Only a UNIFORM scale reaches children in O3DE; a non-uniform
            # one lands on EditorNonUniformScaleComponent, which does not
            # propagate (XFORM_NONUNIFORM_SCALE_NOT_INHERITED).
            ratio = 1.0
            if _is_uniform(local["scale"]) and before["scale"][0]:
                ratio = local["scale"][0] / float(before["scale"][0])
            corrections[item["id"]] = {"quat": decal_build.RY_MINUS_90,
                                       "ratio": ratio}
        _apply_transform(entity_id, local, report, item["name"],
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
        elif item["kind"] == "skeletal_mesh" and item.get("skeletal") is None:
            report.warn("MESH_MISSING", item["name"],
                        "entity is a skeletal mesh actor but carries no "
                        "skeletal reference")

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
    # with a different template already exists at the target path, so the old
    # file must move out of the way -- but MOVED, not deleted. This used to
    # `os.remove`, and a create or flush failure after that point had
    # destroyed the user's prefab (hand edits included) while leaving the
    # ledger behind, so the NEXT import silently rebuilt from the manifest as
    # if nothing had existed. The backup is removed only after the flush
    # actually writes the replacement; until then every failure path leaves
    # `<prefab>.prev` to restore from.
    backup = prefab_path + ".prev"
    if os.path.exists(prefab_path):
        os.replace(prefab_path, backup)
    create = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'CreatePrefabInMemory', root_entity_ids, prefab_path)
    if create is None or not create.IsSuccess():
        reason = "no outcome returned"
        if create is not None:
            try:
                reason = repr(create.GetError())
            except Exception:
                reason = "outcome reported failure with no readable error"
        raise PrefabBuildError(
            "CreatePrefabInMemory failed: " + reason
            + (". The previous prefab was preserved at %s" % backup
               if os.path.exists(backup) else ""))
    general.idle_wait_frames(30)
    return create.GetValue()


COOKED_MESH_EXTENSIONS = (".pxmesh", ".joltmesh")


def _has_cooked_mesh_reference(node):
    """Does this serialized subtree carry a real cooked-physics-mesh reference?

    A real reference is `{"assetId": {"guid": <non-zero>}, "assetHint":
    "....pxmesh"/"....joltmesh"}`. The hint check is not decoration: a mesh
    collider's subtree can carry OTHER asset references (physics material
    slots), and any-non-null-asset would pass on a component whose actual mesh
    slot is empty.
    """
    if isinstance(node, dict):
        asset_id = node.get("assetId")
        hint = node.get("assetHint")
        if (isinstance(asset_id, dict) and isinstance(hint, str)
                and hint.endswith(COOKED_MESH_EXTENSIONS)):
            guid = str(asset_id.get("guid", ""))
            if guid.strip("{}0-"):
                return True
        return any(_has_cooked_mesh_reference(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_cooked_mesh_reference(value) for value in node)
    return False


# Back-compat alias; the check is no longer PhysX-only.
_has_pxmesh_reference = _has_cooked_mesh_reference


def collider_verification(prefab_path, jolt_mesh_is_asset_based=False):
    """Entity names whose mesh collider reached the file with NO geometry.

    Pure file I/O -- the saved prefab is the only place the truth is visible.
    ONE parse serves both backends' checks (the Jolt prefab this runs on is
    hundreds of MB; parsing it twice would double the cost of verification):

      `unbaked` -- Jolt (`EditorJoltMeshColliderComponent`). The collider
      bakes its geometry on the component's tick and serializes it as
      `ShapeConfiguration.CookedData`. Serialize before the bake finishes and
      the component is still there, fully configured, with no cooked data at
      all: a collider that collides with nothing, in a file that saved
      without error. Nothing else in the importer can see this. The
      `mesh_colliders` counter read 2501 on both a run that serialized 2501
      bakes and a run that serialized 2486 -- measured, not hypothetical.
      Four probes went looking for a signal to poll instead and found none;
      the check has to happen after the write, on the bytes.

      `missing_asset` -- the ASSET-BASED colliders on either backend
      (`EditorMeshColliderComponent` on PhysX, `EditorJoltMeshColliderComponent`
      once the Jolt gem moved its mesh colliders to `.joltmesh` products). No
      bake is involved -- the geometry lives in the product -- but the Shape
      enum lesson from M3b applies: a property write the editor accepted is
      not proof of what serialized. A component whose asset reference did not
      reach the file collides with nothing, silently, so the reference is
      checked on the bytes too.

    HEALTH is judged from evidence, and either kind of evidence counts: a
    non-empty `CookedData` or a real cooked-mesh asset reference means the
    collider has geometry, whichever component it came from. That part needs
    no version knowledge and cannot be fooled by the rename.

    CLASSIFYING A FAILURE does need it, and cannot be recovered from the bytes:
    the gem's rename moved `EditorJoltMeshColliderComponent` from baking to
    referencing while keeping the name, and a collider that has neither
    geometry nor an asset serializes the same either way (AZ JSON omits
    defaults, so the empty case is an absent `ShapeConfiguration` in both).
    So the caller says which world it authored in -- `jolt_mesh_is_asset_based`
    comes straight from the adapter's own resolve-time detection -- and the
    default is the pre-rename behaviour, which is what every prefab written
    before the gem changed actually contains.
    """
    import json

    result = {"unbaked": [], "missing_asset": []}
    if not os.path.isfile(prefab_path):
        return result
    with open(prefab_path, "r") as handle:
        document = json.load(handle)
    for entity in (document.get("Entities") or {}).values():
        for component in (entity.get("Components") or {}).values():
            if not isinstance(component, dict):
                continue
            type_name = str(component.get("$type", ""))
            if "MeshCollider" not in type_name:
                continue
            shape = component.get("ShapeConfiguration")
            cooked = shape.get("CookedData") if isinstance(shape, dict) else None
            if isinstance(cooked, str) and cooked:
                continue                       # a bake that reached the file
            if _has_cooked_mesh_reference(component):
                continue                       # an asset reference that did
            if "Jolt" not in type_name:
                bakes = False                  # PhysX: asset-based, always
            elif "Baked" in type_name:
                bakes = True                   # the renamed bake component
            else:
                bakes = not jolt_mesh_is_asset_based
            (result["unbaked"] if bakes else result["missing_asset"]).append(
                entity.get("Name"))
    result["unbaked"].sort()
    result["missing_asset"].sort()
    return result


def unbaked_colliders(prefab_path):
    """Back-compat wrapper: the Jolt half of `collider_verification`."""
    return collider_verification(prefab_path)["unbaked"]


def snapshot_template_ids():
    """Every template id that currently serializes. Take BEFORE creating.

    `flush_template_to_disk` finds the new prefab's template by scanning, and
    "first template containing the level-root name" is ambiguous the moment a
    session holds more than one import of the same level: every chunk of a
    chunked import carries the SAME root name, so chunk 2's flush could find
    chunk 1's template and write chunk 1's JSON to chunk 2's path. A snapshot
    taken before CreatePrefabInMemory lets the flush scan only what is NEW.
    """
    import azlmbr.bus as bus
    import azlmbr.prefab as prefab

    known = set()
    for template_id in range(1, TEMPLATE_ID_SCAN_LIMIT):
        outcome = prefab.PrefabLoaderScriptingBus(
            bus.Broadcast, 'SaveTemplateToString', template_id)
        if outcome and outcome.IsSuccess():
            known.add(template_id)
    return known


def flush_template_to_disk(prefab_path, marker_entity_name, log=None,
                           known_template_ids=None):
    """Write the in-memory template to `prefab_path`.

    See the module docstring: the scan is not a shortcut, it is the only
    reflected route to the on-disk JSON in 26.05. `known_template_ids` is the
    `snapshot_template_ids()` result from before CreatePrefabInMemory; ids in
    it are skipped so a same-named template from an EARLIER import in this
    session can never be flushed into this import's file.
    """
    import json

    import azlmbr.bus as bus
    import azlmbr.prefab as prefab

    def emit(message):
        if log is not None:
            log(message)

    template_json = None
    for template_id in range(1, TEMPLATE_ID_SCAN_LIMIT):
        if known_template_ids and template_id in known_template_ids:
            continue
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
    # The replacement exists on disk; the pre-import backup has served its
    # purpose (see create_prefab_in_memory -- it exists so no failure between
    # rename and THIS line costs the user their file).
    backup = prefab_path + ".prev"
    if os.path.exists(backup):
        os.remove(backup)
    emit("  wrote %s (%d bytes, %d entities)"
         % (prefab_path, len(template_json), len(document.get("Entities", {}))))
    return prefab_path
