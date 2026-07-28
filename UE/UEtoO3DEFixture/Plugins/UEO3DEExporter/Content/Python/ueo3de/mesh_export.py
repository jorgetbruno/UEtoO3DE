"""
mesh_export.py — FBX export of the static meshes a manifest references (plan M2).

--------------------------------------------------------------------------
Lane B, third correction (2026-07-27): SceneAPI ROTATES, it does not mirror
--------------------------------------------------------------------------
UE is left-handed and O3DE is right-handed, so the product geometry must end
up carrying Lane A's basis map: negate Y, /100. The measured stages:

    1. this module bakes `scale_mesh(-1, -1, 1)` into a temp asset     (ours)
    2. UE's FBX exporter negates Y (LH -> RH conversion)               (always)
    3. O3DE SceneAPI applies a 180-degree yaw, diag(-1,-1,1) -- a
       PROPER rotation. Honouring a declared coordinate frame means
       rotating into it; an importer never mirrors.                    (always)

Net: diag(-1,-1,1) * diag(1,-1,1) * diag(-1,-1,1) = diag(1,-1,1) = Lane A.
Units: SceneAPI honours `UnitScaleFactor` (cm -> m), so no scale rule.

Three corrections, each found where the last one's evidence stopped:

  * M0's spike S0.2 concluded SceneAPI applies "no unit conversion, no axis
    conversion" from product metadata and ratios; absolute floats refuted
    both (the 100x-too-small bench, the Y-mirrored nub).
  * M2 briefly removed the bake because stages 1+2 cancel at the FBX level;
    true and irrelevant, because stage 3 changes the geometry again.
  * M4.5 (this correction): the byte tests asserted Y only, and stage 3's
    map was recorded as "negates Y" when it is actually diag(-1,-1,1) --
    the two are indistinguishable on the Y axis. With the old (1,-1,1)
    bake the product came out diag(-1,-1,1)(source): every mesh locally
    X-mirrored, self-consistently (colliders bake from the same geometry),
    invisible on symmetric meshes. The letterF product had its vertex mass
    at +0.5 m where the source has it at -0.5 m. Found by an adversarial
    review agent refusing to trust the Y-only evidence.

The permanent assertions live at the PRODUCT level on BOTH asymmetric axes
(`Tests/m2/test_m2_artifacts.py`, float byte-pattern counts for X and Y).
The FBX-level check asserts the intermediate is mirror-X(source) for normal
entries and verbatim source for mirrored variants (stages 1+2 net
diag(-1,1,1) and identity respectively).

--------------------------------------------------------------------------
Requirements and options
--------------------------------------------------------------------------
The bake needs the **GeometryScripting** plugin. The fixture project enables
it; for arbitrary source projects `export_level.bat` passes
`-EnablePlugins=GeometryScripting` on the command line. A missing plugin
fails loudly at the first `DynamicMesh()`, never silently.

`collision = False` and `level_of_detail = False` are set deliberately: UCX_
and LOD nodes would reach SceneAPI as extra meshes the `.assetinfo` does not
name. Collision travels in the manifest.

Material slots: the baked temp asset carries the SOURCE asset's material list
(`static_materials`, verbatim), so the FBX carries one material per used slot
and the material NAMES are the UE material asset names. That name is what
SceneAPI turns into the azmodel's material slot label, which is what the
importer's per-slot assignment matches on (`prefab_build.assign_material_slots`).
Slot names ("Wood") do NOT survive the FBX -- measured in
`Tests/ue/probe_slots.py`: the FBX contains `M_Fixture_PBR`/`M_Fixture_ORM`
but not `SlotA`/`SlotB` -- so the material asset name is the only label there
is. Per-triangle material IDs survive copy -> scale_mesh -> bake unchanged
(same probe).
"""

import os

import unreal

from . import naming

TEMP_PACKAGE_DIR = "/Game/__UEO3DEExportTemp"


class MeshExportError(Exception):
    pass


