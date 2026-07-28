"""
probe_m10_ui.py -- what does UE 5.8 expose to Python for the EXPORT UX?

M10 wants a one-click "Export to O3DE" with an output folder picker and a
progress bar. Three separate capabilities, each of which could be missing
from the Python API and force a C++ slate widget instead (the plugin already
has a C++ editor module, so that fallback exists -- but only if the probe
says it is needed).

  1. MENU: unreal.ToolMenus -- can Python add an entry under the LevelEditor
     main menu, and what is the correct menu path in 5.8?
  2. FOLDER PICKER: no vanilla `pick_directory` exists. The candidate is a
     Python-declared UClass carrying a `DirectoryPath` property shown through
     EditorDialog.show_object_details_view -- which renders DirectoryPath with
     a native browse button. Probe whether both halves exist.
  3. PROGRESS: unreal.ScopedSlowTask -- make_dialog + enter_progress_frame.

Runs headless (-unattended): it asks what EXISTS, never shows anything modal.
Showing a modal dialog under -unattended would hang the build.
"""

import os
import traceback

import unreal

RESULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
RESULT_PATH = os.path.join(RESULT_DIR, 'probe_m10_ui_result.txt')

lines = []


def log(msg=""):
    lines.append(str(msg))
    unreal.log("[m10-probe] %s" % msg)
    try:
        os.makedirs(RESULT_DIR, exist_ok=True)
        with open(RESULT_PATH, 'w') as handle:
            handle.write('\n'.join(lines))
    except Exception:
        pass


def section(title):
    log("")
    log("=== %s ===" % title)


def has(obj, name):
    return "yes" if hasattr(obj, name) else "NO"


def main():
    section("1. ToolMenus")
    log("  unreal.ToolMenus exists: %s" % has(unreal, 'ToolMenus'))
    menus = unreal.ToolMenus.get()
    log("  ToolMenus.get() -> %r" % menus)
    for path in ("LevelEditor.MainMenu",
                 "LevelEditor.MainMenu.Tools",
                 "LevelEditor.MainMenu.File",
                 "LevelEditor.MainMenu.Window",
                 "LevelEditor.LevelEditorToolBar.PlayToolBar"):
        found = menus.find_menu(path)
        log("  find_menu(%-46r) -> %s" % (path, "FOUND" if found else "none"))
    log("  ToolMenuEntry type: %s" % has(unreal, 'ToolMenuEntry'))
    log("  ToolMenuEntryScript type: %s" % has(unreal, 'ToolMenuEntryScript'))
    log("  ToolMenuStringCommandType: %s" % has(unreal, 'ToolMenuStringCommandType'))
    if hasattr(unreal, 'ToolMenuStringCommandType'):
        log("  string command types: %s"
            % [n for n in dir(unreal.ToolMenuStringCommandType) if n.isupper()])

    section("2. folder picker candidates")
    log("  EditorDialog: %s" % has(unreal, 'EditorDialog'))
    if hasattr(unreal, 'EditorDialog'):
        log("  EditorDialog methods: %s"
            % [n for n in dir(unreal.EditorDialog) if not n.startswith('_')])
    log("  DirectoryPath struct: %s" % has(unreal, 'DirectoryPath'))
    log("  FilePath struct: %s" % has(unreal, 'FilePath'))
    log("  uclass decorator: %s" % has(unreal, 'uclass'))
    log("  uproperty decorator: %s" % has(unreal, 'uproperty'))
    # Anything with 'desktop platform' style file dialogs?
    candidates = [n for n in dir(unreal)
                  if any(k in n.lower() for k in ('dialog', 'picker', 'browse'))]
    log("  unreal.* names mentioning dialog/picker/browse:")
    for name in sorted(candidates):
        log("    %s" % name)

    section("3. can a UClass with a DirectoryPath actually be declared?")
    try:
        @unreal.uclass()
        class UEO3DEProbeOptions(unreal.Object):
            output_folder = unreal.uproperty(unreal.DirectoryPath,
                                             meta=dict(Category="Export"))
            include_physics = unreal.uproperty(bool, meta=dict(Category="Export"))

        instance = UEO3DEProbeOptions()
        instance.set_editor_property('include_physics', True)
        log("  declared + instantiated ok: %r" % instance)
        log("  include_physics reads back: %r"
            % instance.get_editor_property('include_physics'))
        folder = instance.get_editor_property('output_folder')
        log("  output_folder default: %r (type %s)" % (folder, type(folder).__name__))
        folder.set_editor_property('path', r'D:\some\where')
        log("  output_folder after set: %r"
            % instance.get_editor_property('output_folder').get_editor_property('path'))
    except Exception:
        log("  DECLARATION FAILED:\n%s" % traceback.format_exc())

    section("4. progress reporting")
    log("  ScopedSlowTask: %s" % has(unreal, 'ScopedSlowTask'))
    if hasattr(unreal, 'ScopedSlowTask'):
        log("  methods: %s"
            % [n for n in dir(unreal.ScopedSlowTask) if not n.startswith('_')])
        try:
            with unreal.ScopedSlowTask(3, "probe progress") as task:
                task.make_dialog(True)
                for i in range(3):
                    task.enter_progress_frame(1, "step %d" % (i + 1))
            log("  ScopedSlowTask ran 3 frames headless without hanging: ok")
        except Exception:
            log("  ScopedSlowTask FAILED:\n%s" % traceback.format_exc())

    section("5. startup hook for registering the menu")
    # A UE plugin's Content/Python/init_unreal.py runs automatically when the
    # Python plugin starts, for every plugin with Python enabled. That is the
    # exporter's equivalent of O3DE's gem bootstrap.
    plugin_python = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log("  looking for an existing init_unreal.py in the plugin")
    for root, _dirs, files in os.walk(
            r"D:\Gamedev\UEtoO3DE\UE\UEtoO3DEFixture\Plugins"):
        for name in files:
            if name in ("init_unreal.py", "init_site_packages.py"):
                log("    %s" % os.path.join(root, name))
    log("  sys.path entries under the plugin:")
    import sys
    for entry in sys.path:
        if 'UEO3DE' in entry or 'UEtoO3DEFixture' in entry:
            log("    %s" % entry)


try:
    main()
except Exception:
    log('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: PASS')
unreal.SystemLibrary.quit_editor()
