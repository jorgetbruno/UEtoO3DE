"""
add_two_tone.py — add the SM_TwoTone per-slot canary to the EXISTING fixture.

`build_fixture_01.py` cannot re-run against an existing project (it deletes
textures the materials still reference), so, like `rebuild_letter_f.py`, this
adds just the new piece in place: the two-slot mesh asset and one actor in
Fixture_01. Geometry and materials are kept identical to
`build_fixture_01.build_two_tone()`; keep the two in sync by hand.

Idempotent: deletes/rebuilds the mesh asset and replaces any existing
SM_TwoTone actor.

Run:  run_ue_python.bat add_two_tone.py
"""

import traceback

import unreal

MAP_PATH = "/Game/Maps/Fixture_01"
SM_TWOTONE_PATH = "/Game/Meshes/SM_TwoTone"
MAT_PBR = "/Game/Materials/M_Fixture_PBR"
MAT_ORM = "/Game/Materials/M_Fixture_ORM"
MAT_MASKED = "/Game/Materials/M_Fixture_Masked"
RESULT_TAG = "ADD_TWO_TONE"


def log(message):
    unreal.log("[" + RESULT_TAG + "] " + str(message))


def _unwrap(result):
    if isinstance(result, tuple):
        for item in result:
            if not isinstance(item, unreal.GeometryScriptOutcomePins):
                return item
        return result[0]
    return result


def build_asset():
    if unreal.EditorAssetLibrary.does_asset_exist(SM_TWOTONE_PATH):
        unreal.EditorAssetLibrary.delete_asset(SM_TWOTONE_PATH)

    dyn = unreal.DynamicMesh()
    dyn.enable_material_i_ds()
    opts = unreal.GeometryScriptPrimitiveOptions()
    origin = unreal.GeometryScriptPrimitiveOriginMode.BASE

    xform = unreal.Transform(location=unreal.Vector(0.0, 0.0, 0.0))
    dyn = dyn.append_box(opts, xform, 100.0, 50.0, 100.0, 0, 0, 0, origin)
    base_triangles = unreal.GeometryScript_MeshQueries.get_num_triangle_i_ds(dyn)
    xform = unreal.Transform(location=unreal.Vector(0.0, 0.0, 150.0))
    dyn = dyn.append_box(opts, xform, 50.0, 50.0, 50.0, 0, 0, 0, origin)
    total_triangles = unreal.GeometryScript_MeshQueries.get_num_triangle_i_ds(dyn)
    for triangle in range(base_triangles, total_triangles):
        dyn.set_triangle_material_id(triangle, 1)

    create_opts = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    mesh = _unwrap(unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        dyn, SM_TWOTONE_PATH, create_opts))
    if mesh is None:
        raise RuntimeError("CreateNewStaticMeshAssetFromMesh returned no mesh")

    mat_pbr = unreal.EditorAssetLibrary.load_asset(MAT_PBR)
    mat_orm = unreal.EditorAssetLibrary.load_asset(MAT_ORM)
    if mat_pbr is None or mat_orm is None:
        raise RuntimeError("fixture materials missing; run build_fixture_01 first")
    slots = []
    for slot_name, material in (("Base", mat_pbr), ("Top", mat_orm)):
        entry = unreal.StaticMaterial()
        entry.set_editor_property("material_slot_name", slot_name)
        entry.set_editor_property("material_interface", material)
        slots.append(entry)
    mesh.set_editor_property("static_materials", slots)

    if not unreal.EditorAssetLibrary.save_asset(SM_TWOTONE_PATH):
        raise RuntimeError("failed to save " + SM_TWOTONE_PATH)

    read_back = mesh.get_editor_property("static_materials")
    log("slots: %r" % [(str(s.get_editor_property("material_slot_name")),
                        s.get_editor_property("material_interface").get_name())
                       for s in read_back])
    if len(read_back) != 2:
        raise RuntimeError("expected 2 slots, got %d" % len(read_back))
    return mesh


def place_actor(mesh):
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load level " + MAP_PATH)

    for actor in actor_sub.get_all_level_actors():
        if actor.get_actor_label() == "SM_TwoTone":
            actor_sub.destroy_actor(actor)

    actor = actor_sub.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(1800.0, 0.0, 0.0), unreal.Rotator(0.0, 0.0, 0.0))
    if actor is None:
        raise RuntimeError("failed to spawn SM_TwoTone actor")
    actor.set_actor_label("SM_TwoTone")
    smc = actor.static_mesh_component
    smc.set_static_mesh(mesh)
    smc.set_mobility(unreal.ComponentMobility.STATIC)

    # Slot 1 is OVERRIDDEN on the component, so the effective material differs
    # from the mesh asset's own. This is how one tree mesh becomes many species
    # in a real level, and it is the case that broke per-slot assignment on
    # L_Showcase: the baked FBX carries the ASSET's material names, so matching
    # a slot by its *effective* material name never finds it. Without an
    # override here the fixture cannot tell the two names apart.
    override = unreal.EditorAssetLibrary.load_asset(MAT_MASKED)
    if override is None:
        raise RuntimeError("missing " + MAT_MASKED)
    smc.set_material(1, override)
    log("slot 1 overridden with %s (asset slot 1 is M_Fixture_ORM)"
        % override.get_name())

    if not level_sub.save_current_level():
        raise RuntimeError("failed to save level " + MAP_PATH)
    log("actor placed at (1800, 0, 0) and level saved")


def main():
    mesh = build_asset()
    place_actor(mesh)


try:
    main()
except Exception:
    unreal.log_error("[" + RESULT_TAG + "] FAILED")
    unreal.log_error(traceback.format_exc())
    print("RESULT: FAIL")
    raise

print("RESULT: PASS")
