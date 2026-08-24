"""probe_create_level.py -- can this build create a level from Python?

The scratch-level fix needs an EMPTY level that imports own outright. That
only works if the editor can create one headlessly; this asks which API
exists and whether it works, on the TEST project.

Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_create_level.py <result> <project>
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'probe_create_level_result.txt'))

lines = []


def log(message=""):
    lines.append(str(message))
    print(message)


def main():
    import azlmbr.legacy.general as general

    general.idle_enable(True)
    project_root = general.get_game_folder().rstrip('/\\')
    log("project: %s" % project_root)

    candidates = [name for name in dir(general) if "level" in name.lower()]
    log("level-ish API on general: %r" % candidates)

    name = "UEO3DE_Scratch_Probe"
    level_dir = os.path.join(project_root, "Levels", name)
    log("target exists before: %r" % os.path.isdir(level_dir))

    for api, args in (
            ("create_level_no_prompt", (name, 128, 1, 128, False)),
            ("create_level_no_prompt", (name,)),
            ("create_level", (name, 128, 1, 128, False)),
    ):
        fn = getattr(general, api, None)
        if fn is None:
            log("  %s: absent" % api)
            continue
        try:
            outcome = fn(*args)
            log("  %s%r -> %r" % (api, args, outcome))
            general.idle_wait_frames(60)
            level_prefab = os.path.join(level_dir, name + ".prefab")
            log("  on disk after: dir=%r prefab=%r"
                % (os.path.isdir(level_dir), os.path.isfile(level_prefab)))
            if os.path.isfile(level_prefab):
                log("  SUCCESS with %s%r" % (api, args))
                break
        except Exception as error:  # noqa: BLE001
            log("  %s%r raised %r" % (api, args, error))

    # Can we then open DefaultLevel back and re-open the scratch?
    log("open DefaultLevel again: %r" % general.open_level_no_prompt("DefaultLevel"))
    general.idle_wait_frames(30)
    log("re-open scratch: %r" % general.open_level_no_prompt(name))
    general.idle_wait_frames(30)


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
