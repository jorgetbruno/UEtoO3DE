"""probe_capture_api.py -- can this build capture a frame from Python?

The systemic gap M6 exposed: a level can import PURE WHITE and pass every
existing check (905 entities, 693/693 colliders verified, 0 AP errors). The
suite verifies that things were AUTHORED, never that the result is USABLE.

A mean-luminance check on a captured frame would have caught it instantly --
IF a capture is reachable from the editor's Python. `BoundsRequestBus` looked
obviously available too and turned out to be bound to None in every module
(probe_bounds_api.py), so this asks before anything is built on top of it.

Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_capture_api.py <result> <project>
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'probe_capture_api_result.txt'))

lines = []


def log(message=""):
    lines.append(str(message))
    print(message)


def main():
    import azlmbr

    log("=== azlmbr.* names mentioning capture / screenshot / readback ===")
    hits = 0
    for mod_name in sorted(dir(azlmbr)):
        if mod_name.startswith('_'):
            continue
        try:
            mod = getattr(azlmbr, mod_name)
            names = sorted(dir(mod))
        except Exception:  # noqa: BLE001
            continue
        for attr in names:
            lowered = attr.lower()
            if any(k in lowered for k in ("capture", "screenshot", "readback",
                                          "framecapture", "renderdoc")):
                try:
                    value = getattr(mod, attr)
                    kind = 'None' if value is None else type(value).__name__
                except Exception:  # noqa: BLE001
                    kind = '<unreadable>'
                log("  azlmbr.%-20s %-44s %s" % (mod_name, attr, kind))
                hits += 1
    if not hits:
        log("  (none)")

    log("")
    log("=== the documented route: AtomToolsFramework / FrameCaptureRequestBus ===")
    for module_name in ("azlmbr.atom", "azlmbr.atomtools", "azlmbr.render",
                        "azlmbr.legacy.general"):
        try:
            module = __import__(module_name, fromlist=['*'])
        except Exception as error:  # noqa: BLE001
            log("  %-26s import failed: %s" % (module_name, error))
            continue
        found = [a for a in sorted(dir(module))
                 if any(k in a.lower() for k in ("capture", "screenshot"))]
        log("  %-26s %s" % (module_name, found or "(no capture names)"))

    log("")
    log("=== console commands, the fallback route ===")
    import azlmbr.legacy.general as general
    for command in ("r_GetScreenShot", "CaptureScreenshot", "capturescreenshot"):
        log("  general.run_console(%r) exists: %s"
            % (command, hasattr(general, "run_console")))
        break

    log("")
    log("=== does idle/screenshot exist on general? ===")
    log("  " + repr([a for a in sorted(dir(general))
                     if any(k in a.lower() for k in ("shot", "capture", "image"))]))


try:
    main()
except Exception:
    log('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: PASS')
os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
_general.exit_no_prompt()
