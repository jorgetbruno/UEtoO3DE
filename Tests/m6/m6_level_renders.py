"""m6_level_renders.py -- does the imported level actually show a picture?

The one check the suite did not have. A real import
(Docks/VOL4_Albert `Demonstration`, 905 entities) rendered PURE WHITE while
every structural assertion passed: 905 entities created, 693/693 mesh
colliders verified, 140/140 cooked physics meshes, 50/50 colliding in the
running world, 0 Asset Processor errors. Two UE post-process volumes had put
`auto_exposure_bias` 12.0 and 9.5 into Atom's `Manual Compensation` -- EV
stops -- and nothing anywhere could see the result.

This loads the SAVED prefab, points a camera at its contents, captures a
frame through `azlmbr.atom.FrameCaptureRequestBus`, and asks
`Tests/lib/frame_stats.py` whether there is a picture in it.

A CONTROL RUNS FIRST, and it is not optional: an all-white capture and a
capture that never happened look identical from here. The control renders the
default level with no prefab loaded and must come back USABLE; if it does not,
this run reports that it could not measure anything rather than blaming the
level.

Env: UEO3DE_PREFAB   prefab to load (default: the M2 fixture import)
Run: Tests/o3de/run_o3de_python.bat Tests/m6/m6_level_renders.py <result> <project>
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
for _path in (os.path.join(REPO_ROOT, "Tests", "lib"),
              os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import frame_stats  # noqa: E402

RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'm6_level_renders_result.txt'))

CAPTURE_DIR = os.path.join(SCRIPT_DIR, "results", "captures")
CAPTURE_FRAMES = int(os.environ.get("UEO3DE_CAPTURE_FRAMES", "600"))

lines = []
failures = []


def log(message=""):
    lines.append(str(message))
    print(message)


def fail(message):
    failures.append(str(message))
    log('FAIL: ' + str(message))


def capture(name):
    """Capture the viewport to a PNG and return its path, or None."""
    import azlmbr.atom as atom
    import azlmbr.bus as bus
    import azlmbr.legacy.general as general

    os.makedirs(CAPTURE_DIR, exist_ok=True)
    path = os.path.join(CAPTURE_DIR, name + ".png").replace("\\", "/")
    if os.path.exists(path):
        os.remove(path)

    general.idle_wait_frames(10)
    try:
        outcome = atom.FrameCaptureRequestBus(
            bus.Broadcast, 'CaptureScreenshot', path)
    except Exception as error:  # noqa: BLE001
        fail("FrameCaptureRequestBus.CaptureScreenshot raised %r -- the "
             "capture API shape differs on this build; probe_capture_api.py "
             "lists what is available" % (error,))
        return None
    log("  capture requested (%r) -> %r" % (name, outcome))

    # The capture completes on a later frame; poll for the file rather than
    # assume a fixed wait, and say how long it took.
    for waited in range(240):
        general.idle_wait_frames(1)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            log("  written after %d frames (%d bytes)"
                % (waited + 1, os.path.getsize(path)))
            general.idle_wait_frames(2)      # let the writer close it
            return path
    fail("no capture file appeared at %s after 240 frames" % path)
    return None


def describe(stats):
    log("    mean luminance   %.4f" % stats["mean"])
    log("    clipped white    %.1f%%" % (stats["white_clipped"] * 100.0))
    log("    clipped black    %.1f%%" % (stats["black_clipped"] * 100.0))
    log("    5-95%% spread     %.4f" % stats["range"])


def main():
    import azlmbr.bus as bus
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath
    import azlmbr.prefab as prefab

    general.idle_enable(True)
    # Honour the scratch-level env: on a project whose DefaultLevel holds the
    # user's real scene, opening it here discards their unsaved edits with no
    # prompt. Test projects keep the stock default.
    general.open_level_no_prompt(
        os.environ.get("UEO3DE_SCRATCH_LEVEL", "").strip() or "DefaultLevel")
    general.idle_wait_frames(60)

    # --- CONTROL: an empty default level must render a usable frame ----------
    log("=== control: the default level, no prefab ===")
    control_path = capture("control")
    if control_path is None:
        fail("the control capture failed, so nothing below is evidence")
        return
    control = frame_stats.frame_stats(control_path)
    describe(control)
    reason = frame_stats.verdict(control)
    if reason is not None:
        fail("THE CONTROL ITSELF IS UNUSABLE (%s). This build cannot be "
             "measured this way -- the level under test is not implicated."
             % reason)
        return
    log("  control OK: the capture path works and produces a picture")

    # --- the imported level ---------------------------------------------------
    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = os.environ.get("UEO3DE_PREFAB", "").strip() or \
        "%s/Prefabs/Fixture_01.prefab" % project_root
    log("")
    log("=== imported level: %s ===" % prefab_path)
    if not os.path.isfile(prefab_path):
        fail("no prefab at %s -- import a level first; this test refuses to "
             "pass without one" % prefab_path)
        return

    outcome = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'InstantiatePrefab', prefab_path,
        entity_module.EntityId(), azmath.Vector3(0.0, 0.0, 0.0))
    if outcome is None or not outcome.IsSuccess():
        fail("InstantiatePrefab failed for " + prefab_path)
        return

    # Give models, materials and the sky time to stream in. A frame captured
    # mid-load is legitimately dark and would be a false alarm.
    general.idle_wait_frames(CAPTURE_FRAMES)

    level_path = capture("level")
    if level_path is None:
        return
    stats = frame_stats.frame_stats(level_path)
    describe(stats)
    reason = frame_stats.verdict(stats)
    if reason is not None:
        fail("the imported level does not render a usable picture: %s\n"
             "  capture: %s" % (reason, level_path))
    else:
        log("  the imported level renders a picture")


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
