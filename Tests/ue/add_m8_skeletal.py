"""
add_m8_skeletal.py -- M8 skeletal canaries (in place), FULL EDITOR run.

Adds to the EXISTING Fixture_01 (build_fixture_01.py carries the same actors
for a from-scratch build; keep in sync by hand):

  /Game/Skeletal/*    imported once from Tests/ue/data/quaternius_character.fbx
                      (CC0, Quaternius Platformer Game Kit): SkeletalMesh
                      `Character` (40 bones), its Skeleton, 18 AnimSequences.
                      `Anim_Walk_RM` is a duplicate of the Walk sequence with
                      `enable_root_motion=True` -- the ANIM_ROOT_MOTION_DROPPED
                      canary (no stock UndeadPack anim has the flag; measured).

  SkelWave            SkeletalMeshActor, yaw 45, single-node Wave looping --
                      the M8 playback target (arms move through a large arc,
                      so even a pixel-delta observable sees it).
  SkelRootMotion      single-node Anim_Walk_RM looping -- must export with the
                      root-motion warning and still play in place (that is what
                      UE itself does for a SkeletalMeshActor: root motion is
                      only extracted by AnimBlueprints/characters).
  SkelBind            actor with NO animation assigned: Actor component only,
                      no Simple Motion; the acceptance's static control and
                      world-bounds orientation check.

Writes Tests/m8/skel_reference.json with the UE-side truth (bone count/names,
per-canary world bounds, foot joint world positions) for the M8 acceptance to
compare against.

FULL editor because skeletal render objects do not exist in commandlets (the
MeshObject assertion, see probe_m8_skeletal.py); the level save + asset saves
here are the same either way.

Run:
  UnrealEditor.exe <fixture.uproject> -ExecutePythonScript=add_m8_skeletal.py
"""

import json
import os
import traceback

import unreal

MAP_PATH = "/Game/Maps/Fixture_01"
SKELETAL_DIR = "/Game/Skeletal"
# Imported asset names follow the FBX FILE name (measured: Character.fbx ->
# `Character`, anims `<file><take>` like `CharacterCharacterArmature_Wave`),
# so the repo file is named for the asset we want.
SOURCE_FBX = "D:/Gamedev/UEtoO3DE/Tests/ue/data/SK_Canary.fbx"
MESH_PATH = SKELETAL_DIR + "/SK_Canary"
WAVE_PATH = SKELETAL_DIR + "/SK_CanaryCharacterArmature_Wave"
WALK_PATH = SKELETAL_DIR + "/SK_CanaryCharacterArmature_Walk"
WALK_RM_PATH = SKELETAL_DIR + "/Anim_Walk_RM"
REFERENCE_PATH = "D:/Gamedev/UEtoO3DE/Tests/m8/skel_reference.json"
RESULT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/add_m8_skeletal_result.txt"

LABELS = ("SkelWave", "SkelRootMotion", "SkelBind")

os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
_handle = open(RESULT_PATH, "w")


def log(message):
    _handle.write(str(message) + "\n")
    _handle.flush()
    unreal.log("[ADD_M8_SKELETAL] " + str(message))


def import_character():
    """Import the CC0 character once; reuse on reruns."""
    if unreal.EditorAssetLibrary.does_asset_exist(MESH_PATH):
        log("skeletal assets already imported; reusing " + MESH_PATH)
        return unreal.EditorAssetLibrary.load_asset(MESH_PATH)

    ui = unreal.FbxImportUI()
    ui.set_editor_property("import_mesh", True)
    ui.set_editor_property("import_as_skeletal", True)
    ui.set_editor_property("import_animations", True)
    ui.set_editor_property("import_materials", False)
    ui.set_editor_property("import_textures", False)
    ui.set_editor_property("mesh_type_to_import",
                           unreal.FBXImportType.FBXIT_SKELETAL_MESH)
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", SOURCE_FBX)
    task.set_editor_property("destination_path", SKELETAL_DIR)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("options", ui)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    mesh = unreal.EditorAssetLibrary.load_asset(MESH_PATH)
    if mesh is None:
        raise RuntimeError("skeletal import produced no " + MESH_PATH)
    log("imported %s (+ skeleton + animations)" % MESH_PATH)
    return mesh


