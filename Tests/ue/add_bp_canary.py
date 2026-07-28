"""
add_bp_canary.py — try to build the BP-extraction canary via SubobjectData.

`AActor.add_component_by_class` is not exposed to Python in 5.8 (measured).
The 5.x-official route is SubobjectDataSubsystem; this tries it once,
defensively, and reports exactly which step exists. If it fails, the
component-extraction path keeps L_Showcase as its only coverage.

Run:  run_ue_python.bat add_bp_canary.py
"""

import traceback

import unreal

MAP_PATH = "/Game/Maps/Fixture_01"
RESULT_TAG = "ADD_BP_CANARY"


def log(message):
    unreal.log("[" + RESULT_TAG + "] " + str(message))


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load level " + MAP_PATH)

    for actor in actor_sub.get_all_level_actors():
        if actor.get_actor_label() == "BP_Like_Props":
            actor_sub.destroy_actor(actor)

    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    log("SubobjectDataSubsystem: %r" % subsystem)
    if subsystem is None:
        raise RuntimeError("SubobjectDataSubsystem unavailable")

    actor = actor_sub.spawn_actor_from_class(
        unreal.Actor, unreal.Vector(2100.0, 800.0, 0.0))
    actor.set_actor_label("BP_Like_Props")

    handles = subsystem.k2_gather_subobject_data_for_instance(actor)
    log("root handles: %d" % len(handles or []))
    if not handles:
        raise RuntimeError("no subobject handles for the actor instance")

    cube = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube")
    letter_f = unreal.EditorAssetLibrary.load_asset("/Game/Meshes/SM_LetterF")

    added = []
    for mesh, offset in ((cube, unreal.Vector(0.0, 0.0, 50.0)),
                         (letter_f, unreal.Vector(200.0, 0.0, 0.0))):
        params = unreal.AddNewSubobjectParams()
        params.set_editor_property("parent_handle", handles[0])
        params.set_editor_property("new_class", unreal.StaticMeshComponent)
        result = subsystem.add_new_subobject(params)
        handle = result[0] if isinstance(result, tuple) else result
        data_result = subsystem.k2_find_subobject_data_from_handle(handle)
        data = data_result[0] if isinstance(data_result, tuple) else data_result
        component = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
        log("added subobject -> %r" % component)
        if component is None or not isinstance(component, unreal.StaticMeshComponent):
            raise RuntimeError("subobject is not a StaticMeshComponent: %r" % component)
        component.set_static_mesh(mesh)
        component.set_mobility(unreal.ComponentMobility.MOVABLE)
        component.set_relative_location(offset, False, False)
        added.append(component.get_name())
    log("components: %r" % (added,))

    if not level_sub.save_current_level():
        raise RuntimeError("failed to save level " + MAP_PATH)
    log("level saved with BP_Like_Props")


try:
    main()
except Exception:
    unreal.log_error("[" + RESULT_TAG + "] " + traceback.format_exc())
    print("RESULT: FAIL")
    raise

print("RESULT: PASS")
