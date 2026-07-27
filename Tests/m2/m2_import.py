"""
m2_import.py — run the O3DE-side import in the headless editor (plan M2).

Reads Exports/Fixture_01/manifest.json, waits for every product asset it is
about to reference (constraint 8), creates the entities with Mesh components
and the actor hierarchy, and saves one `.prefab`.

Staging is expected to have happened already (`Tests/m2/m2_stage.py`) so that
`AssetProcessorBatch` can be run to completion first -- but `wait_for_asset` is
still called for every asset, because the barrier belongs in the code path that
references the asset, not in the shell script that usually runs first.

Run:  Tests/o3de/run_o3de_python.bat Tests/m2/m2_import.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm2_import_result.txt')

# Defaults target the acceptance fixture; UEO3DE_EXPORT points the same code at
# any exported level (see Tests/ue/export_level.bat).
EXPORT_DIR = os.environ.get("UEO3DE_EXPORT", "").strip() or \
    os.path.join(REPO_ROOT, "Exports", "Fixture_01")
LEVEL_NAME = os.path.basename(os.path.normpath(EXPORT_DIR))

MANIFEST_PATH = os.path.join(EXPORT_DIR, "manifest.json")
SOURCE_ASSETS = os.path.join(EXPORT_DIR, "Assets")
PREFAB_REL_PATH = "Prefabs/%s.prefab" % LEVEL_NAME
REPORT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm2_import_report_%s.json' % LEVEL_NAME)

lines = []
ok = True


def log(msg):
    lines.append(str(msg))
    print(msg)


def main():
    import azlmbr.legacy.general as general

    from ueimporter import importer

    project_root = general.get_game_folder().rstrip('/\\')
    project_assets = os.path.join(project_root, "Assets")
    prefab_path = os.path.join(project_root, *PREFAB_REL_PATH.split("/")).replace(os.sep, "/")

    log("project:  " + project_root)
    log("manifest: " + MANIFEST_PATH)
    log("prefab:   " + prefab_path)
    log("")

    # Physics backend: explicit via UEO3DE_BACKEND, else detected. Detection
    # refuses to guess when both backends resolve (constraint 5).
    backend = os.environ.get("UEO3DE_BACKEND", "").strip() or None

    report, saved = importer.import_level(
        manifest_path=MANIFEST_PATH,
        source_assets_root=SOURCE_ASSETS,
        project_assets_root=project_assets,
        prefab_path=prefab_path,
        backend=backend,
        log=log)

    log("")
    log("counters: %r" % (report.to_dict()["counters"],))
    for record in report.records():
        log("  [%s] %s %s - %s" % (record["severity"], record["code"],
                                   record["subject"], record["detail"]))

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    report.write(REPORT_PATH)
    log("wrote " + REPORT_PATH)

    if report.has_errors():
        raise RuntimeError("import report contains error-severity records")
    if not os.path.exists(saved):
        raise RuntimeError("prefab was not written: " + saved)


try:
    main()
except Exception:
    ok = False
    log('EXCEPTION: ' + traceback.format_exc())

log('RESULT: ' + ('PASS' if ok else 'FAIL'))
os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
if ok:
    _general.exit_no_prompt()
else:
    os._exit(1)
