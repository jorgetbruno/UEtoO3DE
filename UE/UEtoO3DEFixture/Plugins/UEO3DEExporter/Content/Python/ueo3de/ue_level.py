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

def _transform_from_parts(location_cm, quat_xyzw, scale, subject, warnings,
                          fold=True):
    """One TRS, converted. Returns (transform, needs_mirrored_mesh).

    With `fold=True` (the default) negative scale components are folded into
    the rotation first (`lane_a.fold_scale_signs`): an even count of negative
    axes IS a 180-degree rotation and converts exactly; an odd count leaves
    one canonical mirror that must be baked into a mirrored mesh variant,
    which is what the returned flag requests. `fold=False` is the legacy
    abs()+warning path, kept for mirrored actors in attach hierarchies where
    the folded frame would break the children's local transforms.
    """
    if fold:
        quat_xyzw, scale, mirrored = lane_a.fold_scale_signs(quat_xyzw, scale)
        converted_scale, _negative = lane_a.convert_scale(scale)
    else:
        mirrored = False
        converted_scale, negative_axes = lane_a.convert_scale(scale)
        if negative_axes:
            warnings.add("XFORM_NEGATIVE_SCALE", subject,
                         "negative on axis %s; the mirror is NOT represented "
                         "(mirrored-variant folding applies only to flat "
                         "static-mesh actors -- an attach hierarchy or a kind "
                         "with no skinned/baked mirror variant takes this "
                         "path)" % ",".join(negative_axes))
    return {
        "translation": lane_a.convert_position(location_cm),
        "rotation": lane_a.convert_quat(quat_xyzw),
        "scale": converted_scale,
    }, mirrored


def _actor_transforms(actor, subject, warnings, mirror_variant_available=True):
    """World + parent-relative transforms. Returns (transforms, mirrored).

    `mirrored` means the actor's geometry must reference the mirror-X mesh
    variant (odd number of negative scale axes). Folding is only applied to
    FLAT actors: a mirrored actor inside an attach hierarchy falls back to
    the legacy abs() path, because folding rewrites the actor's frame and
    every child's local transform would need re-deriving against it (and the
    children would inherit the mirror in turn). Both real levels measured so
    far have zero such actors; the fallback keeps them placed, unmirrored,
    and reported rather than wrong.

    `mirror_variant_available=False` takes that same fallback for actor
    kinds that HAVE no mirror variant to reference -- skeletal meshes above
    all, since no skinned mirror bake exists. Folding decomposes an odd sign
    pattern as SIGMA_rot * mirror and keeps SIGMA_rot in the rotation, which
    is only correct if the mirror actually arrives: for UE scale (1,-1,1)
    that is a 180-degree yaw, so a mirrored ghoul would have imported
    facing backwards while the warning claimed it was merely "unmirrored".
    """
    in_hierarchy = (actor.get_attach_parent_actor() is not None
                    or bool(actor.get_attached_actors()))
    world_scale = _vec3(actor.get_actor_scale3d())
    wants_mirror = (world_scale[0] < 0.0) ^ (world_scale[1] < 0.0) ^ (world_scale[2] < 0.0)
    fold = not (wants_mirror and (in_hierarchy or not mirror_variant_available))

    world, mirrored_world = _transform_from_parts(
        _vec3(actor.get_actor_location()),
        _quat_xyzw(actor.get_actor_rotation().quaternion()),
        world_scale,
        subject, warnings, fold=fold)

    root = actor.root_component
    if root is None:
        local, mirrored_local = dict(world), mirrored_world
    else:
        local, mirrored_local = _transform_from_parts(
            _vec3(_field(root, "relative_location")),
            _rotator_xyzw(_field(root, "relative_rotation")),
            _vec3(_field(root, "relative_scale3d")),
            subject, warnings, fold=fold)

    if mirrored_world != mirrored_local:
        # A mirror that exists in only one of the two spaces means an
        # ancestor contributes it; that is the hierarchy case again.
        warnings.add("XFORM_NEGATIVE_SCALE", subject,
                     "world/local mirror parity disagrees (an ancestor "
                     "carries the mirror); not represented")
        world["scale"] = [abs(v) for v in world["scale"]]
        local["scale"] = [abs(v) for v in local["scale"]]
        return {"world": world, "local": local}, False

    if mirrored_world:
        warnings.add("XFORM_MIRRORED_MESH_VARIANT", subject,
                     "odd negative-scale axes folded into the rotation; the "
                     "entity references the mirror-X mesh variant")
    return {"world": world, "local": local}, mirrored_world


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


def _mirror_shape(shape):
    """A collision shape under the canonical mirror-X, in converted space.

    Offsets negate x; rotations conjugate by diag(-1,1,1) (keep x, negate y
    and z -- `lane_a.mirror_x_quat`); scalar dimensions (half-extents, radius,
    heights) are isometry-invariant and stay. Convex hulls carry only their
    AABB in the manifest (the collider itself bakes from render geometry,
    which mirrors with the variant mesh), so the AABB mirrors like bounds.
    """
    mirrored = dict(shape)
    if "offset" in mirrored:
        mirrored["offset"] = lane_a.mirror_x_position(mirrored["offset"])
    if "rotation" in mirrored:
        mirrored["rotation"] = lane_a.mirror_x_quat(mirrored["rotation"])
    if mirrored.get("type") == "convex":
        low, high = mirrored["aabb_min"], mirrored["aabb_max"]
        mirrored["aabb_min"] = [-high[0], low[1], low[2]]
        mirrored["aabb_max"] = [-low[0], high[1], high[2]]
    return mirrored


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

_EXTENSIONS = {"static_mesh": "fbx", "material": "material",
               "skeletal_mesh": "fbx", "animation": "fbx"}

# The mirrored-variant ue_path fragment (negative-scale fidelity). Stored
# literally in the asset entry's ue_path: the GUID derives from it, sanitize
# maps '#' to '_' for the O3DE path, and mesh_export strips it to load the
# real asset and to choose the variant bake.
MIRROR_SUFFIX = "#mx"
# The terrain fragment works the same way, except the part before '#' is the
# Landscape ACTOR's path (a landscape has no asset), which mesh_export
# resolves to the live actor in the open level.
TERRAIN_SUFFIX = "#terrain"
# Spline-mesh bakes (M9): the part before '#' is "<actor path>:<component>",
# resolved to the live SplineMeshComponent; the deformed geometry bakes
# through the normal Lane B pipeline in COMPONENT-LOCAL space, so the entity
# keeps the component's transform.
SPLINE_SUFFIX = "#spline"

