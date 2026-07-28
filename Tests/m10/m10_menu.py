"""
m10_menu.py — the gem bootstrap ran, and the Tools menu really has the entry.

The plan singled this out as verify-early ("a Python-only tool gem needs its
script registered via the gem's editor bootstrap to appear in the menu"), and
it has two independent failure modes that look identical from outside:

  * the gem is not registered/enabled, so `bootstrap.py` is never read;
  * the bootstrap ran but the Action Manager rejected the registration.

So this asserts on both halves separately. The second one is the interesting
part, because O3DE reports failure inside an `AZ::Outcome` rather than by
raising: `AddActionToMenu` on a menu that does not exist returns normally.
Every assertion below therefore has a CONTROL that must fail -- if the
controls pass, the test proves nothing and says so.

The action is deliberately NOT triggered: its payload opens a modal dialog,
which under -BatchMode would hang the run forever. Existence is proven by
attempting a DUPLICATE registration, which the Action Manager refuses.

Run: Tests/o3de/run_o3de_python.bat Tests/m10/m10_menu.py <result> <project>
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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm10_menu_result.txt')

lines = []
failures = []


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


def main():
    from ueimporter import menu

    log("=== 1. did the gem bootstrap run at all? ===")
    ran = os.environ.get("UEO3DE_BOOTSTRAP_RAN")
    menu_state = os.environ.get("UEO3DE_BOOTSTRAP_MENU")
    error = os.environ.get("UEO3DE_BOOTSTRAP_ERROR")
    log("  UEO3DE_BOOTSTRAP_RAN   = %r" % ran)
    log("  UEO3DE_BOOTSTRAP_MENU  = %r" % menu_state)
    log("  UEO3DE_BOOTSTRAP_ERROR = %r" % error)
    check(ran == "1",
          "the gem bootstrap never ran -- is UEImporter registered AND enabled "
          "in this project? (o3de register -gp <gem> -espp <project>; "
          "o3de enable-gem -gn UEImporter -pp <project>)")
    check(not error, "the bootstrap reported an error: %s" % error)
    check(menu_state == "ok",
          "the bootstrap ran but did not install the menu (state %r)" % menu_state)
    check(menu.is_installed(),
          "menu.is_installed() is False -- the module the test imported is not "
          "the one the bootstrap installed from, or installation failed")

    log("")
    log("=== 2. the action exists (proven by a refused duplicate) ===")
    import azlmbr.action as action

    properties = action.ActionProperties()
    properties.name = "duplicate probe"
    properties.description = "test"
    properties.category = "UEtoO3DE"
    ok, detail = menu._call(
        action.ActionManagerPythonRequestBus, 'RegisterAction',
        menu.CONTEXT, menu.ACTION_IMPORT, properties, lambda: None)
    log("  duplicate RegisterAction -> ok=%s %r" % (ok, detail))
    check(ok is False and "twice" in detail,
          "registering %r a second time was NOT refused, so the first "
          "registration cannot be shown to have happened"
          % menu.ACTION_IMPORT)

    log("")
    log("=== 2b. CONTROL: a fresh identifier must register cleanly ===")
    # Without this, "duplicate is refused" could equally mean "every
    # registration is refused", which would prove the opposite of the above.
    ok, detail = menu._call(
        action.ActionManagerPythonRequestBus, 'RegisterAction',
        menu.CONTEXT, "ueimporter.test.control", properties, lambda: None)
    log("  fresh RegisterAction -> ok=%s %r" % (ok, detail))
    check(ok is True,
          "a brand-new action could not be registered either (%s) -- the "
          "duplicate check above therefore proves nothing" % detail)

    log("")
    log("=== 3. the action is bound to the TOOLS menu ===")
    # Re-adding is the readback. `GetSortKeyOfActionInMenu` returns a bare None
    # for present and absent alike (measured in probe_m10_menu3), so the only
    # signal O3DE gives is the Menu Manager's refusal to add a duplicate --
    # which is a POSITIVE result here: it can only be raised by a menu that
    # already contains the action the bootstrap put there.
    ok, detail = menu._call(action.MenuManagerPythonRequestBus, 'AddActionToMenu',
                            menu.MENU_TOOLS, menu.ACTION_IMPORT, menu.SORT_KEY + 1)
    log("  AddActionToMenu(tools, real action) -> ok=%s %r" % (ok, detail))
    check(ok is False and "already contains" in detail,
          "the Tools menu does not already contain the importer action, so "
          "the bootstrap did not bind it (got ok=%s %r)" % (ok, detail))

    log("")
    log("=== 3b. CONTROL: a DIFFERENT action must add cleanly to the same menu ===")
    # Without this, "already contains" could not be told apart from a Tools
    # menu that refuses everything.
    ok_fresh, detail_fresh = menu._call(
        action.MenuManagerPythonRequestBus, 'AddActionToMenu',
        menu.MENU_TOOLS, "ueimporter.test.control", menu.SORT_KEY + 2)
    log("  fresh action -> ok=%s %r" % (ok_fresh, detail_fresh))
    check(ok_fresh is True,
          "the Tools menu refused a brand-new action too (%s), so the "
          "'already contains' result above proves nothing" % detail_fresh)

    log("")
    log("=== 3c. CONTROLS: the same call must FAIL when it should ===")
    ok_menu, detail_menu = menu._call(
        action.MenuManagerPythonRequestBus, 'AddActionToMenu',
        "o3de.menu.editor.no.such.menu", menu.ACTION_IMPORT, 1)
    log("  bogus MENU   -> ok=%s %r" % (ok_menu, detail_menu))
    check(ok_menu is False,
          "adding to a nonexistent menu reported success -- the Outcome is "
          "not being unwrapped, so section 3 is meaningless")
    ok_action, detail_action = menu._call(
        action.MenuManagerPythonRequestBus, 'AddActionToMenu',
        menu.MENU_TOOLS, "ueimporter.no.such.action", 1)
    log("  bogus ACTION -> ok=%s %r" % (ok_action, detail_action))
    check(ok_action is False,
          "adding a nonexistent action reported success -- section 3 is "
          "meaningless")

    log("")
    log("=== 4. install() is idempotent ===")
    status = menu.install(log=lambda m: None)
    log("  second install() -> %r" % status)
    check(status.get("action") and status.get("menu"),
          "a second install() reported failure; the bootstrap and a manual "
          "call from the Python console are both legitimate entry points")


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
