"""
probe_m2_mirror2.py — M2 reconnaissance round 2: is the winding actually fixed?

Round 1 showed `scale_mesh(1, -1, 1)` mirrors the centroid and leaves
`get_mesh_volume_area` unchanged at (46250, 262500) -- which matches the F
mesh's hand-computed surface area and summed box volume exactly. But an
unchanged volume only proves winding was preserved if that volume is SIGNED.
If the function returns an absolute value, the reading is worthless.

So test winding directly and independently: for every triangle, take its face
normal and the vector from the mesh centroid to the triangle centroid, and
count how many point the same way. A collection of closed shells wound outward
scores ~100%. If a reflection inverted the winding and nothing fixed it, the
same mesh scores ~0%.

Run:  run_ue_python.bat probe_m2_mirror2.py
Output: Tests/ue/results/probe_m2_mirror2.txt
"""

import os
import traceback

import unreal

MESH_PATH = "/Game/Meshes/SM_LetterF.SM_LetterF"
OUT_DIR = "D:/Gamedev/UEtoO3DE/Tests/ue/results"
OUT_PATH = OUT_DIR + "/probe_m2_mirror2.txt"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_M2B] " + str(msg))


def _unwrap(result):
    if isinstance(result, tuple):
        for item in result:
            if not isinstance(item, unreal.GeometryScriptOutcomePins):
                return item
        return result[0]
    return result


def load_dynamic_mesh():
    mesh = unreal.EditorAssetLibrary.load_asset(MESH_PATH)
    dyn = unreal.DynamicMesh()
    options = unreal.GeometryScriptCopyMeshFromAssetOptions()
    lod = unreal.GeometryScriptMeshReadLOD()
    lod.set_editor_property("lod_type", unreal.GeometryScriptLODType.RENDER_DATA)
    return _unwrap(unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        mesh, dyn, options, lod))


def positions(dyn):
    result = dyn.get_all_vertex_positions(False)
    for item in (result if isinstance(result, tuple) else (result,)):
        if isinstance(item, unreal.GeometryScriptVectorList):
            return unreal.GeometryScript_List.convert_vector_list_to_array(item)
    raise RuntimeError("no vector list")


def face_normals(dyn):
    """Face normal per triangle id."""
    queries = unreal.GeometryScript_MeshQueries
    # UE's Python binding splits "IDs" into "i_ds".
    count = queries.get_num_triangle_i_ds(dyn)
    normals = []
    for triangle_id in range(count):
        result = queries.get_triangle_face_normal(dyn, triangle_id)
        normal = None
        for item in (result if isinstance(result, tuple) else (result,)):
            if isinstance(item, unreal.Vector):
                normal = item
                break
        normals.append(normal)
    return normals


def main():
    out("=== MeshQueries surface ===")
    lib = unreal.GeometryScript_MeshQueries
    out("  " + str(sorted(m for m in dir(lib) if not m.startswith("_")
                          and m not in ("cast", "get_class", "get_default_object",
                                        "static_class"))))

    out()
    out("=== winding, before and after scale_mesh(1, -1, 1) ===")
    dyn = load_dynamic_mesh()
    out("  closed mesh: %r" % (lib.get_is_closed_mesh(dyn),))
    before = face_normals(dyn)

    dyn = _unwrap(unreal.GeometryScript_MeshTransforms.scale_mesh(
        dyn, unreal.Vector(1.0, -1.0, 1.0), unreal.Vector(0.0, 0.0, 0.0))) or dyn
    after = face_normals(dyn)

    # Under B = diag(1, -1, 1), a triangle whose orientation is preserved has
    # its outward normal mapped to B*n = (nx, -ny, nz). If the reflection
    # inverted the winding and nothing corrected it, the normal comes back as
    # -B*n = (-nx, ny, -nz). The two are opposite, so this cannot be ambiguous.
    preserved = 0
    inverted = 0
    other = 0
    for old, new in zip(before, after):
        if old is None or new is None:
            other += 1
            continue
        expect_preserved = (old.x, -old.y, old.z)
        expect_inverted = (-old.x, old.y, -old.z)
        if all(abs(getattr(new, axis) - value) < 1e-4
               for axis, value in zip("xyz", expect_preserved)):
            preserved += 1
        elif all(abs(getattr(new, axis) - value) < 1e-4
                 for axis, value in zip("xyz", expect_inverted)):
            inverted += 1
        else:
            other += 1

    total = len(before)
    out("  triangles=%d  orientation-preserved=%d  inverted=%d  neither=%d"
        % (total, preserved, inverted, other))

    out()
    if total and preserved == total:
        out("VERDICT: scale_mesh fixes winding for a negative-determinant scale "
            "by itself. No explicit flip is needed.")
    elif total and inverted == total:
        out("VERDICT: the mirror INVERTS winding. The exporter must flip triangle "
            "orientation explicitly or every face renders inside-out.")
    else:
        out("VERDICT: inconclusive (%d preserved, %d inverted, %d neither); "
            "do not guess." % (preserved, inverted, other))


status = "PASS"
try:
    main()
except Exception:
    out("FATAL:")
    out(traceback.format_exc())
    status = "FAIL"

_lines.append("RESULT: " + status)
os.makedirs(OUT_DIR, exist_ok=True)
with open(OUT_PATH, "w") as handle:
    handle.write("\n".join(_lines) + "\n")

print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)
