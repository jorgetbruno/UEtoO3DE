"""
m11_realworld.py — port a real level end to end and write down the numbers.

The plan's M11 asks for "one medium UE demo level ported end to end;
performance sanity check in O3DE game mode; entity-count and memory figures
recorded". Every other suite in this repo measures a fixture built to be
measurable. This one measures content that was made by someone else for a
different engine, which is the only kind of input that finds the problems
fixtures cannot: 2905 entities, 453 assets, a landscape, foliage, and
materials nobody designed to convert cleanly.

What is measured, and what each figure is worth:

  * entity/asset counts and warnings by code -- the honest scorecard of how
    much of a real level survives, including how much of it is approximated;
  * import wall-clock -- the number a user waits through;
  * prefab size on disk;
  * process working set, before and after instantiating -- what the level
    costs the editor;
  * frame wall-clock in game mode.

That last one needs a caveat stated plainly rather than buried: this is a
HEADLESS BATCH EDITOR (-BatchMode -autotest_mode). The figure is a sanity
check that the level simulates at a plausible rate and nothing pathological
happens -- it is NOT a shipping-runtime frame rate and must never be quoted
as one.

Env:  UEO3DE_EXPORT (default Exports/L_Showcase), UEO3DE_FRAMES (default 300)
Run:  Tests/o3de/run_o3de_python.bat Tests/m11/m11_realworld.py <result> <project>
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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm11_realworld_result.txt')

EXPORT_DIR = os.environ.get("UEO3DE_EXPORT") or os.path.join(REPO_ROOT, "Exports", "L_Showcase")
FRAMES = int(os.environ.get("UEO3DE_FRAMES", "300"))
FIGURES_PATH = os.path.join(SCRIPT_DIR, "results", "figures.md")

lines = []
failures = []
figures = {}


def log(msg=""):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def check(condition, msg):
    if not condition:
        fail(msg)
    return condition


MEMORY_ERROR = [None]


def working_set_mb():
    """Editor process working set in MB, via psapi. Returns None on failure and
    records why in MEMORY_ERROR.

    No third-party dependency: the editor's Python has no psutil and adding one
    to take a single number would be a poor trade. The two `restype`/`argtypes`
    declarations below are not decoration -- without them ctypes assumes a
    32-bit `int` return, `GetCurrentProcess`'s pseudo-handle is truncated on
    64-bit, and every call fails. The first run of this test reported "memory:
    unavailable" for exactly that reason, and an except-and-return-None with no
    message is how it stayed a mystery for a whole 13-minute run.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t)]

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        handle = kernel32.GetCurrentProcess()

        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        query.restype = wintypes.BOOL

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not query(handle, ctypes.byref(counters), counters.cb):
            MEMORY_ERROR[0] = ("GetProcessMemoryInfo failed (GetLastError=%d)"
                               % ctypes.get_last_error())
            return None
        return counters.WorkingSetSize / (1024.0 * 1024.0)
    except Exception as exc:
        MEMORY_ERROR[0] = "%s: %s" % (type(exc).__name__, exc)
        return None


