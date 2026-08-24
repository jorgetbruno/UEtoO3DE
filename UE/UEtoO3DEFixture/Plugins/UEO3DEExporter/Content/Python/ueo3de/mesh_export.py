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
importer's per-slot assignment matches on (`prefab_build.finish_material_slots`).
Slot names ("Wood") do NOT survive the FBX -- measured in
`Tests/ue/probe_slots.py`: the FBX contains `M_Fixture_PBR`/`M_Fixture_ORM`
but not `SlotA`/`SlotB` -- so the material asset name is the only label there
is. Per-triangle material IDs survive copy -> scale_mesh -> bake unchanged
(same probe).
"""

import json
import os
import struct

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


def _make_export_options(level_of_detail=False):
    """FbxExportOption for the bake exports.

    `level_of_detail` stays False for single-LOD bakes: True wraps even a
    lone mesh in an FbxLODGroup, which changes EVERY node path and would
    break every existing sidecar. Multi-LOD bakes pass True, and their
    sidecars use the measured LODGroup paths
    (`RootNode.<name>.<name>_LOD<i>`).
    """
    options = unreal.FbxExportOption()
    required = {
        "collision": False,
        "level_of_detail": bool(level_of_detail),
    }
    for name, value in required.items():
        try:
            options.set_editor_property(name, value)
        except Exception as exc:
            raise MeshExportError(
                "FbxExportOption.%s could not be set (%s); the exported FBX "
                "would carry nodes the importer does not expect" % (name, exc))
    return options


def _nanite_enabled(mesh):
    """Does this asset render through Nanite? False when unreadable."""
    try:
        return bool(mesh.get_editor_property("nanite_settings")
                    .get_editor_property("enabled"))
    except Exception:
        return False


_NANITE_ON = ("1", "on", "true", "yes")
_NANITE_OFF = ("", "0", "off", "false", "no")


def _nanite_fallback_forced():
    """UEO3DE_NANITE_FALLBACK -> True to keep exporting the fallback mesh."""
    value = os.environ.get("UEO3DE_NANITE_FALLBACK", "").strip().lower()
    if value in _NANITE_ON:
        return True
    if value in _NANITE_OFF:
        return False
    raise MeshExportError(
        "UEO3DE_NANITE_FALLBACK=%r is not one of %s"
        % (value, ", ".join(_NANITE_ON + _NANITE_OFF[1:])))


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
    lod_type = unreal.GeometryScriptLODType.RENDER_DATA
    if _nanite_enabled(source_mesh) and not _nanite_fallback_forced():
        lod_type = unreal.GeometryScriptLODType.MAX_AVAILABLE
    return _baked_dyn_for_lod(source_mesh, mirrored, lod_type, 0)


def _baked_dyn_for_lod(source_mesh, mirrored, lod_type, lod_index):
    """One LOD's geometry, copied, cleaned, and carrying the Lane B bake."""
    dyn = unreal.DynamicMesh()
    copy_options = unreal.GeometryScriptCopyMeshFromAssetOptions()
    requested_lod = unreal.GeometryScriptMeshReadLOD()
    # For LOD 0 the caller passes MAX_AVAILABLE on Nanite assets (the
    # fallback is not what anyone sees -- SM_Wagon_01a: 8,646 fallback tris
    # vs 93,712 real) and RENDER_DATA otherwise; higher chain entries read
    # RENDER_DATA at their own index. UEO3DE_NANITE_FALLBACK=1 restores the
    # old fallback read for LOD 0.
    requested_lod.set_editor_property("lod_type", lod_type)
    requested_lod.set_editor_property("lod_index", int(lod_index))
    dyn = _unwrap(unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        source_mesh, dyn, copy_options, requested_lod))
    if dyn is None:
        raise MeshExportError("copy_mesh_from_static_mesh returned no mesh")

    _remap_section_ids_to_slots(dyn, source_mesh, requested_lod)

    # ORPHANED VERTICES make the bounds lie. Measured on Docks
    # `SM_Crab_Cages_NN_02a` (Nanite, 863,082 tris): the SOURCE MODEL carries
    # vertices at X=-174.89 cm that NO TRIANGLE references -- editing
    # leftovers outside even the asset's own declared bounds (+/-41 cm). Every
    # exporter drops unreferenced vertices, so the written file was correct;
    # but get_mesh_bounding_box counts them, so the intermediate bounds
    # expectation reached 1.75 m the geometry never does, and the export
    # failed on a file that was RIGHT. Compaction removes exactly the
    # unreferenced data (triangle count measured unchanged: 863,082 in, and
    # the same 863,082 reach the glb) and is a no-op on a mesh with none.
    dyn = _unwrap(unreal.GeometryScript_MeshRepair.remove_unused_vertices(dyn))
    if dyn is None:
        raise MeshExportError("remove_unused_vertices returned no mesh")

    bake_x = 1.0 if mirrored else -1.0
    dyn = _unwrap(unreal.GeometryScript_MeshTransforms.scale_mesh(
        dyn, unreal.Vector(bake_x, -1.0, 1.0), unreal.Vector(0.0, 0.0, 0.0)))
    if dyn is None:
        raise MeshExportError("scale_mesh returned no mesh")

    _drop_lightmap_uvs(dyn, source_mesh)
    return dyn


