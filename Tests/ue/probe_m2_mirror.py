"""
probe_m2_mirror.py — M2 reconnaissance: how to bake Lane B's reflection into geometry.

LANE_B.md's open item: UE is left-handed and O3DE is right-handed, so mesh
geometry needs the same determinant -1 map Lane A applies to transforms
(negate Y). SceneAPI cannot express a reflection -- `CoordinateSystemRule`
offers a rotation and a single scalar `scale` -- so it has to be baked into the
FBX at export time, in UE.

A reflection also inverts triangle winding. If the winding is not flipped back,
every face points inward and the mesh renders inside-out. This probe answers:

  1. which transform entry points exist on GeometryScript, and whether any of
     them fixes orientation for a negative-determinant transform by itself
  2. what the mesh's signed volume is before and after -- the direct test for
     inverted winding, since a consistently-wound closed mesh has positive
     signed volume and an inside-out one has negative
  3. whether the reflected mesh's centroid mirrors as expected

Run:  run_ue_python.bat probe_m2_mirror.py
Output: Tests/ue/results/probe_m2_mirror.txt
"""

import os
import traceback

import unreal

MESH_PATH = "/Game/Meshes/SM_LetterF.SM_LetterF"
OUT_DIR = "D:/Gamedev/UEtoO3DE/Tests/ue/results"
OUT_PATH = OUT_DIR + "/probe_m2_mirror.txt"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_M2] " + str(msg))


def section(title):
    out()
    out("=" * 70)
    out(title)
    out("=" * 70)


def guarded(title, fn):
    section(title)
    try:
        fn()
    except Exception:
        out("EXCEPTION:")
        out(traceback.format_exc())


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
    result = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        mesh, dyn, options, lod)
    dyn = _unwrap(result)
    if dyn is None:
        raise RuntimeError("copy_mesh_from_static_mesh failed")
    return dyn


def positions(dyn):
    result = dyn.get_all_vertex_positions(False)
    for item in (result if isinstance(result, tuple) else (result,)):
        if isinstance(item, unreal.GeometryScriptVectorList):
            return unreal.GeometryScript_List.convert_vector_list_to_array(item)
    raise RuntimeError("get_all_vertex_positions returned no vector list")


def describe(dyn, label):
    pts = positions(dyn)
    n = float(len(pts))
    centroid = [sum(p.x for p in pts) / n, sum(p.y for p in pts) / n, sum(p.z for p in pts) / n]
    out("  %-10s vertices=%d centroid=(%.4f, %.4f, %.4f) y-range=[%.3f, %.3f]"
        % (label, len(pts), centroid[0], centroid[1], centroid[2],
           min(p.y for p in pts), max(p.y for p in pts)))
    return centroid


def signed_volume(dyn):
    """Signed volume via the divergence theorem; negative means inverted winding."""
    for candidate in ("GeometryScript_MeshQueries",):
        lib = getattr(unreal, candidate, None)
        if lib is None:
            continue
        for method in ("get_mesh_volume_area", "get_mesh_volume_area_center"):
            fn = getattr(lib, method, None)
            if fn is None:
                continue
            try:
                return method, fn(dyn)
            except Exception as exc:
                out("    %s.%s raised %r" % (candidate, method, exc))
    return None, None


# ---------------------------------------------------------------------------

def probe_libraries():
    for name in ("GeometryScript_MeshTransforms", "GeometryScript_MeshNormals",
                 "GeometryScript_MeshQueries", "GeometryScript_MeshBasicEditing",
                 "GeometryScript_MeshRepair"):
        lib = getattr(unreal, name, None)
        if lib is None:
            out(name + ": <absent>")
            continue
        members = [m for m in dir(lib) if not m.startswith("_")
                   and m not in ("cast", "get_class", "get_default_object", "static_class")]
        out(name + ":")
        for member in sorted(members):
            if any(key in member for key in ("scale", "transform", "flip", "invert",
                                             "reverse", "normal", "volume", "orient")):
                out("    " + member)


def probe_baseline():
    dyn = load_dynamic_mesh()
    describe(dyn, "source")
    method, value = signed_volume(dyn)
    out("  signed volume via %s: %r" % (method, value))


def probe_scale_mesh():
    """ScaleMesh with a negative component -- does it fix orientation itself?"""
    dyn = load_dynamic_mesh()
    before_method, before_value = signed_volume(dyn)
    out("  before: %s -> %r" % (before_method, before_value))

    fn = getattr(unreal.GeometryScript_MeshTransforms, "scale_mesh", None)
    if fn is None:
        out("  scale_mesh absent")
        return
    try:
        result = fn(dyn, unreal.Vector(1.0, -1.0, 1.0), unreal.Vector(0.0, 0.0, 0.0))
        dyn = _unwrap(result) or dyn
        out("  scale_mesh(1,-1,1) accepted (3 positional args)")
    except Exception as exc:
        out("  scale_mesh 3-arg form raised: %r" % exc)
        try:
            result = fn(dyn, unreal.Vector(1.0, -1.0, 1.0), unreal.Vector(0.0, 0.0, 0.0), True)
            dyn = _unwrap(result) or dyn
            out("  scale_mesh(1,-1,1, fix_orientation=True) accepted (4 positional args)")
        except Exception as exc2:
            out("  scale_mesh 4-arg form raised: %r" % exc2)
            return

    describe(dyn, "mirrored")
    after_method, after_value = signed_volume(dyn)
    out("  after:  %s -> %r" % (after_method, after_value))


def probe_flip_normals():
    lib = getattr(unreal, "GeometryScript_MeshNormals", None)
    if lib is None:
        out("GeometryScript_MeshNormals absent")
        return
    for name in ("flip_normals",):
        fn = getattr(lib, name, None)
        out("%s: %r" % (name, fn))


def probe_new_asset_options():
    options = unreal.GeometryScriptCopyMeshToAssetOptions()
    out("GeometryScriptCopyMeshToAssetOptions: " + repr(options))
    create = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    out("GeometryScriptCreateNewStaticMeshAssetOptions: " + repr(create))


def probe_export_options():
    options = unreal.FbxExportOption()
    out("FbxExportOption: " + repr(options))


def main():
    guarded("1. AVAILABLE TRANSFORM / NORMAL / QUERY ENTRY POINTS", probe_libraries)
    guarded("2. BASELINE MESH", probe_baseline)
    guarded("3. SCALE MESH WITH A NEGATIVE COMPONENT", probe_scale_mesh)
    guarded("4. FLIP NORMALS", probe_flip_normals)
    guarded("5. ASSET WRITE OPTIONS", probe_new_asset_options)
    guarded("6. FBX EXPORT OPTIONS", probe_export_options)


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