def _unwrap(result):
    """UE packs UFUNCTION out-params into a tuple; Geometry Script adds an
    outcome enum pin (ExpandEnumAsExecs) that is never the value we want."""
    if isinstance(result, tuple):
        for item in result:
            if not isinstance(item, unreal.GeometryScriptOutcomePins):
                return item
        return result[0]
    return result


def _make_export_options():
    options = unreal.FbxExportOption()
    required = {
        "collision": False,
        "level_of_detail": False,
    }
    for name, value in required.items():
        try:
            options.set_editor_property(name, value)
        except Exception as exc:
            raise MeshExportError(
                "FbxExportOption.%s could not be set (%s); the exported FBX "
                "would carry nodes the importer does not expect" % (name, exc))
    return options


def _baked_dynamic_mesh(source_mesh, mirrored=False):
    """LOD0 render geometry with the bake-stage negations applied.

    Normal bake: `scale_mesh(-1,-1,1)`. THIS IS LANE B'S THIRD CORRECTION
    (2026-07-27; LANE_B.md carries the full story). The old bake was
    (1,-1,1) on the belief that SceneAPI's axis conversion negates Y. Byte
    reads of the product position buffers refuted that: SceneAPI applies a
    180-degree yaw, diag(-1,-1,1) -- a PROPER rotation, which is what
    honouring a declared coordinate frame actually means; importers rotate,
    they never mirror. Under the old bake the product came out as
    diag(-1,-1,1)(source) instead of the diag(1,-1,1)(source) that Lane A's
    entity rotations require -- every imported mesh was locally X-mirrored,
    perfectly self-consistently (colliders bake from the same geometry), so
    only an asymmetric mesh could reveal it and only on the X axis nobody's
    test asserted. The letterF product had 45 vertices at +0.5 m where the
    source has its mass at -0.5 m.

    Chain check with the corrected bake: SceneAPI * Export * bake =
    diag(-1,-1,1) * diag(1,-1,1) * diag(-1,-1,1) = diag(1,-1,1) = Lane A's
    basis map. Determinant of the bake is +1, so winding is untouched
    (verified by signed volume in `Tests/ue/probe_mirror_bake.py`).

    `mirrored=True` is the negative-scale VARIANT bake: the canonical
    mirror-about-X on top, i.e. `scale_mesh(1,-1,1)` (Mx * (-1,-1,1) =
    (1,-1,1)). Its determinant is -1 and `scale_mesh` fixes winding itself
    (measured in `Tests/ue/probe_m2_mirror2.py`) -- no manual flip in either
    path.
    """
    dyn = unreal.DynamicMesh()
    copy_options = unreal.GeometryScriptCopyMeshFromAssetOptions()
    requested_lod = unreal.GeometryScriptMeshReadLOD()
    # The render mesh is what the FBX exporter writes, so read the same thing.
    requested_lod.set_editor_property("lod_type", unreal.GeometryScriptLODType.RENDER_DATA)
    dyn = _unwrap(unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        source_mesh, dyn, copy_options, requested_lod))
    if dyn is None:
        raise MeshExportError("copy_mesh_from_static_mesh returned no mesh")

    bake_x = 1.0 if mirrored else -1.0
    dyn = _unwrap(unreal.GeometryScript_MeshTransforms.scale_mesh(
        dyn, unreal.Vector(bake_x, -1.0, 1.0), unreal.Vector(0.0, 0.0, 0.0)))
    if dyn is None:
        raise MeshExportError("scale_mesh returned no mesh")
    return dyn


def _triangle_material_ids(dyn):
    """Per-triangle material IDs as a plain list (index == triangle id; the
    dynamic mesh is always a fresh compact copy here)."""
    result = unreal.GeometryScript_Materials.get_all_triangle_material_i_ds(dyn)
    id_list = None
    for item in (result if isinstance(result, tuple) else (result,)):
        if isinstance(item, unreal.GeometryScriptIndexList):
            id_list = item
    if id_list is None:
        raise MeshExportError("get_all_triangle_material_i_ds returned no index list")
    return list(unreal.GeometryScript_List.convert_index_list_to_array(id_list))