def _drop_lightmap_uvs(dyn, source_mesh):
    """Discard the lightmap UV set so it cannot become the FBX's UV0.

    UE'S FBX WRITER EMITS THE LIGHTMAP SET FIRST. Measured on 20 of 20 VOL4
    meshes, the exported FBX's UV layers read, in file order:

        LayerElementUV -> "LightMapUV"   <- becomes uv0
        LayerElementUV -> "UVmap_1"      <- becomes uv1

    SceneAPI takes them in order and Atom samples UV0, so every texture was
    sampled through a LIGHTMAP parameterisation -- each face landing on its
    own scrap of the atlas. That is the "wheels and gauges smeared across the
    bodywork" the user photographed. glTF is unaffected: its writer emits
    TEXCOORD_0 as the texture set (measured: TEXCOORD_0 tiles past [0,1] while
    TEXCOORD_1 sits entirely inside it, which is the lightmap's signature).

    The SOURCE is not at fault -- `light_map_coordinate_index` is 1 on every
    asset checked, so UE has the texture UVs at 0 exactly where they belong.
    Only the writer reorders. Dropping the set the asset itself nominates as
    the lightmap leaves the texture UVs alone and gives the writer nothing to
    put first.

    Non-destructive by construction: O3DE builds its own lightmaps and never
    consumes UE's, so this removes data the importer has no use for.
    """
    index = None
    try:
        index = int(source_mesh.get_editor_property("light_map_coordinate_index"))
    except Exception:
        return              # unreadable: leave the mesh exactly as it was
    if index <= 0:
        # 0 would mean the asset nominates the TEXTURE set as its lightmap;
        # trimming there would throw away the only UVs the material has.
        return

    try:
        count = _unwrap(unreal.GeometryScript_MeshQueries.get_num_uv_sets(dyn))
        count = int(count)
    except Exception:
        return
    if count <= index:
        return              # no lightmap set present on the baked mesh

    # set_num_uv_sets TRUNCATES, so this only works when the lightmap is the
    # last set -- which is the measured shape (texture 0, lightmap 1). A mesh
    # with sets beyond the lightmap keeps them all rather than silently
    # discarding a set some material might use.
    if count != index + 1:
        return
    unreal.GeometryScript_UVs.set_num_uv_sets(dyn, index)


def _remap_section_ids_to_slots(dyn, source_mesh, requested_lod):
    """Rewrite the copied mesh's material ids from SECTION ORDINALS to slot
    indices.

    `copy_mesh_from_static_mesh` numbers triangle material ids by SECTION
    ORDER of the read LOD, not by the asset's material slot order. On most
    meshes the two agree; where they do not, every id points at the wrong
    slot and `_compact_slots` labels the geometry with the wrong material
    NAME -- measured on SM_Wagon_01a, whose Nanite-source sections run
    [(MI_Wagon_01a, slot 1), (MI_Wagon_01b, slot 0), ...]: the painted roof
    skin exported under the interior material's name and every distance
    rendered a fabric-bodied wagon, while its sibling SM_Wagon_01b (identity
    section order) exported correctly. SM_Truck_02a's source sections run
    [0,4,5,1,2,3]. The glb exports carried the same bug from the same read.

    `get_section_material_list_from_static_mesh` returns each section's
    MaterialIndex -- the slot index -- for BOTH read paths, including
    MAX_AVAILABLE (probed on 26.05/UE 5.8). Identity maps are left alone;
    a failed query on a permuted mesh must fail the export, not mislabel it.
    """
    result = unreal.GeometryScript_AssetUtils.\
        get_section_material_list_from_static_mesh(source_mesh, requested_lod)
    indices = None
    outcome_ok = True
    for item in (result if isinstance(result, tuple) else (result,)):
        if isinstance(item, unreal.GeometryScriptOutcomePins):
            outcome_ok = item == unreal.GeometryScriptOutcomePins.SUCCESS
    for item in (result if isinstance(result, tuple) else (result,)):
        if isinstance(item, unreal.Array) and len(item) and \
                isinstance(item[0], int):
            indices = list(item)
    if not outcome_ok or indices is None:
        raise MeshExportError(
            "get_section_material_list_from_static_mesh gave no section "
            "material indices for %s" % source_mesh.get_name())
    if indices == list(range(len(indices))):
        return
    ids = _triangle_material_ids(dyn)
    for triangle, section in enumerate(ids):
        if section < len(indices):
            dyn.set_triangle_material_id(triangle, indices[section])
        else:
            raise MeshExportError(
                "%s: triangle %d carries section id %d beyond the %d "
                "sections reported" % (source_mesh.get_name(), triangle,
                                       section, len(indices)))


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