# Instance-expansion ceiling (M9). Foliage/ISM components expand to one child
# entity per instance -- Atom re-instances identical models at render time,
# but the EDITOR does not scale to six figures of entities: a 100k-instance
# level will not open. Per component; the excess is dropped LOUDLY.
INSTANCE_CEILING = int(os.environ.get("UEO3DE_MAX_INSTANCES", "2000"))


class AssetTable:
    """Deduplicates referenced assets by GUID and claims their O3DE paths."""

    def __init__(self, warnings):
        self._entries = {}
        self._registry = naming.PathRegistry()
        self._warnings = warnings
        # Textures are planned here during material classification (M4) and
        # exported to files by the caller after the walk.
        from . import material_export
        self.texture_bank = material_export.TextureBank(self._registry)

    def _claim(self, ue_path, kind):
        try:
            stem = self._registry.claim(ue_path)
        except naming.PathCollisionError as exc:
            self._warnings.add("ASSET_PATH_COLLISION", exc.stem,
                               "%s vs %s" % (exc.first, exc.second))
            raise ExportAborted(str(exc))
        return naming.with_extension(stem, _EXTENSIONS[kind])

    def add_material(self, material):
        from . import material_export

        ue_path = unreal.SystemLibrary.get_path_name(material)
        guid = naming.asset_guid(ue_path)
        if guid not in self._entries:
            # Classification runs once per unique material; texture entries are
            # planned into the shared bank as a side effect (M4).
            material_data = material_export.build_material_data(
                material, self.texture_bank, self._warnings)
            self._entries[guid] = {
                "guid": guid,
                "kind": "material",
                "ue_path": naming.package_path(ue_path),
                "name": material.get_name(),
                "o3de_relative_path": self._claim(ue_path, "material"),
                "material_data": material_data,
            }
        return guid

    def add_static_mesh(self, mesh, mirrored=False):
        """Register `mesh`; with `mirrored=True`, register its mirror-X VARIANT.

        The variant is a separate asset entry whose `ue_path` is the real
        package path plus a literal `#mx` fragment. That single encoding does
        all the work: the GUID derives from the stored path (so the
        validator's re-derivation holds with no special case), sanitization
        maps `#` to `_` giving a distinct `..._mx.fbx` O3DE path through the
        PathRegistry's normal collision protection, `mesh_export` recognises
        the fragment (strip for loading, bake the mirror variant), and the
        schema, validator and importer need no changes at all -- downstream,
        a variant is just one more mesh asset. Collision shapes and bounds
        are mirrored here; the FBX node is `<name>_MX`.
        """
        ue_path = unreal.SystemLibrary.get_path_name(mesh)
        if mirrored:
            guid = naming.asset_guid(naming.package_path(ue_path) + MIRROR_SUFFIX)
        else:
            guid = naming.asset_guid(ue_path)
        if guid in self._entries:
            return guid

        subject = naming.package_path(ue_path)
        source, shapes = _collision_shapes(mesh, subject, self._warnings)

        # M9: only LOD0 is baked (RENDER_DATA LOD0); a multi-LOD source says
        # so once, at the asset, rather than once per placed actor.
        try:
            lod_count = int(mesh.get_num_lods())
        except Exception:
            lod_count = 1
        if lod_count > 1:
            # Reported for the variant too. `subject` and the detail are the
            # source mesh's either way, so Warnings' dedupe collapses the
            # base and its '#mx' variant to one record -- while skipping
            # mirrored entries left a mesh used ONLY at negative scale
            # reporting nothing at all.
            self._warnings.add("LOD_FLATTENED", subject,
                               "%d LODs in UE; only LOD0 exported" % lod_count)

        box = mesh.get_bounding_box()
        aabb_min, aabb_max = _converted_aabb(_vec3(_field(box, "min")),
                                             _vec3(_field(box, "max")))
        if mirrored:
            shapes = [_mirror_shape(shape) for shape in shapes]
            aabb_min, aabb_max = ([-aabb_max[0], aabb_min[1], aabb_min[2]],
                                  [-aabb_min[0], aabb_max[1], aabb_max[2]])

        slot_names = []
        # The material NAMES the baked FBX will carry, per slot. These are the
        # mesh asset's own materials -- NOT the actor's effective ones, which
        # may be overridden per component. They are what SceneAPI turns into
        # the azmodel's slot labels, so they are what the importer matches on.
        # (UE slot names like "Bark" do not survive the FBX; material asset
        # names do -- measured in Tests/ue/probe_slots.py.)
        slot_material_names = []
        for index, slot in enumerate(_field(mesh, "static_materials", []) or []):
            slot_names.append(_name_str(_field(slot, "material_slot_name")))
            material = _field(slot, "material_interface")
            # A null slot gets the bake's placeholder label (see
            # naming.empty_slot_label): UE's FBX export would otherwise DROP
            # the slot and an actor's override of it could never match.
            slot_material_names.append(material.get_name() if material
                                       else naming.empty_slot_label(index))

        # One claim only, keyed by the stored ue_path: the validator asserts
        # o3de_relative_path starts with sanitize(stored ue_path), and the
        # PathRegistry must see the SAME key or a base+variant pair would
        # silently share one stem (claim() returns the existing stem for a
        # repeat claim of the same path).
        claim_key = (naming.package_path(ue_path) + MIRROR_SUFFIX) if mirrored else ue_path
        entry = {
            "guid": guid,
            "kind": "static_mesh",
            "ue_path": subject,
            "name": mesh.get_name(),
            "o3de_relative_path": self._claim(claim_key, "static_mesh"),
            # The mesh node name inside the exported FBX. UE names it after the
            # asset, and mesh_export gives its temporary asset the same name for
            # exactly this reason. The importer builds the `.assetinfo` node
            # path `RootNode.<fbx_node_name>` from it; a wrong value fails the
            # AP job outright rather than passing quietly (LANE_B.md).
            "fbx_node_name": mesh.get_name(),
            "bounds_local": {"min": aabb_min, "max": aabb_max},
            "collision": {"source": source, "shapes": shapes},
            "material_slot_names": slot_names,
            "material_slot_material_names": slot_material_names,
        }
        if mirrored:
            entry["ue_path"] = subject + MIRROR_SUFFIX
            entry["name"] = mesh.get_name() + "_MX"
            entry["fbx_node_name"] = mesh.get_name() + "_MX"
        self._entries[guid] = entry
        return guid

    def add_terrain(self, actor, warnings):
        """Register a Landscape actor's baked-terrain asset entry (M7).

        The entry's ue_path is the ACTOR path plus '#terrain' -- landscapes
        have no asset to reference. mesh_export resolves the actor in the
        open level, samples heights by per-component line traces (needs a
        full-editor session; every commandlet route is measured dead, see
        Tests/ue/probe_m7_*.py) and bakes the grid through the normal Lane B
        pipeline. Returns the guid, or None if the landscape has no
        collision components to sample.
        """
        components = actor.get_components_by_class(
            getattr(unreal, "LandscapeHeightfieldCollisionComponent",
                    unreal.SceneComponent)) or []
        if not components:
            return None

        actor_path = actor.get_path_name()
        key = actor_path + TERRAIN_SUFFIX
        guid = naming.asset_guid(key)
        if guid in self._entries:
            return guid

        label = actor.get_actor_label()
        node_name = "".join(c if c.isalnum() else "_" for c in label) + "_Terrain"
        origin, extent = actor.get_actor_bounds(False)
        aabb_min, aabb_max = _converted_aabb(
            [origin.x - extent.x, origin.y - extent.y, origin.z - extent.z],
            [origin.x + extent.x, origin.y + extent.y, origin.z + extent.z])

        material = None
        try:
            material = actor.get_editor_property("landscape_material")
        except Exception:
            pass
        material_name = material.get_name() if material is not None else ""

        warnings.add("TERRAIN_BAKED_TO_MESH", label,
                     "landscape baked to a world-space grid mesh (%d collision "
                     "components); physics is the importer's render-mesh "
                     "triangle collider" % len(components))

        self._entries[guid] = {
            "guid": guid,
            "kind": "static_mesh",
            "ue_path": key,
            "name": node_name,
            "o3de_relative_path": self._claim(key, "static_mesh"),
            "fbx_node_name": node_name,
            "bounds_local": {"min": aabb_min, "max": aabb_max},
            "collision": {"source": "none", "shapes": []},
            "material_slot_names": ["Terrain"],
            "material_slot_material_names": [material_name],
        }
        return guid

    def add_spline_bake(self, actor_path, component, warnings):
        """Register a SplineMeshComponent's baked-geometry asset entry (M9).

        The ue_path is "<actor path>:<component name>#spline" -- the same
        fragment technique as #mx/#terrain: guid from the stored path,
        sanitize maps ':'/'#' onto '_', mesh_export resolves the live
        component and bakes `copy_mesh_from_component` (component-LOCAL, so
        the entity keeps the component transform) through the normal Lane B
        pipeline. Collision source is "none": the physics block sends the
        importer down its render-mesh triangle-collider path, and the render
        mesh IS the deformed bake.
        """
        key = "%s:%s%s" % (actor_path, component.get_name(), SPLINE_SUFFIX)
        guid = naming.asset_guid(key)
        if guid in self._entries:
            return guid

        label = "%s.%s" % (actor_path.rsplit(".", 1)[-1], component.get_name())
        warnings.add("SPLINE_BAKED", label,
                     "deformed spline-mesh geometry baked to a static mesh; "
                     "the live spline is lost")

        node_name = "".join(c if c.isalnum() else "_" for c in label) + "_Spline"
        slot_names = []
        slot_material_names = []
        for index in range(component.get_num_materials()):
            material = component.get_material(index)
            slot_names.append("")
            slot_material_names.append(material.get_name() if material
                                       else naming.empty_slot_label(index))

        self._entries[guid] = {
            "guid": guid,
            "kind": "static_mesh",
            "ue_path": key,
            "name": node_name,
            "o3de_relative_path": self._claim(key, "static_mesh"),
            "fbx_node_name": node_name,
            # mesh_export fills real bounds at bake time; the asset entry
            # carries the component's local bounds when readable.
            "bounds_local": _spline_local_bounds(component),
            "collision": {"source": "none", "shapes": []},
            "material_slot_names": slot_names,
            "material_slot_material_names": slot_material_names,
        }
        return guid

    def add_skeletal_mesh(self, mesh, component):
        """Register a skeletal mesh asset (M8). `component` supplies the bone
        table -- get_num_bones lives on SkinnedMeshComponent, not the asset.

        The exported FBX goes through UE's NATIVE skeletal exporter (no
        GeometryScript bake is possible without destroying skinning), so its
        product carries the Lane B SKELETAL rule: the importer composes a
        local Rz180 into the entity rotation instead (LANE_B.md, M8).
        Products: `<stem>.actor` (+ skinned azmodel), from the DEFAULT scene
        rules -- no `.assetinfo` sidecar exists for skeletal sources.
        """
        ue_path = unreal.SystemLibrary.get_path_name(mesh)
        guid = naming.asset_guid(ue_path)
        if guid in self._entries:
            return guid

        bounds = mesh.get_bounds()
        origin = _vec3(_field(bounds, "origin"))
        extent = _vec3(_field(bounds, "box_extent"))
        aabb_min, aabb_max = _converted_aabb(
            [origin[0] - extent[0], origin[1] - extent[1], origin[2] - extent[2]],
            [origin[0] + extent[0], origin[1] + extent[1], origin[2] + extent[2]])

        slot_names = []
        slot_material_names = []
        for index, slot in enumerate(_field(mesh, "materials", []) or []):
            slot_names.append(_name_str(_field(slot, "material_slot_name")))
            material = _field(slot, "material_interface")
            slot_material_names.append(material.get_name() if material
                                       else naming.empty_slot_label(index))

        skeleton = _field(mesh, "skeleton")
        bone_count = int(component.get_num_bones())
        self._entries[guid] = {
            "guid": guid,
            "kind": "skeletal_mesh",
            "ue_path": naming.package_path(ue_path),
            "name": mesh.get_name(),
            "o3de_relative_path": self._claim(ue_path, "skeletal_mesh"),
            "bounds_local": {"min": aabb_min, "max": aabb_max},
            "bone_count": bone_count,
            "bone_names": [_name_str(component.get_bone_name(i))
                           for i in range(bone_count)],
            "skeleton_ue_path": (naming.package_path(
                unreal.SystemLibrary.get_path_name(skeleton))
                if skeleton is not None else ""),
            "material_slot_names": slot_names,
            "material_slot_material_names": slot_material_names,
        }
        return guid

    def add_animation(self, sequence):
        """Register an AnimSequence asset (M8). Product: `<stem>.motion`."""
        ue_path = unreal.SystemLibrary.get_path_name(sequence)
        guid = naming.asset_guid(ue_path)
        if guid in self._entries:
            return guid
        try:
            duration = float(sequence.get_play_length())
        except Exception:
            duration = float(_field(sequence, "sequence_length", 0.0) or 0.0)
        self._entries[guid] = {
            "guid": guid,
            "kind": "animation",
            "ue_path": naming.package_path(ue_path),
            "name": sequence.get_name(),
            "o3de_relative_path": self._claim(ue_path, "animation"),
            "duration_seconds": duration,
            "root_motion": bool(_field(sequence, "enable_root_motion", False)),
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
                             ("SkeletalMeshActor", "skeletal_mesh"),
                             ("DecalActor", "decal"),
                             ("CameraActor", "camera"),
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


# ---------------------------------------------------------------------------
# environment (M6)
# ---------------------------------------------------------------------------

# Post-process settings the O3DE side can do something with, as
# (UE settings property, UE override flag, manifest key). UE applies a
# PostProcessVolume setting ONLY when its override flag is set, so anything
# without the flag is a UE default that the artist never chose -- exporting it
# would hand the importer a fabricated intent. Probed live in
# `Tests/ue/probe_m6_env.py`; every name here was confirmed to exist.
_PP_SETTINGS = (
    ("auto_exposure_bias", "override_auto_exposure_bias", "auto_exposure_bias"),
    ("auto_exposure_min_brightness", "override_auto_exposure_min_brightness",
     "auto_exposure_min_brightness"),
    ("auto_exposure_max_brightness", "override_auto_exposure_max_brightness",
     "auto_exposure_max_brightness"),
    ("auto_exposure_speed_up", "override_auto_exposure_speed_up",
     "auto_exposure_speed_up"),
    ("auto_exposure_speed_down", "override_auto_exposure_speed_down",
     "auto_exposure_speed_down"),
    ("bloom_intensity", "override_bloom_intensity", "bloom_intensity"),
    ("bloom_threshold", "override_bloom_threshold", "bloom_threshold"),
    ("vignette_intensity", "override_vignette_intensity", "vignette_intensity"),
    ("depth_of_field_focal_distance", "override_depth_of_field_focal_distance",
     "depth_of_field_focal_distance"),
    ("depth_of_field_fstop", "override_depth_of_field_fstop",
     "depth_of_field_fstop"),
    ("motion_blur_amount", "override_motion_blur_amount", "motion_blur_amount"),
    ("ambient_occlusion_intensity", "override_ambient_occlusion_intensity",
     "ambient_occlusion_intensity"),
)

# Overridden settings with no M6 mapping still have to be reported, so the
# importer can say what it dropped rather than silently losing them.
_PP_MAPPED_IN_M6 = frozenset((
    "auto_exposure_bias", "auto_exposure_min_brightness",
    "auto_exposure_max_brightness", "auto_exposure_speed_up",
    "auto_exposure_speed_down", "bloom_intensity", "bloom_threshold",
))


def _linear_color(value, default=(1.0, 1.0, 1.0)):
    """A UE FLinearColor's RGB as a plain list (already linear)."""
    if value is None:
        return list(default)
    return [float(_field(value, "r", default[0])),
            float(_field(value, "g", default[1])),
            float(_field(value, "b", default[2]))]


def _srgb_color(value, default=(255, 255, 255)):
    """A UE FColor (sRGB bytes) decoded to linear."""
    if value is None:
        return [_srgb_to_linear(channel) for channel in default]
    return [_srgb_to_linear(int(_field(value, "r", default[0]))),
            _srgb_to_linear(int(_field(value, "g", default[1]))),
            _srgb_to_linear(int(_field(value, "b", default[2])))]


def _skylight_block(actor):
    component = actor.light_component
    if component is None:
        return None
    source = _enum_name(_field(component, "source_type"), "unknown")
    # SLS_CAPTURED_SCENE / SLS_SPECIFIED_CUBEMAP -> captured_scene / specified_cubemap
    source = {"sls_captured_scene": "captured_scene",
              "sls_specified_cubemap": "specified_cubemap"}.get(source, "unknown")
    cubemap = _field(component, "cubemap")
    return {
        "type": "skylight",
        "intensity": float(_field(component, "intensity", 1.0)),
        "color_linear": _srgb_color(_field(component, "light_color")),
        "real_time_capture": bool(_field(component, "real_time_capture", False)),
        "source_type": source,
        "cubemap_ue_path": (naming.package_path(
            unreal.SystemLibrary.get_path_name(cubemap)) if cubemap else None),
        "lower_hemisphere_is_black": bool(
            _field(component, "lower_hemisphere_is_black", True)),
    }


def _fog_block(actor):
    component = actor.component
    if component is None:
        return None
    # `fog_inscattering_color` does not exist in 5.8 -- the property is
    # `fog_inscattering_luminance` (measured; probing with hasattr would have
    # reported neither, since UE hides UPROPERTYs from dir()).
    height_cm = 0.0
    root = actor.root_component
    if root is not None:
        location = _field(root, "relative_location")
        if location is not None:
            height_cm = float(_field(location, "z", 0.0))
    return {
        "type": "fog",
        "fog_density": float(_field(component, "fog_density", 0.02)),
        "fog_height_falloff": float(_field(component, "fog_height_falloff", 0.2)),
        "fog_inscattering_color_linear": _linear_color(
            _field(component, "fog_inscattering_luminance"), (0.0, 0.0, 0.0)),
        "start_distance": lane_a.convert_length(
            float(_field(component, "start_distance", 0.0))),
        "fog_cutoff_distance": lane_a.convert_length(
            float(_field(component, "fog_cutoff_distance", 0.0))),
        "fog_max_opacity": float(_field(component, "fog_max_opacity", 1.0)),
        "fog_height_m": lane_a.convert_length(height_cm),
    }


def _sky_atmosphere_block(actor):
    component = actor.get_component_by_class(unreal.SkyAtmosphereComponent)
    if component is None:
        return None
    return {
        "type": "sky_atmosphere",
        "ground_albedo_linear": _srgb_color(_field(component, "ground_albedo"),
                                            (170, 170, 170)),
        "rayleigh_scattering_scale": float(
            _field(component, "rayleigh_scattering_scale", 0.0331)),
        "mie_scattering_scale": float(
            _field(component, "mie_scattering_scale", 0.003996)),
        "multi_scattering_factor": float(
            _field(component, "multi_scattering_factor", 1.0)),
    }


def _post_process_block(actor, subject, warnings):
    settings = _field(actor, "settings")
    overrides = {}
    if settings is not None:
        for name, flag, key in _PP_SETTINGS:
            if not bool(_field(settings, flag, False)):
                continue   # not overridden: a UE default, not an intent
            value = _field(settings, name)
            if value is None:
                continue
            overrides[key] = float(value) if isinstance(value, (int, float)) else value
            if key not in _PP_MAPPED_IN_M6:
                warnings.add("ENV_POSTPROCESS_UNMAPPED", subject,
                             "%s is overridden in UE but has no M6 mapping" % key)

    block = {
        "type": "post_process",
        "priority": float(_field(actor, "priority", 0.0)),
        "blend_weight": float(_field(actor, "blend_weight", 1.0)),
        "unbound": bool(_field(actor, "unbound", False)),
        "enabled": bool(_field(actor, "enabled", True)),
        "overrides": overrides,
    }
    if not block["unbound"]:
        try:
            _origin, extent = actor.get_actor_bounds(False)
            block["extents_m"] = [lane_a.convert_length(abs(float(_field(extent, axis, 0.0))))
                                  for axis in ("x", "y", "z")]
        except Exception:
            warnings.add("ENV_VOLUME_BOUNDS_UNKNOWN", subject,
                         "bounded post-process volume with unreadable bounds")
    return block


def _environment_block(actor, subject, warnings):
    """Environment payload for a sky/fog/post-process actor, or None."""
    for class_name, builder in (("SkyLight", _skylight_block),
                                ("ExponentialHeightFog", _fog_block),
                                ("SkyAtmosphere", _sky_atmosphere_block)):
        cls = getattr(unreal, class_name, None)
        if cls is not None and isinstance(actor, cls):
            return builder(actor)
    cls = getattr(unreal, "PostProcessVolume", None)
    if cls is not None and isinstance(actor, cls):
        return _post_process_block(actor, subject, warnings)
    return None


def _mesh_block_from_component(component, assets, subject, warnings,
                               mirrored=False):
    """The manifest `mesh` block for one StaticMeshComponent."""
    mesh = _field(component, "static_mesh")
    if mesh is None:
        return None, None

    mesh_guid = assets.add_static_mesh(mesh, mirrored=mirrored)
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


def _mesh_block(actor, assets, subject, warnings, mirrored=False):
    return _mesh_block_from_component(actor.static_mesh_component, assets,
                                      subject, warnings, mirrored=mirrored)


def _spline_local_bounds(component):
    """Component-local AABB (converted) for the asset entry, best effort."""
    try:
        local = component.get_local_bounds()
        box_min = local[0] if isinstance(local, tuple) else local
        box_max = local[1] if isinstance(local, tuple) else local
        aabb_min, aabb_max = _converted_aabb(_vec3(box_min), _vec3(box_max))
        return {"min": aabb_min, "max": aabb_max}
    except Exception:
        return {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]}


