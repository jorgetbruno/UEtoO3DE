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

    UEO3DE_MAP           package path of the level, e.g. /Game/Maps/L_Overview
    UEO3DE_OUT           output directory (default: Exports/<level name>)
    UEO3DE_MESH_WORKERS  worker editors for standalone meshes (default 1)
    UEO3DE_SPLINE_WORKERS worker editors that open the level and bake splines (default 0)
    UEO3DE_WORKER_GUI    1 = windowed workers (default: headless, -nullrhi)
    UEO3DE_REUSE_MESHES  1 = keep the previous export's static mesh FBX files

`run_ue_python.bat` sets these; see `export_level.bat` for the wrapper.

Writes <out>/manifest.json, <out>/Assets/**.fbx and <out>/export_records.json,
then ends its result file with `EDITOR: PASS`. The bounds check that proves
every FBX is verbatim UE geometry runs AFTER the editor exits, across every
core (Tests/ue/verify_export.py, called by export_level.bat), and writes the
final RESULT line -- see Tests/lib/export_verify.py and LANE_B.md.

A wide export (UEO3DE_MESH_WORKERS > 1) keeps everything bound to the open
level here -- manifest, textures, spline and terrain bakes, skeletal meshes --
and hands the meshes that load as standalone assets to worker editors on an
empty map, launched at the start of the mesh stage so both run at once.
"""

import json
import os
import subprocess
import sys
import time
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

from ueo3de import export_slices, knob_effects, mesh_export, ue_level  # noqa: E402
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
RECORDS_PATH = OUTPUT_DIR + "/export_records.json"
WORKER_DIR = OUTPUT_DIR + "/_workers"
WORKER_SCRIPT = REPO_ROOT + "/Tests/ue/export_mesh_worker.py"
# A worker that has exited without its done file gets this long (a slow disk
# finishing the write) before it is declared dead. A worker still running is
# never cut short by a clock -- only by exiting.
WORKER_EXIT_GRACE_S = 60

lines = []


def log(message):
    lines.append(str(message))
    unreal.log("[EXPORT_LEVEL] " + str(message))


def launch_workers(count, kind="standalone"):
    """Start `count` worker editors; returns [(label, proc, done)].

    Standalone workers stay on an empty map. Spline workers open the level.
    """
    engine = unreal.Paths.convert_relative_path_to_full(unreal.Paths.engine_dir())
    headless = os.environ.get("UEO3DE_WORKER_GUI", "").strip() != "1"
    editor = os.path.join(engine, "Binaries", "Win64",
                          "UnrealEditor-Cmd.exe" if headless else "UnrealEditor.exe")
    project = unreal.Paths.convert_relative_path_to_full(
        unreal.Paths.get_project_file_path()).replace(BACKSLASH, "/")
    os.makedirs(WORKER_DIR, exist_ok=True)
    launched = []
    for index in range(count):
        done = "%s/%s_worker_%d_of_%d.json" % (WORKER_DIR, kind, index, count)
        if os.path.exists(done):
            os.remove(done)
        # Forward slashes throughout: a backslash path whose next character is
        # a digit (a session folder named `0fe0...`) was read by the editor's
        # command line as an escape, and the script it looked for did not exist.
        script = ("%s %d %d %s %s %s %s %s" % (WORKER_SCRIPT, index, count, MANIFEST_PATH,
                                               ASSETS_ROOT, done, kind, MAP_PATH)
                  ).replace(BACKSLASH, "/")
        args = [editor, project, "/Engine/Maps/Entry",
                "-ExecutePythonScript=" + script,
                "-EnablePlugins=GeometryScripting,PythonScriptPlugin",
                "-unattended", "-nop4", "-nosplash", "-nosound"]
        if headless:
            args.append("-nullrhi")
        label = "%s worker %d/%d" % (kind, index + 1, count)
        launched.append((label, subprocess.Popen(args), done))
        log("  %s started (%s)" % (label, "headless" if headless else "windowed"))
    return launched


def wait_for_workers(launched):
    """Block until every worker has written its done file; returns record sets."""
    pending = {label: (proc, done) for label, proc, done in launched}
    exited_at = {}
    results = []
    while pending:
        for label in sorted(pending):
            proc, done = pending[label]
            if os.path.exists(done):
                with open(done, "r") as handle:
                    payload = json.load(handle)
                del pending[label]
                if payload.get("error"):
                    raise RuntimeError("%s failed:%s%s" % (label, NEWLINE, payload["error"]))
                loaded = payload.get("level_load_seconds")
                log("  %s: %d of %d meshes in %.0fs%s (t+%.0fs)"
                    % (label, len(payload["records"]), payload.get("assigned", -1),
                       payload.get("seconds", 0.0),
                       ", level loaded in %.0fs" % loaded if loaded is not None else "",
                       time.time() - EXPORT_STARTED))
                results.append((label, payload["records"]))
                for key, value in (payload.get("bake_stats") or {}).items():
                    WORKER_BAKE_STATS[key] = WORKER_BAKE_STATS.get(key, 0) + value
                continue
            if proc.poll() is not None:
                exited_at.setdefault(label, time.time())
                if time.time() - exited_at[label] > WORKER_EXIT_GRACE_S:
                    raise RuntimeError(
                        "%s exited (code %s) without its done file %s; "
                        "its Saved/Logs entry has the reason"
                        % (label, proc.returncode, done))
        if pending:
            time.sleep(5)
    return results


def kill_workers(launched):
    """Never leave headless editors behind a failed lead."""
    for _index, proc, _done in launched:
        if proc.poll() is None:
            proc.kill()


WORKER_BAKE_STATS = {}
BACKSLASH = chr(92)
NEWLINE = chr(10)


status = "PASS"
launched = []
EXPORT_STARTED = time.time()


def stage(name):
    log("")
    log("== %s ==  (t+%.0fs)" % (name, time.time() - EXPORT_STARTED))


try:
    log("level:  " + MAP_PATH)
    log("output: " + OUTPUT_DIR)
    log("")

    log("== manifest ==")
    document, warnings, asset_table = ue_level.export_level(MAP_PATH, MANIFEST_PATH)
    log("  wrote " + MANIFEST_PATH)
    # Workers need nothing but the manifest on disk, so they start now and
    # bake through this editor's texture export instead of after it.
    reuse = export_slices.reuse_requested()
    workers = export_slices.mesh_worker_count()
    spline_workers = export_slices.spline_worker_count()
    if workers > 1 and not reuse:
        launched = launch_workers(workers)
    if spline_workers > 0 and not reuse:
        launched += launch_workers(spline_workers, kind="spline")
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

    stage("texture export")
    texture_files = asset_table.texture_bank.export_all(ASSETS_ROOT, OUTPUT_DIR + "/RawTextures")
    log("  %d texture files" % len(texture_files))

    if reuse:
        stage("static mesh FBX export: REUSED (UEO3DE_REUSE_MESHES=1)")
        with open(RECORDS_PATH, "r") as handle:
            exported = export_slices.reuse_mesh_records(
                document["assets"], json.load(handle), ASSETS_ROOT)
    else:
        stage("static mesh FBX export")
        lead = mesh_export.export_meshes(
            export_slices.lead_meshes(document["assets"], workers, spline_workers), ASSETS_ROOT)
        if launched:
            log("  lead: %d level-bound meshes done (t+%.0fs); waiting for workers"
                % (len(lead), time.time() - EXPORT_STARTED))
        worker_sets = wait_for_workers(launched)
        exported = export_slices.merge_records(document["assets"], [("lead", lead)] + worker_sets)
    mesh_assets = [a for a in document["assets"] if a["kind"] == "static_mesh"]
    total_bytes = sum(record["bytes"] for record in exported)
    log("  %d FBX files for %d unique mesh GUIDs (%.1f MB)"
        % (len(exported), len(mesh_assets), total_bytes / (1024.0 * 1024.0)))
    if len(exported) != len(mesh_assets):
        raise RuntimeError("exported %d FBX files for %d mesh assets"
                           % (len(exported), len(mesh_assets)))

    stage("skeletal mesh + animation FBX export (M8, native exporter)")
    skeletal_exported = mesh_export.export_skeletal(
        document["assets"], ASSETS_ROOT, log=log)
    skeletal_assets = [a for a in document["assets"]
                       if a["kind"] in ("skeletal_mesh", "animation")]
    log("  %d FBX files for %d skeletal/animation assets"
        % (len(skeletal_exported), len(skeletal_assets)))
    if len(skeletal_exported) != len(skeletal_assets):
        raise RuntimeError("exported %d skeletal FBX files for %d assets"
                           % (len(skeletal_exported), len(skeletal_assets)))
    empty = []
    for record in skeletal_exported:
        if record["kind"] != "skeletal_mesh":
            continue
        path = os.path.join(ASSETS_ROOT, record["relative_path"])
        with open(path, "rb") as handle:
            if b"PolygonVertexIndex" not in handle.read():
                empty.append(record)
    if empty:
        from ueo3de import manifest as manifest_module
        dropped = manifest_module.drop_empty_skeletal_meshes(
            document, [record["guid"] for record in empty])
        for record in empty:
            os.remove(os.path.join(ASSETS_ROOT, record["relative_path"]))
        skeletal_exported = [r for r in skeletal_exported if r not in empty]
        with open(MANIFEST_PATH, "w") as handle:
            handle.write(manifest_module.dumps(document))
        log("  SKEL_MESH_EMPTY_EXPORT: %d skeletal meshes wrote no geometry (all "
            "cloth) and were dropped from the manifest: %s" % (len(dropped), ", ".join(dropped)))

    # Knobs that had nothing to act on say so: a ratio that parses and then
    # finds no Nanite mesh is indistinguishable from one that worked.
    stats = mesh_export.bake_stats()
    for key, value in WORKER_BAKE_STATS.items():
        stats[key] = stats.get(key, 0) + value
    stage("knobs")
    log("  baked: %d Nanite, %d with authored LODs, %d single-LOD"
        % (stats.get("nanite", 0), stats.get("authored", 0), stats.get("single", 0)))
    for knob, message in knob_effects.no_effect_warnings(stats):
        log("  KNOB_NO_EFFECT %s %s" % (knob, message))
        unreal.log_warning("[EXPORT_LEVEL] KNOB_NO_EFFECT %s %s" % (knob, message))

    # The bounds check runs after this editor exits (Tests/ue/verify_export.py).
    checked = exported + [r for r in skeletal_exported if r["kind"] == "skeletal_mesh"]
    with open(RECORDS_PATH, "w") as handle:
        json.dump(checked, handle)
    stage("editor stages done")
    log("  %d records for the bounds check -> %s" % (len(checked), RECORDS_PATH))
except Exception:
    kill_workers(launched)
    log("EXPORT FAILED")
    log(traceback.format_exc())
    unreal.log_error("[EXPORT_LEVEL] " + traceback.format_exc())
    status = "FAIL"

# PASS is not this script's to give: the bounds check still has to run.
lines.append("EDITOR: PASS" if status == "PASS" else "RESULT: FAIL")
os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
with open(RESULT_PATH, "w") as handle:
    handle.write("\n".join(lines) + "\n")

print("EDITOR: PASS" if status == "PASS" else "RESULT: FAIL")

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
