"""
m8_acceptance.py — M8 acceptance, editor half: skeletal entities import,
carry the measured component recipe, stand in the corrected frame, and PLAY.

The plan's test is "Actor component reports the expected bone count; after N
simulated frames the bone transforms differ from bind pose". Two measured
facts shape the implementation (Tests/o3de/probe_m8_emfx.py, 7 rounds):

  * EMotionFX reflects NO bus to EditorPythonBindings in 26.05 -- no joint
    query, no bounds query, and the Actor attachment feature follows the
    target ENTITY, not a joint. The bone-count assertion therefore runs at
    the .actor product byte level (test_m8_artifacts.py: every manifest bone
    name must appear), which is the same skeleton the Actor component loads.
  * FrameCaptureRequestBus writes real screenshots headless, and the
    edit-mode noise floor is EXACTLY zero. So playback is asserted as pixel
    deltas: the camera over the WAVING canary must see frames change, the
    same camera rig over the BIND-POSE canary must see (near-)identical
    frames, and an edit-mode pair guards the capture pipeline itself.

Also asserted here, because nothing else can see them:
  * the Rz180 frame correction (lane_b_skeletal_rule) is IN the authored
    rotation -- SkelWave's local rotation must equal
    compose_rz180(manifest rotation), not the manifest rotation itself;
  * component wiring readback: Actor asset + Simple Motion motion/flags on
    SkelWave; Actor with NO Simple Motion on SkelBind.

Fresh import (the M3/M7 pattern): the full chain is under test.

Run: Tests/o3de/run_o3de_python.bat Tests/m8/m8_acceptance.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
LIB_ROOT = os.path.join(REPO_ROOT, "Tests", "lib")
for _path in (GEM_SCRIPTS, LIB_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm8_acceptance_result.txt')

EXPORT_DIR = os.path.join(REPO_ROOT, "Exports", "Fixture_01")
SHOT_DIR = os.path.join(SCRIPT_DIR, "results", "shots")

# Camera: straight down from 4 m. The three skeletal canaries stand 3 m
# apart on one line, and a top-down frame at this height spans about +-2 m,
# so each camera sees exactly one of them (a neighbour sits ~40 deg off-axis,
# outside the FOV) -- the bind-pose CONTROL frame cannot be contaminated by a
# neighbour's animation.
CAMERA_HEIGHT = 4.0
WAVE_POSITION = (15.0, 8.0)      # Lane A of UE (1500, -800)
BIND_POSITION = (21.0, 8.0)

lines = []
failures = []


def log(msg):
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
    import azlmbr.asset as asset_api
    import azlmbr.atom as atom
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general

    import png_diff
    from ueimporter import importer, manifest_io, prefab_build, skel_build, staging

    manifest_path = os.path.join(EXPORT_DIR, "manifest.json")
    if not check(os.path.exists(manifest_path),
                 "manifest missing at %s -- run the fixture export first"
                 % manifest_path):
        return
    document = manifest_io.load(manifest_path)

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = "%s/Prefabs/Fixture_01.prefab" % project_root

    log("importing Fixture_01 (fresh; the whole skeletal chain is under test)")
    report, _saved = importer.import_level(
        manifest_path=manifest_path,
        source_assets_root=os.path.join(EXPORT_DIR, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        log=log)
    check(not report.has_errors(), "import report contains errors")
    check(report.counters.get("skeletal_entities") == 3,
          "importer authored %r skeletal entities, expected 3"
          % report.counters.get("skeletal_entities"))

    def find_entity(name):
        search = entity_module.SearchFilter()
        search.names = [name]
        found = entity_module.SearchBus(bus.Broadcast, 'SearchEntities', search)
        return found[0] if found else None

    entities = {}
    for name in ("SkelWave", "SkelRootMotion", "SkelBind"):
        entities[name] = find_entity(name)
        check(entities[name] is not None, "%s not found after import" % name)
    if any(v is None for v in entities.values()):
        return

    log('')
    log('=== 1. the Rz180 frame correction is in the authored rotation ===')
    manifest_entities = {e["name"]: e for e in document["entities"]}
    for name in ("SkelWave", "SkelBind"):
        expected = skel_build.compose_rz180(
            manifest_entities[name]["transform"]["local"]["rotation"])
        actual = components.TransformBus(
            bus.Event, 'GetLocalRotationQuaternion', entities[name])
        dot = abs(sum(a * b for a, b in zip(
            (actual.x, actual.y, actual.z, actual.w), expected)))
        log('  %-14s authored (%.4f, %.4f, %.4f, %.4f) expected %s |dot|=%.6f'
            % (name, actual.x, actual.y, actual.z, actual.w,
               ["%.4f" % v for v in expected], dot))
        check(dot > 0.9999,
              "%s rotation is NOT the Rz180-composed manifest rotation -- "
              "the skeletal frame correction is missing or doubled, every "
              "character faces backwards" % name)

    log('')
    log('=== 2. component wiring readback ===')
    product_prefix = "assets"
    assets_by_guid = manifest_io.assets_by_guid(document)
    wave_manifest = manifest_entities["SkelWave"]["skeletal"]

    actor_type = prefab_build.resolve_component_type(skel_build.ACTOR_COMPONENT)
    motion_type = prefab_build.resolve_component_type(skel_build.SIMPLE_MOTION_COMPONENT)

    def component_property(entity_id, type_id, path):
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id)
        if not outcome or not outcome.IsSuccess():
            return None
        result = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', outcome.GetValue(), path)
        if not result or not result.IsSuccess():
            return None
        return result.GetValue()

    for name in ("SkelWave", "SkelRootMotion", "SkelBind"):
        value = component_property(entities[name], actor_type,
                                   skel_build.ACTOR_ASSET_PROPERTY)
        check(value is not None, "%s: Actor asset unreadable" % name)
        expected_relative = assets_by_guid[
            manifest_entities[name]["skeletal"]["asset_guid"]]["o3de_relative_path"]
        expected_product = staging.skeletal_product_path_for(
            expected_relative, product_prefix, "skeletal_mesh")
        path_back = asset_api.AssetCatalogRequestBus(
            bus.Broadcast, 'GetAssetPathById', value) if value is not None else ""
        log('  %-14s Actor asset -> %s' % (name, path_back))
        check((path_back or "").lower() == expected_product,
              "%s Actor asset resolves to %r, expected %r"
              % (name, path_back, expected_product))

    # The motion must be THIS entity's own animation, not merely some
    # motion: a wiring bug that gave every character the same clip would
    # keep the playback pixel-delta perfectly happy.
    motion_value = component_property(entities["SkelWave"], motion_type,
                                      skel_build.MOTION_PROPERTY)
    check(motion_value is not None, "SkelWave: Simple Motion Motion unreadable")
    if motion_value is not None:
        expected_motion = staging.skeletal_product_path_for(
            assets_by_guid[wave_manifest["animation_guid"]]["o3de_relative_path"],
            product_prefix, "animation")
        motion_path = asset_api.AssetCatalogRequestBus(
            bus.Broadcast, 'GetAssetPathById', motion_value)
        log('  SkelWave Motion -> %s' % motion_path)
        check((motion_path or "").lower() == expected_motion,
              "SkelWave's Simple Motion resolves to %r, expected its OWN "
              "animation %r" % (motion_path, expected_motion))
    play = component_property(entities["SkelWave"], motion_type,
                              skel_build.PLAY_ON_ACTIVE_PROPERTY)
    loop = component_property(entities["SkelWave"], motion_type,
                              skel_build.LOOP_PROPERTY)
    log('  SkelWave Simple Motion: play=%s loop=%s' % (play, loop))
    check(play is True and loop is True,
          "SkelWave Simple Motion flags wrong (play=%r loop=%r)" % (play, loop))

    bind_motion = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', entities["SkelBind"], motion_type)
    check(not (bind_motion and bind_motion.IsSuccess()),
          "SkelBind has a Simple Motion component; a motionless one reads as "
          "configured but plays nothing")

    log('')
    log('=== 3. playback: pixel deltas (the only observable; see docstring) ===')
    os.makedirs(SHOT_DIR, exist_ok=True)

    def capture(name):
        target = os.path.join(SHOT_DIR, name).replace("\\", "/")
        if os.path.exists(target):
            os.remove(target)
        atom.FrameCaptureRequestBus(bus.Broadcast, 'CaptureScreenshot', target)
        general.idle_wait_frames(30)
        return target if os.path.exists(target) else None

    def pair_delta(tag):
        first = capture(tag + "_a.png")
        general.idle_wait_frames(45)
        second = capture(tag + "_b.png")
        if not first or not second:
            fail("screenshot capture failed for %s" % tag)
            return None
        return png_diff.delta(first, second)

    def aim_at(position_xy):
        general.set_current_view_position(position_xy[0], position_xy[1],
                                          CAMERA_HEIGHT)
        general.set_current_view_rotation(-90.0, 0.0, 0.0)
        general.idle_wait_frames(30)

    aim_at(WAVE_POSITION)
    edit_delta = pair_delta("edit_wave")
    if edit_delta is not None:
        log('  edit-mode pair over SkelWave: %r' % edit_delta)
        check(edit_delta["changed"] <= 0.001,
              "edit-mode noise floor is %r; the capture pipeline itself is "
              "unstable and the game-mode deltas below prove nothing"
              % edit_delta)

    general.enter_game_mode()
    general.idle_wait_frames(300)   # fixture dynamic bodies settle; loops run on
    if not check(general.is_in_game_mode(), "editor did not enter game mode"):
        return

    # VISIBILITY PRECONDITION. Two near-identical frames mean either "the
    # motion is not playing" or "there is no character on screen yet" --
    # opposite diagnoses that the delta alone cannot tell apart, and after a
    # full asset reprocess the second one really happens (the actor is still
    # streaming when the first frame is captured). So wait for the canary to
    # actually render, judged against a frame of empty sky, before measuring
    # anything. A timeout here is a precise failure, not a misleading one.
    def sky_reference():
        general.set_current_view_position(WAVE_POSITION[0], WAVE_POSITION[1],
                                          CAMERA_HEIGHT)
        general.set_current_view_rotation(89.0, 0.0, 0.0)     # straight up
        general.idle_wait_frames(30)
        return capture("game_sky.png")

    empty = sky_reference()
    visible = False
    for attempt in range(6):
        aim_at(WAVE_POSITION)
        shot = capture("game_visible_%d.png" % attempt)
        if empty and shot:
            against_empty = png_diff.delta(empty, shot)
            log('  visibility attempt %d: %r' % (attempt, against_empty))
            if against_empty["changed"] >= 0.05:
                visible = True
                break
        general.idle_wait_frames(120)
    if not check(visible,
                 "the skeletal canary never rendered: %d game-mode frames "
                 "were indistinguishable from empty sky, so the playback "
                 "deltas below would measure nothing" % 6):
        general.exit_game_mode()
        return

    aim_at(WAVE_POSITION)
    wave_delta = pair_delta("game_wave")
    aim_at(BIND_POSITION)
    bind_delta = pair_delta("game_bind")

    general.exit_game_mode()
    general.idle_wait_frames(10)

    if wave_delta is None or bind_delta is None:
        return
    log('  game-mode pair over SkelWave: %r' % wave_delta)
    log('  game-mode pair over SkelBind: %r' % bind_delta)
    check(wave_delta["changed"] >= 0.02,
          "SkelWave frames barely differ (%r): the motion is not playing -- "
          "the failure this acceptance exists to catch" % wave_delta)
    check(bind_delta["changed"] <= 0.01,
          "SkelBind (no motion) frames differ (%r): the delta signal is not "
          "coming from animation and the SkelWave assertion is meaningless"
          % bind_delta)
    check(wave_delta["changed"] >= 5 * max(bind_delta["changed"], 0.001),
          "SkelWave's delta (%.4f) is not clearly above the static control's "
          "(%.4f)" % (wave_delta["changed"], bind_delta["changed"]))


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