def _compact_slots(dyns, source):
    """Compact material IDs to 0..n-1 across EVERY LOD and return the slots.

    The bake creates one slot per material ID, so IDs must be contiguous or
    the baked asset's slot indices would not line up with the slot list we
    attach. Sparse IDs are remapped here, deterministically, rather than
    trusting the bake to compact them.

    `dyns` is the WHOLE LOD chain, and that is a bug fix, not a convenience:
    the first chain export remapped only LOD0's triangle IDs while LOD1..N
    kept the SOURCE indices -- so any mesh whose LOD0 uses a sparse or
    reordered slot subset rendered its far LODs with the wrong materials
    ("some lods broke the materials": the white wagon, whose LOD0 happens to
    use slots 0..3 in order, was fine; the black car was scrambled). The
    `used` set is the UNION over the chain -- a slot only a far LOD touches
    must still exist in the compacted list -- and the SAME remap is applied
    to every LOD's triangles.
    """
    if not isinstance(dyns, list):
        dyns = [dyns]
    ids_per_dyn = [_triangle_material_ids(d) for d in dyns]
    used = sorted(set(material_id for ids in ids_per_dyn for material_id in ids))
    if not used:
        used = [0]
    if used != list(range(len(used))):
        remap = {old: new for new, old in enumerate(used)}
        for dyn, ids in zip(dyns, ids_per_dyn):
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


_LOD_OPTIONS = []          # built once, on first multi-LOD export


def _lod_export_options():
    if not _LOD_OPTIONS:
        _LOD_OPTIONS.append(_make_export_options(level_of_detail=True))
    return _LOD_OPTIONS[0]


def lod_chain_enabled():
    """UEO3DE_LOD_CHAIN -> export the authored LOD chain (default ON).

    Unrecognised values raise, per house rule.
    """
    value = os.environ.get("UEO3DE_LOD_CHAIN", "").strip().lower()
    if value in _NANITE_ON or value == "":
        return True
    if value in _NANITE_OFF[1:]:
        return False
    raise MeshExportError(
        "UEO3DE_LOD_CHAIN=%r is not one of %s"
        % (value, ", ".join(_NANITE_ON + _NANITE_OFF[1:])))


def _baked_lod_chain(source_mesh, mirrored=False):
    """Every LOD the export should carry, baked, LOD0 first.

    The chain, measured end to end (Tests: probe_write_lods, the LodRule
    sidecar probe on lod_probe_car.fbx -- one azmodel, four azlods, index
    buffers halving with the tri counts):

      * Nanite asset:  [source geometry] + [source SIMPLIFIED to render LOD
        0..N-1's triangle budgets]. NOT the render LODs themselves: on a
        Nanite asset those are the auto fallback chain, which nobody ever
        sees in UE (Nanite renders the source at every distance) and whose
        per-triangle material assignment can disagree with the source
        outright -- measured on SM_Wagon_01b, whose fallback gives the body
        to slot 2 (paint) while the source gives it slot 1 (the junker's
        stripped-body material). Exporting the fallback made the far LODs
        render a clean wagon under an authored wreck ("only the far away
        works", inverted: the near view was the faithful one).
        Simplification preserves per-triangle material ids (measured:
        46,500 -> 5,891 tris, identical per-id regions), so every distance
        now shows what UE shows. SM_Car_24a still comes out
        90,023 / 6,770 / 3,385 / 1,692 / 846.
      * non-Nanite, multiple LODs: [render LOD 0..N-1] -- the authored chain
        exactly as UE renders it.
      * single LOD, non-Nanite: [render LOD0] -- the pipeline's original
        shape, byte-identical exports, no LODGroup wrapper (a lone mesh in a
        group would change every node path the sidecars pin).

    O3DE has no Nanite: without this chain every imported mesh renders its
    full geometry at every distance, which is why a level of 90k-tri cars is
    the fidelity/perf item this exists for.
    """
    single = _baked_dynamic_mesh(source_mesh, mirrored=mirrored)
    if not lod_chain_enabled():
        return [single]
    try:
        lod_count = int(source_mesh.get_num_lods())
    except Exception:
        lod_count = 1
    nanite = _nanite_enabled(source_mesh) and not _nanite_fallback_forced()
    if lod_count <= 1 and not nanite:
        return [single]
    chain = [single]
    if nanite:
        for index in range(lod_count):
            try:
                target = int(source_mesh.get_num_triangles(index))
            except Exception:
                target = 0
            if target <= 0:
                continue
            reduced = _baked_dyn_for_lod(
                source_mesh, mirrored,
                unreal.GeometryScriptLODType.MAX_AVAILABLE, 0)
            reduced = _unwrap(
                unreal.GeometryScript_MeshSimplification.
                apply_simplify_to_triangle_count(
                    reduced, target,
                    unreal.GeometryScriptSimplifyMeshOptions()))
            if reduced is None:
                raise MeshExportError(
                    "apply_simplify_to_triangle_count returned no mesh")
            chain.append(reduced)
    else:
        for index in range(1, lod_count):
            chain.append(_baked_dyn_for_lod(
                source_mesh, mirrored,
                unreal.GeometryScriptLODType.RENDER_DATA, index))
    return chain


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
    chain = dyn if isinstance(dyn, list) else [dyn]
    baked = _unwrap(unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        chain[0], temp_path, options))
    if baked is None:
        raise MeshExportError("create_new_static_mesh_asset_from_mesh failed for " + temp_path)
    # Higher LODs are written into the asset's LOD slots; the FBX exporter
    # then emits the LODGroup (measured: SUCCESS per write, the asset
    # reports the full count, and the file carries <name>_LOD<i> nodes).
    write_options = unreal.GeometryScriptCopyMeshToAssetOptions()
    for index, lod_dyn in enumerate(chain[1:], start=1):
        write_lod = unreal.GeometryScriptMeshWriteLOD()
        write_lod.set_editor_property("lod_index", index)
        outcome = unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh(
            lod_dyn, baked, write_options, write_lod)
        pins = [x for x in (outcome if isinstance(outcome, tuple) else (outcome,))
                if isinstance(x, unreal.GeometryScriptOutcomePins)]
        if pins and pins[0] != unreal.GeometryScriptOutcomePins.SUCCESS:
            raise MeshExportError(
                "writing LOD %d into %s failed (%r); exporting a partial "
                "chain would silently drop the far LODs"
                % (index, temp_path, pins[0]))
    _disable_lightmap_uv_generation(baked, temp_path)
    return temp_path, baked


