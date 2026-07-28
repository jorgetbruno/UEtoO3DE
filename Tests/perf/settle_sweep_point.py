"""
settle_sweep_point.py — one point of the settle sweep: import at a given
settle and count how many collider bakes reached the file.

The four probes that led here established that the settle cannot become a
readiness poll: the bake is not reflected, the template does not track late
bakes, the prefab cannot be re-created in-session, and a baked collider is
indistinguishable from an unbaked one through every Python-visible reading.
So the settle stays a constant -- but a constant should at least be a MEASURED
one, and today's 41,040 frames is a formula that grew while chasing a
different bug entirely.

This is the measurement. `UEO3DE_SETTLE_FRAMES=N` and one line of output:

    settle=3000  cooked=2501/2501  import=142.3s

Each point is its own editor process, deliberately: two imports in one session
would share a warmed asset cache and the second would not be measuring the
same thing as the first.

Env:  UEO3DE_EXPORT       export directory (default Exports/L_Showcase)
      UEO3DE_SETTLE_FRAMES the point being measured (required here)
      UEO3DE_SWEEP_LOG    file to APPEND the one-line result to
Run:  Tests/o3de/run_o3de_python.bat Tests/perf/settle_sweep_point.py
"""

import json
import os
import sys
import time
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'settle_sweep_point_result.txt')

EXPORT_DIR = os.environ.get("UEO3DE_EXPORT", "").strip() or \
    os.path.join(REPO_ROOT, "Exports", "L_Showcase")
LEVEL_NAME = os.path.basename(os.path.normpath(EXPORT_DIR))
SWEEP_LOG = os.environ.get("UEO3DE_SWEEP_LOG", "").strip()

lines = []
failures = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def cooked_census(path):
    with open(path, "r") as handle:
        document = json.load(handle)
    components = with_data = 0
    missing = []
    for entity in (document.get("Entities") or {}).values():
        for component in (entity.get("Components") or {}).values():
            if not isinstance(component, dict):
                continue
            if "MeshCollider" not in str(component.get("$type", "")):
                continue
            components += 1
            shape = component.get("ShapeConfiguration")
            cooked = shape.get("CookedData") if isinstance(shape, dict) else None
            if isinstance(cooked, str) and cooked:
                with_data += 1
            else:
                missing.append(entity.get("Name"))
    return components, with_data, sorted(missing)


def main():
    import azlmbr.legacy.general as general
    from ueimporter import importer

    settle = os.environ.get("UEO3DE_SETTLE_FRAMES", "").strip()
    if not settle:
        fail("UEO3DE_SETTLE_FRAMES is not set, so this point has no meaning")
        return

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = "%s/Prefabs/%s_sweep%s.prefab" % (project_root, LEVEL_NAME, settle)
    for stale in (prefab_path, prefab_path[:-len(".prefab")] + ".ueimport.json"):
        if os.path.exists(stale):
            os.remove(stale)

    started = time.perf_counter()
    report, _saved = importer.import_level(
        manifest_path=os.path.join(EXPORT_DIR, "manifest.json"),
        source_assets_root=os.path.join(EXPORT_DIR, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        log=lambda m: None)
    elapsed = time.perf_counter() - started

    authored = report.counters.get("mesh_colliders", 0)
    components, with_data, missing = cooked_census(prefab_path)
    settle_s = report.timings.get("settle: collider bakes", 0.0)

    line = ("settle=%-6s cooked=%d/%d  components=%d  import=%.1fs  settle_phase=%.1fs%s"
            % (settle, with_data, authored, components, elapsed, settle_s,
               ("  MISSING: " + ", ".join(missing[:6])) if missing else ""))
    log(line)
    if SWEEP_LOG:
        with open(SWEEP_LOG, "a") as handle:
            handle.write(line + "\n")

    os.remove(prefab_path)
    ledger = prefab_path[:-len(".prefab")] + ".ueimport.json"
    if os.path.exists(ledger):
        os.remove(ledger)


try:
    main()
except Exception:
    fail('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: ' + ('PASS' if not failures else 'FAIL (%d)' % len(failures)))
os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
if not failures:
    _general.exit_no_prompt()
else:
    os._exit(1)
