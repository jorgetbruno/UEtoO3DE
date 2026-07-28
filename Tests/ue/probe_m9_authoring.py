"""
probe_m9_authoring.py -- M9: can the fixture AUTHOR each stretch feature from
Python, and what does each expose for export? FULL editor session.

  1. DECAL: spawn a DecalActor; read decal_size / sort_order / material;
     what does a decal material's get_editor_property("material_domain") say?
  2. SPLINE MESH: spawn an actor, add a SplineMeshComponent, set a bent
     start/end; does GeometryScript copy_mesh_from_component return the
     DEFORMED geometry (bounds must differ from the straight source)?
  3. LOD: StaticMeshEditorSubsystem.set_lod_from_static_mesh -- can a
     2-LOD mesh be built procedurally (cube LOD0 + cylinder LOD1)? What does
     get_num_lods / get_lod_count report? What does the existing exporter's
     RENDER_DATA LOD0 bake see?
  4. CAMERA: spawn a CameraActor; camera_component field_of_view (horizontal
     degrees in UE), aspect_ratio, projection mode, ortho width.

Output: Tests/ue/results/probe_m9_authoring.txt (incremental)
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m9_authoring.txt"
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
_handle = open(OUT_PATH, "w")


def out(msg=""):
    _handle.write(str(msg) + "\n")
    _handle.flush()


def _unwrap(result):
    if isinstance(result, tuple):
        for item in result:
            if not isinstance(item, unreal.GeometryScriptOutcomePins):
                return item
        return result[0]
    return result


def _try(obj, name):
    try:
        return repr(obj.get_editor_property(name))
    except Exception as exc:
        return "<unreadable: %s>" % str(exc)[:60]


def main():
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    out("=== 1. decal actor ===")
    decal = actor_sub.spawn_actor_from_class(
        unreal.DecalActor, unreal.Vector(0, 0, 100))
    component = decal.get_component_by_class(unreal.DecalComponent)
    out("spawned DecalActor, component=%s" % component.get_class().get_name())
    for prop in ("decal_size", "sort_order", "fade_screen_size",
                 "decal_material"):
        out("  component.%s = %s" % (prop, _try(component, prop)))
    material = None
    try:
        material = component.get_decal_material()
        out("  get_decal_material() = %s" % (material.get_name() if material else None))
    except Exception as exc:
        out("  get_decal_material failed: %s" % str(exc)[:80])
    if material is not None:
        out("  material_domain: %s" % _try(material, "material_domain"))

    out("")
    out("=== 2. spline mesh component: does the bake see the deformation? ===")
    # AActor.add_component_by_class is not exposed in 5.8 (measured, twice);
    # SubobjectDataSubsystem is the route that works (add_bp_canary.py).
    cylinder = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder")
    holder = actor_sub.spawn_actor_from_class(unreal.Actor, unreal.Vector(0, 0, 0))
    holder.set_actor_label("M9_SplineProbe")
    subobjects = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = subobjects.k2_gather_subobject_data_for_instance(holder)
    params = unreal.AddNewSubobjectParams()
    params.set_editor_property("parent_handle", handles[0])
    params.set_editor_property("new_class", unreal.SplineMeshComponent)
    result = subobjects.add_new_subobject(params)
    handle = result[0] if isinstance(result, tuple) else result
    data_result = subobjects.k2_find_subobject_data_from_handle(handle)
    data = data_result[0] if isinstance(data_result, tuple) else data_result
    spline_mesh = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
    out("added SplineMeshComponent: %r" % spline_mesh)
    spline_mesh.set_static_mesh(cylinder)
    spline_mesh.set_forward_axis(unreal.SplineMeshAxis.Z, True)
    spline_mesh.set_start_and_end(
        unreal.Vector(0, 0, 0), unreal.Vector(0, 0, 400),
        unreal.Vector(300, 0, 400), unreal.Vector(300, 0, 0), True)
    out("start/end set: straight cylinder along Z, end bent +300 in X")

    source_box = cylinder.get_bounding_box()
    out("source bounds: %s .. %s" % (source_box.min, source_box.max))
    dyn = unreal.DynamicMesh()
    options = unreal.GeometryScriptCopyMeshFromComponentOptions()
    result = unreal.GeometryScript_SceneUtils.copy_mesh_from_component(
        spline_mesh, dyn, options, True)
    dyn = _unwrap(result)
    if dyn is None:
        out("copy_mesh_from_component returned NO mesh")
    else:
        tri_result = unreal.GeometryScript_MeshQueries.get_num_triangle_i_ds(dyn)
        out("copied triangles: %s" % tri_result)
        bounds = unreal.GeometryScript_MeshQueries.get_mesh_bounding_box(dyn)
        out("baked bounds: %s" % bounds)

    out("")
    out("=== 2b. instanced static mesh authoring (the foliage shape) ===")
    # The Ghoul map's InstancedFoliageActor is EMPTY (0 instances, measured),
    # so Fixture_02 authors its own ISMC and the exporter expands instances.
    params = unreal.AddNewSubobjectParams()
    params.set_editor_property("parent_handle", handles[0])
    params.set_editor_property("new_class", unreal.InstancedStaticMeshComponent)
    result = subobjects.add_new_subobject(params)
    handle = result[0] if isinstance(result, tuple) else result
    data_result = subobjects.k2_find_subobject_data_from_handle(handle)
    data = data_result[0] if isinstance(data_result, tuple) else data_result
    ismc = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
    out("added InstancedStaticMeshComponent: %r" % ismc)
    ismc.set_static_mesh(cylinder)
    for index in range(3):
        ismc.add_instance(unreal.Transform(
            location=unreal.Vector(200.0 * index, 100.0, 0.0)), False)
    out("instance count after add_instance x3: %d" % ismc.get_instance_count())
    got = ismc.get_instance_transform(1, True)
    transform = got[1] if isinstance(got, tuple) else got
    out("instance 1 world loc: %s" % transform.translation)

    out("")
    out("=== 3. procedural LODs ===")
    subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    out("StaticMeshEditorSubsystem: %s" % (subsystem is not None))
    cube = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube")
    out("cube get_num_lods: %s" % cube.get_num_lods())
    temp_path = "/Game/__M9Probe/LodProbe"
    if unreal.EditorAssetLibrary.does_asset_exist(temp_path):
        unreal.EditorAssetLibrary.delete_asset(temp_path)
    duplicated = unreal.EditorAssetLibrary.duplicate_asset(
        "/Engine/BasicShapes/Cube", temp_path)
    out("duplicated cube: %s" % (duplicated is not None))
    if duplicated is not None and subsystem is not None:
        try:
            added = subsystem.set_lod_from_static_mesh(
                duplicated, 1, cylinder, 0, True)
            out("set_lod_from_static_mesh -> LOD index %s; num_lods now %s"
                % (added, duplicated.get_num_lods()))
        except Exception as exc:
            out("set_lod_from_static_mesh failed: %s" % str(exc)[:120])

    out("")
    out("=== 4. camera actor ===")
    camera = actor_sub.spawn_actor_from_class(
        unreal.CameraActor, unreal.Vector(0, 0, 200))
    camera_component = camera.get_component_by_class(unreal.CameraComponent)
    out("spawned CameraActor, component=%s" % camera_component.get_class().get_name())
    for prop in ("field_of_view", "aspect_ratio", "constrain_aspect_ratio",
                 "projection_mode", "ortho_width", "post_process_blend_weight"):
        out("  camera.%s = %s" % (prop, _try(camera_component, prop)))

    # cleanup
    for actor in (decal, holder, camera):
        actor_sub.destroy_actor(actor)
    if unreal.EditorAssetLibrary.does_directory_exist("/Game/__M9Probe"):
        unreal.EditorAssetLibrary.delete_directory("/Game/__M9Probe")


status = "PASS"
try:
    main()
except Exception:
    out("FATAL: " + traceback.format_exc())
    status = "FAIL"

out("RESULT: " + status)
_handle.close()
print("RESULT: " + status)
try:
    unreal.SystemLibrary.quit_editor()
except Exception:
    pass
if status != "PASS":
    raise SystemExit(1)