def _disable_lightmap_uv_generation(baked, temp_path):
    """Stop the temp asset's BUILD from inventing a lightmap UV set.

    THE LIGHTMAP SET IN THE FBX IS NOT COPIED FROM THE SOURCE -- IT IS
    GENERATED HERE. Probed on SM_Car_24a: the source asset nominates UV 1 as
    its lightmap, yet the dynamic mesh copied from it carries exactly ONE UV
    set -- and the FBX still came out with layers [LightMapUV, UVmap_1], 63
    files of 63. UE's default StaticMesh build settings have
    `generate_lightmap_u_vs` ON, so the freshly built temp asset grows a new
    lightmap set at build time, and UE's FBX writer emits that set FIRST.
    SceneAPI reads UV sets in file order and Atom samples UV0, so every
    texture was sampled through a lightmap parameterisation -- the "wheels
    and gauges smeared across the bodywork" failure. (glTF is unaffected: its
    writer keeps the texture set first, which is why the glb path rendered
    correctly with no importer change.)

    An earlier attempt trimmed the DYNAMIC MESH instead
    (`GeometryScript_UVs.set_num_uv_sets`); it verified clean in isolation
    and changed nothing in the written FBX, because it ran before the thing
    it was trying to prevent. The build settings are where the set is born,
    so this is where it is turned off.

    FAILS LOUDLY. O3DE never consumes UE lightmaps, so the set is pure noise
    -- but if this knob cannot be set, the export must not quietly ship the
    smeared-texture FBX again. Silent fallback here is how the first fix
    "worked" without working.
    """
    try:
        subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        for lod_index in range(subsystem.get_lod_count(baked)):
            build = subsystem.get_lod_build_settings(baked, lod_index)
            if not build.get_editor_property("generate_lightmap_u_vs"):
                continue
            build.set_editor_property("generate_lightmap_u_vs", False)
            # Setting build settings through the subsystem triggers the
            # rebuild that makes them take effect in the render data the FBX
            # writer reads.
            subsystem.set_lod_build_settings(baked, lod_index, build)
    except Exception as exc:
        raise MeshExportError(
            "could not disable lightmap UV generation on %s (%s): the "
            "exported FBX would carry LightMapUV as its FIRST uv set, and "
            "every texture in O3DE would sample through the lightmap "
            "parameterisation" % (temp_path, exc))