def _compact_slots(dyn, source):
    """Compact the mesh's material IDs to 0..n-1 and return the matching
    source slot list.

    The bake creates one slot per material ID, so IDs must be contiguous or
    the baked asset's slot indices would not line up with the slot list we
    attach. Sparse IDs (a source slot unused by LOD0) are remapped here,
    deterministically, rather than trusting the bake to compact them.
    """
    ids = _triangle_material_ids(dyn)
    used = sorted(set(ids)) if ids else [0]
    if used != list(range(len(used))):
        remap = {old: new for new, old in enumerate(used)}
        for triangle, material_id in enumerate(ids):
            dyn.set_triangle_material_id(triangle, remap[material_id])

    source_slots = list(source.get_editor_property("static_materials") or [])
    slots = []
    for old_id in used:
        if old_id < len(source_slots):
            entry = source_slots[old_id]
            if _field_material(entry) is None:
                # UE's FBX exporter DROPS a null-material slot -- the model
                # would have fewer slots than the manifest, and an actor
                # override of this slot could never be assigned. Substitute a
                # placeholder whose NAME both sides derive from the original
                # slot index (naming.empty_slot_label).
                entry = _placeholder_slot(entry, old_id)
            slots.append(entry)
        else:
            # A material ID beyond the source slot list: keep the index space
            # aligned; same placeholder treatment so the slot survives.
            entry = unreal.StaticMaterial()
            entry.set_editor_property("material_interface",
                                      _placeholder_material(old_id))
            slots.append(entry)
    return slots


def _field_material(static_material):
    try:
        return static_material.get_editor_property("material_interface")
    except Exception:
        return None


_placeholder_cache = {}


def _placeholder_material(slot_index):
    """A transient material named for the slot; lives in the temp package."""
    label = naming.empty_slot_label(slot_index)
    cached = _placeholder_cache.get(label)
    if cached is not None:
        return cached
    path = TEMP_PACKAGE_DIR + "/" + label
    material = unreal.EditorAssetLibrary.load_asset(path)
    if material is None:
        tools = unreal.AssetToolsHelpers.get_asset_tools()
        material = tools.create_asset(label, TEMP_PACKAGE_DIR, unreal.Material,
                                      unreal.MaterialFactoryNew())
    if material is None:
        raise MeshExportError("could not create placeholder material " + label)
    _placeholder_cache[label] = material
    return material


def _placeholder_slot(source_slot, slot_index):
    entry = unreal.StaticMaterial()
    try:
        entry.set_editor_property(
            "material_slot_name",
            source_slot.get_editor_property("material_slot_name"))
    except Exception:
        pass
    entry.set_editor_property("material_interface",
                              _placeholder_material(slot_index))
    return entry


def _bake_temp_asset(dyn, asset_name):
    """Bake to a temp StaticMesh named after the SOURCE asset.

    The FBX mesh node is named after the asset, and the importer's
    `.assetinfo` references `RootNode.<node name>` exactly -- a wrong name
    fails the AP job outright (LANE_B.md).
    """
    temp_path = TEMP_PACKAGE_DIR + "/" + asset_name
    if unreal.EditorAssetLibrary.does_asset_exist(temp_path):
        unreal.EditorAssetLibrary.delete_asset(temp_path)

    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    # Collision travels in the manifest; a baked body setup would only end up
    # as UCX_ nodes in the FBX.
    options.set_editor_property("enable_collision", False)
    baked = _unwrap(unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        dyn, temp_path, options))
    if baked is None:
        raise MeshExportError("create_new_static_mesh_asset_from_mesh failed for " + temp_path)
    return temp_path, baked


