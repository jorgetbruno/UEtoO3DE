"""
probe_m10_menu3.py -- unwrap the Outcome, and make the check able to FAIL.

Probe 2 got calls through and proved the important thing: an action registered
from an ordinary --runpython script can be TRIGGERED and runs its Python
handler. But every call returned an opaque
`Outcome<void, AZStd::basic_string...> via PythonProxyObject`, and every
readback returned None -- including `IsActionRegistered`, which cannot
literally be None-for-registered. So probe 2 cannot distinguish

    AddActionToMenu('o3de.menu.editor.tools')   -> worked
    AddActionToMenu('o3de.menu.editor.help')    -> worked
    AddActionToMenu('total.nonsense')           -> would ALSO have "worked"

which makes the menu half of it worthless as evidence. This probe fixes that:

  1. unwrap the Outcome (PythonProxyObject exposes .invoke) so success and
     failure are distinguishable, and print the error string on failure;
  2. run a CONTROL -- a deliberately bogus menu identifier. If that reports
     success too, the readback is a no-op and the real Tools identifier
     cannot be established this way at all;
  3. only then trust the identifier that succeeds.
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m10_menu3_result.txt')

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


def unwrap(value):
    """Turn an AZ::Outcome proxy into (ok, payload_or_error)."""
    if value is None:
        return None, None
    for method in ('IsSuccess', 'is_success'):
        try:
            ok = value.invoke(method)
        except Exception:
            continue
        if ok is None:
            continue
        if ok:
            for getter in ('GetValue', 'get_value'):
                try:
                    return True, value.invoke(getter)
                except Exception:
                    pass
            return True, None
        for getter in ('GetError', 'get_error'):
            try:
                return False, value.invoke(getter)
            except Exception:
                pass
        return False, None
    return None, value


def call(bus_holder, event, *args):
    import azlmbr.bus as bus
    try:
        raw = bus_holder(bus.Broadcast, event, *args)
    except Exception as exc:
        return 'raised', "%s: %s" % (type(exc).__name__, str(exc)[:200])
    ok, payload = unwrap(raw)
    if ok is None:
        return 'plain', raw
    return ('ok' if ok else 'FAILED'), payload


def main():
    import azlmbr.action as action

    section("0. what does a PythonProxyObject Outcome expose?")
    properties = action.ActionProperties()
    properties.name = "UE Importer (probe)"
    properties.description = "probe only"
    properties.category = "UEtoO3DE"
    import azlmbr.bus as bus
    raw = action.ActionManagerPythonRequestBus(
        bus.Broadcast, 'RegisterAction', "o3de.context.editor.mainwindow",
        "ueimporter.probe.unwrap", properties, lambda: None)
    log("  raw type: %s" % type(raw).__name__)
    log("  dir(raw): %s" % [n for n in dir(raw) if not n.startswith('_')])
    for method in ('IsSuccess', 'GetError', 'GetValue', 'typename'):
        try:
            log("  raw.invoke(%-10r) -> %r" % (method, raw.invoke(method)))
        except Exception as exc:
            log("  raw.invoke(%-10r) raised %s: %s"
                % (method, type(exc).__name__, str(exc)[:120]))
    try:
        log("  raw.to_json() -> %r" % raw.to_json())
    except Exception as exc:
        log("  raw.to_json() raised %s" % exc)

    section("1. register the real action (unwrapped)")
    identifier = "ueimporter.probe.action3"
    fired = []
    status, payload = call(action.ActionManagerPythonRequestBus, 'RegisterAction',
                           "o3de.context.editor.mainwindow", identifier,
                           properties, lambda: fired.append(1))
    log("  RegisterAction -> %s %r" % (status, payload))

    section("2. CONTROL: registering the SAME identifier twice must fail")
    # If a duplicate registration reports success, the status is meaningless
    # and nothing below can be believed.
    status, payload = call(action.ActionManagerPythonRequestBus, 'RegisterAction',
                           "o3de.context.editor.mainwindow", identifier,
                           properties, lambda: None)
    log("  RegisterAction (duplicate) -> %s %r" % (status, payload))
    log("  ^ this MUST say FAILED for the rest of this probe to mean anything")

    section("3. CONTROL: a bogus menu identifier must fail")
    status, payload = call(action.MenuManagerPythonRequestBus, 'AddActionToMenu',
                           "o3de.menu.editor.total.nonsense", identifier, 100)
    log("  AddActionToMenu(bogus menu)   -> %s %r" % (status, payload))
    status, payload = call(action.MenuManagerPythonRequestBus, 'AddActionToMenu',
                           "o3de.menu.editor.tools", "ueimporter.no.such.action", 100)
    log("  AddActionToMenu(bogus action) -> %s %r" % (status, payload))

    section("4. the real thing: which menu identifier accepts the action?")
    for menu_identifier in ("o3de.menu.editor.tools",
                            "o3de.menu.editor.tools.tools",
                            "o3de.menu.editor.file",
                            "o3de.menu.editor.edit",
                            "o3de.menu.editor.view",
                            "o3de.menu.editor.help"):
        status, payload = call(action.MenuManagerPythonRequestBus, 'AddActionToMenu',
                               menu_identifier, identifier, 4200)
        log("  %-34s -> %-6s %r" % (menu_identifier, status, payload))

    section("5. readback of a menu that DID accept it")
    for menu_identifier in ("o3de.menu.editor.tools", "o3de.menu.editor.total.nonsense"):
        status, payload = call(action.MenuManagerPythonRequestBus,
                               'GetSortKeyOfActionInMenu', menu_identifier, identifier)
        log("  GetSortKeyOfActionInMenu(%-34r) -> %s %r"
            % (menu_identifier, status, payload))

    section("6. does triggering still run Python?")
    status, payload = call(action.ActionManagerPythonRequestBus,
                           'TriggerAction', identifier)
    log("  TriggerAction -> %s %r ; handler fired %d time(s)"
        % (status, payload, len(fired)))

    section("7. submenu: can we make our own 'UEtoO3DE' menu under Tools?")
    menu_properties = action.MenuProperties()
    menu_properties.name = "UEtoO3DE (probe)"
    status, payload = call(action.MenuManagerPythonRequestBus, 'RegisterMenu',
                           "ueimporter.probe.menu", menu_properties)
    log("  RegisterMenu -> %s %r" % (status, payload))
    status, payload = call(action.MenuManagerPythonRequestBus, 'AddSubMenuToMenu',
                           "o3de.menu.editor.tools", "ueimporter.probe.menu", 4300)
    log("  AddSubMenuToMenu -> %s %r" % (status, payload))


try:
    main()
except Exception:
    log('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: PASS')

import azlmbr.legacy.general as _general
_general.exit_no_prompt()