def _make_gltf_export_options():
    """Options for a `.glb` static-mesh export.

    UE picks the exporter from the FILENAME EXTENSION, so the only thing that
    has to change per format is this options object and the path -- the
    AssetExportTask call below is shared.

    EVERY DEFAULT HERE IS WRONG FOR THIS PIPELINE, and two of them are wrong by
    three orders of magnitude. Measured on SiegeOfPonthus before these were
    set: `sm_armour_a_kneeguards.glb` came out **164.2 MB, of which 164.1 MB
    was embedded PNG and 0.1 MB was the mesh**. The whole export was 11.9 GB
    against the FBX path's 3.4 GB. UE's glTF exporter BAKES MATERIAL INPUTS TO
    TEXTURES AND EMBEDS THEM, and this pipeline already exports textures and
    materials through the manifest -- so every byte of that is a duplicate the
    Asset Processor would then have to chew through.

      texture_image_format = NONE     no image data in the container at all
      bake_material_inputs = DISABLED do not render material graphs to textures
      export_preview_mesh  = False    an extra mesh node the .assetinfo does
                                      not name; staging REFUSES a multi-mesh
                                      glTF rather than picking one silently

    A knob that cannot be set RAISES rather than being skipped. Silently
    falling back to the defaults is exactly how the 164 MB armour happened, and
    it looked like a successful export in every log.
    """
    if not hasattr(unreal, "GLTFExportOptions"):
        raise MeshExportError(
            "this UE build has no unreal.GLTFExportOptions, so it cannot "
            "export .glb; use UEO3DE_MESH_FORMAT=fbx")
    options = unreal.GLTFExportOptions()

    # PINNED, NOT INHERITED. UE PERSISTS GLTF EXPORTER SETTINGS IN CONFIG, so
    # `GLTFExportOptions()` hands back whatever was last chosen in the export
    # dialog -- not a fixed default. Measured the hard way: with 1.0 saved in
    # the UI, every mesh exported 100x too large (a 1 m cube came out as
    # [-50,-50,-50]..[50,50,50]) and the intermediate bounds check failed the
    # whole export. UE authors in CENTIMETRES; glTF and O3DE are in METRES, so
    # 0.01 IS the unit conversion and the pipeline's correctness rests on it.
    # Anything the importer depends on is set here explicitly, whatever the
    # editor's saved state says.
    required = [("export_uniform_scale", 0.01),
                ("export_preview_mesh", False)]
    if hasattr(unreal, "GLTFTextureImageFormat"):
        required.append(("texture_image_format", unreal.GLTFTextureImageFormat.NONE))
    if hasattr(unreal, "GLTFMaterialBakeMode"):
        required.append(("bake_material_inputs", unreal.GLTFMaterialBakeMode.DISABLED))

    # SKELETAL-ONLY KNOBS, all defaulting True, all meaningless for a
    # StaticMesh -- a static mesh has no morph targets, no skin weights and no
    # skinned root to reparent. They are turned off anyway because this path
    # exports STATIC MESHES ONLY (skeletal sources go through UE's native FBX
    # exporter, see export_skeletal below), and every option left on is one
    # more way for a node the `.assetinfo` does not name to appear in the
    # scene. Staging REFUSES a multi-mesh glTF rather than guessing which node
    # to select, so a stray node is a hard failure, not a cosmetic one.
    #
    # Expect no visual change from these: measured on a level with zero
    # skeletal meshes, they alter nothing. This is hygiene.
    required.extend([
        ("export_morph_targets", False),
        ("export_vertex_skin_weights", False),
        ("make_skinned_meshes_root", False),
    ])

    # EXPORT_SOURCE_MODEL: set deliberately, and pinned either way so it is
    # never inherited from the export dialog's saved state (see the scale
    # comment above -- that lesson cost a 100x level).
    #
    # The risk it carries is specific: this path does NOT export the mesh as
    # UE stores it. `_bake_temp_asset` builds a temporary StaticMesh through
    # GeometryScript carrying the Lane B basis correction, LOD0 flattening and
    # material-slot compaction, and THAT is what gets exported. A procedurally
    # created asset has no imported source model, so "export the source rather
    # than the engine-processed mesh" may have nothing to fall back on, or may
    # bypass the bake outright. The intermediate bounds check in
    # export_level.py is what catches the second case: a bypassed bake changes
    # the geometry's basis and the written file stops matching its expected
    # bounds.
    required.append(("export_source_model", True))

    for name, value in required:
        try:
            options.set_editor_property(name, value)
        except Exception as exc:
            raise MeshExportError(
                "GLTFExportOptions.%s could not be set to %r (%s). Refusing to "
                "export: the defaults embed baked textures in every mesh (164 MB "
                "for one measured armour piece, against 0.1 MB of geometry), "
                "which this pipeline already exports through the manifest."
                % (name, value, exc))
    return options


_GLTF_OPTIONS = []          # built once, on first .glb export


