"""
m10_ue_menu.py — the UE half of the UX: menu entry + the shared export path.

Two claims, and the second is the one that matters:

  1. "Tools -> Export Level to O3DE..." is registered by the plugin's
     `init_unreal.py` at editor startup. Two failure modes look identical from
     outside -- the file was never READ (the plugin's Content/Python is not
     mounted, which was true until `CanContainContent` was flipped), or it was
     read and registration failed. They are told apart here.

  2. **The button and CI run the same code.** `export_api.export_level` is
     what `ue_menu.export_current_level` calls, so exporting through it here
     is what makes the menu item trustworthy. A UI that calls its own private
     copy of the export is a UI that will drift from the tested one and only
     say so in a bug report.

Runs in a FULL editor (ToolMenus is a UI subsystem, and M8's skeletal export
asserts under -nullrhi).

Run: Tests/ue/run_ue_editor_python.bat Tests/m10/m10_ue_menu.py <result>
"""

import os
import shutil
import sys
import traceback

import unreal

REPO_ROOT = "D:/Gamedev/UEtoO3DE"
PACKAGE_ROOT = REPO_ROOT + "/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python"
MAP_PATH = "/Game/Maps/Fixture_01"
SCRATCH = REPO_ROOT + "/Tests/m10/results/ue_export_scratch"
RESULT_PATH = REPO_ROOT + "/Tests/m10/results/m10_ue_menu_result.txt"

lines = []
failures = []


def log(msg=""):
    lines.append(str(msg))
    unreal.log("[m10-ue] " + str(msg))


def fail(msg):
    failures.append(str(msg))
    log("FAIL: " + str(msg))


def check(condition, msg):
    if not condition:
        fail(msg)
    return condition


