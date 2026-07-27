"""
ue_level.py — the UE-side level walk that produces `manifest.json` (plan M1).

This is the only module in the package that imports `unreal`; everything it
computes is handed to the pure modules (`lane_a`, `naming`, `manifest`,
`warnings`) so the result can be re-derived and asserted without an editor.

Every API used here was verified against UE 5.8 by `Tests/ue/probe_m1_apis*.py`
before a line of this file was written; the probe output is committed under
`Tests/ue/results/`. The non-obvious findings, all of which this module
depends on:

  * `dir()` does not list every readable UPROPERTY. `BodyInstance.override_mass`
    and `AWorldSettings.WorldPartition` both resolve through
    `get_editor_property()` while being absent from `dir()`. Never probe
    availability with `hasattr`.
  * World Partition: UWorld exposes neither `persistent_level` nor
    `world_partition`, and the .umap carries no asset-registry tag for it.
    The working detector is `world.get_world_settings()` ->
    `get_editor_property("world_partition")`, which returns None on a
    non-partitioned level and a UWorldPartition object otherwise.
  * `KBoxElem`/`KSphereElem`/`KConvexElem` print as `{}` and expose no field
    attributes, but their named properties read fine. `KConvexElem`'s vertex
    data and element box are protected, so they are recovered from
    `export_text()`, which UE emits deterministically.
  * `KBoxElem.x/y/z` are FULL extents, not half-extents (the 100 cm engine
    cube reports 100).
  * `LightComponentBase.light_color` is an FColor in sRGB, not linear.
"""

import os

import unreal

from . import lane_a
from . import manifest as manifest_module
from . import naming
from .warnings import Warnings


class ExportAborted(Exception):
    """Raised when the export cannot honestly continue (see plan M1)."""


# ---------------------------------------------------------------------------
# small readers — UE structs expose fields inconsistently, so read defensively
# ---------------------------------------------------------------------------

def _field(obj, name, default=None):
    """Read a struct/object field, trying attribute then UPROPERTY."""
    try:
        return getattr(obj, name)
    except Exception:
        pass
    try:
        return obj.get_editor_property(name)
    except Exception:
        return default


def _vec3(vector):
    return [float(_field(vector, "x", 0.0)),
            float(_field(vector, "y", 0.0)),
            float(_field(vector, "z", 0.0))]


def _quat_xyzw(quat):
    return [float(_field(quat, "x", 0.0)),
            float(_field(quat, "y", 0.0)),
            float(_field(quat, "z", 0.0)),
            float(_field(quat, "w", 1.0))]


def _rotator_xyzw(rotator):
    return _quat_xyzw(rotator.quaternion())


def _enum_name(value, default="unknown"):
    name = getattr(value, "name", None)
    if name is None:
        return default
    return str(name).lower()


def _name_str(value):
    return str(value) if value is not None else ""


def _srgb_to_linear(channel):
    """Standard sRGB EOTF; UE stores light colors as sRGB-encoded FColor."""
    c = channel / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


# ---------------------------------------------------------------------------
# transforms
# ---------------------------------------------------------------------------

def _transform_from_parts(location_cm, quat_xyzw, scale, subject, warnings):
    converted_scale, negative_axes = lane_a.convert_scale(scale)
    if negative_axes:
        warnings.add("XFORM_NEGATIVE_SCALE", subject,
                     "negative on axis " + ",".join(negative_axes))
    return {
        "translation": lane_a.convert_position(location_cm),
        "rotation": lane_a.convert_quat(quat_xyzw),
        "scale": converted_scale,
    }


def _actor_transforms(actor, subject, warnings):
    """World transform, plus the transform relative to the attach parent."""
    world = _transform_from_parts(
        _vec3(actor.get_actor_location()),
        _quat_xyzw(actor.get_actor_rotation().quaternion()),
        _vec3(actor.get_actor_scale3d()),
        subject, warnings)

    root = actor.root_component
    if root is None:
        local = dict(world)
    else:
        local = _transform_from_parts(
            _vec3(_field(root, "relative_location")),
            _rotator_xyzw(_field(root, "relative_rotation")),
            _vec3(_field(root, "relative_scale3d")),
            subject, warnings)
    return {"world": world, "local": local}


# ---------------------------------------------------------------------------
# collision geometry (read from the mesh asset, per plan M3)
# ---------------------------------------------------------------------------

_DEGENERATE_M = 1e-4


def _flag_degenerate(shape, values, subject, warnings):
    if any(abs(v) < _DEGENERATE_M for v in values):
        warnings.add("PHYS_DEGENERATE_SHAPE", subject,
                     shape + " has a dimension below %g m" % _DEGENERATE_M)


