"""
rebuild_letter_f.py — rewrite /Game/Meshes/SM_LetterF in place (M1 fixture fix).

Why this exists as its own script: the mesh baked in M0 was mirror-symmetric in
X and Y. `GeometryScriptPrimitiveOriginMode.BASE` centers a box in X and Y, so
the three boxes stacked concentrically instead of forming an F -- measured
bounds x[-50,50] y[-12.5,12.5] and centroid [0, 0, 132.5]. A level mirrored
about X or Y would therefore have passed every assertion the canary exists to
fail, which is the exact blindness plan M0 calls out.

`build_fixture_01.py` carries the corrected geometry, but it cannot be re-run
against an existing project: it deletes and recreates the textures, and the
delete fails while the materials still reference them. Copying the new mesh
into the EXISTING StaticMesh asset instead keeps every reference intact -- the
level, its actors and their entity ids are untouched, so only the mesh changes.

Run:  run_ue_python.bat rebuild_letter_f.py
Then: run_ue_python.bat export_sm_letterf.py   (refreshes the S0.2 reference)
"""

import traceback

import unreal

SM_LETTERF_PATH = "/Game/Meshes/SM_LetterF"
RESULT_TAG = "REBUILD_LETTER_F"


def log(message):
    unreal.log("[" + RESULT_TAG + "] " + str(message))


def _unwrap(result):
    if isinstance(result, tuple):
        for item in result:
            if not isinstance(item, unreal.GeometryScriptOutcomePins):
                return item
        return result[0]
    return result


def build_mesh():
    dyn = unreal.DynamicMesh()
    opts = unreal.GeometryScriptPrimitiveOptions()
    origin = unreal.GeometryScriptPrimitiveOriginMode.BASE

    def box(loc_x, loc_y, loc_z, dim_x, dim_y, dim_z):
        nonlocal dyn
        xform = unreal.Transform(location=unreal.Vector(loc_x, loc_y, loc_z))
        dyn = dyn.append_box(opts, xform, dim_x, dim_y, dim_z, 0, 0, 0, origin)

    # Kept identical to build_fixture_01.build_letter_f(); see its docstring.
    box(-37.5, 0.0, 0.0,    25.0, 25.0, 200.0)   # stem       X -50..-25
    box(0.0, 0.0, 170.0,   100.0, 25.0, 30.0)    # top arm    X -50.. 50
    box(-12.5, 0.0, 100.0,  75.0, 25.0, 25.0)    # middle arm X -50.. 25
    box(-37.5, 25.0, 175.0, 25.0, 25.0, 25.0)    # side nub   Y  12.5..37.5
    return dyn


def vertex_positions(dyn):
    positions = dyn.get_all_vertex_positions(False)
    for item in (positions if isinstance(positions, tuple) else (positions,)):
        if isinstance(item, unreal.GeometryScriptVectorList):
            return unreal.GeometryScript_List.convert_vector_list_to_array(item)
    raise RuntimeError("get_all_vertex_positions returned no vector list")


def check_asymmetry(dyn):
    """Assert the mesh is asymmetric about all three planes.

    Bounds alone prove nothing -- the top arm spans the full width, so the X
    bounds are symmetric by design. What matters is that the vertex centroid
    sits off the center of the bounding box on every axis: that is the quantity
    a mirror flips, and it is what M2's mirror check compares.
    """
    positions = vertex_positions(dyn)
    if not positions:
        raise RuntimeError("mesh has no vertices")

    offsets = {}
    for axis in ("x", "y", "z"):
        values = [getattr(p, axis) for p in positions]
        centroid = sum(values) / float(len(values))
        center = (min(values) + max(values)) * 0.5
        offsets[axis] = centroid - center
        log("  %s: bounds [%.3f, %.3f] center %.3f centroid %.3f offset %.3f cm"
            % (axis.upper(), min(values), max(values), center, centroid, offsets[axis]))

    for axis, offset in offsets.items():
        if abs(offset) < 1.0:
            raise RuntimeError(
                "centroid offset on %s is %.4f cm; the mesh is effectively "
                "symmetric about that plane and the mirror canary would be "
                "blind on it" % (axis.upper(), offset))


def main():
    mesh_asset = unreal.EditorAssetLibrary.load_asset(SM_LETTERF_PATH)
    if mesh_asset is None:
        raise RuntimeError("asset not found: " + SM_LETTERF_PATH)

    before = mesh_asset.get_bounding_box()
    log("before: min=%s max=%s" % (before.min, before.max))

    dyn = build_mesh()
    log("asymmetry check (asset space, cm):")
    check_asymmetry(dyn)

    options = unreal.GeometryScriptCopyMeshToAssetOptions()
    options.set_editor_property("enable_recompute_normals", True)
    options.set_editor_property("enable_recompute_tangents", True)
    # Keep the existing material slot; the fixture actor relies on slot 0.
    options.set_editor_property("replace_materials", False)
    target_lod = unreal.GeometryScriptMeshWriteLOD()

    result = unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh(
        dyn, mesh_asset, options, target_lod)
    _unwrap(result)

    if not unreal.EditorAssetLibrary.save_asset(SM_LETTERF_PATH):
        raise RuntimeError("failed to save " + SM_LETTERF_PATH)

    mesh_asset = unreal.EditorAssetLibrary.load_asset(SM_LETTERF_PATH)
    after = mesh_asset.get_bounding_box()
    log("after:  min=%s max=%s" % (after.min, after.max))
    log("mesh is asymmetric about X, Y and Z")


try:
    main()
except Exception:
    unreal.log_error("[" + RESULT_TAG + "] FAILED")
    unreal.log_error(traceback.format_exc())
    print("RESULT: FAIL")
    raise

print("RESULT: PASS")
