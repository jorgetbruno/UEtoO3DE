"""
probe_m8_skeletal.py — M8: skeletal import/export API mechanics, measured.

FULL EDITOR SESSION (-ExecutePythonScript), not a commandlet: the first
commandlet run died on `Assertion failed: MeshObject` (SkinnedMeshComponent
.cpp:4987, via MeshMergeUtilities) -- skeletal render objects do not exist
under -nullrhi, the same class of failure as M7's render-target route.
Writes INCREMENTALLY (the M7 lesson): every line hits disk before the next
call can crash the editor.

Questions, in dependency order:

  1. FBX skeletal IMPORT (the fixture canary path): AssetImportTask +
     FbxImportUI on the CC0 Quaternius Character.fbx -- what assets appear.
  2. Bone count APIs: SkeletalMeshComponent.get_num_bones() on a spawned
     SkeletalMeshActor.
  3. Animation properties: animation_mode, animation_data (5.8's home of
     anim_to_play -- the flat property does not exist, measured on the
     UndeadPack showcase maps), enable_root_motion, play length.
  4. Native FBX EXPORT of the skeletal mesh (AssetExportTask): what it
     writes (Deformer/AnimationStack counts), FbxExportOption properties.
  5. Native FBX export of ONE AnimSequence with export_preview_mesh False:
     skeleton + curves, no mesh geometry.
  6. Skeletal mesh material slots.

Output: Tests/ue/results/probe_m8_skeletal.txt (incremental)
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m8_skeletal.txt"
SOURCE_FBX = ("D:/Gamedev/quaternius/Platformer Game Kit - Dec 2021-"
              "20250429T022255Z-001/Platformer Game Kit - Dec 2021/"
              "Character/FBX/Character.fbx")
PROBE_DIR = "/Game/__M8Probe"
SCRATCH = "C:/Users/jorge/AppData/Local/Temp/claude/d--Gamedev-UEtoO3DE/" \
          "0fe0aba3-c585-4b52-86e4-35629323097a/scratchpad/m8"

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
_handle = open(OUT_PATH, "w")


def out(msg=""):
    _handle.write(str(msg) + "\n")
    _handle.flush()


def _count_in_file(path, needles):
    with open(path, "rb") as handle:
        blob = handle.read()
    return {needle: blob.count(needle.encode("ascii")) for needle in needles}


def _try_get(obj, name):
    try:
        return repr(obj.get_editor_property(name))
    except Exception as exc:
        return "<unreadable: %s>" % str(exc)[:60]


def main():
    os.makedirs(SCRATCH, exist_ok=True)

    out("=== 1. skeletal FBX import (fixture canary path) ===")
    if unreal.EditorAssetLibrary.does_directory_exist(PROBE_DIR):
        unreal.EditorAssetLibrary.delete_directory(PROBE_DIR)
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
    task.set_editor_property("destination_path", PROBE_DIR)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", False)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("options", ui)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    imported = task.get_editor_property("imported_object_paths") or []
    out("imported_object_paths: %d" % len(imported))
    for path in list(imported)[:8]:
        out("  " + str(path))

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_path(PROBE_DIR, recursive=True) or []
    skel_mesh = None
    anim_seqs = []
    by_class = {}
    for data in assets:
        cls = str(data.asset_class_path.asset_name)
        by_class[cls] = by_class.get(cls, 0) + 1
        if cls == "SkeletalMesh" and skel_mesh is None:
            skel_mesh = data.get_asset()
        if cls == "AnimSequence":
            anim_seqs.append(data.get_asset())
    out("assets by class: %s" % sorted(by_class.items()))
    if skel_mesh is None:
        raise RuntimeError("no SkeletalMesh imported")
    out("skeletal mesh: %s" % skel_mesh.get_path_name())
    out("anim sequences: %d -> %s" % (
        len(anim_seqs), sorted(a.get_name() for a in anim_seqs)[:20]))

    out("")
    out("=== 2. bone count APIs ===")
    skeleton = None
    try:
        skeleton = skel_mesh.get_editor_property("skeleton")
        out("skeleton asset: %s" % (skeleton.get_path_name() if skeleton else None))
    except Exception as exc:
        out("skeleton property unreadable: %s" % exc)

    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actor = actor_sub.spawn_actor_from_class(
        unreal.SkeletalMeshActor, unreal.Vector(0, 0, 0))
    out("spawned SkeletalMeshActor: %s" % (actor is not None))
    component = actor.get_component_by_class(unreal.SkeletalMeshComponent)
    out("component: %s" % (component.get_class().get_name() if component else None))
    try:
        component.set_editor_property("skeletal_mesh_asset", skel_mesh)
        out("set via 'skeletal_mesh_asset': ok")
    except Exception as exc:
        out("skeletal_mesh_asset failed (%s); trying set_skeletal_mesh" % str(exc)[:80])
        component.set_skeletal_mesh(skel_mesh)
    try:
        out("component.get_num_bones() = %d" % component.get_num_bones())
        out("bone 0..4: %s" % [str(component.get_bone_name(i))
                               for i in range(min(5, component.get_num_bones()))])
    except Exception as exc:
        out("get_num_bones failed: %s" % exc)

    out("")
    out("=== 3. animation properties (single-node playback anatomy) ===")
    for prop in ("animation_mode", "animation_data", "anim_class"):
        out("component.%s = %s" % (prop, _try_get(component, prop)))
    try:
        data = component.get_editor_property("animation_data")
        out("animation_data.anim_to_play = %s" % _try_get(data, "anim_to_play"))
        out("animation_data.looping      = %s" % _try_get(data, "looping"))
        out("animation_data.playing      = %s" % _try_get(data, "playing"))
    except Exception as exc:
        out("animation_data dig failed: %s" % str(exc)[:100])
    if anim_seqs:
        seq = sorted(anim_seqs, key=lambda a: a.get_name())[0]
        out("probe sequence: %s" % seq.get_name())
        for prop in ("enable_root_motion", "root_motion_root_lock",
                     "sequence_length"):
            out("  seq.%s = %s" % (prop, _try_get(seq, prop)))
        if hasattr(seq, "get_play_length"):
            try:
                out("  seq.get_play_length() = %s" % seq.get_play_length())
            except Exception as exc:
                out("  get_play_length failed: %s" % str(exc)[:80])
        try:
            component.set_editor_property(
                "animation_mode", unreal.AnimationMode.ANIMATION_SINGLE_NODE)
            data = component.get_editor_property("animation_data")
            data.set_editor_property("anim_to_play", seq)
            component.set_editor_property("animation_data", data)
            readback = component.get_editor_property("animation_data")
            out("  set SINGLE_NODE + animation_data.anim_to_play: readback %s"
                % _try_get(readback, "anim_to_play"))
        except Exception as exc:
            out("  single-node set failed: %s" % str(exc)[:120])

    out("")
    out("=== 4. native skeletal FBX export ===")
    options = unreal.FbxExportOption()
    for prop in ("ascii", "force_front_x_axis", "vertex_color",
                 "level_of_detail", "collision", "export_morph_targets",
                 "export_preview_mesh", "map_skeletal_motion_to_root",
                 "export_local_time", "fbx_export_compatibility",
                 "welded_vertices", "bake_material_inputs",
                 "bake_camera_and_light_animation", "bake_actor_animation"):
        out("FbxExportOption.%s = %s" % (prop, _try_get(options, prop)))
    options.set_editor_property("collision", False)
    options.set_editor_property("level_of_detail", False)

    mesh_fbx = SCRATCH + "/probe_character.fbx"
    export = unreal.AssetExportTask()
    export.object = skel_mesh
    export.filename = mesh_fbx
    export.automated = True
    export.replace_identical = True
    export.prompt = False
    export.options = options
    out("running skeletal mesh export...")
    ok = unreal.Exporter.run_asset_export_task(export)
    out("skeletal mesh export: %s -> %s (%s bytes)" % (
        ok, mesh_fbx,
        os.path.getsize(mesh_fbx) if os.path.exists(mesh_fbx) else "MISSING"))
    if os.path.exists(mesh_fbx):
        out("  content counts: %s" % _count_in_file(
            mesh_fbx, ["Deformer", "AnimationStack", "AnimationCurve",
                       "Geometry", "LimbNode"]))

    out("")
    out("=== 5. native AnimSequence FBX export (skeleton + curves) ===")
    if anim_seqs:
        seq = sorted(anim_seqs, key=lambda a: a.get_name())[0]
        anim_fbx = SCRATCH + "/probe_anim.fbx"
        anim_options = unreal.FbxExportOption()
        anim_options.set_editor_property("collision", False)
        anim_options.set_editor_property("level_of_detail", False)
        try:
            anim_options.set_editor_property("export_preview_mesh", False)
        except Exception as exc:
            out("export_preview_mesh not settable: %s" % str(exc)[:80])
        export = unreal.AssetExportTask()
        export.object = seq
        export.filename = anim_fbx
        export.automated = True
        export.replace_identical = True
        export.prompt = False
        export.options = anim_options
        out("running anim export...")
        ok = unreal.Exporter.run_asset_export_task(export)
        out("anim export (%s): %s -> %s (%s bytes)" % (
            seq.get_name(), ok, anim_fbx,
            os.path.getsize(anim_fbx) if os.path.exists(anim_fbx) else "MISSING"))
        if os.path.exists(anim_fbx):
            out("  content counts: %s" % _count_in_file(
                anim_fbx, ["Deformer", "AnimationStack", "AnimationCurveNode",
                           "Geometry", "LimbNode"]))

    out("")
    out("=== 6. skeletal material slots ===")
    try:
        materials = skel_mesh.get_editor_property("materials") or []
        out("materials: %d" % len(materials))
        for index, slot in enumerate(materials):
            name = "?"
            interface = None
            try:
                name = str(slot.get_editor_property("material_slot_name"))
                interface = slot.get_editor_property("material_interface")
            except Exception:
                pass
            out("  [%d] slot_name=%r material=%s" % (
                index, name,
                interface.get_name() if interface else None))
    except Exception as exc:
        out("materials unreadable: %s" % exc)

    actor_sub.destroy_actor(actor)
    unreal.EditorAssetLibrary.delete_directory(PROBE_DIR)


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