def _export_fbx(asset, output_path, options):
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    task = unreal.AssetExportTask()
    task.object = asset
    task.filename = output_path
    task.automated = True
    task.replace_identical = True
    task.prompt = False
    task.options = options
    if not unreal.Exporter.run_asset_export_task(task):
        raise MeshExportError("FBX export failed for " + output_path)
    if not os.path.exists(output_path):
        raise MeshExportError("FBX export reported success but wrote nothing: " + output_path)


# ---------------------------------------------------------------------------
# terrain (M7): Landscape -> world-space grid mesh, by line-traced heights
# ---------------------------------------------------------------------------
# Grid spacing in cm. 200 (2 m) puts a 500 m landscape at ~127k triangles --
# reasonable for one render mesh and its triangle collider; overridable for
# denser terrains via UEO3DE_TERRAIN_SPACING.
TERRAIN_SPACING_CM = float(os.environ.get("UEO3DE_TERRAIN_SPACING", "200"))
# The exporter refuses to ship a terrain whose sampled mesh disagrees with a
# direct trace by more than this (cm).
TERRAIN_TOLERANCE_CM = 1.0


def _trace_component_height(component, x, y, z_top, z_bottom):
    """Height of `component`'s collision under (x, y), or None.

    K2_LineTraceComponent traces ONE component -- no filtering problem with
    the 2900 meshes sitting on the terrain. Requires a full-editor session:
    commandlets have no physics scene (measured, probe_m7_geometry)."""
    try:
        result = component.line_trace_component(
            unreal.Vector(x, y, z_top), unreal.Vector(x, y, z_bottom),
            True, False)
    except Exception:
        return None
    if isinstance(result, tuple):
        hit = bool(result[0])
        location = result[1] if len(result) > 1 else None
    else:
        hit, location = bool(result), None
    if not hit or location is None:
        return None
    return float(location.z)


def _terrain_grid(actor, log):
    """Sampled heights for a Landscape. Returns (xs, ys, zs, samples).

    `zs[j][i]` is the world-space height at (xs[i], ys[j]); `samples` is five
    (x, y, z) verification points re-traced independently -- they ship to
    `terrain_samples.json` for the M7 sphere-drop acceptance and double as
    the exporter's own self-check.
    """
    origin, extent = actor.get_actor_bounds(False)
    z_top, z_bottom = origin.z + extent.z + 1000.0, origin.z - extent.z - 1000.0

    components = list(actor.get_components_by_class(
        unreal.LandscapeHeightfieldCollisionComponent) or [])
    if not components:
        raise MeshExportError("landscape has no heightfield collision components")
    lookup = []
    for component in components:
        # Component bounds via the K2 helper (Bounds is not a plain
        # property). It returns (origin, box extent, sphere radius).
        bounds = unreal.SystemLibrary.get_component_bounds(component)
        lookup.append((component, bounds[0], bounds[1]))

    def component_for(x, y):
        for component, c_origin, c_extent in lookup:
            if (abs(x - c_origin.x) <= c_extent.x + 1.0
                    and abs(y - c_origin.y) <= c_extent.y + 1.0):
                return component
        return None

    def height(x, y):
        component = component_for(x, y)
        if component is None:
            return None
        return _trace_component_height(component, x, y, z_top, z_bottom)

    min_x, max_x = origin.x - extent.x, origin.x + extent.x
    min_y, max_y = origin.y - extent.y, origin.y + extent.y
    nx = max(2, int(round((max_x - min_x) / TERRAIN_SPACING_CM)))
    ny = max(2, int(round((max_y - min_y) / TERRAIN_SPACING_CM)))
    xs = [min_x + (max_x - min_x) * i / nx for i in range(nx + 1)]
    ys = [min_y + (max_y - min_y) * j / ny for j in range(ny + 1)]

    zs = []
    misses = 0
    last_good = origin.z
    for y in ys:
        row = []
        for x in xs:
            z = height(x, y)
            if z is None:
                misses += 1
                z = last_good     # landscape holes / edge texels: keep C0
            else:
                last_good = z
            row.append(z)
        zs.append(row)
    total = (nx + 1) * (ny + 1)
    if log is not None:
        log("  terrain grid %dx%d (%.0f cm spacing), %d samples, %d misses"
            % (nx + 1, ny + 1, TERRAIN_SPACING_CM, total, misses))
    if misses > total * 0.05:
        raise MeshExportError(
            "terrain sampling missed %d of %d points (>5%%); the collision "
            "lookup or trace is broken, refusing to ship a guessed surface"
            % (misses, total))

    # Verification points sit EXACTLY on grid nodes, so the baked mesh's
    # height there equals the grid value with no interpolation term -- the
    # sphere-drop acceptance can then use a tight tolerance. Each point is
    # re-traced independently and compared against the grid: a row/column or
    # axis mix-up shows up as metres, not millimetres.
    samples = []
    for fx, fy in ((0.5, 0.5), (0.2, 0.2), (0.8, 0.8), (0.2, 0.8), (0.8, 0.2)):
        i = min(nx, max(0, int(round(nx * fx))))
        j = min(ny, max(0, int(round(ny * fy))))
        x, y = xs[i], ys[j]
        z = height(x, y)
        if z is None:
            raise MeshExportError(
                "terrain verification point (%.0f, %.0f) did not trace" % (x, y))
        if abs(z - zs[j][i]) > TERRAIN_TOLERANCE_CM:
            raise MeshExportError(
                "terrain self-check failed at node (%d, %d): re-trace z=%.2f "
                "vs grid z=%.2f -- axis or indexing bug, refusing to export"
                % (i, j, z, zs[j][i]))
        samples.append((x, y, zs[j][i]))
    return xs, ys, zs, samples


