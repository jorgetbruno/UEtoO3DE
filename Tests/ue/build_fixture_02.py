"""
build_fixture_02.py -- Fixture_02, the M9 feature level (idempotent rebuild).

Fixture_01 stays FROZEN (plan M9); every stretch feature gets one actor here:

  Fixture02_Floor   engine cube flattened -- something to stand on.
  Foliage_ISM       a plain Actor carrying an InstancedStaticMeshComponent
                    (engine cylinder) with 5 instances, one rotated and one
                    scaled: the ISM/HISM expansion canary. (The real levels'
                    InstancedFoliageActor is EMPTY -- measured,
                    probe_m9_foliage.py -- so the fixture authors its own.)
  SplineArch        a plain Actor with a SplineMeshComponent: an engine
                    cylinder bent 90 degrees (the probe shape) -- the
                    SPLINE_BAKED canary, asymmetric by construction.
  LodMesh           a StaticMeshActor over SM_TwoLod (LOD0 = SM_LetterF,
                    LOD1 = engine cube, built procedurally): LOD_FLATTENED
                    plus a byte-checkable asymmetric LOD0.
  Decal_01          a DecalActor with M_Fixture_Decal (deferred-decal domain,
                    base colour from T_Fixture_BaseColor), sort order 7,
                    yawed 30 so the projection remap is not axis-trivial.
  Cam_Main          a CameraActor, fov 72 horizontal, default aspect.

Components attach via SubobjectDataSubsystem (add_component_by_class is not
exposed in 5.8 -- measured twice).

FULL editor run (StaticMeshEditorSubsystem is None in commandlets --
measured):
  UnrealEditor.exe <fixture.uproject> -ExecutePythonScript=build_fixture_02.py
"""

import traceback

import unreal

MAP_PATH = "/Game/Maps/Fixture_02"
TWO_LOD_PATH = "/Game/Meshes/SM_TwoLod"
DECAL_MATERIAL_PATH = "/Game/Materials/M_Fixture_Decal"
RESULT_TAG = "BUILD_FIXTURE_02"


def log(message):
    unreal.log("[" + RESULT_TAG + "] " + str(message))


def add_component(subobjects, actor, component_class):
    handles = subobjects.k2_gather_subobject_data_for_instance(actor)
    params = unreal.AddNewSubobjectParams()
    params.set_editor_property("parent_handle", handles[0])
    params.set_editor_property("new_class", component_class)
    result = subobjects.add_new_subobject(params)
    handle = result[0] if isinstance(result, tuple) else result
    data_result = subobjects.k2_find_subobject_data_from_handle(handle)
    data = data_result[0] if isinstance(data_result, tuple) else data_result
    component = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
    if component is None:
        raise RuntimeError("add_new_subobject produced no %s"
                           % component_class.get_name())
    return component


def build_two_lod_mesh(cylinder_unused):
    """SM_TwoLod: LOD0 = the asymmetric letterF, LOD1 = the engine cube."""
    if unreal.EditorAssetLibrary.does_asset_exist(TWO_LOD_PATH):
        mesh = unreal.EditorAssetLibrary.load_asset(TWO_LOD_PATH)
        if mesh.get_num_lods() >= 2:
            log("SM_TwoLod exists with %d LODs; reusing" % mesh.get_num_lods())
            return mesh
        unreal.EditorAssetLibrary.delete_asset(TWO_LOD_PATH)
    mesh = unreal.EditorAssetLibrary.duplicate_asset(
        "/Game/Meshes/SM_LetterF", TWO_LOD_PATH)
    if mesh is None:
        raise RuntimeError("could not duplicate SM_LetterF")
    subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    cube = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube")
    added = subsystem.set_lod_from_static_mesh(mesh, 1, cube, 0, True)
    log("SM_TwoLod: LOD1 from cube -> index %s, num_lods %d"
        % (added, mesh.get_num_lods()))
    if mesh.get_num_lods() < 2:
        raise RuntimeError("SM_TwoLod did not gain a second LOD")
    unreal.EditorAssetLibrary.save_asset(TWO_LOD_PATH)
    return mesh


