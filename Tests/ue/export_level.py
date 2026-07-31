"""
export_level.py — export ANY level from ANY UE 5.8 project (plan M1 + M2).

`export_fixture.py` is pinned to Fixture_01 because it is an acceptance test.
This is the same pipeline with the level, project and output directory supplied
from outside, for exporting real content.

Nothing has to be installed into the target project: the `ueo3de` package is
pure Python apart from its `unreal` imports, so it is added to `sys.path` here
and the project's own plugins are irrelevant.

Configured through environment variables, because the pythonscript commandlet
gives a script no clean way to take arguments:

    UEO3DE_MAP     package path of the level, e.g. /Game/Maps/L_Overview
    UEO3DE_OUT     output directory (default: Exports/<level name>)

`run_ue_python.bat` sets these; see `export_level.bat` for the wrapper.

Writes <out>/manifest.json and <out>/Assets/**.fbx, then re-reads every FBX and
checks it is verbatim UE geometry (the bake and UE's export negation cancel at
the file level; SceneAPI applies the net reflection and unit conversion in the
product) -- see export_fixture.py and LANE_B.md.
"""

import os
import sys
import traceback

import unreal

# Derived from this file, never configured: a value that can be
# computed cannot be configured WRONG, and 40 files hardcoding one
# machine's drive letters is what that mistake looked like here.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))).replace("\\", "/")
PACKAGE_ROOT = REPO_ROOT + "/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python"
LIB_ROOT = REPO_ROOT + "/Tests/lib"

