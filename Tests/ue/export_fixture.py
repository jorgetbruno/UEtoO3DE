"""
export_fixture.py — the UE-side export of Fixture_01 (plan M1 + M2).

One entry point for both halves of the UE side, because the second depends on
the first: the manifest decides which meshes are referenced and what each one's
O3DE-relative path is, and the FBX export writes exactly those files to exactly
those paths. Two scripts would be two places to keep in sync.

    Exports/Fixture_01/manifest.json                     (M1)
    Exports/Fixture_01/Assets/uetoo3de/**/*.fbx          (M2, one per unique GUID)

After exporting, every written FBX is re-read and its bounds checked against
`lane_a.convert_position` applied to the source asset's bounds. That check is
the reason this milestone works at all: Lane B's reflection is applied by UE's
FBX exporter (it converts left- to right-handed by negating Y), and an earlier
revision of `mesh_export` applied a second one, which cancelled it out
perfectly. Nothing downstream noticed -- the meshes simply came out mirrored
relative to their own placement. Checking the artifact rather than the
intention is what caught it, so the check lives here and runs every time.

Run:  run_ue_python.bat export_fixture.py
Writes Tests/ue/results/export_fixture_result.txt and exits non-zero on failure.
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


def verify_lane_b(record):
    """The written FBX must equal Lane A applied to the source asset's bounds.

    Lane A in centimetres -- the basis map without the metre conversion, since
    the `.assetinfo` supplies that later.
    """
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
            "  Lane A predicts %s .. %s\n"
            "Either the exporter stopped negating Y, or something mirrored the "
            "mesh a second time and cancelled it out."
            % (record["relative_path"],
               [round(v, 3) for v in stats["min"]], [round(v, 3) for v in stats["max"]],
               [round(v, 3) for v in expected_min], [round(v, 3) for v in expected_max]))
    return stats, expected_min, expected_max


status = "PASS"
try:
    log("== manifest ==")
    document, warnings = ue_level.export_level(MAP_PATH, MANIFEST_PATH)
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

    log("== Lane B check: every written FBX against Lane A ==")
    for record in exported:
        stats, expected_min, expected_max = verify_lane_b(record)
        log("  %-46s y [%.3f, %.3f] (UE source y [%.3f, %.3f])"
            % (record["relative_path"], stats["min"][1], stats["max"][1],
               record["ue_bounds_min"][1], record["ue_bounds_max"][1]))
    log("  ok: all %d meshes match Lane A" % len(exported))
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
if status != "PASS":
    raise SystemExit(1)
