"""
export_fixture_manifest.py — M1 entry point: export Fixture_01 to manifest.json.

Thin wrapper. All logic lives in the `ueo3de` package that ships inside the
plugin, so the same code path runs from the editor UI in M10.

The package is added to sys.path explicitly rather than relying on UE's
plugin Content/Python auto-registration: this script must work in a
commandlet run whether or not the plugin's binary module loaded.

Run:  run_ue_python.bat export_fixture_manifest.py
Output: Exports/Fixture_01/manifest.json  (+ RESULT line, non-zero exit on failure)
"""

import os
import sys
import traceback

import unreal

REPO_ROOT = "D:/Gamedev/UEtoO3DE"
PACKAGE_ROOT = REPO_ROOT + "/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python"
MAP_PATH = "/Game/Maps/Fixture_01"
OUTPUT_PATH = REPO_ROOT + "/Exports/Fixture_01/manifest.json"
RESULT_PATH = REPO_ROOT + "/Tests/ue/results/export_fixture_manifest_result.txt"

if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from ueo3de import ue_level  # noqa: E402  (path must be set first)
from ueo3de.warnings import ERROR, WARN  # noqa: E402

lines = []


def log(message):
    lines.append(str(message))
    unreal.log("[M1_EXPORT] " + str(message))


status = "PASS"
try:
    document, warnings = ue_level.export_level(MAP_PATH, OUTPUT_PATH)
    log("wrote " + OUTPUT_PATH)
    log("entities: %d" % len(document["entities"]))
    log("assets:   %d" % len(document["assets"]))
    log("warnings: %d (%d warn, %d error)"
        % (len(warnings), warnings.count_by_severity(WARN),
           warnings.count_by_severity(ERROR)))
    for record in document["warnings"]:
        log("  [%s] %s %s — %s" % (record["severity"], record["code"],
                                   record["subject"], record["detail"]))
except Exception:
    log("EXPORT FAILED")
    log(traceback.format_exc())
    unreal.log_error("[M1_EXPORT] " + traceback.format_exc())
    status = "FAIL"

lines.append("RESULT: " + status)
os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
with open(RESULT_PATH, "w") as handle:
    handle.write("\n".join(lines) + "\n")

print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)
