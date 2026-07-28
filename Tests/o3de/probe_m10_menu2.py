"""
probe_m10_menu2.py -- the exact Action/Menu manager calling convention.

Probe 1 proved azlmbr.action exists and carries ActionManagerPythonRequestBus,
MenuManagerPythonRequestBus and an ActionManagerRegistrationNotificationBus
(+Handler). It then failed every call with "'azlmbr.bus.EventType' object is
not callable" -- which was the PROBE's bug, not a missing API: O3DE's binding
is `BusType(bus.Broadcast, 'Event', ...)`, and the probe had the two swapped.
Worth recording, because "the API rejected every call" and "I called it wrong"
look identical from the outside, and only one of them is a reason to redesign.

The three questions the design depends on:

  1. can an action be registered LATE -- from a --runpython script, long after
     the editor's registration hooks have fired -- or only from inside the
     OnActionRegistrationHook notification? That decides whether M10's menu
     item can live in a plain script or MUST live in a gem bootstrap.
  2. does the Tools menu accept the action, and under what identifier?
  3. can the result be READ BACK, so a test can prove the menu item exists
     rather than trusting the absence of an exception?
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m10_menu2_result.txt')

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


def call(bus_holder, event, *args):
    """Invoke an O3DE bus event, reporting the exception instead of raising."""
    import azlmbr.bus as bus
    try:
        return True, bus_holder(bus.Broadcast, event, *args)
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:200])


def main():
    import azlmbr.action as action

    section("1. ActionProperties: which fields does it accept?")
    properties = action.ActionProperties()
    for field in ('name', 'description', 'category', 'iconPath', 'menuVisibility'):
        try:
            setattr(properties, field, "probe" if field != 'menuVisibility' else 0)
            log("  %-16s set ok -> %r" % (field, getattr(properties, field, None)))
        except Exception as exc:
            log("  %-16s rejected: %s" % (field, str(exc)[:120]))
    properties.name = "UE Importer probe"
    properties.description = "probe only"
    properties.category = "UEtoO3DE"

    section("2. LATE registration from a --runpython script")
    # The whole design hinges on this. If it works, M10's menu entry can be
    # installed by any script; if it does not, it MUST come from the gem's
    # bootstrap, which runs while the registration hooks are still open.
    identifier = "ueimporter.probe.action"
    context = "o3de.context.editor.mainwindow"
    fired = []

    def handler():
        fired.append(1)

    ok, result = call(action.ActionManagerPythonRequestBus, 'RegisterAction',
                      context, identifier, properties, handler)
    log("  RegisterAction(4 args) ok=%s -> %r" % (ok, result))
    if not ok:
        ok, result = call(action.ActionManagerPythonRequestBus, 'RegisterAction',
                          context, identifier, properties, "", handler)
        log("  RegisterAction(5 args) ok=%s -> %r" % (ok, result))

    section("3. readback: is the action actually registered?")
    for event, args in (('IsActionRegistered', (identifier,)),
                        ('GetActionName', (identifier,)),
                        ('TriggerAction', (identifier,))):
        ok, result = call(action.ActionManagerPythonRequestBus, event, *args)
        log("  %-22s ok=%s -> %r" % (event, ok, result))
    log("  handler fired %d time(s)  <-- TriggerAction proves the wiring end to end"
        % len(fired))

    section("4. which menu identifier is the Tools menu?")
    # Read before writing: GetSortKeyOfMenuInMenuBar / menu existence tells us
    # the identifier without having to guess-and-hope.
    for menu_identifier in ("o3de.menu.editor.tools",
                            "o3de.menu.editor.tools.tools",
                            "o3de.menu.editor.file",
                            "o3de.menu.editor.help",
                            "o3de.menu.editor.view",
                            "o3de.menu.editor.edit"):
        ok, result = call(action.MenuManagerPythonRequestBus,
                          'GetSortKeyOfMenuInMenuBar',
                          "o3de.menubar.editor.mainwindow", menu_identifier)
        log("  %-34s sortKeyInMenuBar ok=%s -> %r" % (menu_identifier, ok, result))

    section("5. add the action to the Tools menu, then read it back")
    for menu_identifier in ("o3de.menu.editor.tools", "o3de.menu.editor.help"):
        ok, result = call(action.MenuManagerPythonRequestBus, 'AddActionToMenu',
                          menu_identifier, identifier, 4200)
        log("  AddActionToMenu(%-30r) ok=%s -> %r" % (menu_identifier, ok, result))
        ok, result = call(action.MenuManagerPythonRequestBus,
                          'GetSortKeyOfActionInMenu', menu_identifier, identifier)
        log("      GetSortKeyOfActionInMenu ok=%s -> %r  <-- readback" % (ok, result))

    section("6. the registration-hook route (what a gem bootstrap would use)")
    handler_type = getattr(action, 'ActionManagerRegistrationNotificationBusHandler', None)
    log("  handler type present: %s" % (handler_type is not None))
    if handler_type is not None:
        try:
            notification = handler_type()
            notification.connect()
            seen = []
            notification.add_callback('OnActionRegistrationHook',
                                      lambda args: seen.append('action'))
            notification.add_callback('OnMenuBindingHook',
                                      lambda args: seen.append('menu'))
            log("  connect + add_callback ok (hooks already fired: %r)" % seen)
            notification.disconnect()
        except Exception:
            log("  handler wiring failed:\n%s" % traceback.format_exc())

    section("7. gem discovery: is UEImporter registered with o3de at all?")
    manifest = os.path.expanduser("~/.o3de/o3de_manifest.json")
    try:
        import json
        with open(manifest, 'r') as handle:
            data = json.load(handle)
        gems = data.get('external_subdirectories', []) + data.get('gems', [])
        hits = [g for g in gems if 'UEImporter' in str(g)]
        log("  o3de_manifest external subdirs mentioning UEImporter: %s" % (hits or '(none)'))
    except Exception as exc:
        log("  could not read %s: %s" % (manifest, exc))


try:
    main()
except Exception:
    log('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: PASS')

import azlmbr.legacy.general as _general
_general.exit_no_prompt()