def _export_mesh(asset, output_path, options):
    """Run one asset export task. The FORMAT comes from `output_path`.

    UE selects the exporter from the filename extension, so the extension is
    already the source of truth here -- and the options object must agree with
    it or the task fails on a type mismatch. Rather than thread a second
    options object through four call sites, the extension picks it. Callers
    pass the FBX options and keep working unchanged.
    """
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    label = os.path.splitext(output_path)[1].lstrip(".").upper() or "mesh"
    if output_path.lower().endswith(".glb"):
        if not _GLTF_OPTIONS:
            _GLTF_OPTIONS.append(_make_gltf_export_options())
        options = _GLTF_OPTIONS[0]
    task = unreal.AssetExportTask()
    task.object = asset
    task.filename = output_path
    task.automated = True
    task.replace_identical = True
    task.prompt = False
    task.options = options
    if not unreal.Exporter.run_asset_export_task(task):
        raise MeshExportError("%s export failed for %s" % (label, output_path))
    if not os.path.exists(output_path):
        raise MeshExportError("%s export reported success but wrote nothing: %s"
                              % (label, output_path))
    # A .glb that arrived beside a .bin means UE wrote the JSON container under
    # a .glb name. Staging copies exactly one file, so the mesh data would be
    # left behind and the AP would fail on a dangling buffer reference.
    if output_path.lower().endswith(".glb"):
        companion = os.path.splitext(output_path)[0] + ".bin"
        if os.path.exists(companion):
            raise MeshExportError(
                "%s was written with a companion %s: that is the .gltf layout "
                "under a .glb name, and staging copies only the one file"
                % (output_path, os.path.basename(companion)))
        _refuse_embedded_images(output_path)


def _refuse_embedded_images(path):
    """A written `.glb` must carry NO image data.

    The options in `_make_gltf_export_options` turn texture baking and image
    embedding off; this checks the RESULT, because that is what actually
    shipped. Measured before it existed: 164.1 MB of embedded PNG around 0.1 MB
    of geometry, and every log line said the export succeeded. An options knob
    that stops working in a future UE version fails silently in exactly the
    same way, so the file is inspected rather than the settings trusted.

    The header walk is a deliberate 12 lines rather than a call into
    `ueimporter.gltf_source`: this plugin runs inside UE and must not depend on
    the O3DE gem's import path. It only READS a length here; the container
    rewriting all lives in that one module.
    """
    with open(path, "rb") as handle:
        header = handle.read(20)
        if len(header) < 20 or header[:4] != b"glTF":
            raise MeshExportError("%s is not a .glb container" % path)
        json_length = struct.unpack_from("<I", header, 12)[0]
        document = json.loads(handle.read(json_length).decode("utf-8"))

    images = document.get("images") or []
    if images:
        raise MeshExportError(
            "%s embeds %d image(s): UE's glTF exporter baked material inputs to "
            "textures despite the options. This pipeline exports textures "
            "through the manifest, so those are duplicates -- one measured "
            "armour mesh came to 164.2 MB of which 164.1 MB was embedded PNG."
            % (os.path.basename(path), len(images)))


