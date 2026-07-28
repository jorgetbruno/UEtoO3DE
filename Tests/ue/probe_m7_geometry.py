"""
probe_m7_geometry.py — M7 round 2b: terrain geometry without a render target.

Round 2a died at `Assertion failed: RenderTarget` (Canvas.cpp): the
heightmap-to-render-target route needs a real viewport and is DEAD in a
commandlet, with or without an RHI. This rerun writes its findings
INCREMENTALLY (the crash ate round 2a's successful section-1 results) and
probes the two viewport-free routes:

  1. GeometryScript copy_collision_meshes_from_object on the landscape's
     heightfield collision -- triangles here mean the whole existing bake
     pipeline applies unchanged;
  2. line traces (SystemLibrary.line_trace_single) as the height sampler /
     calibration mechanism, plus a timing estimate for a full grid.

Output: Tests/ue/results/probe_m7_geometry.txt (written as it goes)
"""

import os
import time
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m7_geometry.txt"
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


def trace_height(world_context, x, y):
    hit = unreal.SystemLibrary.line_trace_single(
        world_context, unreal.Vector(x, y, 100000.0), unreal.Vector(x, y, -100000.0),
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
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load " + MAP_PATH)

    landscape = None
    for actor in actor_sub.get_all_level_actors():
        if actor.get_class().get_name() == "Landscape":
            landscape = actor
            break
    if landscape is None:
        raise RuntimeError("no Landscape")
    origin, extent = landscape.get_actor_bounds(False)
    out("bounds origin=(%.0f, %.0f, %.1f) extent=(%.0f, %.0f, %.1f)"
        % (origin.x, origin.y, origin.z, extent.x, extent.y, extent.z))

    out("")
    out("=== 1. copy_collision_meshes_from_object ===")
    collision = (landscape.get_components_by_class(
        unreal.LandscapeHeightfieldCollisionComponent) or [None])[0]
    for label, target in (("Landscape actor", landscape),
                          ("collision component", collision)):
        if target is None:
            continue
        try:
            dyn = unreal.DynamicMesh()
            result = unreal.GeometryScript_SceneUtils.copy_collision_meshes_from_object(
                target, dyn, False, False)
            dyn2 = _unwrap(result)
            count = unreal.GeometryScript_MeshQueries.get_num_triangle_i_ds(dyn2) \
                if dyn2 is not None else None
            out("  %-20s -> triangles: %r" % (label, count))
            if count:
                box = unreal.GeometryScript_MeshQueries.get_mesh_bounding_box(dyn2)
                out("     dyn bounds: min=(%.0f,%.0f,%.1f) max=(%.0f,%.0f,%.1f)"
                    % (box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z))
        except Exception as exc:
            out("  %-20s RAISED %s: %s" % (label, type(exc).__name__, str(exc)[:160]))

    out("")
    out("=== 2. line traces (sampler + calibration) ===")
    hits = 0
    started = time.time()
    for dx, dy in ((0, 0), (-10000, -10000), (10000, 10000),
                   (-15000, 12000), (12000, -15000)):
        x, y = origin.x + dx, origin.y + dy
        z = trace_height(landscape, x, y)
        hits += 1 if z is not None else 0
        out("  trace (%.0f, %.0f) -> z=%r" % (x, y, z))
    out("  5 traces in %.3f s, %d hit" % (time.time() - started, hits))

    started = time.time()
    count = 0
    for i in range(500):
        x = origin.x - extent.x + (2.0 * extent.x) * (i % 25) / 24.0
        y = origin.y - extent.y + (2.0 * extent.y) * (i // 25) / 19.0
        if trace_height(landscape, x, y) is not None:
            count += 1
    elapsed = time.time() - started
    out("  500-trace batch: %.2f s (%.0f/s), %d hit -> full 505x505 grid ~%.0f s"
        % (elapsed, 500.0 / elapsed if elapsed else 0, count,
           (505 * 505) / (500.0 / elapsed) if elapsed else -1))


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