def _parse_convex_export_text(text):
    """Recover a KConvexElem's vertex count and local AABB from export_text().

    VertexData and ElemBox are protected UPROPERTYs, so this is the only route
    to them from Python. The format is UE's own struct serializer and is
    stable; a parse failure is reported, never guessed around.
    """
    vertex_count = 0
    vertex_start = text.find("VertexData=(")
    index_start = text.find(",IndexData=")
    if vertex_start != -1 and index_start > vertex_start:
        vertex_count = text.count("(X=", vertex_start, index_start)

    box_start = text.find("ElemBox=(")
    if box_start == -1:
        return vertex_count, None, None

    segment = text[box_start:text.find(")", text.find("Max=(", box_start)) + 1]

    def _corner(label):
        at = segment.find(label + "=(")
        if at == -1:
            return None
        body = segment[at + len(label) + 2:segment.find(")", at)]
        parts = {}
        for piece in body.split(","):
            key, _sep, value = piece.partition("=")
            parts[key.strip()] = float(value)
        if not {"X", "Y", "Z"} <= set(parts):
            return None
        return [parts["X"], parts["Y"], parts["Z"]]

    return vertex_count, _corner("Min"), _corner("Max")


def _converted_aabb(min_cm, max_cm):
    """Convert a UE AABB and re-derive min/max.

    Lane A negates Y, so the UE minimum Y becomes the O3DE maximum Y. Taking
    the corners across without re-sorting produces an inverted box that reads
    as valid everywhere downstream.
    """
    a = lane_a.convert_position(min_cm)
    b = lane_a.convert_position(max_cm)
    return ([min(a[i], b[i]) for i in range(3)],
            [max(a[i], b[i]) for i in range(3)])


def _collision_shapes(mesh, subject, warnings):
    """UE simple collision (UStaticMesh::BodySetup->AggGeom) -> shape list."""
    body_setup = _field(mesh, "body_setup")
    if body_setup is None:
        return "none", []
    agg = _field(body_setup, "agg_geom")
    if agg is None:
        return "none", []

    shapes = []

    for elem in _field(agg, "box_elems", []) or []:
        half = [lane_a.convert_length(float(_field(elem, axis, 0.0)) * 0.5)
                for axis in ("x", "y", "z")]
        _flag_degenerate("box collider", half, subject, warnings)
        shapes.append({
            "type": "box",
            "half_extents": half,
            "offset": lane_a.convert_position(_vec3(_field(elem, "center"))),
            "rotation": lane_a.convert_quat(_rotator_xyzw(_field(elem, "rotation"))),
        })

    for elem in _field(agg, "sphere_elems", []) or []:
        radius = lane_a.convert_length(float(_field(elem, "radius", 0.0)))
        _flag_degenerate("sphere collider", [radius], subject, warnings)
        shapes.append({
            "type": "sphere",
            "radius": radius,
            "offset": lane_a.convert_position(_vec3(_field(elem, "center"))),
        })

    for elem in _field(agg, "sphyl_elems", []) or []:
        radius = lane_a.convert_length(float(_field(elem, "radius", 0.0)))
        # UE's sphyl "length" is the cylindrical segment only; total height is
        # length + 2*radius. Both are emitted so no consumer has to guess.
        segment = lane_a.convert_length(float(_field(elem, "length", 0.0)))
        _flag_degenerate("capsule collider", [radius], subject, warnings)
        shapes.append({
            "type": "capsule",
            "radius": radius,
            "segment_height": segment,
            "total_height": segment + 2.0 * radius,
            "offset": lane_a.convert_position(_vec3(_field(elem, "center"))),
            "rotation": lane_a.convert_quat(_rotator_xyzw(_field(elem, "rotation"))),
        })

    for elem in _field(agg, "convex_elems", []) or []:
        try:
            text = elem.export_text()
        except Exception as exc:
            warnings.add("PHYS_SHAPE_UNSUPPORTED", subject,
                         "convex element unreadable: " + str(exc)[:120])
            continue
        vertex_count, min_cm, max_cm = _parse_convex_export_text(text)
        if min_cm is None or max_cm is None:
            warnings.add("PHYS_SHAPE_UNSUPPORTED", subject,
                         "convex element has no parseable ElemBox")
            continue
        aabb_min, aabb_max = _converted_aabb(min_cm, max_cm)
        shapes.append({
            "type": "convex",
            "vertex_count": vertex_count,
            "aabb_min": aabb_min,
            "aabb_max": aabb_max,
        })

    for prop_name, label in (("tapered_capsule_elems", "tapered capsule"),
                             ("level_set_elems", "level set")):
        for _elem in _field(agg, prop_name, []) or []:
            warnings.add("PHYS_SHAPE_UNSUPPORTED", subject,
                         label + " collision has no v1 mapping")

    if not shapes:
        warnings.add("PHYS_NO_SIMPLE_COLLISION", subject,
                     "BodySetup carries no simple collision primitives")
        return "none", []
    return "simple", shapes


