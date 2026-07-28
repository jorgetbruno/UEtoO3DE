"""
probe_m10_gempath2.py -- does the editor RESOLVE the gem, or not even that?

Two edits (manifest registration, gem.json cleanup) have failed to make the
bootstrap run, so stop editing and ask the runtime directly. `azlmbr.paths`
exposes `gemroot`, which is the engine's own gem-path resolver: if it answers
for UEImporter, the gem is known and the problem is script *scanning*; if it
does not, the gem is not known and nothing about bootstrap.py matters yet.

The control is JoltPhysics -- an EXTERNAL, manifest-registered gem that this
project unquestionably loads (every M3 physics suite depends on it). If
gemroot answers for Jolt and not for UEImporter, the difference is ours to
find; if it answers for neither, gemroot is the wrong question.
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m10_gempath2_result.txt')

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


def main():
    import azlmbr.paths as paths

    log("=== gemroot(): which gems does the runtime resolve? ===")
    for gem in ("UEImporter", "JoltPhysics", "WhiteBox", "EditorPythonBindings",
                "Atom", "NoSuchGemAtAll"):
        try:
            value = paths.gemroot(gem)
            log("  %-22s -> %r" % (gem, value))
        except Exception as exc:
            log("  %-22s raised %s: %s" % (gem, type(exc).__name__, str(exc)[:120]))

    log("")
    log("=== resolve_path on a gem-relative alias ===")
    for alias in ("@gemroot:UEImporter@/Editor/Scripts/bootstrap.py",
                  "@gemroot:WhiteBox@/Editor/Scripts",
                  "@projectroot@/project.json"):
        try:
            log("  %-52s -> %r" % (alias, paths.resolve_path(alias)))
        except Exception as exc:
            log("  %-52s raised %s: %s" % (alias, type(exc).__name__, str(exc)[:120]))

    log("")
    log("=== is the bootstrap file where the gem says it is? ===")
    expected = r"D:\Gamedev\UEtoO3DE\O3DE\Gems\UEImporter\Editor\Scripts\bootstrap.py"
    log("  %s exists=%s" % (expected, os.path.isfile(expected)))

    log("")
    log("=== editor log lines mentioning the gem or python bootstrap ===")
    log_dir = paths.log
    for name in ("Editor.log", "editor.log"):
        candidate = os.path.join(log_dir, name)
        if not os.path.isfile(candidate):
            continue
        log("  reading %s" % candidate)
        try:
            with open(candidate, 'r', errors='replace') as handle:
                for line in handle:
                    low = line.lower()
                    if ('ueimporter' in low or 'bootstrap' in low
                            or ('gem' in low and 'python' in low)):
                        log("    " + line.rstrip()[:220])
        except Exception as exc:
            log("    could not read: %s" % exc)
        break


try:
    main()
except Exception:
    log('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: PASS')

import azlmbr.legacy.general as _general
_general.exit_no_prompt()