def _terrain_dynamic_mesh(actor, log):
    """The world-space grid mesh for a Landscape, plus its samples/bounds."""
    xs, ys, zs, samples = _terrain_grid(actor, log)
    nx, ny = len(xs) - 1, len(ys) - 1

    dyn = unreal.DynamicMesh()
    opts = unreal.GeometryScriptPrimitiveOptions()
    origin = unreal.Transform(location=unreal.Vector(
        (xs[0] + xs[-1]) * 0.5, (ys[0] + ys[-1]) * 0.5, 0.0))
    dyn = _unwrap(unreal.GeometryScript_Primitives.append_rectangle_xy(
        dyn, opts, origin, xs[-1] - xs[0], ys[-1] - ys[0], nx, ny))
    if dyn is None:
        raise MeshExportError("append_rectangle_xy failed for the terrain grid")

    # Lift each vertex to its sampled height. Vertex positions are the grid
    # intersections by construction; the nearest sample is exact.
    count = dyn.get_num_vertex_i_ds() if hasattr(dyn, "get_num_vertex_i_ds") \
        else unreal.GeometryScript_MeshQueries.get_num_vertex_i_ds(dyn)
    step_x = (xs[-1] - xs[0]) / nx
    step_y = (ys[-1] - ys[0]) / ny
    for vertex_id in range(count):
        result = unreal.GeometryScript_MeshQueries.get_vertex_position(
            dyn, vertex_id)
        position = None
        for item in (result if isinstance(result, tuple) else (result,)):
            if isinstance(item, unreal.Vector):
                position = item
        if position is None:
            continue
        i = min(nx, max(0, int(round((position.x - xs[0]) / step_x))))
        j = min(ny, max(0, int(round((position.y - ys[0]) / step_y))))
        unreal.GeometryScript_MeshEdits.set_vertex_position(
            dyn, vertex_id, unreal.Vector(position.x, position.y, zs[j][i]),
            False)

    # (Sample-vs-grid consistency is asserted inside _terrain_grid, exactly
    # on grid nodes.)
    z_low = min(min(row) for row in zs)
    z_high = max(max(row) for row in zs)
    z_range = max(1e-3, z_high - z_low)
    height_rows = [[int(255.0 * (z - z_low) / z_range) for z in row]
                   for row in zs]

    bounds_min = [xs[0], ys[0], z_low]
    bounds_max = [xs[-1], ys[-1], z_high]
    return dyn, samples, (bounds_min, bounds_max), height_rows