# ---------------------------------------------------------------------------
# asset table
# ---------------------------------------------------------------------------

_EXTENSIONS = {"static_mesh": "fbx", "material": "material"}


class AssetTable:
    """Deduplicates referenced assets by GUID and claims their O3DE paths."""

    def __init__(self, warnings):
        self._entries = {}
        self._registry = naming.PathRegistry()
        self._warnings = warnings

    def _claim(self, ue_path, kind):
        try:
            stem = self._registry.claim(ue_path)
        except naming.PathCollisionError as exc:
            self._warnings.add("ASSET_PATH_COLLISION", exc.stem,
                               "%s vs %s" % (exc.first, exc.second))
            raise ExportAborted(str(exc))
        return naming.with_extension(stem, _EXTENSIONS[kind])

    def add_material(self, material):
        ue_path = unreal.SystemLibrary.get_path_name(material)
        guid = naming.asset_guid(ue_path)
        if guid not in self._entries:
            self._entries[guid] = {
                "guid": guid,
                "kind": "material",
                "ue_path": naming.package_path(ue_path),
                "name": material.get_name(),
                "o3de_relative_path": self._claim(ue_path, "material"),
            }
        return guid

    def add_static_mesh(self, mesh):
        ue_path = unreal.SystemLibrary.get_path_name(mesh)
        guid = naming.asset_guid(ue_path)
        if guid in self._entries:
            return guid

        subject = naming.package_path(ue_path)
        source, shapes = _collision_shapes(mesh, subject, self._warnings)

        box = mesh.get_bounding_box()
        aabb_min, aabb_max = _converted_aabb(_vec3(_field(box, "min")),
                                             _vec3(_field(box, "max")))

        slot_names = []
        for slot in _field(mesh, "static_materials", []) or []:
            slot_names.append(_name_str(_field(slot, "material_slot_name")))

        self._entries[guid] = {
            "guid": guid,
            "kind": "static_mesh",
            "ue_path": subject,
            "name": mesh.get_name(),
            "o3de_relative_path": self._claim(ue_path, "static_mesh"),
            # The mesh node name inside the exported FBX. UE names it after the
            # asset, and mesh_export gives its temporary asset the same name for
            # exactly this reason. The importer builds the `.assetinfo` node
            # path `RootNode.<fbx_node_name>` from it; a wrong value fails the
            # AP job outright rather than passing quietly (LANE_B.md).
            "fbx_node_name": mesh.get_name(),
            "bounds_local": {"min": aabb_min, "max": aabb_max},
            "collision": {"source": source, "shapes": shapes},
            "material_slot_names": slot_names,
        }
        return guid

    def entries(self):
        return sorted(self._entries.values(), key=lambda e: e["ue_path"])


# ---------------------------------------------------------------------------
# per-actor extraction
# ---------------------------------------------------------------------------

def _classify(actor):
    """Coarse entity kind. Physics/trigger detection is behavioural, below."""
    for class_name, kind in (("StaticMeshActor", "static_mesh"),
                             ("Light", "light"),
                             ("SkyLight", "environment"),
                             ("ExponentialHeightFog", "environment"),
                             ("SkyAtmosphere", "environment"),
                             ("PostProcessVolume", "environment"),
                             ("TriggerBase", "trigger")):
        cls = getattr(unreal, class_name, None)
        if cls is not None and isinstance(actor, cls):
            return kind
    return "unknown"


def _primitive_component(actor):
    return actor.get_component_by_class(unreal.PrimitiveComponent)


