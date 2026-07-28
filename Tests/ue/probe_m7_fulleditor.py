"""
probe_m7_fulleditor.py — M7 round 4: everything again, in a FULL editor.

Every commandlet route is measured dead (no viewport for render targets, no
physics scene for traces, heightmap textures hidden from Python, silent
no-op FBX selection export). This probe runs under
`UnrealEditor.exe -ExecutePythonScript=...` -- a real editor session that
auto-exits -- and re-measures the two routes that a viewport unlocks:

  1. line traces (the height sampler + calibration mechanism);
  2. heightmap -> render target -> read_render_target_raw_pixel_area, and a
     displaced-plane build from it, spot-checked against the traces.

If these work, the export design is: commandlet sessions for everything as
today, and a full-editor session for levels that contain a Landscape.

Output (incremental): Tests/ue/results/probe_m7_fulleditor.txt
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m7_fulleditor.txt"
MAP_PATH = "/Game/EasternProvince/Levels/L_Showcase"

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
_handle = open(OUT_PATH, "w", buffering=1)


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


def trace_height(context, x, y):
    hit = unreal.SystemLibrary.line_trace_single(
        context, unreal.Vector(x, y, 100000.0), unreal.Vector(x, y, -100000.0),
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, [],
        unreal.DrawDebugTrace.NONE, True, unreal.LinearColor.RED,
        unreal.LinearColor.GREEN, 0.0)
    if hit is None:
        return None
    try:
        return float(hit.to_tuple()[4].z)
    except Exception:
        try:
            return float(hit.get_editor_property("location").z)
        except Exception:
            return None


def main():
    out("mode: commandlet? %r" % unreal.SystemLibrary.get_command_line().find("-run=") >= 0
        if False else "full editor (ExecutePythonScript)")
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load " + MAP_PATH)
    landscape = next(a for a in actor_sub.get_all_level_actors()
                     if a.get_class().get_name() == "Landscape")
    origin, extent = landscape.get_actor_bounds(False)
    out("bounds origin=(%.0f, %.0f, %.1f) extent=(%.0f, %.0f, %.1f)"
        % (origin.x, origin.y, origin.z, extent.x, extent.y, extent.z))

    out("")
    out("=== 1. line traces ===")
    trace_points = []
    for dx, dy in ((0, 0), (-10000, -10000), (10000, 10000),
                   (-15000, 12000), (12000, -15000)):
        x, y = origin.x + dx, origin.y + dy
        z = trace_height(landscape, x, y)
        trace_points.append((x, y, z))
        out("  trace (%.0f, %.0f) -> z=%r" % (x, y, z))

    out("")
    out("=== 2. heightmap -> RT -> pixels ===")
    rt = unreal.RenderingLibrary.create_render_target2d(
        landscape, 505, 505, unreal.TextureRenderTargetFormat.RTF_RGBA32F,
        unreal.LinearColor.BLACK, False)
    out("  RT: %r" % rt)
    worked = landscape.landscape_export_heightmap_to_render_target(rt, True, False)
    out("  export_heightmap(world units) -> %r" % worked)
    if worked:
        pixels = unreal.RenderingLibrary.read_render_target_raw_pixel_area(
            landscape, rt, 250, 250, 254, 254)
        out("  center 4x4 R: %r" % [round(p.r, 1) for p in (pixels or [])])
        # Compare an RT texel against a trace at the same world position.
        for x, y, z in trace_points:
            if z is None:
                continue
            # world -> texel: the RT spans the landscape bounds.
            u = (x - (origin.x - extent.x)) / (2.0 * extent.x)
            v = (y - (origin.y - extent.y)) / (2.0 * extent.y)
            px, py = int(u * 504), int(v * 504)
            if not (0 <= px <= 504 and 0 <= py <= 504):
                continue
            sample = unreal.RenderingLibrary.read_render_target_raw_pixel_area(
                landscape, rt, px, py, px + 1, py + 1)
            out("  RT(%d,%d).r=%.1f  vs trace z=%.1f"
                % (px, py, sample[0].r if sample else float("nan"), z))

        out("")
        out("=== 3. displaced plane from the RT ===")
        dyn = unreal.DynamicMesh()
        opts = unreal.GeometryScriptPrimitiveOptions()
        frame = unreal.Transform(location=unreal.Vector(origin.x, origin.y, 0.0))
        dyn = _unwrap(unreal.GeometryScript_Primitives.append_rectangle_xy(
            dyn, opts, frame, extent.x * 2.0, extent.y * 2.0, 252, 252))
        out("  plane triangles: %r"
            % unreal.GeometryScript_MeshQueries.get_num_triangle_i_ds(dyn))
        displace = unreal.GeometryScriptDisplaceFromTextureOptions()
        displace.set_editor_property("magnitude", 1.0)
        displace.set_editor_property("center", 0.0)
        selection = unreal.GeometryScriptMeshSelection()
        dyn = _unwrap(unreal.GeometryScript_MeshDeformers.apply_displace_from_texture_map(
            dyn, rt, selection, displace))
        box = unreal.GeometryScript_MeshQueries.get_mesh_bounding_box(dyn)
        out("  displaced bounds z: [%.1f, %.1f] (trace z range for comparison: %r)"
            % (box.min.z, box.max.z,
               [round(z, 1) for _x, _y, z in trace_points if z is not None]))


try:
    main()
    out("RESULT: PASS")
except Exception:
    out("FATAL: " + traceback.format_exc())
    out("RESULT: FAIL")
_handle.close()
# Full editor: quit explicitly so the process exits for the runner.
unreal.SystemLibrary.quit_editor()