def _export_terrain(asset, actor_path, output_root, options, emit):
    """Bake and export one Landscape's terrain entry. Returns the record.

    Side artifacts, written next to the manifest (NOT under Assets/):
      terrain_samples.json  five (x, y, z) surface points, Lane-A converted
                            to O3DE metres -- the M7 sphere-drop acceptance
                            drops probes exactly there;
      <name>_heightmap.tga  8-bit visualization/stretch-path heightmap.
    """
    from . import lane_a, tga

    actor = _find_level_actor(actor_path)
    if actor is None:
        raise MeshExportError(
            "terrain actor %r is not in the open level (terrain export "
            "requires the level session that produced the manifest)" % actor_path)

    dyn, samples, (bounds_min, bounds_max), height_rows = \
        _terrain_dynamic_mesh(actor, emit)

    # The normal Lane B bake (terrain is never a mirror variant).
    dyn = _unwrap(unreal.GeometryScript_MeshTransforms.scale_mesh(
        dyn, unreal.Vector(-1.0, -1.0, 1.0), unreal.Vector(0.0, 0.0, 0.0)))
    if dyn is None:
        raise MeshExportError("terrain bake scale_mesh failed")

    node_name = asset["fbx_node_name"]
    output_path = os.path.join(output_root, asset["o3de_relative_path"]).replace("\\", "/")
    temp_path, baked = _bake_temp_asset(dyn, node_name)
    try:
        material = None
        try:
            material = actor.get_editor_property("landscape_material")
        except Exception:
            pass
        if material is not None:
            entry = unreal.StaticMaterial()
            entry.set_editor_property("material_slot_name", "Terrain")
            entry.set_editor_property("material_interface", material)
            baked.set_editor_property("static_materials", [entry])
        _export_fbx(baked, output_path, options)
    finally:
        unreal.EditorAssetLibrary.delete_asset(temp_path)

    export_root = os.path.dirname(os.path.normpath(output_root))
    samples_path = os.path.join(export_root, "terrain_samples.json")
    import json
    with open(samples_path, "w") as handle:
        json.dump({
            "comment": "world-space terrain surface points, O3DE metres "
                       "(Lane A converted); the M7 acceptance drops spheres "
                       "exactly here",
            "samples": [lane_a.convert_position([x, y, z])
                        for x, y, z in samples],
        }, handle, indent=2)
    heightmap_path = os.path.join(export_root, node_name + "_heightmap.tga")
    tga.write_grayscale(heightmap_path, len(height_rows[0]), len(height_rows),
                        height_rows)
    emit("  terrain samples -> %s; heightmap -> %s"
         % (samples_path, heightmap_path))

    # The FBX intermediate follows the normal-entry rule: mirror-X of the
    # (world-space) geometry the grid sampled.
    return {
        "guid": asset["guid"],
        "ue_path": asset["ue_path"],
        "relative_path": asset["o3de_relative_path"],
        "node_name": node_name,
        "ue_bounds_min": [-bounds_max[0], bounds_min[1], bounds_min[2]],
        "ue_bounds_max": [-bounds_min[0], bounds_max[1], bounds_max[2]],
        "bytes": os.path.getsize(output_path),
    }


def _find_level_actor(actor_path):
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in subsystem.get_all_level_actors() or []:
        if actor.get_path_name() == actor_path:
            return actor
    return None


def source_bounds(mesh):
    """The asset's local AABB in UE space (centimetres)."""
    box = mesh.get_bounding_box()
    return ([box.min.x, box.min.y, box.min.z], [box.max.x, box.max.y, box.max.z])


