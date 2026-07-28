"""
probe_mirror_bake.py — can the Lane B pipeline bake a MIRRORED mesh variant?

Design under test (negative-scale fidelity): a UE actor with det<0 scale gets
its mirror canonicalized to mirror-about-X and baked into a second mesh asset,
so the entity can carry positive scale. The variant's bake vector is
scale_mesh(-1,-1,1):

    net product   = bake vector          (export + SceneAPI Y-negations cancel)
    normal bake   = ( 1,-1, 1) -> product = negate-Y(source)      (Lane B)
    variant bake  = (-1,-1, 1) -> product = negate-Y(mirror-X(source))
                                        = mirror-X(normal product)

Questions:
  1. does scale_mesh(-1,-1,1) (det +1) leave winding correct with NO manual
     flip -- per-triangle normals of the exported FBX should map to
     M_x(source normals) exactly, none inverted;
  2. the FBX intermediate should be mirror-X(source) VERBATIM: X bounds
     flipped, Y and Z bounds identical to the source's;
  3. material IDs survive (same as probe_slots, quick re-check on this path).

Uses SM_LetterF (asymmetric in all three axes, the mesh the byte-level product
tests key on).

Output: Tests/ue/results/probe_mirror_bake.txt
Run:    run_ue_python.bat probe_mirror_bake.py
"""

import os
import sys
import traceback

import unreal

sys.path.insert(0, "D:/Gamedev/UEtoO3DE/Tests/lib")
import fbx_reader  # noqa: E402

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_mirror_bake.txt"
FBX_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_mirror_letterf.fbx"
SOURCE = "/Game/Meshes/SM_LetterF"
TEMP_DIR = "/Game/__ProbeMirror"
TEMP_ASSET = TEMP_DIR + "/SM_LetterF_MX"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_MIRROR] " + str(msg))


def _unwrap(result):
    if isinstance(result, tuple):
        for item in result:
            if not isinstance(item, unreal.GeometryScriptOutcomePins):
                return item
        return result[0]
    return result


def main():
    source = unreal.EditorAssetLibrary.load_asset(SOURCE)
    if source is None:
        raise RuntimeError("missing " + SOURCE)
    box = source.get_bounding_box()
    out("source bounds: min=(%.3f, %.3f, %.3f) max=(%.3f, %.3f, %.3f)"
        % (box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z))

    dyn = unreal.DynamicMesh()
    copy_options = unreal.GeometryScriptCopyMeshFromAssetOptions()
    requested_lod = unreal.GeometryScriptMeshReadLOD()
    requested_lod.set_editor_property("lod_type", unreal.GeometryScriptLODType.RENDER_DATA)
    dyn = _unwrap(unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        source, dyn, copy_options, requested_lod))

    # The variant bake: mirror-X on top of the normal (1,-1,1) handedness bake.
    dyn = _unwrap(unreal.GeometryScript_MeshTransforms.scale_mesh(
        dyn, unreal.Vector(-1.0, -1.0, 1.0), unreal.Vector(0.0, 0.0, 0.0)))
    if dyn is None:
        raise RuntimeError("scale_mesh(-1,-1,1) returned no mesh")

    if unreal.EditorAssetLibrary.does_asset_exist(TEMP_ASSET):
        unreal.EditorAssetLibrary.delete_asset(TEMP_ASSET)
    create_opts = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    create_opts.set_editor_property("enable_collision", False)
    baked = _unwrap(unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        dyn, TEMP_ASSET, create_opts))
    if baked is None:
        raise RuntimeError("bake failed")

    export_opts = unreal.FbxExportOption()
    export_opts.set_editor_property("collision", False)
    export_opts.set_editor_property("level_of_detail", False)
    task = unreal.AssetExportTask()
    task.object = baked
    task.filename = FBX_PATH
    task.automated = True
    task.replace_identical = True
    task.prompt = False
    task.options = export_opts
    os.makedirs(os.path.dirname(FBX_PATH), exist_ok=True)
    if not unreal.Exporter.run_asset_export_task(task):
        raise RuntimeError("FBX export failed")

    stats = fbx_reader.vertex_stats(FBX_PATH)
    out("variant FBX bounds: min=%s max=%s"
        % ([round(v, 3) for v in stats["min"]], [round(v, 3) for v in stats["max"]]))

    # Expectation: FBX = mirror-X(source), verbatim otherwise.
    expected_min = [-box.max.x, box.min.y, box.min.z]
    expected_max = [-box.min.x, box.max.y, box.max.z]
    for index, axis in enumerate("xyz"):
        if abs(stats["min"][index] - expected_min[index]) > 1e-3 or \
           abs(stats["max"][index] - expected_max[index]) > 1e-3:
            raise RuntimeError(
                "FBX %s bounds [%.3f, %.3f], expected [%.3f, %.3f]: the "
                "variant is NOT mirror-X(source)"
                % (axis, stats["min"][index], stats["max"][index],
                   expected_min[index], expected_max[index]))
    out("FBX intermediate is mirror-X(source): X flipped, Y/Z verbatim")

    # Winding, by signed volume against the NORMAL export of the same mesh:
    # mirroring without winding correction flips the sign; a correct mirrored
    # bake preserves both sign and magnitude.
    reference = "D:/Gamedev/UEtoO3DE/Exports/Fixture_01/Assets/uetoo3de/game/meshes/sm_letterf.fbx"
    volume_reference = fbx_reader.signed_volume(reference)
    volume_variant = fbx_reader.signed_volume(FBX_PATH)
    out("signed volume: reference %.1f, variant %.1f"
        % (volume_reference, volume_variant))
    if volume_reference * volume_variant <= 0.0:
        raise RuntimeError(
            "signed volume sign flipped (%.1f vs %.1f): the mirrored bake is "
            "inside-out and needs a manual winding flip"
            % (volume_reference, volume_variant))
    if abs(abs(volume_variant) - abs(volume_reference)) > abs(volume_reference) * 1e-3:
        raise RuntimeError(
            "signed volume magnitude changed (%.1f vs %.1f): the variant is "
            "not the same solid" % (volume_reference, volume_variant))


status = "PASS"
try:
    main()
except Exception:
    out("FATAL: " + traceback.format_exc())
    status = "FAIL"
finally:
    try:
        if unreal.EditorAssetLibrary.does_directory_exist(TEMP_DIR):
            unreal.EditorAssetLibrary.delete_directory(TEMP_DIR)
    except Exception:
        pass

_lines.append("RESULT: " + status)
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w") as handle:
    handle.write("\n".join(_lines) + "\n")
print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)