for _path in (PACKAGE_ROOT, LIB_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import fbx_reader  # noqa: E402
import gltf_reader  # noqa: E402
from ueo3de import mesh_export, ue_level  # noqa: E402
from ueo3de.warnings import ERROR, WARN  # noqa: E402

MAP_PATH = os.environ.get("UEO3DE_MAP", "").strip()
if not MAP_PATH:
    raise SystemExit("UEO3DE_MAP is not set (package path of the level to export)")

LEVEL_NAME = MAP_PATH.rstrip("/").rsplit("/", 1)[-1]
OUTPUT_DIR = os.environ.get("UEO3DE_OUT", "").strip() or (
    REPO_ROOT + "/Exports/" + LEVEL_NAME)
MANIFEST_PATH = OUTPUT_DIR + "/manifest.json"
ASSETS_ROOT = OUTPUT_DIR + "/Assets"
RESULT_PATH = REPO_ROOT + "/Tests/ue/results/export_" + LEVEL_NAME + "_result.txt"

BOUNDS_TOLERANCE_CM = 1e-3

lines = []


def log(message):
    lines.append(str(message))
    unreal.log("[EXPORT_LEVEL] " + str(message))


def gltf_source_is_gltf(path):
    """Is this mesh a glTF container? Asked of the PATH, never of a flag.

    A glb run is a MIXED-format export -- skeletal meshes and animations stay
    FBX -- so "which reader" is a per-file question.
    """
    return gltf_reader.gltf_source.is_gltf_source(path)


def verify_fbx_intermediate(record):
    """The written mesh must match the RECORD's expected bounds.

    mesh_export mirrors the expectation for normal entries (the bake nets
    diag(-1,1,1) at the FBX level, Lane B rev 4) and leaves #mx variants
    verbatim.

    Bake and UE-export negations cancel at the file level; SceneAPI applies
    the net reflection and the cm->m conversion in the product (LANE_B.md).
    """
    expected_min = list(record["ue_bounds_min"])
    expected_max = list(record["ue_bounds_max"])

    path = os.path.join(ASSETS_ROOT, record["relative_path"]).replace("\\", "/")
    tolerance = record.get("tolerance_cm", BOUNDS_TOLERANCE_CM)

    # ONE recorded expectation, converted per container. The record holds the
    # FBX-file expectation; a glTF is Y-up and in METRES, so both the numbers
    # and the tolerance have to be converted or the check is meaningless --
    # a 1e-3 cm tolerance against metre-scale values would pass anything.
    if gltf_source_is_gltf(path):
        label = "glTF"
        expected_min, expected_max = gltf_reader.expected_from_fbx_bounds(
            expected_min, expected_max)
        tolerance = tolerance / 100.0
        stats = gltf_reader.vertex_stats(path)
    else:
        label = "FBX"
        stats = fbx_reader.vertex_stats(path)

    deltas = [max(abs(stats["min"][i] - expected_min[i]),
                  abs(stats["max"][i] - expected_max[i])) for i in range(3)]
    if max(deltas) > tolerance:
        raise RuntimeError(
            "%s: %s does not match its expected intermediate bounds.\n"
            "  file bounds %s .. %s\n"
            "  expected    %s .. %s\n"
            "The bake stage and the writer's negation should cancel here; one "
            "is missing or doubled, and the product will be mirrored."
            % (record["relative_path"], label,
               [round(v, 4) for v in stats["min"]], [round(v, 4) for v in stats["max"]],
               [round(v, 4) for v in expected_min], [round(v, 4) for v in expected_max]))


status = "PASS"
try:
    log("level:  " + MAP_PATH)
    log("output: " + OUTPUT_DIR)
    log("")

    log("== manifest ==")
    document, warnings, asset_table = ue_level.export_level(MAP_PATH, MANIFEST_PATH)
    log("  wrote " + MANIFEST_PATH)
    log("  entities: %d  assets: %d  warnings: %d (%d warn, %d error)"
        % (len(document["entities"]), len(document["assets"]), len(warnings),
           warnings.count_by_severity(WARN), warnings.count_by_severity(ERROR)))

    # Real levels produce many warnings; summarize by code and show a sample so
    # the log stays readable without hiding anything.
    by_code = {}
    for record in document["warnings"]:
        by_code.setdefault(record["code"], []).append(record)
    for code in sorted(by_code):
        records = by_code[code]
        log("    %-28s %-6s x%d  e.g. %s"
            % (code, records[0]["severity"], len(records), records[0]["subject"]))

    log("")
    log("== entity kinds ==")
    kinds = {}
    for entity in document["entities"]:
        kinds[entity["kind"]] = kinds.get(entity["kind"], 0) + 1
    for kind in sorted(kinds):
        log("    %-14s %d" % (kind, kinds[kind]))

    log("")
    log("== texture export ==")
    texture_files = asset_table.texture_bank.export_all(ASSETS_ROOT, OUTPUT_DIR + "/RawTextures")
    log("  %d texture files" % len(texture_files))

    log("")
    log("== static mesh FBX export ==")
    exported = mesh_export.export_meshes(document["assets"], ASSETS_ROOT)
    mesh_assets = [a for a in document["assets"] if a["kind"] == "static_mesh"]
    total_bytes = sum(record["bytes"] for record in exported)
    log("  %d FBX files for %d unique mesh GUIDs (%.1f MB)"
        % (len(exported), len(mesh_assets), total_bytes / (1024.0 * 1024.0)))
    if len(exported) != len(mesh_assets):
        raise RuntimeError("exported %d FBX files for %d mesh assets"
                           % (len(exported), len(mesh_assets)))

    log("")
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

    log("")
    log("== FBX intermediate check: bake and export negations cancel ==")
    for record in exported:
        verify_fbx_intermediate(record)
    log("  ok: all %d FBX files match their expected intermediate bounds (mirror-X for normal entries, verbatim for #mx variants)" % len(exported))
    for record in skeletal_exported:
        if record["kind"] == "skeletal_mesh":
            verify_fbx_intermediate(record)   # mirror-Y, no bake stage (M8)
except Exception:
    log("EXPORT FAILED")
    log(traceback.format_exc())
    unreal.log_error("[EXPORT_LEVEL] " + traceback.format_exc())
    status = "FAIL"

lines.append("RESULT: " + status)
os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
with open(RESULT_PATH, "w") as handle:
    handle.write("\n".join(lines) + "\n")

print("RESULT: " + status)

# Under -ExecutePythonScript (a FULL editor session -- required since M7:
# terrain sampling needs the physics scene and commandlets have none) the
# editor must be told to exit, and its process exit code is not meaningful.
# The .bat asserts on the RESULT line in the result file instead.
try:
    unreal.SystemLibrary.quit_editor()
except Exception:
    pass
if status != "PASS":
    raise SystemExit(1)