def export_meshes(assets, output_root, log=None):
    """Export every static_mesh asset entry to `<output_root>/<relative path>`.

    Per mesh: copy LOD0 -> mirror (bake stage) -> temp asset -> FBX -> delete
    temp. Returns one record per exported FBX -- one per unique GUID -- with
    the source asset's UE-space bounds attached so the caller can verify the
    written file without reopening the asset.
    """
    def emit(message):
        if log is not None:
            log(message)

    options = _make_export_options()
    exported = []
    seen_guids = set()

    for asset in assets:
        if asset.get("kind") != "static_mesh":
            continue
        guid = asset["guid"]
        if guid in seen_guids:
            raise MeshExportError("duplicate static mesh GUID in manifest: " + guid)
        seen_guids.add(guid)

        # ue_path fragments mark the special bakes: '#mx' is the mirrored
        # variant of a real mesh asset, '#terrain' is a Landscape ACTOR baked
        # to a world-space grid.
        base_path, _, fragment = asset["ue_path"].partition("#")
        mirrored = fragment == "mx"

        if fragment == "terrain":
            record = _export_terrain(asset, base_path, output_root, options, emit)
            exported.append(record)
            emit("  %-42s -> %-46s (%d bytes, node %r, TERRAIN)"
                 % (base_path, asset["o3de_relative_path"],
                    record["bytes"], record["node_name"]))
            continue

        source = unreal.EditorAssetLibrary.load_asset(base_path)
        if source is None:
            raise MeshExportError("could not load source mesh " + base_path)

        # The node name comes from the manifest, not the asset: the variant's
        # node is <name>_MX and the `.assetinfo` selection references it
        # exactly (a mismatch fails the AP job outright, per LANE_B.md).
        node_name = asset.get("fbx_node_name") or source.get_name()
        output_path = os.path.join(output_root, asset["o3de_relative_path"]).replace("\\", "/")

        dyn = _baked_dynamic_mesh(source, mirrored=mirrored)
        slots = _compact_slots(dyn, source)
        temp_path, baked = _bake_temp_asset(dyn, node_name)
        try:
            # The FBX carries one material per slot, named after the UE
            # material asset -- the label the importer assigns by. Set for
            # every mesh (single-slot included) so labels are always real
            # material names, never the bake's WorldGridMaterial default.
            baked.set_editor_property("static_materials", slots)
            _export_fbx(baked, output_path, options)
        finally:
            unreal.EditorAssetLibrary.delete_asset(temp_path)

        bounds_min, bounds_max = source_bounds(source)
        if not mirrored:
            # With the corrected bake the NORMAL FBX intermediate is
            # mirror-X(source) (bake (-1,-1,1) then UE's export Y-negation
            # leaves net diag(-1,1,1)); the VARIANT's is verbatim source.
            # The export verifiers (export_fixture / export_level) compare
            # the written FBX against these bounds, so mirror the normal
            # entries' expectation and leave the variant's alone.
            bounds_min, bounds_max = ([-bounds_max[0], bounds_min[1], bounds_min[2]],
                                      [-bounds_min[0], bounds_max[1], bounds_max[2]])
        exported.append({
            "guid": guid,
            "ue_path": asset["ue_path"],
            "relative_path": asset["o3de_relative_path"],
            "node_name": node_name,
            "ue_bounds_min": bounds_min,
            "ue_bounds_max": bounds_max,
            "bytes": os.path.getsize(output_path),
        })
        emit("  %-42s -> %-46s (%d bytes, node %r)"
             % (asset["ue_path"], asset["o3de_relative_path"],
                exported[-1]["bytes"], node_name))

    if unreal.EditorAssetLibrary.does_directory_exist(TEMP_PACKAGE_DIR):
        unreal.EditorAssetLibrary.delete_directory(TEMP_PACKAGE_DIR)
    # The placeholders lived in the temp dir just deleted; a second export in
    # the same editor session must recreate them, not reuse dead pointers.
    _placeholder_cache.clear()

    return exported


