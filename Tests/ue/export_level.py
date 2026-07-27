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
checks it against Lane A -- see export_fixture.py for why that check exists.
"""

import os
import sys
import traceback

import unreal

REPO_ROOT = "D:/Gamedev/UEtoO3DE"
PACKAGE_ROOT = REPO_ROOT + "/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python"
LIB_ROOT = REPO_ROOT + "/Tests/lib"

for _path in (PACKAGE_ROOT, LIB_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import fbx_reader  # noqa: E402
from ueo3de import lane_a, mesh_export, ue_level  # noqa: E402
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


def verify_lane_b(record):
    """The written FBX must equal Lane A applied to the source asset's bounds."""
    def to_o3de_cm(vector):
        return [component * 100.0 for component in lane_a.convert_position(vector)]

    corner_a = to_o3de_cm(record["ue_bounds_min"])
    corner_b = to_o3de_cm(record["ue_bounds_max"])
    expected_min = [min(corner_a[i], corner_b[i]) for i in range(3)]
    expected_max = [max(corner_a[i], corner_b[i]) for i in range(3)]

    path = os.path.join(ASSETS_ROOT, record["relative_path"]).replace("\\", "/")
    stats = fbx_reader.vertex_stats(path)
    deltas = [max(abs(stats["min"][i] - expected_min[i]),
                  abs(stats["max"][i] - expected_max[i])) for i in range(3)]
    if max(deltas) > BOUNDS_TOLERANCE_CM:
        raise RuntimeError(
            "%s: exported geometry disagrees with Lane A.\n"
            "  FBX bounds      %s .. %s\n"
            "  Lane A predicts %s .. %s"
            % (record["relative_path"],
               [round(v, 3) for v in stats["min"]], [round(v, 3) for v in stats["max"]],
               [round(v, 3) for v in expected_min], [round(v, 3) for v in expected_max]))


status = "PASS"
try:
    log("level:  " + MAP_PATH)
    log("output: " + OUTPUT_DIR)
    log("")

    log("== manifest ==")
    document, warnings = ue_level.export_level(MAP_PATH, MANIFEST_PATH)
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
    log("== Lane B check: every written FBX against Lane A ==")
    for record in exported:
        verify_lane_b(record)
    log("  ok: all %d meshes match Lane A" % len(exported))
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
if status != "PASS":
    raise SystemExit(1)