def _decal_block(actor, assets, subject, warnings):
    """The manifest `decal` block for a DecalActor (M9).

    UE decals project along the component's LOCAL +X, with `decal_size` as
    HALF-extents (x = projection depth, cm). Atom's Decal component projects
    along the entity's -Z with the footprint from the entity scale; the
    importer owns that remapping (decal_build). The material converts through
    the M4 StandardPBR subset, never an Atom decal material type -- say so.
    """
    component = actor.get_component_by_class(unreal.DecalComponent)
    if component is None:
        return None
    material = None
    try:
        material = component.get_decal_material()
    except Exception:
        material = _field(component, "decal_material")
    material_guid = assets.add_material(material) if material is not None else None
    warnings.add("DECAL_MATERIAL_APPROX", subject,
                 "decal material %r converts through the StandardPBR subset"
                 % (material.get_name() if material else None))
    size = _vec3(_field(component, "decal_size", unreal.Vector(128, 256, 256)))
    return {
        "material_guid": material_guid,
        # Half-extents, metres, UE order (x = projection depth).
        "half_extents_m": [lane_a.convert_length(abs(v)) for v in size],
        "sort_order": int(_field(component, "sort_order", 0) or 0),
        "fade_screen_size": float(_field(component, "fade_screen_size", 0.01) or 0.0),
    }


