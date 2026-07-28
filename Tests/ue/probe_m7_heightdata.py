"""
probe_m7_heightdata.py — M7 round 3: the two CPU-only terrain routes.

Dead so far: CopyMeshFromComponent (0 tris), copy_collision_meshes_from_object
(0 tris), heightmap->RT (needs a viewport; crashes commandlets), line traces
(no physics scene in commandlets). What is left is what UE itself stores:

  1. the per-component HEIGHTMAP TEXTURES. Height is a uint16 split across
     the R and G bytes of a BGRA8 texture; if `heightmap_texture`,
     `section_base_x/y` and `heightmap_scale_bias` are readable, and the
     texture EXPORTS to TGA via AssetExportTask (pure CPU), the exporter can
     decode exact source heights headlessly. Decode formula sanity-checked
     against the actor's Z bounds;
  2. a LevelExporterFBX export of the selected landscape actor (UE's own
     "export level to FBX" tessellates landscapes) -- if that runs in a
     commandlet, the FBX can be imported back as a temp static mesh and fed
     through the EXISTING bake.

Output (incremental): Tests/ue/results/probe_m7_heightdata.txt
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m7_heightdata.txt"
SCRATCH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/m7_scratch"
MAP_PATH = "/Game/EasternProvince/Levels/L_Showcase"

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
os.makedirs(SCRATCH, exist_ok=True)
_handle = open(OUT_PATH, "w", buffering=1)


def out(msg=""):
    _handle.write(str(msg) + "\n")
    _handle.flush()


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load " + MAP_PATH)
    landscape = next(a for a in actor_sub.get_all_level_actors()
                     if a.get_class().get_name() == "Landscape")
    components = list(landscape.get_components_by_class(unreal.LandscapeComponent))
    out("components: %d" % len(components))

    out("")
    out("=== 1a. component height data properties ===")
    component = components[0]
    for prop in ("heightmap_texture", "section_base_x", "section_base_y",
                 "heightmap_scale_bias", "component_size_quads",
                 "section_size_quads", "num_subsections"):
        try:
            value = component.get_editor_property(prop)
            out("  %-24s = %r" % (prop, value))
        except Exception as exc:
            out("  %-24s   MISSING (%s)" % (prop, type(exc).__name__))

    out("")
    out("=== 1b. heightmap texture export to TGA ===")
    try:
        texture = component.get_editor_property("heightmap_texture")
        out("  texture: %r size=%sx%s format=%s"
            % (texture.get_name(),
               texture.blueprint_get_size_x(), texture.blueprint_get_size_y(),
               texture.get_editor_property("compression_settings")))
        shared = len({str(c.get_editor_property("heightmap_texture").get_name())
                      for c in components})
        out("  distinct heightmap textures across %d components: %d"
            % (len(components), shared))

        task = unreal.AssetExportTask()
        task.object = texture
        task.filename = SCRATCH + "/heightmap0.tga"
        task.automated = True
        task.replace_identical = True
        task.prompt = False
        worked = unreal.Exporter.run_asset_export_task(task)
        exists = os.path.exists(task.filename)
        out("  TGA export -> %r, file exists: %r, bytes: %s"
            % (worked, exists, os.path.getsize(task.filename) if exists else "-"))
        if exists:
            import sys
            sys.path.insert(0, "D:/Gamedev/UEtoO3DE/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python")
            from ueo3de import tga
            image = tga.read(task.filename)
            out("  decoded TGA: %sx%s bpp=%s"
                % (image.get("width"), image.get("height"), image.get("bpp")))
            pixels = image["pixels"]
            bpp = image["bpp"] // 8
            width = image["width"]

            def height_at(px, py):
                base = (py * width + px) * bpp
                blue, green, red = pixels[base], pixels[base + 1], pixels[base + 2]
                return (red << 8) | green
            samples = [height_at(x, y) for x, y in
                       ((0, 0), (width // 2, width // 2), (width - 1, width - 1))]
            out("  u16 height samples: %r" % samples)
            zs = [(h - 32768) / 128.0 for h in samples]
            out("  decoded local z (x scaleZ=100 -> cm): %r"
                % [round(z * 100.0, 1) for z in zs])
            out("  actor z bounds for comparison: origin.z=-443.4 extent=706.6 "
                "-> world z range [-1150.0, 263.2]")
    except Exception as exc:
        out("  RAISED %s: %s" % (type(exc).__name__, str(exc)[:200]))

    out("")
    out("=== 2. LevelExporterFBX on the selected landscape ===")
    try:
        actor_sub.set_selected_level_actors([landscape])
        world = unreal.EditorLevelLibrary.get_editor_world()
        task = unreal.AssetExportTask()
        task.object = world
        task.filename = SCRATCH + "/landscape_level.fbx"
        task.automated = True
        task.replace_identical = True
        task.prompt = False
        task.selected = True
        options = unreal.FbxExportOption()
        options.set_editor_property("collision", False)
        options.set_editor_property("level_of_detail", False)
        task.options = options
        worked = unreal.Exporter.run_asset_export_task(task)
        exists = os.path.exists(task.filename)
        out("  FBX export -> %r, file exists: %r, bytes: %s"
            % (worked, exists, os.path.getsize(task.filename) if exists else "-"))
        if exists:
            import sys
            sys.path.insert(0, "D:/Gamedev/UEtoO3DE/Tests/lib")
            import fbx_reader
            stats = fbx_reader.vertex_stats(task.filename)
            out("  FBX vertices: %d, bounds min=%s max=%s"
                % (stats["count"], [round(v) for v in stats["min"]],
                   [round(v) for v in stats["max"]]))
    except Exception as exc:
        out("  RAISED %s: %s" % (type(exc).__name__, str(exc)[:200]))


try:
    main()
    out("RESULT: PASS")
    print("RESULT: PASS")
except Exception:
    out("FATAL: " + traceback.format_exc())
    out("RESULT: FAIL")
    print("RESULT: FAIL")
    _handle.close()
    raise SystemExit(1)
_handle.close()
