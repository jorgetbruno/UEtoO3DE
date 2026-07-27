"""
probe_m2_fbx_handedness.py — does UE's FBX exporter negate Y by itself?

Evidence so far: the baked temp asset has y = [-37.5, 12.5] (Lane B mirror
applied) and the FBX written from it has y = [-12.5, 37.5] -- the mirror
undone. The obvious explanation is that UE's FBX exporter performs the
left-handed to right-handed conversion itself, which for UE means negating Y.

S0.2 concluded the exporter writes geometry "verbatim", but it measured a mesh
that was symmetric about Y (y in [-12.5, 12.5]), so a Y negation was invisible.
The rebuilt canary is asymmetric about Y, which is exactly what makes this
measurable now.

Exports the SAME mesh twice -- once unmodified, once mirrored -- and reports
the y range stored in each file. Interpretation:

    unmirrored FBX y = [-37.5, 12.5]  -> the exporter negates Y; Lane B needs
                                         no mirror of its own
    unmirrored FBX y = [-12.5, 37.5]  -> the exporter is verbatim and the
                                         mirror belongs in the exporter

Run:  run_ue_python.bat probe_m2_fbx_handedness.py
"""

import os
import sys
import traceback

import unreal

REPO_ROOT = "D:/Gamedev/UEtoO3DE"
PACKAGE_ROOT = REPO_ROOT + "/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python"
LIB_ROOT = REPO_ROOT + "/Tests/lib"
for path in (PACKAGE_ROOT, LIB_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

OUT_PATH = REPO_ROOT + "/Tests/ue/results/probe_m2_fbx_handedness.txt"
SCRATCH = REPO_ROOT + "/Tests/ue/results/handedness"
SOURCE = "/Game/Meshes/SM_LetterF.SM_LetterF"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_M2_HAND] " + str(msg))


def export(asset, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    task = unreal.AssetExportTask()
    task.object = asset
    task.filename = path
    task.automated = True
    task.replace_identical = True
    task.prompt = False
    options = unreal.FbxExportOption()
    options.set_editor_property("collision", False)
    options.set_editor_property("level_of_detail", False)
    task.options = options
    if not unreal.Exporter.run_asset_export_task(task):
        raise RuntimeError("export failed: " + path)


def y_range(path):
    import fbx_reader
    stats = fbx_reader.vertex_stats(path)
    return stats["min"][1], stats["max"][1], stats


def main():
    from ueo3de import mesh_export

    source = unreal.EditorAssetLibrary.load_asset(SOURCE)
    box = source.get_bounding_box()
    out("UE source asset      y = [%.3f, %.3f]" % (box.min.y, box.max.y))

    # --- 1. export the source directly, no mirror ---
    plain_path = SCRATCH + "/plain.fbx"
    export(source, plain_path)
    low, high, stats = y_range(plain_path)
    out("FBX from source      y = [%.3f, %.3f]  (%d control points)"
        % (low, high, stats["count"]))
    plain_negated = abs(low + 37.5) < 0.01 and abs(high - 12.5) < 0.01

    # --- 2. export a mirrored bake, the way mesh_export currently does ---
    dyn = mesh_export._mirrored_dynamic_mesh(source)
    temp_path, baked = mesh_export._bake_temp_asset(dyn, "SM_LetterF")
    baked_box = baked.get_bounding_box()
    out("baked mirrored asset y = [%.3f, %.3f]" % (baked_box.min.y, baked_box.max.y))
    mirrored_path = SCRATCH + "/mirrored.fbx"
    export(baked, mirrored_path)
    low2, high2, _stats2 = y_range(mirrored_path)
    out("FBX from mirrored    y = [%.3f, %.3f]" % (low2, high2))
    unreal.EditorAssetLibrary.delete_asset(temp_path)
    unreal.EditorAssetLibrary.delete_directory(mesh_export.TEMP_PACKAGE_DIR)

    out("")
    if plain_negated:
        out("VERDICT: UE's FBX exporter negates Y itself. Lane B's reflection is "
            "already applied by the export, so mesh_export must NOT mirror -- "
            "doing so double-mirrors and cancels it out.")
    elif abs(low + 12.5) < 0.01 and abs(high - 37.5) < 0.01:
        out("VERDICT: the exporter is verbatim; the mirror belongs in "
            "mesh_export after all.")
    else:
        out("VERDICT: inconclusive, y = [%.3f, %.3f]; do not guess." % (low, high))


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