def make_root_motion_anim():
    if not unreal.EditorAssetLibrary.does_asset_exist(WALK_RM_PATH):
        if not unreal.EditorAssetLibrary.duplicate_asset(WALK_PATH, WALK_RM_PATH):
            raise RuntimeError("could not duplicate Walk -> Anim_Walk_RM")
    anim = unreal.EditorAssetLibrary.load_asset(WALK_RM_PATH)
    anim.set_editor_property("enable_root_motion", True)
    unreal.EditorAssetLibrary.save_asset(WALK_RM_PATH)
    log("Anim_Walk_RM: enable_root_motion=%s"
        % anim.get_editor_property("enable_root_motion"))
    return anim


def place(actor_sub, mesh, label, location, yaw, anim, looping):
    actor = actor_sub.spawn_actor_from_class(unreal.SkeletalMeshActor, location)
    actor.set_actor_label(label)
    actor.set_actor_rotation(unreal.Rotator(0.0, 0.0, yaw), False)
    component = actor.get_component_by_class(unreal.SkeletalMeshComponent)
    component.set_editor_property("skeletal_mesh_asset", mesh)
    component.set_editor_property(
        "animation_mode", unreal.AnimationMode.ANIMATION_SINGLE_NODE)
    if anim is not None:
        data = component.get_editor_property("animation_data")
        data.set_editor_property("anim_to_play", anim)
        data.set_editor_property("saved_looping", looping)
        data.set_editor_property("saved_playing", True)
        component.set_editor_property("animation_data", data)
    log("%s: anim=%s looping=%s" % (
        label, anim.get_name() if anim else None, looping))
    return actor, component


def reference_block(actor, component):
    origin, extent = actor.get_actor_bounds(False)
    block = {
        "world_bounds_origin_cm": [origin.x, origin.y, origin.z],
        "world_bounds_extent_cm": [extent.x, extent.y, extent.z],
        "actor_location_cm": [actor.get_actor_location().x,
                              actor.get_actor_location().y,
                              actor.get_actor_location().z],
        "actor_yaw_deg": actor.get_actor_rotation().yaw,
    }
    for foot in ("Foot_L", "Foot_R"):
        try:
            world = component.get_socket_location(foot)
            block[foot + "_world_cm"] = [world.x, world.y, world.z]
        except Exception as exc:
            block[foot + "_world_cm"] = "unavailable: %s" % str(exc)[:60]
    return block


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    mesh = import_character()
    wave = unreal.EditorAssetLibrary.load_asset(WAVE_PATH)
    if wave is None:
        raise RuntimeError("missing " + WAVE_PATH)
    walk_rm = make_root_motion_anim()

    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load level " + MAP_PATH)
    for actor in actor_sub.get_all_level_actors():
        if actor.get_actor_label() in LABELS:
            actor_sub.destroy_actor(actor)

    reference = {
        "comment": "UE-side truth for the M8 acceptance; regenerated by "
                   "add_m8_skeletal.py, units are UE centimetres",
        "bone_count": None,
        "bone_names": [],
        "canaries": {},
    }

    wave_actor, wave_component = place(
        actor_sub, mesh, "SkelWave", unreal.Vector(1500.0, -800.0, 0.0),
        45.0, wave, True)
    reference["bone_count"] = wave_component.get_num_bones()
    reference["bone_names"] = [
        str(wave_component.get_bone_name(i))
        for i in range(wave_component.get_num_bones())]
    reference["canaries"]["SkelWave"] = reference_block(wave_actor, wave_component)

    rm_actor, rm_component = place(
        actor_sub, mesh, "SkelRootMotion", unreal.Vector(1800.0, -800.0, 0.0),
        0.0, walk_rm, True)
    reference["canaries"]["SkelRootMotion"] = reference_block(rm_actor, rm_component)

    bind_actor, bind_component = place(
        actor_sub, mesh, "SkelBind", unreal.Vector(2100.0, -800.0, 0.0),
        0.0, None, False)
    reference["canaries"]["SkelBind"] = reference_block(bind_actor, bind_component)

    log("bone count: %d" % reference["bone_count"])

    if not level_sub.save_current_level():
        raise RuntimeError("failed to save level " + MAP_PATH)
    log("level saved")

    os.makedirs(os.path.dirname(REFERENCE_PATH), exist_ok=True)
    with open(REFERENCE_PATH, "w") as handle:
        json.dump(reference, handle, indent=2, sort_keys=True)
    log("reference -> " + REFERENCE_PATH)


status = "PASS"
try:
    main()
except Exception:
    log("FATAL: " + traceback.format_exc())
    status = "FAIL"

log("RESULT: " + status)
_handle.close()
print("RESULT: " + status)
try:
    unreal.SystemLibrary.quit_editor()
except Exception:
    pass
if status != "PASS":
    raise SystemExit(1)
