"""
probe_m7_landscape.py — M7: what UE exposes for Landscape, measured in place.

Runs against EasternProvince / L_Showcase (the level with the real Landscape).
Questions, in the order the design depends on them:

  1. anatomy: proxy class, component counts, section/quad sizes, transform,
     bounds, landscape material (and whether the M4 classifier can do
     anything with it);
  2. geometry routes, best first:
       a. GeometryScript CopyMeshFromComponent on a LandscapeComponent or the
          heightfield collision component -- if that yields triangles, the
          ENTIRE existing Lane B bake pipeline applies unchanged;
       b. get_height_at_location sampling (grid rebuild fallback);
  3. heightmap PNG: landscape_export_heightmap_to_render_target +
     ImageWrite export (the plan's "for later" deliverable).

Output: Tests/ue/results/probe_m7_landscape.txt
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m7_landscape.txt"
MAP_PATH = "/Game/EasternProvince/Levels/L_Showcase"

_lines = []


def out(msg=""):
    _lines.append(str(msg))


def _unwrap(result):
    if isinstance(result, tuple):
        for item in result:
            if not isinstance(item, unreal.GeometryScriptOutcomePins):
                return item
        return result[0]
    return result


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load " + MAP_PATH)

    landscape = None
    for actor in actor_sub.get_all_level_actors():
        if actor.get_class().get_name() in ("Landscape", "LandscapeStreamingProxy"):
            landscape = actor
            break
    if landscape is None:
        raise RuntimeError("no Landscape in " + MAP_PATH)

    out("=== 1. anatomy ===")
    out("class: %s  label: %s" % (landscape.get_class().get_name(),
                                  landscape.get_actor_label()))
    out("transform: loc=%s scale=%s" % (landscape.get_actor_location(),
                                        landscape.get_actor_scale3d()))
    origin, extent = landscape.get_actor_bounds(False)
    out("bounds: origin=%s extent=%s" % (origin, extent))
    for prop in ("landscape_material", "component_size_quads",
                 "subsection_size_quads", "num_subsections",
                 "landscape_hole_material", "nanite_lod_index"):
        try:
            out("  %-24s = %r" % (prop, landscape.get_editor_property(prop)))
        except Exception as exc:
            out("  %-24s   MISSING (%s)" % (prop, type(exc).__name__))

    render_components = list(landscape.get_components_by_class(
        unreal.LandscapeComponent) or [])
    collision_components = list(landscape.get_components_by_class(
        unreal.LandscapeHeightfieldCollisionComponent) or [])
    out("render components: %d, collision components: %d"
        % (len(render_components), len(collision_components)))

    material = None
    try:
        material = landscape.get_editor_property("landscape_material")
    except Exception:
        pass
    if material is not None:
        out("landscape material: %s (%s)" % (material.get_name(),
                                             material.get_class().get_name()))

    out("")
    out("=== 2a. GeometryScript CopyMeshFromComponent ===")
    scene_utils = [n for n in dir(unreal.GeometryScript_SceneUtils)
                   if "copy" in n.lower()]
    out("SceneUtils copy functions: %r" % scene_utils)
    for label, component in (("LandscapeComponent", render_components[0] if render_components else None),
                             ("HeightfieldCollision", collision_components[0] if collision_components else None)):
        if component is None:
            continue
        try:
            dyn = unreal.DynamicMesh()
            options = unreal.GeometryScriptCopyMeshFromComponentOptions()
            result = unreal.GeometryScript_SceneUtils.copy_mesh_from_component(
                component, dyn, options, False)
            dyn2 = _unwrap(result)
            count = unreal.GeometryScript_MeshQueries.get_num_triangle_i_ds(dyn2) \
                if dyn2 is not None else None
            out("  %-22s -> triangles: %r" % (label, count))
        except Exception as exc:
            out("  %-22s RAISED %s: %s" % (label, type(exc).__name__, str(exc)[:140]))

    out("")
    out("=== 2b. get_height_at_location ===")
    for holder in (landscape,):
        for name in ("get_height_at_location",):
            method = getattr(holder, name, None)
            if method is None:
                out("  %s: MISSING" % name)
                continue
            try:
                sample = method(unreal.Vector(0.0, 0.0, 0.0))
                out("  %s((0,0,0)) -> %r" % (name, sample))
            except Exception as exc:
                out("  %s RAISED %s: %s" % (name, type(exc).__name__, str(exc)[:120]))

    out("")
    out("=== 3. heightmap export ===")
    method = getattr(landscape, "landscape_export_heightmap_to_render_target", None)
    if method is None:
        out("  landscape_export_heightmap_to_render_target: MISSING")
    else:
        try:
            target = unreal.RenderTarget2D()
            # A transient RT needs explicit size before use.
            target.set_editor_property("size_x", 512)
            target.set_editor_property("size_y", 512)
            worked = method(target, True, False)
            out("  export_heightmap(512x512, world units) -> %r" % worked)
        except Exception as exc:
            out("  export RAISED %s: %s" % (type(exc).__name__, str(exc)[:140]))

    out("")
    out("=== 4. classifier vs landscape material ===")
    if material is not None:
        import sys
        sys.path.insert(0, "D:/Gamedev/UEtoO3DE/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python")
        from ueo3de import material_export, naming
        from ueo3de.warnings import Warnings

        class _Registry:
            def claim(self, path):
                return naming.sanitize_path(path)
        bank = material_export.TextureBank(naming.PathRegistry())
        warnings = Warnings()
        data = material_export.build_material_data(material, bank, warnings)
        out("  material_data: %s" % ("None" if data is None else
                                     sorted((data.get("properties") or {}).keys())))
        for record in warnings.records():
            out("    [%s] %s - %s" % (record["code"], record["subject"],
                                      record["detail"][:110]))


status = "PASS"
try:
    main()
except Exception:
    out("FATAL: " + traceback.format_exc())
    status = "FAIL"

_lines.append("RESULT: " + status)
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w") as handle:
    handle.write("\n".join(_lines) + "\n")
print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)