def build_decal_material():
    if unreal.EditorAssetLibrary.does_asset_exist(DECAL_MATERIAL_PATH):
        return unreal.EditorAssetLibrary.load_asset(DECAL_MATERIAL_PATH)
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset("M_Fixture_Decal", "/Game/Materials",
                                  unreal.Material, unreal.MaterialFactoryNew())
    material.set_editor_property("material_domain",
                                 unreal.MaterialDomain.MD_DEFERRED_DECAL)
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    texture = unreal.EditorAssetLibrary.load_asset("/Game/Textures/T_Fixture_BaseColor")
    sample = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionTextureSample, -400, 0)
    sample.set_editor_property("texture", texture)
    unreal.MaterialEditingLibrary.connect_material_property(
        sample, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    unreal.MaterialEditingLibrary.recompile_material(material)
    unreal.EditorAssetLibrary.save_asset(DECAL_MATERIAL_PATH)
    log("created M_Fixture_Decal (deferred decal domain)")
    return material


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    subobjects = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)

    cube = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube")
    cylinder = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder")
    pbr = unreal.EditorAssetLibrary.load_asset("/Game/Materials/M_Fixture_PBR")
    two_lod = build_two_lod_mesh(cylinder)
    decal_material = build_decal_material()

    if unreal.EditorAssetLibrary.does_asset_exist(MAP_PATH):
        if not level_sub.load_level(MAP_PATH):
            raise RuntimeError("failed to load existing " + MAP_PATH)
        actor_sub.destroy_actors(actor_sub.get_all_level_actors())
    else:
        if not level_sub.new_level(MAP_PATH):
            raise RuntimeError("failed to create " + MAP_PATH)

    floor = actor_sub.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(0.0, 0.0, -50.0))
    floor.set_actor_label("Fixture02_Floor")
    floor.static_mesh_component.set_static_mesh(cube)
    floor.static_mesh_component.set_mobility(unreal.ComponentMobility.STATIC)
    floor.set_actor_scale3d(unreal.Vector(20.0, 20.0, 1.0))

    foliage = actor_sub.spawn_actor_from_class(
        unreal.Actor, unreal.Vector(-400.0, -400.0, 0.0))
    foliage.set_actor_label("Foliage_ISM")
    ismc = add_component(subobjects, foliage, unreal.InstancedStaticMeshComponent)
    ismc.set_static_mesh(cylinder)
    if pbr is not None:
        ismc.set_material(0, pbr)
    ismc.set_mobility(unreal.ComponentMobility.STATIC)
    instance_transforms = (
        unreal.Transform(location=unreal.Vector(0, 0, 50)),
        unreal.Transform(location=unreal.Vector(200, 0, 50)),
        unreal.Transform(location=unreal.Vector(0, 200, 50),
                         rotation=unreal.Rotator(0.0, 0.0, 30.0)),
        unreal.Transform(location=unreal.Vector(200, 200, 75),
                         scale=unreal.Vector(1.0, 1.0, 1.5)),
        unreal.Transform(location=unreal.Vector(400, 100, 50)),
    )
    for transform in instance_transforms:
        ismc.add_instance(transform, False)
    log("Foliage_ISM: %d instances" % ismc.get_instance_count())

    arch = actor_sub.spawn_actor_from_class(
        unreal.Actor, unreal.Vector(600.0, -400.0, 0.0))
    arch.set_actor_label("SplineArch")
    spline = add_component(subobjects, arch, unreal.SplineMeshComponent)
    spline.set_static_mesh(cylinder)
    if pbr is not None:
        spline.set_material(0, pbr)
    spline.set_mobility(unreal.ComponentMobility.STATIC)
    spline.set_forward_axis(unreal.SplineMeshAxis.Z, True)
    spline.set_start_and_end(
        unreal.Vector(0, 0, 0), unreal.Vector(0, 0, 400),
        unreal.Vector(300, 0, 400), unreal.Vector(300, 0, 0), True)
    log("SplineArch: bent cylinder set")

    lod_actor = actor_sub.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(600.0, 400.0, 0.0))
    lod_actor.set_actor_label("LodMesh")
    lod_actor.static_mesh_component.set_static_mesh(two_lod)
    lod_actor.static_mesh_component.set_mobility(unreal.ComponentMobility.STATIC)

    decal = actor_sub.spawn_actor_from_class(
        unreal.DecalActor, unreal.Vector(-400.0, 400.0, 100.0))
    decal.set_actor_label("Decal_01")
    decal.set_actor_rotation(unreal.Rotator(0.0, 0.0, 30.0), False)
    decal_component = decal.get_component_by_class(unreal.DecalComponent)
    decal_component.set_decal_material(decal_material)
    decal_component.set_editor_property("decal_size",
                                        unreal.Vector(64.0, 128.0, 192.0))
    decal_component.set_editor_property("sort_order", 7)
    log("Decal_01: material + size + sort order set")

    camera = actor_sub.spawn_actor_from_class(
        unreal.CameraActor, unreal.Vector(0.0, 800.0, 300.0))
    camera.set_actor_label("Cam_Main")
    camera.set_actor_rotation(unreal.Rotator(-15.0, 0.0, -90.0), False)
    camera_component = camera.get_component_by_class(unreal.CameraComponent)
    camera_component.set_editor_property("field_of_view", 72.0)
    log("Cam_Main: fov 72 (horizontal)")

    if not level_sub.save_current_level():
        raise RuntimeError("failed to save " + MAP_PATH)
    log("level saved: " + MAP_PATH)


_status = "PASS"
try:
    main()
except Exception:
    unreal.log_error("[" + RESULT_TAG + "] FAILED")
    unreal.log_error(traceback.format_exc())
    _status = "FAIL"

print("RESULT: " + _status)
try:
    unreal.SystemLibrary.quit_editor()
except Exception:
    pass
if _status != "PASS":
    raise SystemExit(1)