def _physics_block(component, shapes_from_asset, subject, warnings):
    """Body flags + actor-owned shapes, in the manifest's neutral vocabulary."""
    body = _field(component, "body_instance")
    collision_enabled = _enum_name(_field(body, "collision_enabled"), "no_collision")
    has_collision = collision_enabled != "no_collision"
    simulates = bool(_field(body, "simulate_physics", False))
    overlap_events = bool(_field(component, "generate_overlap_events", False))

    # A trigger is defined by behaviour, not by class: query-only collision that
    # raises overlap events. ATriggerBox matches; so does any hand-configured
    # overlap volume, which a class check would miss.
    is_trigger = has_collision and collision_enabled == "query_only" and overlap_events

    mobility = _enum_name(_field(component, "mobility"), "static")
    # Plan M3: movable + collision + not simulating -> kinematic body.
    kinematic = (mobility == "movable" and has_collision
                 and not simulates and not is_trigger)

    mass_override = bool(_field(body, "override_mass", False))
    shapes = []
    box_component = getattr(unreal, "BoxComponent", None)
    if box_component is not None and isinstance(component, box_component):
        # Trigger volumes own their shape rather than borrowing a mesh asset's.
        half = [lane_a.convert_length(v)
                for v in _vec3(component.get_unscaled_box_extent())]
        _flag_degenerate("trigger box", half, subject, warnings)
        shapes.append({
            "type": "box",
            "half_extents": half,
            "offset": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0, 1.0],
        })

    return {
        "has_collision": has_collision,
        "collision_enabled": collision_enabled,
        "collision_profile": _name_str(component.get_collision_profile_name()),
        "simulates_physics": simulates,
        "kinematic": kinematic,
        "is_trigger": is_trigger,
        "generate_overlap_events": overlap_events,
        "enable_gravity": bool(_field(body, "enable_gravity", True)),
        "ccd": bool(_field(body, "use_ccd", False)),
        "linear_damping": float(_field(body, "linear_damping", 0.0)),
        "angular_damping": float(_field(body, "angular_damping", 0.0)),
        "mass_override": mass_override,
        "mass_kg": float(_field(body, "mass_in_kg_override", 0.0)) if mass_override else None,
        "shapes": shapes,
        "shapes_from_asset": shapes_from_asset,
    }


_LIGHT_TYPES = (("DirectionalLightComponent", "directional"),
                ("SpotLightComponent", "spot"),
                ("PointLightComponent", "point"))


def _light_block(actor):
    component = actor.get_component_by_class(unreal.LightComponentBase)
    if component is None:
        return None

    light_type = "unknown"
    for class_name, name in _LIGHT_TYPES:
        cls = getattr(unreal, class_name, None)
        if cls is not None and isinstance(component, cls):
            light_type = name
            break

    color = _field(component, "light_color")
    srgb8 = [int(_field(color, "r", 255)),
             int(_field(color, "g", 255)),
             int(_field(color, "b", 255))]

    block = {
        "type": light_type,
        "intensity": float(_field(component, "intensity", 0.0)),
        # A directional light's intensity is in lux and carries no units enum
        # (verified: the property does not exist on DirectionalLightComponent).
        "intensity_units": _enum_name(_field(component, "intensity_units"), "lux"),
        "color_srgb8": srgb8,
        "color_linear": [_srgb_to_linear(c) for c in srgb8],
        "cast_shadows": bool(_field(component, "cast_shadows", True)),
        "temperature_k": float(_field(component, "temperature", 6500.0)),
        "use_temperature": bool(_field(component, "use_temperature", False)),
    }

    radius = _field(component, "attenuation_radius")
    if radius is not None:
        block["attenuation_radius"] = lane_a.convert_length(float(radius))
    source_radius = _field(component, "source_radius")
    if source_radius is not None:
        block["source_radius"] = lane_a.convert_length(float(source_radius))
    if light_type == "spot":
        block["inner_cone_angle_deg"] = float(_field(component, "inner_cone_angle", 0.0))
        block["outer_cone_angle_deg"] = float(_field(component, "outer_cone_angle", 0.0))
    return block


def _mesh_block(actor, assets, subject, warnings):
    component = actor.static_mesh_component
    mesh = _field(component, "static_mesh")
    if mesh is None:
        return None, None

    mesh_guid = assets.add_static_mesh(mesh)
    slot_names = []
    for slot in _field(mesh, "static_materials", []) or []:
        slot_names.append(_name_str(_field(slot, "material_slot_name")))

    slots = []
    for index in range(component.get_num_materials()):
        material = component.get_material(index)
        if material is None:
            warnings.add("MESH_SLOT_EMPTY", subject, "material slot %d" % index)
            material_guid = None
        else:
            material_guid = assets.add_material(material)
        slots.append({
            "index": index,
            "slot_name": slot_names[index] if index < len(slot_names) else "",
            "material_guid": material_guid,
        })

    return {"asset_guid": mesh_guid, "material_slots": slots}, mesh_guid


