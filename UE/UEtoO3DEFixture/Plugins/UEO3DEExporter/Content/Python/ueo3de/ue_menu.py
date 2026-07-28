"""
ue_menu.py — "Export to O3DE" in UE's Tools menu (plan M10).

Measured first, in `Tests/ue/probe_m10_ui.py`, because two of the three pieces
had no obvious API and could have forced a C++ Slate widget instead:

  * MENU -- `unreal.ToolMenus` reaches `LevelEditor.MainMenu.Tools`, and a
    `ToolMenuEntry` accepts a PYTHON string command. No C++ needed.
  * FOLDER PICKER -- UE's Python API has no `pick_directory`. What it does
    have is `EditorDialog.show_object_details_view`, and a UClass declared in
    Python with a `DirectoryPath` property renders with the engine's own
    browse button. So the "options dialog" and the "folder picker" are the
    same modal, which is also fewer clicks than a separate picker.
  * PROGRESS -- `unreal.ScopedSlowTask` with `make_dialog`, confirmed to run
    headless without hanging (it degrades to nothing under -unattended).

The export itself is `export_api.export_level`, the same call CI makes.
"""

import os
import traceback

import unreal

MENU_PATH = "LevelEditor.MainMenu.Tools"
SECTION = "UEtoO3DE"
ENTRY_NAME = "UEO3DEExportLevel"

# Set by install(). `Tests/m10/m10_ue_menu.py` reads it to tell "init_unreal.py
# ran and the entry went in" from "init_unreal.py was never read" -- which look
# identical from outside, and have completely different fixes.
INSTALLED = False

_DEFAULT_OUTPUT = os.path.join(os.path.expanduser("~"), "UEtoO3DE_Exports")


@unreal.uclass()
class UEO3DEExportOptions(unreal.Object):
    """The options modal. A `DirectoryPath` property is what gives us a native
    folder browser without writing one."""

    output_folder = unreal.uproperty(
        unreal.DirectoryPath,
        meta=dict(Category="Export",
                  ToolTip="Folder to write manifest.json and Assets/ into"))
    subfolder_per_level = unreal.uproperty(
        bool,
        meta=dict(Category="Export",
                  ToolTip="Write into <folder>/<LevelName>/ instead of "
                          "directly into the chosen folder"))


def _current_map_path():
    subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    world = subsystem.get_current_level().get_outer()
    path = world.get_path_name()
    # '/Game/Maps/Fixture_01.Fixture_01:PersistentLevel' -> '/Game/Maps/Fixture_01'
    return path.split(":")[0].split(".")[0]


def export_current_level():
    """The menu item's payload: ask, export with a progress bar, report."""
    from . import export_api

    try:
        map_path = _current_map_path()
    except Exception:
        unreal.log_error("[UEO3DE] no level is open")
        unreal.EditorDialog.show_message(
            "Export to O3DE", "Open a level first.", unreal.AppMsgType.OK)
        return

    level_name = map_path.rsplit("/", 1)[-1]
    options = UEO3DEExportOptions()
    options.get_editor_property("output_folder").set_editor_property(
        "path", _DEFAULT_OUTPUT)
    options.set_editor_property("subfolder_per_level", True)

    if not unreal.EditorDialog.show_object_details_view(
            "Export %s to O3DE" % level_name, options):
        unreal.log("[UEO3DE] export cancelled")
        return

    folder = options.get_editor_property("output_folder").get_editor_property("path")
    if not folder:
        unreal.EditorDialog.show_message(
            "Export to O3DE", "No output folder was chosen.", unreal.AppMsgType.OK)
        return
    if options.get_editor_property("subfolder_per_level"):
        folder = os.path.join(folder, level_name)

    total = len(export_api.STEPS)
    with unreal.ScopedSlowTask(total, "Exporting %s to O3DE" % level_name) as task:
        task.make_dialog(True)

        def progress(index, _total, label):
            task.enter_progress_frame(1, label)

        try:
            result = export_api.export_level(
                map_path, folder,
                log=lambda message: unreal.log("[UEO3DE] " + str(message)),
                progress=progress,
                # Never reload: this exports the level the user is standing
                # in, and reloading it would throw away their unsaved edits
                # and then export the older version from disk.
                load=False)
        except Exception as exc:
            unreal.log_error("[UEO3DE] export failed:\n" + traceback.format_exc())
            unreal.EditorDialog.show_message(
                "Export failed", "%s: %s" % (type(exc).__name__, exc),
                unreal.AppMsgType.OK)
            return

    unreal.log("[UEO3DE] " + export_api.summary_text(result).replace("\n", " | "))
    unreal.EditorDialog.show_message(
        "Export complete", export_api.summary_text(result), unreal.AppMsgType.OK)
    return result


def install(log=None):
    """Add the entry to Tools. Idempotent, and never raises.

    This runs from `init_unreal.py` during editor startup, so a failure here
    must cost a menu item and nothing else.
    """
    global INSTALLED

    def emit(message):
        if log is not None:
            log(message)
        else:
            unreal.log("[UEO3DE] " + str(message))

    try:
        menus = unreal.ToolMenus.get()
        menu = menus.find_menu(MENU_PATH)
        if menu is None:
            emit("could not find %s; no menu entry added" % MENU_PATH)
            return False

        entry = unreal.ToolMenuEntry(
            name=ENTRY_NAME,
            type=unreal.MultiBlockType.MENU_ENTRY,
            insert_position=unreal.ToolMenuInsert(
                "", unreal.ToolMenuInsertType.DEFAULT))
        entry.set_label("Export Level to O3DE...")
        entry.set_tool_tip("Export this level to a UEtoO3DE interchange folder")
        entry.set_string_command(
            unreal.ToolMenuStringCommandType.PYTHON, "",
            string="from ueo3de import ue_menu; ue_menu.export_current_level()")

        menu.add_section(SECTION, unreal.Text("UEtoO3DE"))
        menu.add_menu_entry(SECTION, entry)
        menus.refresh_all_widgets()
        INSTALLED = True
        emit("Tools -> Export Level to O3DE... registered")
        return True
    except Exception:
        unreal.log_error("[UEO3DE] menu registration failed:\n"
                         + traceback.format_exc())
        return False
