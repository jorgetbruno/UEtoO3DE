# M0 acceptance sanity check (v2, follows the JoltPhysicsTest smoke_test.py pattern):
# - discovers how to execute a console command in this azlmbr version,
# - runs `jolt_Debug 1` (no crash = pass),
# - writes RESULT: PASS/FAIL to a result file,
# - QUITS the editor via general.exit_no_prompt() (without this the editor idles forever).
# Run: Editor.exe --project-path=<proj> -BatchMode -autotest_mode --runpython jolt_init_check.py <result_file>
# The JoltPhysics init log line is grepped from user/log/Editor.log afterwards.

import os
import sys
import traceback

RESULT_PATH = (
    sys.argv[-1]
    if len(sys.argv) > 1 and sys.argv[-1].lower().endswith(".txt")
    else r"D:/Gamedev/UEtoO3DE/Tests/o3de/results/jolt_init_result.txt"
)
lines = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def main():
    import azlmbr.legacy.general as general

    general.idle_enable(True)

    # Discover the console-command API exposed by this build.
    candidates = [n for n in dir(general) if ("console" in n.lower() or "command" in n.lower() or "cvar" in n.lower())]
    log("general console-ish attrs: %s" % candidates)

    executed = False
    for name in ("execute_command", "run_console", "execute_console_command", "run_console_command"):
        fn = getattr(general, name, None)
        if fn is None:
            continue
        try:
            fn("jolt_Debug 1")
            log("jolt_Debug 1 executed via general.%s" % name)
            executed = True
            break
        except Exception as e:
            log("general.%s raised: %r" % (name, e))

    if not executed:
        log("FAIL: no working console-command entry point found")
        return False

    general.idle_wait_frames(5)
    log("jolt_Debug 1 survived for 5 idle frames")
    return True


ok = True
try:
    ok = main()
except Exception:
    ok = False
    log("EXCEPTION: " + traceback.format_exc().replace("\n", " | "))

log("RESULT: " + ("PASS" if ok else "FAIL"))

os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
with open(RESULT_PATH, "w") as f:
    f.write("\n".join(lines))

import azlmbr.legacy.general as general

general.exit_no_prompt()
