"""
probe_slots.py — can the baked export FBX carry real per-slot materials?

Questions, in pipeline order (each one is a way the per-slot plan dies):
  1. Do per-triangle material IDs survive DynamicMesh -> bake ->
     create_new_static_mesh_asset_from_mesh (how many slots does the baked
     asset get)?
  2. Can the baked asset's static_materials be replaced with the SOURCE slot
     list (materials + slot names), and does it survive a save?
  3. Does copy_mesh_from_static_mesh preserve material IDs when reading a
     multi-slot asset back (that is what export_meshes does to real meshes)?
  4. Does scale_mesh(1,-1,1) (the Lane B bake stage) leave material IDs alone?
  5. Does the exported FBX contain BOTH material names?

Output: Tests/ue/results/probe_slots.txt
Run:    run_ue_python.bat probe_slots.py
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_slots.txt"
FBX_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_slots.fbx"
TEMP_DIR = "/Game/__ProbeSlots"
TEMP_ASSET = TEMP_DIR + "/SM_TwoSlotProbe"
MAT_A = "/Game/Materials/M_Fixture_PBR"
MAT_B = "/Game/Materials/M_Fixture_ORM"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_SLOTS] " + str(msg))


def _unwrap(result):
    if isinstance(result, tuple):
        for item in result:
            if not isinstance(item, unreal.GeometryScriptOutcomePins):
                return item
        return result[0]
    return result


def material_ids(dyn):
    """Per-triangle material IDs as a plain list."""
    result = unreal.GeometryScript_Materials.get_all_triangle_material_i_ds(dyn)
    id_list = None
    for item in (result if isinstance(result, tuple) else (result,)):
        if isinstance(item, unreal.GeometryScriptIndexList):
            id_list = item
    if id_list is None:
        raise RuntimeError("get_all_triangle_material_i_ds returned no index list")
    return list(unreal.GeometryScript_List.convert_index_list_to_array(id_list))


def main():
    out("material-fn surface on DynamicMesh: %r"
        % sorted(m for m in dir(unreal.DynamicMesh()) if "material" in m))
    gs_names = sorted(n for n in dir(unreal) if n.startswith("GeometryScript_"))
    out("GeometryScript libs: %r" % gs_names)

    # --- 1. two boxes, material IDs 0 and 1 -------------------------------
    dyn = unreal.DynamicMesh()
    dyn.enable_material_i_ds()
    opts = unreal.GeometryScriptPrimitiveOptions()
    origin = unreal.GeometryScriptPrimitiveOriginMode.BASE

    count = unreal.GeometryScript_MeshQueries.get_num_triangle_i_ds
    xform = unreal.Transform(location=unreal.Vector(0.0, 0.0, 0.0))
    dyn = dyn.append_box(opts, xform, 100.0, 50.0, 100.0, 0, 0, 0, origin)
    n1 = count(dyn)
    xform = unreal.Transform(location=unreal.Vector(0.0, 0.0, 150.0))
    dyn = dyn.append_box(opts, xform, 50.0, 50.0, 50.0, 0, 0, 0, origin)
    n2 = count(dyn)
    out("triangles: box1=%d total=%d" % (n1, n2))

    for tri in range(n1, n2):
        dyn.set_triangle_material_id(tri, 1)
    ids = material_ids(dyn)
    out("dyn IDs: id0=%d id1=%d" % (ids.count(0), ids.count(1)))
    if ids.count(1) != n2 - n1:
        raise RuntimeError("setting material IDs did not take")

    # --- 2. bake to asset -------------------------------------------------
    if unreal.EditorAssetLibrary.does_asset_exist(TEMP_ASSET):
        unreal.EditorAssetLibrary.delete_asset(TEMP_ASSET)
    create_opts = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    create_opts.set_editor_property("enable_collision", False)
    baked = _unwrap(unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        dyn, TEMP_ASSET, create_opts))
    if baked is None:
        raise RuntimeError("create_new_static_mesh_asset_from_mesh failed")

    slots = baked.get_editor_property("static_materials")
    out("baked slot count: %d" % len(slots))
    for index, slot in enumerate(slots):
        out("  slot %d: name=%r material=%r"
            % (index, str(slot.get_editor_property("material_slot_name")),
               slot.get_editor_property("material_interface")))

    # --- 3. replace static_materials with named source slots --------------
    mat_a = unreal.EditorAssetLibrary.load_asset(MAT_A)
    mat_b = unreal.EditorAssetLibrary.load_asset(MAT_B)
    if mat_a is None or mat_b is None:
        raise RuntimeError("fixture materials missing; run build_fixture_01 first")
    new_slots = []
    for name, mat in (("SlotA", mat_a), ("SlotB", mat_b)):
        entry = unreal.StaticMaterial()
        entry.set_editor_property("material_slot_name", name)
        entry.set_editor_property("material_interface", mat)
        new_slots.append(entry)
    baked.set_editor_property("static_materials", new_slots)

    read_back = baked.get_editor_property("static_materials")
    out("after set: %d slots: %r"
        % (len(read_back),
           [(str(s.get_editor_property("material_slot_name")),
             s.get_editor_property("material_interface").get_name()
             if s.get_editor_property("material_interface") else None)
            for s in read_back]))
    if not unreal.EditorAssetLibrary.save_asset(TEMP_ASSET):
        raise RuntimeError("save_asset failed for " + TEMP_ASSET)

    # --- 4. copy back (what export_meshes does to real assets) ------------
    dyn2 = unreal.DynamicMesh()
    copy_options = unreal.GeometryScriptCopyMeshFromAssetOptions()
    requested_lod = unreal.GeometryScriptMeshReadLOD()
    requested_lod.set_editor_property("lod_type", unreal.GeometryScriptLODType.RENDER_DATA)
    dyn2 = _unwrap(unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        baked, dyn2, copy_options, requested_lod))
    ids2 = material_ids(dyn2)
    out("copied-back IDs: id0=%d id1=%d total=%d"
        % (ids2.count(0), ids2.count(1), len(ids2)))
    if ids2.count(1) == 0:
        raise RuntimeError("material IDs lost on copy_mesh_from_static_mesh")

    # --- 5. mirror bake stage leaves IDs alone -----------------------------
    dyn2 = _unwrap(unreal.GeometryScript_MeshTransforms.scale_mesh(
        dyn2, unreal.Vector(1.0, -1.0, 1.0), unreal.Vector(0.0, 0.0, 0.0)))
    ids3 = material_ids(dyn2)
    out("after scale_mesh IDs: id0=%d id1=%d" % (ids3.count(0), ids3.count(1)))
    if ids3.count(1) != ids2.count(1):
        raise RuntimeError("scale_mesh changed material IDs")

    # --- 6. FBX export carries both material names -------------------------
    os.makedirs(os.path.dirname(FBX_PATH), exist_ok=True)
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
    if not unreal.Exporter.run_asset_export_task(task):
        raise RuntimeError("FBX export failed")
    data = open(FBX_PATH, "rb").read()
    for needle in (b"M_Fixture_PBR", b"M_Fixture_ORM", b"SlotA", b"SlotB"):
        out("fbx contains %-16s: %d hits" % (needle.decode(), data.count(needle)))
    if data.count(b"M_Fixture_PBR") == 0 or data.count(b"M_Fixture_ORM") == 0:
        raise RuntimeError("exported FBX does not carry both material names")


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
