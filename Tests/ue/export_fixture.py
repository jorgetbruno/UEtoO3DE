"""
export_fixture.py — the UE-side export of Fixture_01 (plan M1 + M2).

One entry point for both halves of the UE side, because the second depends on
the first: the manifest decides which meshes are referenced and what each one's
O3DE-relative path is, and the FBX export writes exactly those files to exactly
those paths. Two scripts would be two places to keep in sync.

    Exports/Fixture_01/manifest.json                     (M1)
    Exports/Fixture_01/Assets/uetoo3de/**/*.fbx          (M2, one per unique GUID)

After exporting, every written FBX is re-read and its bounds checked. The
expectation is **verbatim UE bounds (in cm)**: the exporter's baked mirror and
UE's own FBX-writer negation cancel at the file level, and SceneAPI's axis
conversion then applies the net reflection in the product. (The product-level
Lane A assertion lives in `Tests/m2/test_m2_artifacts.py`, reading the cache's
position buffers directly -- the FBX is the wrong place to assert the final
orientation, which is exactly the mistake that shipped a mirrored pipeline
during M2. See LANE_B.md.)

Run:  export_fixture.bat (a FULL editor session since M8 -- the skeletal
canaries go through UE's native FBX exporter, which asserts on missing render
objects in commandlets; see probe_m8_skeletal.py).
Writes Tests/ue/results/export_fixture_result.txt; the .bat asserts on the
RESULT line because the editor's exit code is meaningless under quit_editor.
"""

import os
import sys
import traceback

import unreal

REPO_ROOT = "D:/Gamedev/UEtoO3DE"
PACKAGE_ROOT = REPO_ROOT + "/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python"
LIB_ROOT = REPO_ROOT + "/Tests/lib"
MAP_PATH = "/Game/Maps/Fixture_01"
OUTPUT_DIR = REPO_ROOT + "/Exports/Fixture_01"
MANIFEST_PATH = OUTPUT_DIR + "/manifest.json"
ASSETS_ROOT = OUTPUT_DIR + "/Assets"
RESULT_PATH = REPO_ROOT + "/Tests/ue/results/export_fixture_result.txt"

