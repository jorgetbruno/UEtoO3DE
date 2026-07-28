"""
add_mirror_canaries.py — negative-scale + BP-extraction canaries (in place).

Adds to the EXISTING Fixture_01 (build_fixture_01.py carries the same actors
for a from-scratch build; keep in sync by hand):

  MirroredF        SM_LetterF at scale (-1, 1, 1): ODD negative axes -- a true
                   mirror. Must export with positive scale, rotation unchanged
                   (SIGMA_rot = I for (-,+,+)) and a mesh reference to the
                   `#mx` mirrored variant. The letterF is the only fixture
                   mesh asymmetric enough to catch a wrong or missing mirror
                   at the product-byte level.

  RotationFold_Box Cube at scale (1, -2, -0.5): EVEN negative axes -- exactly
                   an Rx(180) rotation, no mirror, no variant. Must export
                   with scale (1, 2, 0.5) and the 180 folded into the
                   rotation. Also non-uniform, so the NonUniformScale path
                   composes with the fold.

  BP_Like_Props    an unmapped-class actor (plain AActor) carrying TWO
                   StaticMeshComponents, if the editor allows adding instance
                   components from Python -- the component-extraction canary.
                   Skipped with a loud log when the API is unavailable.

Run:  run_ue_python.bat add_mirror_canaries.py
"""

import traceback

import unreal

MAP_PATH = "/Game/Maps/Fixture_01"
RESULT_TAG = "ADD_MIRROR_CANARIES"

LABELS = ("MirroredF", "RotationFold_Box", "BP_Like_Props")


def log(message):
    unreal.log("[" + RESULT_TAG + "] " + str(message))


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load level " + MAP_PATH)

    for actor in actor_sub.get_all_level_actors():
        if actor.get_actor_label() in LABELS:
            actor_sub.destroy_actor(actor)

    letter_f = unreal.EditorAssetLibrary.load_asset("/Game/Meshes/SM_LetterF")
    cube = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube")
    if letter_f is None or cube is None:
        raise RuntimeError("fixture meshes missing")

    mirrored = actor_sub.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(1500.0, 800.0, 0.0))
    mirrored.set_actor_label("MirroredF")
    mirrored.static_mesh_component.set_static_mesh(letter_f)
    mirrored.static_mesh_component.set_mobility(unreal.ComponentMobility.STATIC)
    mirrored.set_actor_scale3d(unreal.Vector(-1.0, 1.0, 1.0))
    log("MirroredF: scale %s" % mirrored.get_actor_scale3d())

    fold = actor_sub.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(2100.0, 0.0, 100.0))
    fold.set_actor_label("RotationFold_Box")
    fold.static_mesh_component.set_static_mesh(cube)
    fold.static_mesh_component.set_mobility(unreal.ComponentMobility.STATIC)
    fold.set_actor_scale3d(unreal.Vector(1.0, -2.0, -0.5))
    log("RotationFold_Box: scale %s" % fold.get_actor_scale3d())

    # --- BP-extraction canary: instance components on a plain Actor ---------
    try:
        props = actor_sub.spawn_actor_from_class(
            unreal.Actor, unreal.Vector(2100.0, 800.0, 0.0))
        props.set_actor_label("BP_Like_Props")
        added = []
        for mesh, offset in ((cube, unreal.Vector(0.0, 0.0, 50.0)),
                             (letter_f, unreal.Vector(200.0, 0.0, 0.0))):
            transform = unreal.Transform(location=offset)
            component = props.add_component_by_class(
                unreal.StaticMeshComponent, False, transform, False)
            if component is None:
                raise RuntimeError("add_component_by_class returned None")
            component.set_static_mesh(mesh)
            component.set_mobility(unreal.ComponentMobility.MOVABLE)
            added.append(component.get_name())
        log("BP_Like_Props: components %r" % (added,))
    except Exception as exc:
        # The extraction path then has no fixture canary; L_Showcase's 106
        # Blueprint actors remain its only coverage. Loud, not fatal.
        log("BP_Like_Props SKIPPED: %s: %s" % (type(exc).__name__, exc))
        for actor in actor_sub.get_all_level_actors():
            if actor.get_actor_label() == "BP_Like_Props":
                actor_sub.destroy_actor(actor)

    if not level_sub.save_current_level():
        raise RuntimeError("failed to save level " + MAP_PATH)
    log("level saved")


try:
    main()
except Exception:
    unreal.log_error("[" + RESULT_TAG + "] FAILED")
    unreal.log_error(traceback.format_exc())
    print("RESULT: FAIL")
    raise

print("RESULT: PASS")
