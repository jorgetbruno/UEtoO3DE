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

        # A mirrored VARIANT entry (negative-scale fidelity) is marked by a
        # literal `#mx` fragment on its ue_path; strip it to load the real
        # asset, keep it to choose the variant bake.
        mirrored = "#" in asset["ue_path"]
        source_path = asset["ue_path"].split("#", 1)[0]
        source = unreal.EditorAssetLibrary.load_asset(source_path)
        if source is None:
            raise MeshExportError("could not load source mesh " + source_path)

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