for _path in (PACKAGE_ROOT, LIB_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import fbx_reader  # noqa: E402
from ueo3de import lane_a, mesh_export, ue_level  # noqa: E402
from ueo3de.warnings import ERROR, WARN  # noqa: E402

# The FBX is still in centimetres; only the .assetinfo scales it to metres.
BOUNDS_TOLERANCE_CM = 1e-3

lines = []


def log(message):
    lines.append(str(message))
    unreal.log("[EXPORT_FIXTURE] " + str(message))


def verify_fbx_intermediate(record):
    """The written FBX must match the RECORD's expected bounds (cm).

    mesh_export mirrors the expectation for normal entries (the bake nets
    diag(-1,1,1) at the FBX level, Lane B rev 4) and leaves #mx variants
    verbatim.

    The baked mirror (stage 1) and UE's FBX-writer negation (stage 2) cancel
    at the file level; SceneAPI (stage 3) applies the net reflection and the
    unit conversion in the product. An FBX that does NOT match verbatim means
    one of the first two stages is missing or doubled -- and the product would
    come out mirrored.
    """
    expected_min = list(record["ue_bounds_min"])
    expected_max = list(record["ue_bounds_max"])

    path = os.path.join(ASSETS_ROOT, record["relative_path"]).replace("\\", "/")
    stats = fbx_reader.vertex_stats(path)

    tolerance = record.get("tolerance_cm", BOUNDS_TOLERANCE_CM)
    deltas = [max(abs(stats["min"][i] - expected_min[i]),
                  abs(stats["max"][i] - expected_max[i])) for i in range(3)]
    if max(deltas) > tolerance:
        raise RuntimeError(
            "%s: FBX does not match its expected intermediate bounds.\n"
            "  FBX bounds %s .. %s\n"
            "  UE source  %s .. %s\n"
            "The bake stage and UE's export negation should cancel here; one "
            "of them is missing or doubled, and the product will be mirrored."
            % (record["relative_path"],
               [round(v, 3) for v in stats["min"]], [round(v, 3) for v in stats["max"]],
               [round(v, 3) for v in expected_min], [round(v, 3) for v in expected_max]))
    return stats, expected_min, expected_max


status = "PASS"
try:
    log("== manifest ==")
    document, warnings, asset_table = ue_level.export_level(MAP_PATH, MANIFEST_PATH)
    log("  wrote " + MANIFEST_PATH)
    log("  entities: %d  assets: %d  warnings: %d (%d warn, %d error)"
        % (len(document["entities"]), len(document["assets"]), len(warnings),
           warnings.count_by_severity(WARN), warnings.count_by_severity(ERROR)))
    for record in document["warnings"]:
        log("    [%s] %s %s - %s" % (record["severity"], record["code"],
                                     record["subject"], record["detail"]))

    log("== static mesh FBX export ==")
    exported = mesh_export.export_meshes(document["assets"], ASSETS_ROOT, log=log)
    mesh_assets = [a for a in document["assets"] if a["kind"] == "static_mesh"]
    log("  %d FBX files for %d unique mesh GUIDs" % (len(exported), len(mesh_assets)))
    if len(exported) != len(mesh_assets):
        raise RuntimeError("exported %d FBX files for %d mesh assets"
                           % (len(exported), len(mesh_assets)))

    log("== skeletal mesh + animation FBX export (M8, native exporter) ==")
    skeletal_exported = mesh_export.export_skeletal(
        document["assets"], ASSETS_ROOT, log=log)
    skeletal_assets = [a for a in document["assets"]
                       if a["kind"] in ("skeletal_mesh", "animation")]
    log("  %d FBX files for %d skeletal/animation assets"
        % (len(skeletal_exported), len(skeletal_assets)))
    if len(skeletal_exported) != len(skeletal_assets):
        raise RuntimeError("exported %d skeletal FBX files for %d assets"
                           % (len(skeletal_exported), len(skeletal_assets)))

    log("== texture export (TGA, role-suffixed; ORM channels split) ==")
    raw_root = OUTPUT_DIR + "/RawTextures"
    texture_files = asset_table.texture_bank.export_all(ASSETS_ROOT, raw_root, log=log)
    texture_assets = [a for a in document["assets"] if a["kind"] == "texture"]
    log("  %d texture files for %d texture entries" % (len(texture_files), len(texture_assets)))
    if len(texture_files) != len(texture_assets):
        raise RuntimeError("texture export count mismatch")

    log("== FBX intermediate check: bake and export negations cancel ==")
    for record in exported:
        stats, expected_min, expected_max = verify_fbx_intermediate(record)
        log("  %-46s y [%.3f, %.3f] (UE source y [%.3f, %.3f])"
            % (record["relative_path"], stats["min"][1], stats["max"][1],
               record["ue_bounds_min"][1], record["ue_bounds_max"][1]))
    log("  ok: all %d FBX files match their expected intermediate bounds (mirror-X for normal entries, verbatim for #mx variants)" % len(exported))

    log("== skeletal FBX intermediate check: mirror-Y (no bake stage) ==")
    for record in skeletal_exported:
        if record["kind"] != "skeletal_mesh":
            continue    # animations carry no geometry; curve check ran at export
        stats, _emin, _emax = verify_fbx_intermediate(record)
        log("  %-46s y [%.3f, %.3f]"
            % (record["relative_path"], stats["min"][1], stats["max"][1]))
except Exception:
    log("EXPORT FAILED")
    log(traceback.format_exc())
    unreal.log_error("[EXPORT_FIXTURE] " + traceback.format_exc())
    status = "FAIL"

lines.append("RESULT: " + status)
os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
with open(RESULT_PATH, "w") as handle:
    handle.write("\n".join(lines) + "\n")

print("RESULT: " + status)
# Under -ExecutePythonScript the editor must be told to exit; harmless in a
# commandlet, where the process ends with the script anyway.
try:
    unreal.SystemLibrary.quit_editor()
except Exception:
    pass
if status != "PASS":
    raise SystemExit(1)