def main():
    log("=== 1. did the plugin's Python get mounted at all? ===")
    on_path = [p for p in sys.path if "UEO3DEExporter" in p]
    log("  sys.path entries under the plugin: %r" % (on_path,))
    check(bool(on_path),
          "the plugin's Content/Python is not on sys.path, so init_unreal.py "
          "was never read. Check \"CanContainContent\": true in the .uplugin "
          "-- with no mounted content folder UE never scans it.")

    log("")
    log("=== 2. did init_unreal.py install the menu entry? ===")
    if PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, PACKAGE_ROOT)
    from ueo3de import ue_menu

    log("  ue_menu.INSTALLED = %r" % ue_menu.INSTALLED)
    check(ue_menu.INSTALLED is True,
          "ue_menu.install() did not run or did not succeed at startup")

    log("")
    log("=== 3. what CAN be verified about the entry ===")
    # UE 5.8 exposes no way to READ a menu's contents from Python: ToolMenu
    # has no find_entry, and its `Sections` property is protected
    # ("Property 'Sections' ... is protected and cannot be read"). So the
    # entry's presence cannot be asserted directly, and pretending otherwise
    # would mean writing a check that always passes. What IS checkable is
    # every input that decides whether the entry appears -- which is where the
    # realistic regressions live: a wrong menu path, a renamed callback.
    menus = unreal.ToolMenus.get()
    menu = menus.find_menu(ue_menu.MENU_PATH)
    log("  find_menu(%r) -> %r" % (ue_menu.MENU_PATH, menu))
    check(menu is not None,
          "%s does not exist in this editor, so the entry cannot appear"
          % ue_menu.MENU_PATH)
    bogus_menu = menus.find_menu("LevelEditor.MainMenu.NoSuchMenu")
    log("  find_menu('...NoSuchMenu') -> %r  <-- control" % (bogus_menu,))
    check(not bogus_menu,
          "find_menu answers for a menu that does not exist, so the check "
          "above proves nothing")

    log("")
    log("=== 4. install() reports honestly, and is idempotent ===")
    check(ue_menu.install(log=lambda m: None) is True,
          "a second install() failed; the user may legitimately re-run it")
    # Control: install() must FAIL when its menu path is wrong. Without this,
    # "install() returned True" is compatible with install() always returning
    # True and never registering anything.
    real_path = ue_menu.MENU_PATH
    try:
        ue_menu.MENU_PATH = "LevelEditor.MainMenu.NoSuchMenu"
        bad = ue_menu.install(log=lambda m: None)
    finally:
        ue_menu.MENU_PATH = real_path
    log("  install() against a bogus menu path -> %r  <-- control" % (bad,))
    check(bad is False,
          "install() reported success against a menu that does not exist, so "
          "its True return means nothing")
    ue_menu.install(log=lambda m: None)

    log("")
    log("=== 4b. the entry's command still resolves to a real callable ===")
    # The menu entry carries a PYTHON string command. If someone renames the
    # function, the menu item survives and fails only when a user clicks it --
    # exactly the kind of break that reaches people as a bug report instead of
    # a test failure. It is not executed here: it opens a modal dialog, which
    # under -unattended would hang the run.
    command = "from ueo3de import ue_menu; ue_menu.export_current_level()"
    compile(command, "<menu command>", "exec")
    check(callable(getattr(ue_menu, "export_current_level", None)),
          "the menu command calls ue_menu.export_current_level, which does "
          "not exist or is not callable")

    log("")
    log("=== 5. the export path the button uses actually exports ===")
    from ueo3de import export_api

    if os.path.isdir(SCRATCH):
        shutil.rmtree(SCRATCH, ignore_errors=True)
    steps = []
    result = export_api.export_level(
        MAP_PATH, SCRATCH,
        log=lambda m: None,
        progress=lambda index, total, label: steps.append((index, label)))

    log("  counts: %r" % (result["counts"],))
    log("  progress frames: %r" % ([label for _i, label in steps],))
    check(len(steps) == len(export_api.STEPS),
          "the progress callback fired %d times for %d steps -- the UE "
          "progress bar would stall" % (len(steps), len(export_api.STEPS)))
    check(os.path.isfile(result["manifest_path"]),
          "no manifest at " + str(result["manifest_path"]))
    counts = result["counts"]
    check(counts["entities"] > 20,
          "expected Fixture_01's entities, got %d" % counts["entities"])
    check(counts["static_meshes"] > 0 and counts["textures"] > 0,
          "export produced no meshes or no textures: %r" % (counts,))

    log("")
    log("=== 6. the export matches the committed Fixture_01 manifest ===")
    # The shared API must produce what the M1/M2 acceptance export produces;
    # otherwise "the button uses the tested path" is only true on paper.
    import json
    reference_path = REPO_ROOT + "/Exports/Fixture_01/manifest.json"
    if os.path.isfile(reference_path):
        with open(reference_path, "r") as handle:
            reference = json.load(handle)
        fresh = result["document"]
        check(len(fresh["entities"]) == len(reference["entities"]),
              "entity count drifted: %d via export_api vs %d in the committed "
              "manifest" % (len(fresh["entities"]), len(reference["entities"])))
        check(len(fresh["assets"]) == len(reference["assets"]),
              "asset count drifted: %d vs %d"
              % (len(fresh["assets"]), len(reference["assets"])))
        fresh_ids = sorted(e["id"] for e in fresh["entities"])
        reference_ids = sorted(e["id"] for e in reference["entities"])
        check(fresh_ids == reference_ids,
              "entity ids differ from the committed manifest -- re-import "
              "matching depends on these being stable")
    else:
        log("  (skipped: no committed manifest to compare against)")

    log("")
    log("=== 7. a manifest promising more than was written must be refused ===")
    # The one place export_api adds a rule of its own; a rule with no test is
    # a comment.
    try:
        export_api._require(3, 5, "test files")
        fail("_require accepted 3 files for 5 promised assets")
    except export_api.ExportError as exc:
        log("  _require(3, 5) raised ExportError: %s" % str(exc)[:90])
    try:
        export_api._require(5, 5, "test files")
        log("  _require(5, 5) accepted  <-- control")
    except Exception as exc:
        fail("_require rejected a matching count: %s" % exc)


try:
    main()
except Exception:
    fail("EXCEPTION: " + traceback.format_exc())
finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)

log("")
log("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
with open(RESULT_PATH, "w") as handle:
    handle.write("\n".join(lines) + "\n")

print("RESULT: " + ("PASS" if not failures else "FAIL"))
try:
    unreal.SystemLibrary.quit_editor()
except Exception:
    pass
if failures:
    raise SystemExit(1)