# ---------------------------------------------------------------------------
# skeletal meshes + animations (M8): UE's NATIVE FBX exporter
# ---------------------------------------------------------------------------
# No GeometryScript bake is possible without destroying skinning, so skeletal
# sources ship through UE's own exporter and carry the SKELETAL Lane B rule:
# FBX = diag(1,-1,1) * source (the writer's LH->RH negation), SceneAPI then
# applies its diag(-1,-1,1) yaw, net product = diag(-1,1,1) * source =
# LaneA * Rz180 -- the importer composes a local Rz180 into skeletal entity
# rotations (LANE_B.md, M8). Measured: product Y negated vs FBX and Z kept,
# at azmodel-buffer byte level (the probe character's X is symmetric; the X
# sign follows from SceneAPI being a rotation, the load-bearing fact of Lane
# B correction #3).
#
# FULL-EDITOR ONLY: the skeletal FBX exporter walks render objects that do
# not exist in commandlets (Assertion failed: MeshObject, SkinnedMeshComponent
# .cpp:4987 -- measured, probe_m8_skeletal.py round 1).

def _skeletal_export_options(preview_mesh):
    options = unreal.FbxExportOption()
    required = {
        "collision": False,
        "level_of_detail": False,
        "export_preview_mesh": preview_mesh,
    }
    for name, value in required.items():
        try:
            options.set_editor_property(name, value)
        except Exception as exc:
            raise MeshExportError(
                "FbxExportOption.%s could not be set (%s)" % (name, exc))
    return options


def export_skeletal(assets, output_root, log=None):
    """Export every skeletal_mesh and animation asset entry to FBX.

    Returns one record per file. Mesh records carry expected FBX bounds =
    mirror-Y(source bounds) -- stage 2's negation with NO bake stage 1.
    Animation records carry no geometry at all (export_preview_mesh False);
    they are byte-checked here for animation curves instead, loudly.
    """
    def emit(message):
        if log is not None:
            log(message)

    exported = []
    for asset in assets:
        kind = asset.get("kind")
        if kind not in ("skeletal_mesh", "animation"):
            continue
        source = unreal.EditorAssetLibrary.load_asset(asset["ue_path"])
        if source is None:
            raise MeshExportError("could not load skeletal source " + asset["ue_path"])
        output_path = os.path.join(
            output_root, asset["o3de_relative_path"]).replace("\\", "/")
        _export_fbx(source, output_path,
                    _skeletal_export_options(preview_mesh=False))

        record = {
            "guid": asset["guid"],
            "kind": kind,
            "ue_path": asset["ue_path"],
            "relative_path": asset["o3de_relative_path"],
            "bytes": os.path.getsize(output_path),
        }
        if kind == "skeletal_mesh":
            bounds = source.get_bounds()
            origin = bounds.get_editor_property("origin")
            extent = bounds.get_editor_property("box_extent")
            source_min = [origin.x - extent.x, origin.y - extent.y,
                          origin.z - extent.z]
            source_max = [origin.x + extent.x, origin.y + extent.y,
                          origin.z + extent.z]
            # Native export negates Y only (measured on SM_LetterF in S0.2);
            # min/max swap on the negated axis.
            record["ue_bounds_min"] = [source_min[0], -source_max[1], source_min[2]]
            record["ue_bounds_max"] = [source_max[0], -source_min[1], source_max[2]]
            # The asset's BoxSphereBounds is not vertex-exact the way a baked
            # static's bounding box is; a centimetre catches axis bugs (which
            # show up as tens of cm) without tripping on bounds padding.
            record["tolerance_cm"] = 1.0
        else:
            with open(output_path, "rb") as handle:
                blob = handle.read()
            if b"AnimationCurveNode" not in blob:
                raise MeshExportError(
                    "%s exported without animation curves; the .motion product "
                    "would be an empty pose" % asset["ue_path"])
            record["duration_seconds"] = asset.get("duration_seconds")
        exported.append(record)
        emit("  %-42s -> %-46s (%d bytes, %s)"
             % (asset["ue_path"], asset["o3de_relative_path"],
                record["bytes"], kind))
    return exported
