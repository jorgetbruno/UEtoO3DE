"""
probe_m10_menu.py -- what does O3DE 26.05 expose to Python for MENUS and Qt?

M10 wants "Tools -> Import UE Manifest" from a gem that contains no C++ at
all. The plan flags this as verify-early for good reason: the menu could be
C++-only, in which case the milestone's UX half needs a different shape (a
script that the user runs from the Python console, or a real C++ tool module).
Nothing gets designed until this prints facts.

Questions, in the order they can kill the design:
  1. is there ANY Python-reachable menu/action registry?  (azlmbr.action's
     ActionManager/MenuManager buses, or the legacy EditorMenuRequestBus)
  2. what are the exact bus + method names, and do they take an identifier
     string like 'o3de.menu.editor.tools'?
  3. is PySide2 importable, and can we reach the editor main window to parent
     a dialog to it?
  4. does this editor even have QtForPython loaded?

Writes findings incrementally: an API probe that dies mid-way must still
leave behind everything it learned before it died.
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m10_menu_result.txt')

lines = []


def log(msg=""):
    lines.append(str(msg))
    print(msg)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
        with open(RESULT_PATH, 'w') as handle:
            handle.write('\n'.join(lines))
    except Exception:
        pass


def section(title):
    log("")
    log("=== %s ===" % title)


def probe_module(name):
    try:
        module = __import__(name, fromlist=['*'])
    except Exception as exc:
        log("  %-34s IMPORT FAILED: %s" % (name, exc))
        return None
    log("  %-34s ok" % name)
    return module


def main():
    section("1. azlmbr modules relevant to menus/actions")
    import azlmbr
    action = probe_module('azlmbr.action')
    editor = probe_module('azlmbr.editor')
    probe_module('azlmbr.qt')

    if action is not None:
        section("2. azlmbr.action contents")
        for name in sorted(dir(action)):
            if name.startswith('_'):
                continue
            log("  %s" % name)

    section("3. bus names containing menu/action/tool")
    # The bus registry is the only reliable inventory: dir() on the module
    # shows types, not which of them are addressable buses.
    for module_name in ('azlmbr.action', 'azlmbr.editor', 'azlmbr.bus'):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        hits = [n for n in dir(module)
                if any(k in n.lower() for k in ('menu', 'action', 'toolbar', 'hotkey'))]
        log("  %-20s %s" % (module_name, hits if hits else '(none)'))

    section("4. can a Broadcast reach a menu bus at all?")
    import azlmbr.bus as bus
    candidates = [
        ('MenuManagerPythonRequestBus', 'AddActionToMenu'),
        ('ActionManagerPythonRequestBus', 'RegisterAction'),
        ('MenuManagerPythonRequestBus', 'GetSortKeyOfActionInMenu'),
        ('EditorMenuRequestBus', 'AddMenuAction'),
    ]
    for bus_name, method in candidates:
        holder = None
        for module in (action, editor):
            if module is not None and hasattr(module, bus_name):
                holder = module
                break
        if holder is None:
            log("  %-32s bus type NOT exposed" % bus_name)
            continue
        # Call with deliberately wrong args: a TypeError/arg-count error proves
        # the method EXISTS and is callable; "no handler" proves the opposite.
        try:
            result = bus.Broadcast(getattr(holder, bus_name), method)
            log("  %-32s .%s -> %r" % (bus_name, method, result))
        except Exception as exc:
            log("  %-32s .%s raised %s: %s"
                % (bus_name, method, type(exc).__name__, str(exc)[:160]))

    section("5. PySide2 / Qt reachability")
    qt_widgets = None
    try:
        from PySide2 import QtWidgets, QtCore
        qt_widgets = QtWidgets
        log("  PySide2 import ok (Qt %s)" % QtCore.qVersion())
        app = QtWidgets.QApplication.instance()
        log("  QApplication.instance() -> %r" % app)
    except Exception as exc:
        log("  PySide2 IMPORT FAILED: %s" % exc)
    try:
        import az_qt_helpers
        log("  az_qt_helpers ok: %s"
            % [n for n in dir(az_qt_helpers) if not n.startswith('_')])
    except Exception as exc:
        log("  az_qt_helpers unavailable: %s" % exc)

    if qt_widgets is not None:
        section("6. editor main window handle")
        try:
            import azlmbr.editor as ed
            import azlmbr.bus as b
            window_id = b.Broadcast(ed.EditorWindowRequestBus, 'GetEditorMainWindow')
            log("  GetEditorMainWindow -> %r" % (window_id,))
        except Exception as exc:
            log("  GetEditorMainWindow failed: %s" % exc)

    section("7. does THIS editor have the importer gem's scripts on sys.path?")
    # If the gem were registered+enabled, EditorPythonBindings would have put
    # its Editor/Scripts on sys.path and run its bootstrap.py. Every suite so
    # far inserts that path by hand, which strongly suggests it is not enabled.
    hits = [p for p in sys.path if 'UEImporter' in p]
    log("  sys.path entries mentioning UEImporter: %s" % (hits or '(none)'))
    log("  UEO3DE_BOOTSTRAP marker in os.environ: %r"
        % os.environ.get('UEO3DE_BOOTSTRAP_RAN'))
    try:
        import ueimporter_bootstrap_marker  # noqa: F401
        log("  bootstrap marker module: IMPORTED (bootstrap ran)")
    except Exception:
        log("  bootstrap marker module: absent")


try:
    main()
except Exception:
    log('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: PASS')

import azlmbr.legacy.general as _general
_general.exit_no_prompt()
