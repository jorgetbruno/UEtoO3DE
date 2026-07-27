"""
export_sm_letterf.py — S0.2 support (Lane B geometry orientation lock, plan v2.2 M0).

Exports /Game/Meshes/SM_LetterF.SM_LetterF to FBX using UE's DEFAULT axis/unit
conversion (a stock UFbxExportOption CDO — nothing is customized; S0.2 exists to
measure what UE + O3DE SceneAPI do by default) and dumps the UE-side reference
data the O3DE-side comparison runs against.

Outputs (dirs created as needed):
    D:/Gamedev/UEtoO3DE/Exports/LaneB/SM_LetterF.fbx
    D:/Gamedev/UEtoO3DE/Exports/LaneB/SM_LetterF.ue_reference.json

Reference JSON contents (all in UE asset space: centimeters, Z-up, left-handed):
    bounds_min / bounds_max  — AABB from the mesh's actual vertex positions
    centroid                 — arithmetic mean of vertex positions
    vertex_count             — number of vertices (LOD0 render mesh)
    actor_transform          — the transform of the "SM_LetterF" actor in
                               /Game/Maps/Fixture_01 (identity if level/actor missing)

Run AFTER build_fixture_01.py:  run_ue_python.bat export_sm_letterf.py
"""

import json
import os
import traceback

import unreal

MESH_ASSET_PATH = "/Game/Meshes/SM_LetterF.SM_LetterF"
MAP_PATH = "/Game/Maps/Fixture_01"
ACTOR_LABEL = "SM_LetterF"

OUTPUT_DIR = "D:/Gamedev/UEtoO3DE/Exports/LaneB"
FBX_PATH = OUTPUT_DIR + "/SM_LetterF.fbx"
JSON_PATH = OUTPUT_DIR + "/SM_LetterF.ue_reference.json"

RESULT_TAG = "EXPORT_SM_LETTERF"


def log(msg):
    unreal.log("[" + RESULT_TAG + "] " + str(msg))


def _unwrap(result):
    """UE Python packs UFUNCTION return values as (return_value, out_param1, ...);
    Geometry Script adds an EGeometryScriptOutcomePins out pin (ExpandEnumAsExecs)."""
    if isinstance(result, tuple):
        for item in result:
            if not isinstance(item, unreal.GeometryScriptOutcomePins):
                return item
        return result[0]
    return result


def get_vertex_positions(mesh_asset):
    """Copy LOD0 render mesh into a DynamicMesh and read all vertex positions."""
    dyn = unreal.DynamicMesh()
    asset_options = unreal.GeometryScriptCopyMeshFromAssetOptions()
    # Read the render mesh LOD0 explicitly — that is what the FBX exporter exports.
    requested_lod = unreal.GeometryScriptMeshReadLOD()
    requested_lod.set_editor_property("lod_type", unreal.GeometryScriptLODType.RENDER_DATA)
    result = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        mesh_asset, dyn, asset_options, requested_lod)
    dyn = _unwrap(result)
    if dyn is None:
        raise RuntimeError("copy_mesh_from_static_mesh failed")

    # One in-arg (skip_gaps=False -> all positions); PositionList comes back in the return tuple.
    positions_result = dyn.get_all_vertex_positions(False)
    # returns (TargetMesh, PositionList, bHasVertexIDGaps); find the vector list
    vector_list = None
    for item in (positions_result if isinstance(positions_result, tuple) else (positions_result,)):
        if isinstance(item, unreal.GeometryScriptVectorList):
            vector_list = item
            break
    if vector_list is None:
        raise RuntimeError("get_all_vertex_positions returned no vector list")
    return unreal.GeometryScript_List.convert_vector_list_to_array(vector_list)


def compute_reference_data(mesh_asset):
    positions = get_vertex_positions(mesh_asset)
    if len(positions) == 0:
        raise RuntimeError("mesh has no vertices")

    min_x = min(p.x for p in positions)
    min_y = min(p.y for p in positions)
    min_z = min(p.z for p in positions)
    max_x = max(p.x for p in positions)
    max_y = max(p.y for p in positions)
    max_z = max(p.z for p in positions)
    n = float(len(positions))
    centroid = [
        sum(p.x for p in positions) / n,
        sum(p.y for p in positions) / n,
        sum(p.z for p in positions) / n,
    ]
    return {
        "bounds_min": [min_x, min_y, min_z],
        "bounds_max": [max_x, max_y, max_z],
        "centroid": centroid,
        "vertex_count": len(positions),
    }


def get_actor_transform():
    """World transform of the SM_LetterF actor in Fixture_01 (identity if not found)."""
    identity = {
        "location": [0.0, 0.0, 0.0],
        "rotation_rotator_pitch_yaw_roll": [0.0, 0.0, 0.0],
        "scale": [1.0, 1.0, 1.0],
    }
    if not unreal.EditorAssetLibrary.does_asset_exist(MAP_PATH):
        log("WARNING: level not found, recording identity actor transform: " + MAP_PATH)
        return identity
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        log("WARNING: failed to load level, recording identity actor transform")
        return identity
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in actor_sub.get_all_level_actors():
        if actor.get_actor_label() == ACTOR_LABEL:
            loc = actor.get_actor_location()
            rot = actor.get_actor_rotation()
            scale = actor.get_actor_scale3d()
            return {
                "location": [loc.x, loc.y, loc.z],
                "rotation_rotator_pitch_yaw_roll": [rot.pitch, rot.yaw, rot.roll],
                "scale": [scale.x, scale.y, scale.z],
            }
    log("WARNING: actor '" + ACTOR_LABEL + "' not found in level, recording identity transform")
    return identity


def export_fbx(mesh_asset):
    task = unreal.AssetExportTask()
    task.object = mesh_asset
    task.filename = FBX_PATH
    task.automated = True
    task.replace_identical = True
    task.prompt = False
    # Stock default options: NO axis/unit customization (S0.2 measures the default behavior).
    task.options = unreal.FbxExportOption()
    if not unreal.Exporter.run_asset_export_task(task):
        raise RuntimeError("FBX export failed for " + MESH_ASSET_PATH)
    log("wrote " + FBX_PATH)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    mesh_asset = unreal.EditorAssetLibrary.load_asset(MESH_ASSET_PATH)
    if mesh_asset is None:
        raise RuntimeError("mesh asset not found: " + MESH_ASSET_PATH
                           + " (run build_fixture_01.py first)")

    reference = compute_reference_data(mesh_asset)
    reference["actor_transform"] = get_actor_transform()
    reference["mesh_asset_path"] = MESH_ASSET_PATH
    reference["fbx_path"] = FBX_PATH
    reference["units"] = "cm"
    reference["coordinate_system"] = "UE asset space: centimeters, Z-up, left-handed"
    reference["notes"] = ("UE-side reference for S0.2 / Lane B. FBX exported with UE default "
                          "FbxExportOption (no axis/unit customization). Compare against O3DE "
                          "SceneAPI output to lock the .assetinfo correction in LANE_B.md.")

    export_fbx(mesh_asset)

    with open(JSON_PATH, "w") as f:
        json.dump(reference, f, indent=2, sort_keys=True)
        f.write("\n")
    log("wrote " + JSON_PATH)
    log("bounds " + str(reference["bounds_min"]) + " .. " + str(reference["bounds_max"])
        + " centroid " + str(reference["centroid"]) + " vertices " + str(reference["vertex_count"]))


try:
    main()
except Exception:
    unreal.log_error("[" + RESULT_TAG + "] FAILED")
    unreal.log_error(traceback.format_exc())
    print("RESULT: FAIL")
    raise

print("RESULT: PASS")