# Kept as the old name so nothing outside this module has to change; every
# call site now goes through _export_mesh, which dispatches on the extension.
_export_fbx = _export_mesh


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

    # OUTSIDE and FAILED are different things and must never be added together.
    # The grid spans the landscape's BOUNDING BOX, so a landscape that is not a
    # filled rectangle has samples in the gaps where no component exists --
    # nothing is broken, there is simply no terrain there. A trace that finds
    # nothing INSIDE a component is the broken case the guard below exists for.
    #
    # Conflating them made the exporter refuse a perfectly good level: measured
    # on a 4.27-era sample map, 3,040 of 30,210 samples missed and every single
    # one was outside the footprint (Tests/ue/probe_terrain_misses.py). Its 27
    # components cover 90.0% of the bounding box and the misses were 10.1% --
    # the same number twice.
    OUTSIDE = object()

    def height(x, y):
        component = component_for(x, y)
        if component is None:
            return OUTSIDE
        return _trace_component_height(component, x, y, z_top, z_bottom)

    min_x, max_x = origin.x - extent.x, origin.x + extent.x
    min_y, max_y = origin.y - extent.y, origin.y + extent.y
    nx = max(2, int(round((max_x - min_x) / TERRAIN_SPACING_CM)))
    ny = max(2, int(round((max_y - min_y) / TERRAIN_SPACING_CM)))
    xs = [min_x + (max_x - min_x) * i / nx for i in range(nx + 1)]
    ys = [min_y + (max_y - min_y) * j / ny for j in range(ny + 1)]

    zs = []
    outside = 0
    failed = 0
    last_good = origin.z
    for y in ys:
        row = []
        for x in xs:
            z = height(x, y)
            if z is OUTSIDE:
                outside += 1
                z = last_good     # no terrain here: keep the surface C0
            elif z is None:
                failed += 1
                z = last_good     # landscape holes / edge texels: keep C0
            else:
                last_good = z
            row.append(z)
        zs.append(row)
    total = (nx + 1) * (ny + 1)
    if log is not None:
        log("  terrain grid %dx%d (%.0f cm spacing), %d samples, "
            "%d outside the footprint, %d failed traces"
            % (nx + 1, ny + 1, TERRAIN_SPACING_CM, total, outside, failed))
    if outside and log is not None:
        # Not an error, but the user is getting geometry UE does not have: the
        # baked mesh is the full bounding box, so the gaps come out as a flat
        # skirt at the last sampled height. Say so rather than let it be
        # discovered in the viewport.
        log("  NOTE: this Landscape is not a filled rectangle -- %.1f%% of the "
            "baked terrain is outside it and is filled flat (see DIVERGENCES.md)"
            % (100.0 * outside / total))
    if failed > total * 0.05:
        raise MeshExportError(
            "terrain sampling failed on %d of %d points INSIDE the landscape's "
            "own collision components (>5%%); the trace is broken, refusing to "
            "ship a guessed surface. (%d further points were outside the "
            "footprint, which is normal for a non-rectangular landscape and is "
            "not counted here.)" % (failed, total, outside))

    # Verification points sit EXACTLY on grid nodes, so the baked mesh's
    # height there equals the grid value with no interpolation term -- the
    # sphere-drop acceptance can then use a tight tolerance. Each point is
    # re-traced independently and compared against the grid: a row/column or
    # axis mix-up shows up as metres, not millimetres.
    samples = []
    for fx, fy in ((0.5, 0.5), (0.2, 0.2), (0.8, 0.8), (0.2, 0.8), (0.8, 0.2)):
        i = min(nx, max(0, int(round(nx * fx))))
        j = min(ny, max(0, int(round(ny * fy))))
        # A verification point that lands in a gap verifies nothing -- its grid
        # value is filler, not a traced height. Walk to the nearest node that is
        # actually on the landscape rather than failing the export or, worse,
        # "verifying" the filler against itself.
        found = None
        for radius in range(0, max(nx, ny) + 1):
            for dj in range(-radius, radius + 1):
                for di in range(-radius, radius + 1):
                    if radius and max(abs(di), abs(dj)) != radius:
                        continue
                    ii, jj = i + di, j + dj
                    if not (0 <= ii <= nx and 0 <= jj <= ny):
                        continue
                    z = height(xs[ii], ys[jj])
                    if z is not OUTSIDE and z is not None:
                        found = (ii, jj, z)
                        break
                if found:
                    break
            if found:
                break
        if found is None:
            raise MeshExportError(
                "no terrain verification point near node (%d, %d) traced at "
                "all -- the landscape has collision components but none of "
                "them answers a trace" % (i, j))
        i, j, z = found
        x, y = xs[i], ys[j]
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