def main():
    import azlmbr.bus as bus
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath
    import azlmbr.prefab as prefab

    from ueimporter import importer

    manifest_path = os.path.join(EXPORT_DIR, "manifest.json")
    if not check(os.path.isfile(manifest_path),
                 "no manifest at %s -- export a real level first (see "
                 "Tests/ue/export_level.bat)" % manifest_path):
        return
    with open(manifest_path, "r") as handle:
        document = json.load(handle)

    level_name = (document.get("level") or {}).get("name") or "RealWorld"
    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = "%s/Prefabs/%s_M11.prefab" % (project_root, level_name)

    figures["level"] = level_name
    figures["manifest_entities"] = len(document["entities"])
    figures["manifest_assets"] = len(document["assets"])
    figures["export_warnings"] = len(document.get("warnings") or [])
    log("=== %s: %d entities, %d assets, %d export warnings ==="
        % (level_name, figures["manifest_entities"], figures["manifest_assets"],
           figures["export_warnings"]))

    check(figures["manifest_entities"] >= 500,
          "this is meant to be a MEDIUM real level; %d entities is a fixture, "
          "and the figures would not mean what the plan asks them to mean"
          % figures["manifest_entities"])

    # A CLEAN slate, or the figures describe the wrong thing. With a prefab and
    # ledger left by an earlier run, this becomes an incremental re-import:
    # entities the previous run left at edited transforms are detected as
    # conflicts and KEPT, the import does less work than a real port, and the
    # recorded scorecard describes a state no user would ever be in -- while
    # still printing PASS.
    from ueimporter import reimport as reimport_module
    for stale in (prefab_path, reimport_module.ledger_path_for(prefab_path)):
        if os.path.exists(stale):
            os.remove(stale)
            log("  removed stale %s" % os.path.basename(stale))

    baseline = working_set_mb()
    figures["memory_baseline_mb"] = baseline
    log("  editor working set before import: %s"
        % ("%.0f MB" % baseline if baseline else
           "unavailable (%s)" % MEMORY_ERROR[0]))
    # The plan asks for memory figures by name. A run that reports everything
    # else and shrugs at memory has not produced the deliverable, so it fails
    # here rather than passing with a hole in it.
    check(baseline is not None,
          "could not read the process working set (%s) -- memory is one of the "
          "figures M11 exists to record" % MEMORY_ERROR[0])

    log("")
    log("=== importing (wall clock) ===")
    started = time.perf_counter()
    report, saved = importer.import_level(
        manifest_path=manifest_path,
        source_assets_root=os.path.join(EXPORT_DIR, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        restage=True,
        log=lambda m: None)
    elapsed = time.perf_counter() - started
    figures["import_seconds"] = elapsed
    figures["entities_created"] = report.counters.get("entities_created", 0)
    log("  %.1f s for %d entities (%.1f ms/entity)"
        % (elapsed, figures["entities_created"],
           1000.0 * elapsed / max(1, figures["entities_created"])))

    check(not report.has_errors(), "the import reported errors")
    check(figures["entities_created"] == figures["manifest_entities"],
          "created %d of %d entities"
          % (figures["entities_created"], figures["manifest_entities"]))
    # Prove the slate was clean rather than assuming the deletes above worked.
    # A conflict here means this measured a re-import, and the figures would be
    # describing less work than a real port does.
    check(report.counters.get("reimport_conflicts", 0) == 0
          and report.counters.get("reimport_added", 0) == 0,
          "this was not a first import (conflicts=%r added=%r), so the figures "
          "describe an incremental re-import rather than porting a level"
          % (report.counters.get("reimport_conflicts"),
             report.counters.get("reimport_added")))

    log("")
    log("=== counters ===")
    for key in sorted(report.counters):
        log("  %-28s %d" % (key, report.counters[key]))
        figures["counter_" + key] = report.counters[key]

    by_code = {}
    for record in report.records():
        by_code[record["code"]] = by_code.get(record["code"], 0) + 1
    log("=== import warnings by code ===")
    for code in sorted(by_code):
        log("  %-32s x%d" % (code, by_code[code]))
    figures["import_warnings"] = len(report.records())
    figures["import_warning_codes"] = by_code

    size_mb = os.path.getsize(prefab_path) / (1024.0 * 1024.0)
    figures["prefab_mb"] = size_mb
    log("")
    log("  prefab on disk: %.2f MB" % size_mb)

    after_import = working_set_mb()
    figures["memory_after_import_mb"] = after_import
    if baseline and after_import:
        log("  editor working set after import: %.0f MB (+%.0f MB)"
            % (after_import, after_import - baseline))

    log("")
    log("=== instantiate + simulate ===")
    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(60)

    started = time.perf_counter()
    outcome = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'InstantiatePrefab', prefab_path, entity_module.EntityId(),
        azmath.Vector3(0.0, 0.0, 0.0))
    if not check(outcome is not None and outcome.IsSuccess(),
                 "InstantiatePrefab failed for the real level"):
        return
    general.idle_wait_frames(120)
    figures["instantiate_seconds"] = time.perf_counter() - started
    log("  instantiated in %.1f s" % figures["instantiate_seconds"])

    after_instantiate = working_set_mb()
    figures["memory_after_instantiate_mb"] = after_instantiate
    if baseline and after_instantiate:
        log("  editor working set with the level loaded: %.0f MB (+%.0f MB "
            "over baseline)" % (after_instantiate, after_instantiate - baseline))

    general.enter_game_mode()
    general.idle_wait_frames(60)          # let the first frames settle
    started = time.perf_counter()
    general.idle_wait_frames(FRAMES)
    frame_elapsed = time.perf_counter() - started
    general.exit_game_mode()
    general.idle_wait_frames(30)

    figures["frames"] = FRAMES
    figures["frame_ms"] = 1000.0 * frame_elapsed / FRAMES
    log("  %d frames in game mode: %.1f s (%.1f ms/frame)"
        % (FRAMES, frame_elapsed, figures["frame_ms"]))
    log("  NOTE: headless batch editor -- a sanity check that the level runs, "
        "NOT a shipping frame rate.")

    # The sanity part of "performance sanity check": a level that takes
    # seconds per frame is broken in a way worth failing over, while anything
    # in the normal range is simply recorded.
    check(figures["frame_ms"] < 1000.0,
          "%.0f ms/frame -- the level does not simulate at a plausible rate"
          % figures["frame_ms"])

    os.makedirs(os.path.dirname(FIGURES_PATH), exist_ok=True)
    with open(FIGURES_PATH, "w") as handle:
        json.dump(figures, handle, indent=2, sort_keys=True, default=str)
    log("")
    log("  figures written to " + FIGURES_PATH)


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
