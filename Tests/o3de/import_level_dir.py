"""
import_level_dir.py — import ANY staged+processed export directory (generic).

The per-milestone acceptance scripts import their own fixed levels; this is
the loose tool for real content (L_Showcase, Showcase_Ghoul, ...): fresh
import into Prefabs/<level>.prefab, counters logged, PASS iff the report has
no errors.

Prereqs: the export dir was staged (Tests/m2/m2_stage.py --manifest ...) and
AssetProcessorBatch ran.

Env:  UEO3DE_EXPORT = export directory (default Exports/Showcase_Ghoul)
Run:  Tests/o3de/run_o3de_python.bat Tests/o3de/import_level_dir.py
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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'import_level_dir_result.txt')

EXPORT_DIR = os.environ.get("UEO3DE_EXPORT", "").strip() or \
    os.path.join(REPO_ROOT, "Exports", "Showcase_Ghoul")
LEVEL_NAME = os.path.basename(os.path.normpath(EXPORT_DIR))

lines = []
failures = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def main():
    import azlmbr.legacy.general as general
    from ueimporter import importer

    project_root = general.get_game_folder().rstrip('/\\')
    # UEO3DE_CHUNK=i/n gets its own prefab, or every chunk would overwrite the
    # last and the level would end up as its final twelfth.
    chunk = os.environ.get("UEO3DE_CHUNK", "").strip()
    suffix = ""
    if chunk:
        index, _, total = chunk.partition("/")
        suffix = "_part%02d_of_%02d" % (int(index), int(total))
    prefab_path = "%s/Prefabs/%s%s.prefab" % (project_root, LEVEL_NAME, suffix)

    # The bisect knob importer.py documents but nothing exposed: import only
    # the first N entities. A 2905-entity level is a 13-minute measurement,
    # which is a poor loop to debug a performance question in; a few hundred
    # entities of the SAME level answers "which sub-phase dominates" in a
    # fraction of the time. Full-scale confirmation still comes from
    # Tests/m11/run_m11.bat.
    max_entities = os.environ.get("UEO3DE_MAX_ENTITIES", "").strip()
    max_entities = int(max_entities) if max_entities else None
    if max_entities:
        log("UEO3DE_MAX_ENTITIES=%d -- a SAMPLE, not the whole level"
            % max_entities)

    log("importing %s -> %s" % (EXPORT_DIR, prefab_path))
    report, _saved = importer.import_level(
        manifest_path=os.path.join(EXPORT_DIR, "manifest.json"),
        source_assets_root=os.path.join(EXPORT_DIR, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        max_entities=max_entities,
        log=log)

    log('')
    log('=== phase timings ===')
    for name, seconds, share in report.timing_rows():
        log('  %-46s %8.1f s  %5.1f%%' % (name, seconds, share))
    if report.subtimings:
        log('  -- within a phase (already counted above) --')
        for name, seconds in sorted(report.subtimings.items(), key=lambda kv: -kv[1]):
            log('    %-44s %8.1f s' % (name, seconds))
    log('')
    log('=== counters ===')
    for key in sorted(report.counters):
        log('  %-28s %d' % (key, report.counters[key]))
    log('=== importer warnings by code ===')
    by_code = {}
    for record in report.records():
        by_code[record["code"]] = by_code.get(record["code"], 0) + 1
    for code in sorted(by_code):
        log('  %-28s x%d' % (code, by_code[code]))

    if report.has_errors():
        fail("import report contains errors")


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
