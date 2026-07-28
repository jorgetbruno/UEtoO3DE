"""
menu.py — the "Tools -> UE Importer" entry (plan M10).

O3DE 26.05 exposes its Action Manager to Python through `azlmbr.action`, and
`Tests/o3de/probe_m10_menu3.py` measured the whole contract before any of this
was written:

  * `BusType(bus.Broadcast, 'Event', ...)` -- the binding takes the address
    type FIRST. (Probe 2 had it the other way round and read the resulting
    "EventType object is not callable" as a missing API. It was not.)
  * every call returns an `AZ::Outcome` proxy whose success is only visible
    through `.invoke('IsSuccess')` / `.invoke('GetError')`. A bare call that
    does not throw proves NOTHING -- adding an action to a nonexistent menu
    "succeeds" at the Python level and fails silently inside the Outcome.
  * the Tools menu is `o3de.menu.editor.tools`. `o3de.menu.editor.tools.tools`
    is not a menu, and the difference is invisible without the unwrap above.
  * registration works LATE, from an ordinary script, not only from inside
    `OnActionRegistrationHook` -- but at gem-bootstrap time the manager is not
    up yet, so both routes are needed and this module tries them in order.

`install()` is idempotent: a second call finds the action already registered
and reports that instead of raising, because the bootstrap and a manual call
from the Python console are both legitimate ways in.
"""

CONTEXT = "o3de.context.editor.mainwindow"
MENU_TOOLS = "o3de.menu.editor.tools"
ACTION_IMPORT = "ueimporter.action.import_manifest"
SORT_KEY = 5000

# The registration handler must outlive install(): a garbage-collected handler
# disconnects itself, and the hooks then fire into nothing.
_handler = None
_installed = False

# Where the install got to, as an environment marker. It lives here rather
# than in bootstrap.py because the hook route finishes LONG after the bootstrap
# returns -- writing the marker from the bootstrap recorded "hook" forever,
# even on runs where the hook then completed successfully a moment later.
MARKER_ENV = "UEO3DE_BOOTSTRAP_MENU"
ERROR_ENV = "UEO3DE_BOOTSTRAP_ERROR"


def _mark(state, error=None):
    import os
    os.environ[MARKER_ENV] = str(state)
    if error:
        os.environ[ERROR_ENV] = str(error)
    elif state == "ok":
        os.environ.pop(ERROR_ENV, None)


def unwrap(value):
    """`AZ::Outcome` proxy -> `(ok, error)`. `ok` is None if not an Outcome.

    Everything in this module routes through here. An unwrapped call is a call
    whose failure is invisible.
    """
    if value is None:
        return None, ""
    try:
        ok = value.invoke('IsSuccess')
    except Exception:
        return None, ""
    if ok is None:
        return None, ""
    if ok:
        return True, ""
    try:
        return False, str(value.invoke('GetError') or "")
    except Exception:
        return False, ""


def _call(bus_holder, event, *args):
    """Invoke a bus event and return `(ok, detail)` with the Outcome unwrapped.

    `ok` is False only for a *reported* failure; a call that returns no Outcome
    at all is reported as True with an empty detail, because that is what the
    query events do and treating it as failure would be a lie in the other
    direction.
    """
    import azlmbr.bus as bus
    try:
        raw = bus_holder(bus.Broadcast, event, *args)
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    ok, error = unwrap(raw)
    if ok is None:
        return True, ""
    return ok, error


def _on_import_clicked():
    """The menu item's payload. Imported lazily: the editor must start even if
    PySide2 or the dialog module is broken, and a startup-time import of the
    whole importer would make every editor launch pay for it."""
    try:
        from . import dialog
        dialog.run_import_dialog()
    except Exception:
        import traceback
        print("[UEImporter] import dialog failed:\n" + traceback.format_exc())


def _register_action(log):
    import azlmbr.action as action

    properties = action.ActionProperties()
    properties.name = "Import UE Manifest..."
    properties.description = ("Import a level manifest exported by the "
                              "UEO3DEExporter plugin for Unreal Engine")
    properties.category = "UEtoO3DE"
    ok, detail = _call(action.ActionManagerPythonRequestBus, 'RegisterAction',
                       CONTEXT, ACTION_IMPORT, properties, _on_import_clicked)
    if not ok and "twice" in detail:
        log("action already registered; leaving it alone")
        return True, "already registered"
    return ok, detail


def _bind_menu(log):
    import azlmbr.action as action
    return _call(action.MenuManagerPythonRequestBus, 'AddActionToMenu',
                 MENU_TOOLS, ACTION_IMPORT, SORT_KEY)


def install(log=None):
    """Put "Import UE Manifest..." under Tools. Returns a status dict.

    Never raises: this runs from the gem bootstrap, and an exception there
    would take the whole editor's Python startup with it -- including every
    acceptance suite in this repo, which is a spectacular way to turn a
    cosmetic UX feature into a total outage.
    """
    global _handler, _installed

    def emit(message):
        text = "[UEImporter] " + str(message)
        if log is not None:
            log(text)
        else:
            print(text)

    status = {"action": False, "menu": False, "route": "direct", "error": ""}
    if _installed:
        status["route"] = "already-installed"
        status["action"] = status["menu"] = True
        return status

    try:
        import azlmbr.action as action  # noqa: F401
    except Exception as exc:
        status["error"] = "azlmbr.action unavailable: %s" % exc
        emit(status["error"])
        return status

    try:
        ok, detail = _register_action(emit)
        status["action"] = bool(ok)
        if not ok:
            status["error"] = detail
        if ok:
            ok_menu, detail_menu = _bind_menu(emit)
            status["menu"] = bool(ok_menu)
            if not ok_menu:
                status["error"] = detail_menu
    except Exception as exc:
        status["error"] = "%s: %s" % (type(exc).__name__, exc)

    if status["action"] and status["menu"]:
        _installed = True
        _mark("ok")
        emit("Tools -> Import UE Manifest... registered")
        return status

    # Direct registration failed -- the expected case at bootstrap time, when
    # the Action Manager has not stood up its contexts yet. Fall back to the
    # registration hooks, which fire later in startup.
    status["route"] = "hook"
    _mark("hook-pending", status["error"])
    emit("direct registration unavailable (%s); waiting for the registration "
         "hooks" % (status["error"] or "no detail"))
    try:
        import azlmbr.action as action

        handler = action.ActionManagerRegistrationNotificationBusHandler()
        handler.connect()

        def on_action_hook(_args):
            ok, detail = _register_action(emit)
            if not ok:
                _mark("hook-action-failed", detail)
                emit("hook: RegisterAction failed: %s" % detail)

        def on_menu_hook(_args):
            global _installed
            ok, detail = _bind_menu(emit)
            if ok:
                _installed = True
                _mark("ok")
                emit("Tools -> Import UE Manifest... registered (hook)")
            else:
                _mark("hook-menu-failed", detail)
                emit("hook: AddActionToMenu failed: %s" % detail)

        handler.add_callback('OnActionRegistrationHook', on_action_hook)
        handler.add_callback('OnMenuBindingHook', on_menu_hook)
        _handler = handler
        status["error"] = ""
    except Exception as exc:
        status["error"] = "hook wiring failed: %s: %s" % (type(exc).__name__, exc)
        _mark("hook-wiring-failed", status["error"])
        emit(status["error"])
    return status


def is_installed():
    return _installed