def _camera_block(actor, subject, warnings):
    """The manifest `camera` block for a CameraActor (M9).

    UE's field_of_view is HORIZONTAL degrees; O3DE's Camera takes VERTICAL.
    Both numbers ship (plus the aspect ratio the conversion needs) so the
    importer's arithmetic is testable offline.
    """
    component = actor.get_component_by_class(unreal.CameraComponent)
    if component is None:
        return None
    projection = _enum_name(_field(component, "projection_mode"), "perspective")
    if projection != "perspective":
        warnings.add("CAMERA_UNSUPPORTED_MODE", subject,
                     "projection mode %r has no v1 mapping" % projection)
        return None
    return {
        "fov_horizontal_deg": float(_field(component, "field_of_view", 90.0)),
        "aspect_ratio": float(_field(component, "aspect_ratio", 16.0 / 9.0)),
        "constrain_aspect_ratio": bool(_field(component, "constrain_aspect_ratio",
                                              False)),
    }


def _skeletal_block(component, assets, subject, warnings):
    """The manifest `skeletal` block for one SkeletalMeshComponent (M8).

    Single-node playback maps to the Simple Motion component; an Animation
    Blueprint has no mapping (bind pose + ANIM_BLUEPRINT_UNMAPPED). The
    anim-to-play lives in `animation_data` in 5.8 -- the flat `anim_to_play`
    property does not exist (measured on the UndeadPack showcase maps).
    """
    mesh = _field(component, "skeletal_mesh_asset")
    if mesh is None:
        return None, None
    mesh_guid = assets.add_skeletal_mesh(mesh, component)

    slot_names = []
    for slot in _field(mesh, "materials", []) or []:
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

    block = {
        "asset_guid": mesh_guid,
        "animation_guid": None,
        "loop": False,
        "play": False,
        "material_slots": slots,
    }

    mode = _enum_name(_field(component, "animation_mode"), "")
    if mode == "animation_single_node":
        data = _field(component, "animation_data")
        sequence = _field(data, "anim_to_play")
        if sequence is None:
            pass    # bind pose on purpose: Actor component only
        elif isinstance(sequence, unreal.AnimSequence):
            block["animation_guid"] = assets.add_animation(sequence)
            block["loop"] = bool(_field(data, "saved_looping", True))
            block["play"] = bool(_field(data, "saved_playing", True))
            if bool(_field(sequence, "enable_root_motion", False)):
                warnings.add("ANIM_ROOT_MOTION_DROPPED", subject,
                             "%s has enable_root_motion; the motion plays in "
                             "place" % sequence.get_name())
        else:
            # Montages/composites are graph-adjacent assets, same drop rule.
            warnings.add("ANIM_BLUEPRINT_UNMAPPED", subject,
                         "single-node asset %s (%s) is not a plain AnimSequence"
                         % (sequence.get_name(),
                            sequence.get_class().get_name()))
    elif mode == "animation_blueprint":
        anim_class = _field(component, "anim_class")
        warnings.add("ANIM_BLUEPRINT_UNMAPPED", subject,
                     "driven by %s; imported in bind pose"
                     % (anim_class.get_name() if anim_class is not None
                        else "an Animation Blueprint"))
    return block, mesh_guid


