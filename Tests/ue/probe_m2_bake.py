"""
probe_m2_bake.py — M2: where does the Lane B mirror get lost?

`verify_reflection` measures the mirrored DynamicMesh as exactly what Lane A
predicts, but the exported FBX comes out with the ORIGINAL geometry
(y[-12.5, 37.5] instead of y[-37.5, 12.5]) and an identity node transform. So
the mirror survives `scale_mesh` and is gone by the time the file is written.

Two suspects, and both are cheap to test:
  1. `create_new_static_mesh_asset_from_mesh` returns a tuple, and `_unwrap`
     takes the first non-outcome element. If that element is the passthrough
     DynamicMesh rather than the new StaticMesh, the export task was handed the
     wrong object entirely.
  2. the bake itself does not carry the geometry across.

Prints the type of every element of the returned tuple, then reads the bounds
back off the asset actually on disk.

Run:  run_ue_python.bat probe_m2_bake.py
"""

import os
import sys
import traceback

import unreal

REPO_ROOT = "D:/Gamedev/UEtoO3DE"
PACKAGE_ROOT = REPO_ROOT + "/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python"
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

OUT_PATH = REPO_ROOT + "/Tests/ue/results/probe_m2_bake.txt"
SOURCE = "/Game/Meshes/SM_LetterF.SM_LetterF"
TEMP_PATH = "/Game/__UEO3DEProbeTemp/SM_LetterF"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_M2_BAKE] " + str(msg))


def bounds_of_dynamic(dyn):
    result = dyn.get_all_vertex_positions(False)
    points = None
    for item in (result if isinstance(result, tuple) else (result,)):
        if isinstance(item, unreal.GeometryScriptVectorList):
            points = unreal.GeometryScript_List.convert_vector_list_to_array(item)
            break
    ys = [p.y for p in points]
    return "n=%d y=[%.3f, %.3f]" % (len(points), min(ys), max(ys))


def main():
    from ueo3de import mesh_export

    source = unreal.EditorAssetLibrary.load_asset(SOURCE)
    box = source.get_bounding_box()
    out("source asset bounds y: [%.3f, %.3f]" % (box.min.y, box.max.y))

    dyn = mesh_export._mirrored_dynamic_mesh(source)
    out("mirrored DynamicMesh: " + bounds_of_dynamic(dyn))

    out("")
    out("=== create_new_static_mesh_asset_from_mesh return shape ===")
    if unreal.EditorAssetLibrary.does_asset_exist(TEMP_PATH):
        unreal.EditorAssetLibrary.delete_asset(TEMP_PATH)
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    options.set_editor_property("enable_collision", False)
    result = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        dyn, TEMP_PATH, options)
    items = result if isinstance(result, tuple) else (result,)
    out("  tuple length: %d" % len(items))
    for index, item in enumerate(items):
        out("    [%d] %s = %r" % (index, type(item).__name__, item))

    out("")
    out("=== what _unwrap picks ===")
    picked = mesh_export._unwrap(result)
    out("  _unwrap -> %s = %r" % (type(picked).__name__, picked))
    out("  is a StaticMesh: %s" % isinstance(picked, unreal.StaticMesh))
    out("  is a DynamicMesh: %s" % isinstance(picked, unreal.DynamicMesh))

    out("")
    out("=== the asset actually on disk ===")
    unreal.EditorAssetLibrary.save_asset(TEMP_PATH)
    loaded = unreal.EditorAssetLibrary.load_asset(TEMP_PATH)
    out("  loaded: %r" % (loaded,))
    if loaded is not None:
        baked_box = loaded.get_bounding_box()
        out("  baked asset bounds y: [%.3f, %.3f]" % (baked_box.min.y, baked_box.max.y))
        out("  mirrored correctly: %s" % (abs(baked_box.min.y + 37.5) < 0.01))

    unreal.EditorAssetLibrary.delete_directory("/Game/__UEO3DEProbeTemp")


status = "PASS"
try:
    main()
except Exception:
    out("FATAL:")
    out(traceback.format_exc())
    status = "FAIL"

_lines.append("RESULT: " + status)
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w") as handle:
    handle.write("\n".join(_lines) + "\n")
print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)