def _build_entity(actor, assets, warnings):
    actor_path = actor.get_path_name()
    label = actor.get_actor_label()
    kind = _classify(actor)

    entity = {
        "id": naming.entity_id(actor_path),
        "name": label,
        "ue_class": actor.get_class().get_name(),
        "ue_actor_path": actor_path,
        "kind": kind,
        "parent_id": None,
        "mobility": "static",
        "transform": _actor_transforms(actor, label, warnings),
    }

    parent = actor.get_attach_parent_actor()
    if parent is not None:
        entity["parent_id"] = naming.entity_id(parent.get_path_name())

    root = actor.root_component
    if root is not None:
        entity["mobility"] = _enum_name(_field(root, "mobility"), "static")

    mesh_guid = None
    if kind == "static_mesh":
        mesh_block, mesh_guid = _mesh_block(actor, assets, label, warnings)
        if mesh_block is not None:
            entity["mesh"] = mesh_block

    if kind == "light":
        light = _light_block(actor)
        if light is not None:
            entity["light"] = light

    if kind == "environment":
        warnings.add("ACTOR_DEFERRED", label,
                     actor.get_class().get_name() + " is imported in M6")
    elif kind == "unknown":
        warnings.add("ACTOR_CLASS_UNMAPPED", label,
                     "no v1 mapping for " + actor.get_class().get_name())

    component = _primitive_component(actor)
    if component is not None:
        physics = _physics_block(component, mesh_guid, label, warnings)
        # "No collision -> render-only entity, no physics components" (plan M3).
        # Emitting a block that says has_collision=false for every light and fog
        # actor would only give the O3DE side something to ignore.
        if physics["has_collision"]:
            entity["physics"] = physics
            # A trigger volume is a trigger whatever its declared class.
            if physics["is_trigger"]:
                entity["kind"] = "trigger"

    return entity


# ---------------------------------------------------------------------------
# World Partition guard (plan M1 / Known Hard Spot 8)
# ---------------------------------------------------------------------------

def _guard_world_partition(world, level, map_path, warnings):
    """Abort on a partitioned level instead of exporting a near-empty list.

    Iterating actors in an unloaded WP level yields almost nothing and looks
    exactly like a successful export, so this is conservative: a detection
    failure aborts too.
    """
    settings = world.get_world_settings()
    if settings is None:
        warnings.add("LEVEL_WP_DETECT_FAILED", map_path,
                     "UWorld.get_world_settings() returned None")
        raise ExportAborted("cannot determine whether the level is partitioned")

    try:
        partition = settings.get_editor_property("world_partition")
    except Exception as exc:
        warnings.add("LEVEL_WP_DETECT_FAILED", map_path,
                     "AWorldSettings.WorldPartition unreadable: " + str(exc)[:120])
        raise ExportAborted("cannot determine whether the level is partitioned")

    if partition is not None:
        warnings.add("LEVEL_WORLD_PARTITION", map_path,
                     "AWorldSettings.WorldPartition is set; v1 supports "
                     "non-World-Partition levels only")
        raise ExportAborted("level is World Partition enabled")

    # One File Per Actor without World Partition is legal and still enumerates
    # completely, but nothing in v1 has been tested against it.
    if level is not None and bool(_field(level, "use_external_actors", False)):
        warnings.add("LEVEL_EXTERNAL_ACTORS", map_path,
                     "ULevel.bUseExternalActors is set")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def export_level(map_path, output_path):
    """Export `map_path` to `output_path`. Returns (document, warnings).

    On an aborting condition the manifest is still written -- carrying the
    error record and an empty entity list -- so CI has a machine-readable
    reason, and ExportAborted is raised afterwards.
    """
    warnings = Warnings()
    level_name = map_path.rsplit("/", 1)[-1]
    level_info = {"package": map_path, "name": level_name}

    level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level_subsystem.load_level(map_path):
        raise ExportAborted("failed to load level " + map_path)

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = actor_subsystem.get_all_level_actors()
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    level = actors[0].get_level() if actors else None

    assets = AssetTable(warnings)
    entities = []
    abort_reason = None
    try:
        _guard_world_partition(world, level, map_path, warnings)
        for actor in sorted(actors, key=lambda a: a.get_path_name()):
            entities.append(_build_entity(actor, assets, warnings))
    except ExportAborted as exc:
        abort_reason = str(exc)
        entities = []

    document = manifest_module.build(
        level=level_info,
        assets=assets.entries(),
        entities=sorted(entities, key=lambda e: e["ue_actor_path"]),
        warning_records=warnings.records(),
        engine_version=unreal.SystemLibrary.get_engine_version(),
    )

    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w") as handle:
        handle.write(manifest_module.dumps(document))

    if abort_reason is not None:
        raise ExportAborted(abort_reason)
    return document, warnings