# Actor classes deliberately NOT component-extracted, with the deferral they
# belong to. BP_Sky_Sphere is a giant textured sphere enclosing the level --
# extracting it would wrap the imported level in an unconvertible shell while
# M6's Physical Sky already stands in for the UE sky.
_DEFERRED_UNKNOWN_CLASSES = {
    "LandscapeStreamingProxy": "streaming-proxy landscapes are not supported "
                               "(M7 handles a single Landscape actor)",
    "BP_Sky_Sphere_C": "sky sphere is replaced by M6's Physical Sky",
    "SphereReflectionCapture": "reflection captures are a documented v1 drop (M6)",
    "BoxReflectionCapture": "reflection captures are a documented v1 drop (M6)",
    "PlanarReflectionCapture": "reflection captures are a documented v1 drop (M6)",
}

# Component classes that are editor-side decoration, never geometry.
_EXTRACT_SKIP_COMPONENTS = ("BillboardComponent", "ArrowComponent",
                            "DrawSphereComponent", "TextRenderComponent")


def _extract_mesh_components(actor, actor_entity, assets, warnings):
    """Child entities for an unmapped actor's StaticMeshComponents.

    Blueprint actors (vases, market stands, box piles...) are ordinary
    actors whose geometry lives in components; the CLASS has no mapping but
    the components are exactly the meshes M2 already handles. Each becomes a
    child entity under the actor's placeholder entity, world-transformed from
    the component and locally re-derived against the actor.

    ChildActorComponents are skipped on purpose: the child ACTOR they spawn
    enumerates in the level's actor list in its own right (measured on
    L_Showcase -- the box piles' vases appear both as components and as
    actors), so extracting the component too would duplicate the geometry.
    """
    actor_path = actor.get_path_name()
    label = actor_entity["name"]
    children = []
    skipped = []
    for component in actor.get_components_by_class(unreal.StaticMeshComponent) or []:
        subject = "%s.%s" % (label, component.get_name())
        if isinstance(component, getattr(unreal, "SplineMeshComponent", ())):
            # M9: the deformed geometry bakes to a '#spline' asset in
            # COMPONENT-LOCAL space, so the child entity keeps the
            # component's own transform and the mesh pipeline is unchanged.
            children.extend(_spline_child(actor, actor_entity, component,
                                          subject, assets, warnings))
            continue
        if isinstance(component, getattr(unreal, "InstancedStaticMeshComponent", ())):
            # M9: expand instances into child entities sharing one mesh
            # asset (Atom re-instances identical models at render time).
            children.extend(_instance_children(actor, actor_entity, component,
                                               subject, assets, warnings))
            continue

        world_ue = component.get_world_transform()
        world, mirrored = _transform_from_parts(
            _vec3(world_ue.translation),
            _quat_xyzw(world_ue.rotation),
            _vec3(world_ue.scale3d),
            subject, warnings)
        relative_ue = unreal.MathLibrary.make_relative_transform(
            world_ue, actor.get_actor_transform())
        local, mirrored_local = _transform_from_parts(
            _vec3(relative_ue.translation),
            _quat_xyzw(relative_ue.rotation),
            _vec3(relative_ue.scale3d),
            subject, warnings)
        if mirrored != mirrored_local:
            # The actor itself carries the mirror; same hierarchy rule as
            # actors -- keep it placed, unmirrored, and reported.
            warnings.add("XFORM_NEGATIVE_SCALE", subject,
                         "component/actor mirror parity disagrees; not represented")
            mirrored = False

        mesh_block, mesh_guid = _mesh_block_from_component(
            component, assets, subject, warnings, mirrored=mirrored)
        if mesh_block is None:
            continue

        entity = {
            "id": naming.entity_id(actor_path + ":" + component.get_name()),
            "name": subject,
            "ue_class": component.get_class().get_name(),
            "ue_actor_path": actor_path + ":" + component.get_name(),
            "kind": "static_mesh",
            "parent_id": actor_entity["id"],
            "mobility": _enum_name(_field(component, "mobility"), "static"),
            "transform": {"world": world, "local": local},
            "mesh": mesh_block,
        }
        physics = _physics_block(component, mesh_guid, subject, warnings)
        if physics["has_collision"]:
            entity["physics"] = physics
        children.append(entity)

    # Skeletal components extract the same way (M8): BP_Ghoul-style actors
    # carry their character in SkeletalMeshComponents (mesh + rags + armor).
    # Almost always AnimBlueprint-driven, so each child usually lands in bind
    # pose with ANIM_BLUEPRINT_UNMAPPED from _skeletal_block.
    skeletal_count = 0
    for component in actor.get_components_by_class(unreal.SkeletalMeshComponent) or []:
        subject = "%s.%s" % (label, component.get_name())
        world_ue = component.get_world_transform()
        world, mirrored = _transform_from_parts(
            _vec3(world_ue.translation),
            _quat_xyzw(world_ue.rotation),
            _vec3(world_ue.scale3d),
            subject, warnings)
        relative_ue = unreal.MathLibrary.make_relative_transform(
            world_ue, actor.get_actor_transform())
        local, _mirrored_local = _transform_from_parts(
            _vec3(relative_ue.translation),
            _quat_xyzw(relative_ue.rotation),
            _vec3(relative_ue.scale3d),
            subject, warnings)
        if mirrored:
            warnings.add("XFORM_NEGATIVE_SCALE", subject,
                         "mirrored skeletal component; no skinned mirror "
                         "variant exists, imported unmirrored")

        skeletal_block, _guid = _skeletal_block(component, assets, subject,
                                                warnings)
        if skeletal_block is None:
            continue
        children.append({
            "id": naming.entity_id(actor_path + ":" + component.get_name()),
            "name": subject,
            "ue_class": component.get_class().get_name(),
            "ue_actor_path": actor_path + ":" + component.get_name(),
            "kind": "skeletal_mesh",
            "parent_id": actor_entity["id"],
            "mobility": _enum_name(_field(component, "mobility"), "static"),
            "transform": {"world": world, "local": local},
            "skeletal": skeletal_block,
        })
        skeletal_count += 1

    for component in actor.get_components_by_class(unreal.SceneComponent) or []:
        kind_name = component.get_class().get_name()
        if isinstance(component, (unreal.StaticMeshComponent,
                                  unreal.SkeletalMeshComponent)):
            continue
        if kind_name in _EXTRACT_SKIP_COMPONENTS or kind_name == "SceneComponent" \
                or kind_name == "ChildActorComponent":
            continue
        skipped.append("%s (%s)" % (component.get_name(), kind_name))

    if children:
        warnings.add("ACTOR_COMPONENTS_EXTRACTED", label,
                     "%s: %d static + %d skeletal mesh component(s) extracted%s"
                     % (actor.get_class().get_name(),
                        len(children) - skeletal_count, skeletal_count,
                        ("; not extracted: " + ", ".join(sorted(set(skipped))))
                        if skipped else ""))
    return children