def _export_spline(asset, base_path, output_root, options, emit):
    """Bake and export one SplineMeshComponent's deformed geometry (M9).

    `base_path` is "<actor path>:<component name>". The copy runs in
    COMPONENT-LOCAL space (bWantsWorldSpace False), so the manifest entity's
    transform places it -- unlike terrain, a spline bake stays movable.
    Measured: copy_mesh_from_component DOES return the deformed geometry
    (a bent cylinder's bounds, probe_m9_authoring.py), unlike its landscape
    behaviour (0 triangles, probe_m7_geometry.py).
    """
    actor_path, _, component_name = base_path.rpartition(":")
    actor = _find_level_actor(actor_path)
    if actor is None:
        raise MeshExportError(
            "spline actor %r is not in the open level" % actor_path)
    component = None
    for candidate in actor.get_components_by_class(unreal.SplineMeshComponent) or []:
        if candidate.get_name() == component_name:
            component = candidate
    if component is None:
        raise MeshExportError(
            "spline component %r not found on %r" % (component_name, actor_path))

    dyn = unreal.DynamicMesh()
    copy_options = unreal.GeometryScriptCopyMeshFromComponentOptions()
    dyn = _unwrap(unreal.GeometryScript_SceneUtils.copy_mesh_from_component(
        component, dyn, copy_options, False))
    if dyn is None:
        raise MeshExportError("copy_mesh_from_component returned no mesh for "
                              + base_path)
    box = _unwrap(unreal.GeometryScript_MeshQueries.get_mesh_bounding_box(dyn))
    local_min = [box.min.x, box.min.y, box.min.z]
    local_max = [box.max.x, box.max.y, box.max.z]

    # The normal Lane B bake; a spline bake is never a mirror variant.
    dyn = _unwrap(unreal.GeometryScript_MeshTransforms.scale_mesh(
        dyn, unreal.Vector(-1.0, -1.0, 1.0), unreal.Vector(0.0, 0.0, 0.0)))
    if dyn is None:
        raise MeshExportError("spline bake scale_mesh failed")

    node_name = asset["fbx_node_name"]
    output_path = os.path.join(
        output_root, asset["o3de_relative_path"]).replace("\\", "/")
    temp_path, baked = _bake_temp_asset(dyn, node_name)
    try:
        slots = []
        for index in range(component.get_num_materials()):
            material = component.get_material(index)
            entry = unreal.StaticMaterial()
            entry.set_editor_property(
                "material_interface",
                material if material is not None
                else _placeholder_material(index))
            slots.append(entry)
        if slots:
            baked.set_editor_property("static_materials", slots)
        _export_fbx(baked, output_path, options)
    finally:
        unreal.EditorAssetLibrary.delete_asset(temp_path)

    # Normal-entry rule: the FBX intermediate is mirror-X of the local bake.
    return {
        "guid": asset["guid"],
        "ue_path": asset["ue_path"],
        "relative_path": asset["o3de_relative_path"],
        "node_name": node_name,
        "ue_bounds_min": [-local_max[0], local_min[1], local_min[2]],
        "ue_bounds_max": [-local_min[0], local_max[1], local_max[2]],
        "bytes": os.path.getsize(output_path),
    }


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

        if fragment == "spline":
            record = _export_spline(asset, base_path, output_root, options, emit)
            exported.append(record)
            emit("  %-42s -> %-46s (%d bytes, node %r, SPLINE)"
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

        # The LOD chain is FBX-ONLY: a glb with several mesh nodes is exactly
        # what staging refuses (it cannot name two nodes apart), so the glb
        # container keeps the single flattened mesh and its LOD_FLATTENED
        # report stays true there.
        is_fbx = output_path.lower().endswith(".fbx")
        if is_fbx:
            chain = _baked_lod_chain(source, mirrored=mirrored)
        else:
            chain = [_baked_dynamic_mesh(source, mirrored=mirrored)]
        dyn = chain[0]
        slots = _compact_slots(chain, source)
        temp_path, baked = _bake_temp_asset(chain, node_name)
        try:
            # The FBX carries one material per slot, named after the UE
            # material asset -- the label the importer assigns by. Set for
            # every mesh (single-slot included) so labels are always real
            # material names, never the bake's WorldGridMaterial default.
            # AFTER the LOD writes: copy_mesh_to_static_mesh touches the
            # asset's material list, and the labels must win.
            baked.set_editor_property("static_materials", slots)
            _export_fbx(baked, output_path,
                        _lod_export_options() if len(chain) > 1 else options)
        finally:
            unreal.EditorAssetLibrary.delete_asset(temp_path)

        try:
            lod_count = int(source.get_num_lods())
        except Exception:
            lod_count = 1
        # The expectation must describe WHAT WAS EXPORTED, and two cases
        # export something other than "the asset": multiple LODs (bake reads
        # LOD0 only) and a NANITE SOURCE READ (bake reads the source model,
        # whose geometry need not fill the asset's declared bounds). Measured
        # on Docks `SM_Crab_Cages_NN_02a`: a single-LOD Nanite asset whose
        # bounds reach X=1.75 m while its source geometry ends at 0.41 m --
        # the asset-bounds expectation failed the whole export on a mesh the
        # bake had exported perfectly. RetroCars never hit this because every
        # mesh there has 4 LODs and already took the dyn-derived branch.
        if lod_count > 1 or (_nanite_enabled(source)
                             and not _nanite_fallback_forced()):
            # The expectation must describe the WRITTEN FILE, which is the
            # union of every exported LOD. With a chain that is not LOD0's
            # box: quadric reduction moves vertices, and the far LODs bulge a
            # couple of centimetres outside LOD0 (measured on SM_Car_24a: the
            # file reached -253.43 where LOD0 ends at -251.95, and the 1e-3 cm
            # tolerance rightly refused the export). Union over the chain's
            # own baked boxes keeps the check EXACT rather than loosening the
            # tolerance until a real bake error could hide in it. The dyns
            # already carry the bake's negations, so mirror-Y of the union is
            # the FBX-writer expectation for normal and variant entries alike.
            union_min = [float("inf")] * 3
            union_max = [float("-inf")] * 3
            for lod_dyn in chain:
                box = _unwrap(unreal.GeometryScript_MeshQueries
                              .get_mesh_bounding_box(lod_dyn))
                for axis, (low, high) in enumerate((
                        (box.min.x, box.max.x), (box.min.y, box.max.y),
                        (box.min.z, box.max.z))):
                    union_min[axis] = min(union_min[axis], low)
                    union_max[axis] = max(union_max[axis], high)
            bounds_min = [union_min[0], -union_max[1], union_min[2]]
            bounds_max = [union_max[0], -union_min[1], union_max[2]]
            exported.append({
                "guid": guid,
                "ue_path": asset["ue_path"],
                "relative_path": asset["o3de_relative_path"],
                "node_name": node_name,
                "ue_bounds_min": bounds_min,
                "ue_bounds_max": bounds_max,
                "bytes": os.path.getsize(output_path),
            })
            emit("  %-42s -> %-46s (%d bytes, node %r, LOD0 of %d)"
                 % (asset["ue_path"], asset["o3de_relative_path"],
                    exported[-1]["bytes"], node_name, lod_count))
            continue

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
