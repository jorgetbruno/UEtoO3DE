"""
mesh_export.py — FBX export of the static meshes a manifest references (plan M2).

--------------------------------------------------------------------------
Lane B: THREE Y-negations, net one (measured; the third was found by a human)
--------------------------------------------------------------------------
UE is left-handed and O3DE is right-handed, so the product geometry must end
up carrying Lane A's basis map: negate Y, /100. Three stages each negate Y:

    1. this module bakes `scale_mesh(1, -1, 1)` into a temp asset      (ours)
    2. UE's FBX exporter negates Y (LH -> RH conversion)               (always)
    3. O3DE SceneAPI negates Y again -- it honours the FBX's declared
       `FrontAxis = Y, sign -1` and converts into O3DE's frame         (always)

Net: one negation. Units: SceneAPI honours `UnitScaleFactor` (cm -> m), so no
scale rule is needed anywhere.

Stage 3 is the one everybody missed, twice:

  * M0's spike S0.2 concluded SceneAPI applies "no unit conversion, no axis
    conversion". Its evidence was product *metadata* and buffer *ratios*,
    never absolute product floats. Both claims were wrong: byte-level reads of
    the product position buffers show the engine cube at exactly +/-0.5 m with
    no rule at all, and the F mesh's Y-asymmetric nub flipped back to +Y.
  * During M2 this module's bake was removed because an FBX-level check showed
    stages 1 and 2 cancelling. True -- and irrelevant, because stage 3 then
    re-mirrored the geometry. Every automated check passed; the first human
    look at an imported level (a bench 100x too small next to the shader
    ball) is what exposed the scale half, and the product-float reads that
    followed exposed the mirror half.

The permanent assertion therefore lives at the PRODUCT level
(`Tests/m2/test_m2_artifacts.py` reads the cache's position buffers by float
byte-pattern), and the FBX-level check asserts the intermediate is verbatim UE
(stages 1+2 cancelled), which is what this pipeline actually produces.

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

Known limitation, owned by M4: the baked temp asset carries a single default
material slot, so multi-slot meshes flatten to one slot in the FBX. The
manifest records the real slot list from the source asset.
"""

import os

import unreal

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


def _mirrored_dynamic_mesh(source_mesh):
    """LOD0 render geometry with the bake-stage Y-negation applied.

    `scale_mesh` with a negative-determinant scale fixes triangle winding by
    itself -- measured in `Tests/ue/probe_m2_mirror2.py`: all 48 face normals
    of the F mesh mapped to B*n, none to -B*n. Do NOT add a manual flip.
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

    dyn = _unwrap(unreal.GeometryScript_MeshTransforms.scale_mesh(
        dyn, unreal.Vector(1.0, -1.0, 1.0), unreal.Vector(0.0, 0.0, 0.0)))
    if dyn is None:
        raise MeshExportError("scale_mesh returned no mesh")
    return dyn


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

        source = unreal.EditorAssetLibrary.load_asset(asset["ue_path"])
        if source is None:
            raise MeshExportError("could not load source mesh " + asset["ue_path"])

        node_name = source.get_name()
        output_path = os.path.join(output_root, asset["o3de_relative_path"]).replace("\\", "/")

        dyn = _mirrored_dynamic_mesh(source)
        temp_path, baked = _bake_temp_asset(dyn, node_name)
        try:
            _export_fbx(baked, output_path, options)
        finally:
            unreal.EditorAssetLibrary.delete_asset(temp_path)

        bounds_min, bounds_max = source_bounds(source)
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

    return exported