def _child_transforms(world_ue, actor, subject, warnings,
                      mirror_variant_available=True):
    """(world, local, mirrored) for a component/instance world transform."""
    fold = mirror_variant_available
    world, mirrored = _transform_from_parts(
        _vec3(world_ue.translation), _quat_xyzw(world_ue.rotation),
        _vec3(world_ue.scale3d), subject, warnings, fold=fold)
    relative_ue = unreal.MathLibrary.make_relative_transform(
        world_ue, actor.get_actor_transform())
    local, mirrored_local = _transform_from_parts(
        _vec3(relative_ue.translation), _quat_xyzw(relative_ue.rotation),
        _vec3(relative_ue.scale3d), subject, warnings, fold=fold)
    if mirrored != mirrored_local:
        warnings.add("XFORM_NEGATIVE_SCALE", subject,
                     "component/actor mirror parity disagrees; not represented")
        mirrored = False
    return world, local, mirrored


def _spline_child(actor, actor_entity, component, subject, assets, warnings):
    """One child entity over a '#spline' baked asset (M9)."""
    mesh_guid = assets.add_spline_bake(actor.get_path_name(), component, warnings)
    world, local, mirrored = _child_transforms(
        component.get_world_transform(), actor, subject, warnings,
        mirror_variant_available=False)
    if mirrored:
        # Defensive: _child_transforms takes the unmirrored path for spline
        # components, because _export_spline hard-codes the normal bake and
        # no mirrored spline variant exists. Keeping a fold rotation without
        # one would place unmirrored geometry at a 180-degree rotation.
        warnings.add("XFORM_NEGATIVE_SCALE", subject,
                     "mirrored spline component; no mirrored bake exists, "
                     "imported unmirrored")

    slots = []
    for index in range(component.get_num_materials()):
        material = component.get_material(index)
        slots.append({
            "index": index,
            "slot_name": "",
            "material_guid": assets.add_material(material)
            if material is not None else None,
        })

    actor_path = actor.get_path_name()
    entity = {
        "id": naming.entity_id(actor_path + ":" + component.get_name()),
        "name": subject,
        "ue_class": component.get_class().get_name(),
        "ue_actor_path": actor_path + ":" + component.get_name(),
        "kind": "static_mesh",
        "parent_id": actor_entity["id"],
        "mobility": _enum_name(_field(component, "mobility"), "static"),
        "transform": {"world": world, "local": local},
        "mesh": {"asset_guid": mesh_guid, "material_slots": slots},
    }
    physics = _physics_block(component, mesh_guid, subject, warnings)
    if physics["has_collision"]:
        entity["physics"] = physics
    return [entity]


def _instance_children(actor, actor_entity, component, subject, assets, warnings):
    """Child entities for an ISM/HISM component's instances (M9)."""
    count = component.get_instance_count()
    if count == 0:
        return []
    exported = min(count, INSTANCE_CEILING)
    if exported < count:
        warnings.add("INSTANCES_TRUNCATED", subject,
                     "%d of %d instances exported (UEO3DE_MAX_INSTANCES=%d)"
                     % (exported, count, INSTANCE_CEILING))
    warnings.add("ACTOR_INSTANCES_EXPANDED", subject,
                 "%d instance(s) of %s expanded to child entities"
                 % (exported, component.get_class().get_name()))

    actor_path = actor.get_path_name()
    children = []
    for index in range(exported):
        result = component.get_instance_transform(index, True)
        ok = result[0] if isinstance(result, tuple) else True
        world_ue = result[1] if isinstance(result, tuple) else result
        if not ok:
            continue
        instance_subject = "%s#%d" % (subject, index)
        # Transform warnings are genuinely per-instance (one instance can be
        # mirrored while its siblings are not), so they keep the indexed
        # subject. Mesh and physics warnings are properties of the COMPONENT
        # and identical for every instance -- reporting them per instance
        # defeated Warnings' dedupe (its key includes the subject) and would
        # put thousands of identical records in a foliage level's manifest.
        world, local, mirrored = _child_transforms(
            world_ue, actor, instance_subject, warnings)
        mesh_block, mesh_guid = _mesh_block_from_component(
            component, assets, subject, warnings, mirrored=mirrored)
        if mesh_block is None:
            break   # no mesh on the component: nothing to place, once
        entity = {
            "id": naming.entity_id("%s:%s#%d" % (actor_path,
                                                 component.get_name(), index)),
            "name": instance_subject,
            "ue_class": component.get_class().get_name(),
            "ue_actor_path": "%s:%s#%d" % (actor_path, component.get_name(),
                                           index),
            "kind": "static_mesh",
            "parent_id": actor_entity["id"],
            "mobility": _enum_name(_field(component, "mobility"), "static"),
            "transform": {"world": world, "local": local},
            "mesh": mesh_block,
        }
        physics = _physics_block(component, mesh_guid, subject, warnings)
        if physics["has_collision"]:
            entity["physics"] = physics
        children.append(entity)
    return children


def _terrain_entity(actor, entity, assets, warnings):
    """A Landscape actor -> a static-mesh entity over a '#terrain' asset (M7).

    The terrain mesh is BAKED IN WORLD SPACE (mesh_export samples heights by
    per-component line traces and builds a grid), so the entity's transform
    is forced to identity -- decomposing a scale-100 landscape transform buys
    nothing and multiplies the ways the bake can disagree with the placement.
    Physics: collision source "none" sends the importer down its existing
    render-mesh triangle-collider path, which is exactly the plan's v1
    terrain physics. Returns the mesh guid (for the physics block).
    """
    guid = assets.add_terrain(actor, warnings)
    if guid is None:
        warnings.add("ACTOR_DEFERRED", entity["name"],
                     "Landscape could not be baked (no collision components)")
        return None
    entity["kind"] = "static_mesh"
    identity = {"translation": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "scale": [1.0, 1.0, 1.0]}
    entity["transform"] = {"world": dict(identity), "local": dict(identity)}

    material = None
    try:
        material = actor.get_editor_property("landscape_material")
    except Exception:
        pass
    material_guid = assets.add_material(material) if material is not None else None
    if material is not None:
        warnings.add("TERRAIN_LAYERS_FLATTENED", entity["name"],
                     "landscape layer blending is approximated by the single "
                     "converted material %r (the classifier's nearest-texture "
                     "rule picks one layer per channel)" % material.get_name())
    entity["mesh"] = {"asset_guid": guid,
                      "material_slots": [{"index": 0, "slot_name": "Terrain",
                                          "material_guid": material_guid}]}

    component = (actor.get_components_by_class(
        getattr(unreal, "LandscapeHeightfieldCollisionComponent", unreal.SceneComponent))
        or [None])[0]
    if component is not None:
        physics = _physics_block(component, guid, entity["name"], warnings)
        if physics["has_collision"]:
            entity["physics"] = physics
    return guid


def _build_entity(actor, assets, warnings):
    """One actor -> its entity, plus any component-extracted child entities."""
    actor_path = actor.get_path_name()
    label = actor.get_actor_label()
    kind = _classify(actor)

    # Only static meshes have a mirror-X variant bake to reference; every
    # other kind must take the unmirrored fallback rather than keep a fold
    # rotation that compensates for geometry that never arrives.
    transforms, mirrored = _actor_transforms(
        actor, label, warnings, mirror_variant_available=(kind == "static_mesh"))
    entity = {
        "id": naming.entity_id(actor_path),
        "name": label,
        "ue_class": actor.get_class().get_name(),
        "ue_actor_path": actor_path,
        "kind": kind,
        "parent_id": None,
        "mobility": "static",
        "transform": transforms,
    }
    extracted = []

    parent = actor.get_attach_parent_actor()
    if parent is not None:
        entity["parent_id"] = naming.entity_id(parent.get_path_name())

    root = actor.root_component
    if root is not None:
        entity["mobility"] = _enum_name(_field(root, "mobility"), "static")

    mesh_guid = None
    if kind == "static_mesh":
        mesh_block, mesh_guid = _mesh_block(actor, assets, label, warnings,
                                            mirrored=mirrored)
        if mesh_block is not None:
            entity["mesh"] = mesh_block

    if kind == "skeletal_mesh":
        # `mirrored` is always False here since M9's review: skeletal actors
        # take the unmirrored fallback in _actor_transforms (which reports
        # XFORM_NEGATIVE_SCALE itself), because folding would leave a
        # SIGMA_rot in the rotation waiting on a skinned mirror bake that
        # cannot exist. Kept as a guard, not as the reporting path.
        if mirrored:
            # A skeletal mirror variant would need a mirrored SKINNED bake,
            # which the native exporter cannot produce; the fold already made
            # the scale positive, the mirror itself is dropped.
            warnings.add("XFORM_NEGATIVE_SCALE", label,
                         "mirrored skeletal actor; no skinned mirror variant "
                         "exists, imported unmirrored")
        skeletal_block, mesh_guid = _skeletal_block(
            actor.skeletal_mesh_component, assets, label, warnings)
        if skeletal_block is not None:
            entity["skeletal"] = skeletal_block

    if kind == "decal":
        decal = _decal_block(actor, assets, label, warnings)
        if decal is not None:
            entity["decal"] = decal

    if kind == "camera":
        camera = _camera_block(actor, label, warnings)
        if camera is not None:
            entity["camera"] = camera

    if kind == "light":
        light = _light_block(actor)
        if light is not None:
            entity["light"] = light

    if kind == "environment":
        environment = _environment_block(actor, label, warnings)
        if environment is not None:
            entity["environment"] = environment
        else:
            warnings.add("ACTOR_DEFERRED", label,
                         actor.get_class().get_name() + " is imported in M6")
    elif kind == "unknown":
        class_name = actor.get_class().get_name()
        if class_name == "Landscape":
            mesh_guid = _terrain_entity(actor, entity, assets, warnings)
        elif class_name in _DEFERRED_UNKNOWN_CLASSES:
            warnings.add("ACTOR_DEFERRED", label,
                         _DEFERRED_UNKNOWN_CLASSES[class_name])
        else:
            extracted = _extract_mesh_components(actor, entity, assets, warnings)
            if not extracted:
                warnings.add("ACTOR_CLASS_UNMAPPED", label,
                             "no v1 mapping for " + class_name)

    component = _primitive_component(actor)
    if component is not None:
        physics = _physics_block(component, mesh_guid, label, warnings)
        if kind == "skeletal_mesh":
            # UE serves skeletal collision from the per-bone PhysicsAsset;
            # per-bone bodies have no v1 mapping, and a static trimesh of a
            # bind pose would be worse than nothing on an animated character.
            if physics["has_collision"]:
                warnings.add("SKEL_PHYSICS_DROPPED", label,
                             "collision_enabled on the skeletal component")
        # "No collision -> render-only entity, no physics components" (plan M3).
        # Emitting a block that says has_collision=false for every light and fog
        # actor would only give the O3DE side something to ignore.
        elif physics["has_collision"]:
            entity["physics"] = physics
            # A trigger volume is a trigger whatever its declared class.
            if physics["is_trigger"]:
                entity["kind"] = "trigger"

    return [entity] + extracted


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
    """Export `map_path` to `output_path`.

    Returns (document, warnings, asset_table); the caller runs
    `asset_table.texture_bank.export_all(...)` to write the texture files the
    manifest references (M4).

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
            entities.extend(_build_entity(actor, assets, warnings))
    except ExportAborted as exc:
        abort_reason = str(exc)
        entities = []

    document = manifest_module.build(
        level=level_info,
        assets=sorted(assets.entries() + assets.texture_bank.entries(),
                      key=lambda e: (e["ue_path"], e.get("role") or "",
                                     e.get("channel") or "")),
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
    return document, warnings, assets
