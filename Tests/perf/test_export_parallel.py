"""
test_export_parallel.py — a wide export loses no mesh, and its check still bites.

Pure: no editor. Run: python Tests/perf/test_export_parallel.py

WHY THIS EXISTS. A level export used to run every mesh bake and the bounds
check on the editor's one Python thread (NYC_Level_WC: about three hours).
It now splits standalone meshes across worker editors and runs the check
after the editors exit, across every core. Both changes fail silently if they
are wrong:

  * a mesh assigned to no editor is simply missing from the level, and a
    mesh assigned to two is written twice by processes racing on one file;
  * a check that runs somewhere else can quietly stop running at all, or
    start passing an export that checked nothing.

So every assertion here is about coverage, ownership and the check's teeth.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))
sys.path.insert(0, os.path.join(REPO_ROOT, "Tests", "lib"))

import types  # noqa: E402

# mesh_export (for its knob parsers) imports `unreal`; the parsers never call it.
if "unreal" not in sys.modules:
    sys.modules["unreal"] = types.ModuleType("unreal")

from ueo3de import export_slices as es  # noqa: E402
from ueo3de import mesh_export  # noqa: E402
import export_verify  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def mesh(i, fragment=""):
    path = "/Game/M/SM_%03d" % i + (("#" + fragment) if fragment else "")
    return {"kind": "static_mesh", "guid": "g%03d%s" % (i, fragment),
            "ue_path": path, "o3de_relative_path": "m/sm_%03d%s.fbx" % (i, fragment)}


assets = ([mesh(i) for i in range(50)]
          + [mesh(i, "mx") for i in range(5)]
          + [mesh(100 + i, "spline") for i in range(30)]
          + [mesh(200, "terrain")]
          + [{"kind": "material", "guid": "mat", "ue_path": "/Game/M/M"},
             {"kind": "skeletal_mesh", "guid": "sk", "ue_path": "/Game/M/SK"}])
standalone = [a for a in assets if a["kind"] == "static_mesh"
              and a["ue_path"].partition("#")[2] not in ("spline", "terrain")]

def run():
    # --- 1. the knob ------------------------------------------------------------
    check(es.mesh_worker_count({}) == 1, "unset UEO3DE_MESH_WORKERS must be the serial export")
    check(es.mesh_worker_count({"UEO3DE_MESH_WORKERS": " 3 "}) == 3, "the count must parse")
    for garbage in ("three", "0", "-2", "99"):
        try:
            es.mesh_worker_count({"UEO3DE_MESH_WORKERS": garbage})
            check(False, "UEO3DE_MESH_WORKERS=%r must raise, not fall back" % garbage)
        except es.SliceError:
            pass

    # the batching knob: deleting a UE asset runs a garbage collection over every
    # loaded object (1.56 s of a 1.72 s spline bake with NYC open), so cleanup is
    # batched -- and a garbage value must not silently fall back
    check(mesh_export.temp_flush_every({}) == 32, "temp cleanup must batch by default")
    check(mesh_export.temp_flush_every({"UEO3DE_TEMP_FLUSH_EVERY": "1"}) == 1,
          "1 must restore per-bake cleanup")
    for garbage in ("many", "0", "-4"):
        try:
            mesh_export.temp_flush_every({"UEO3DE_TEMP_FLUSH_EVERY": garbage})
            check(False, "UEO3DE_TEMP_FLUSH_EVERY=%r must raise" % garbage)
        except mesh_export.MeshExportError:
            pass
    old_env = os.environ.pop("UEO3DE_DEFER_BUILD", None)
    try:
        check(mesh_export.defer_build_enabled() is True, "deferred rebuilds must be the default")
        os.environ["UEO3DE_DEFER_BUILD"] = "0"
        check(mesh_export.defer_build_enabled() is False, "0 must restore per-write rebuilds")
        os.environ["UEO3DE_DEFER_BUILD"] = "sometimes"
        try:
            mesh_export.defer_build_enabled()
            check(False, "a garbage UEO3DE_DEFER_BUILD must raise")
        except mesh_export.MeshExportError:
            pass
    finally:
        os.environ.pop("UEO3DE_DEFER_BUILD", None)
        if old_env is not None:
            os.environ["UEO3DE_DEFER_BUILD"] = old_env

    # --- 2. ownership: every mesh exactly once, level-bound ones with the lead ---
    for workers in (1, 2, 3, 7):
        owned = [a["guid"] for a in es.lead_meshes(assets, workers)]
        for index in range(workers if workers > 1 else 0):
            owned += [a["guid"] for a in es.worker_meshes(assets, index, workers)]
        all_meshes = sorted(a["guid"] for a in assets if a["kind"] == "static_mesh")
        check(sorted(owned) == all_meshes,
              "%d workers: every static mesh must be owned exactly once" % workers)
        check(len(owned) == len(set(owned)), "%d workers: no mesh may be owned twice" % workers)

    lead = es.lead_meshes(assets, 3)
    check(all(es.is_level_bound(a) for a in lead) and len(lead) == 31,
          "with workers the lead keeps exactly the 30 splines + terrain (they need "
          "the open level), got %d" % len(lead))
    check(len(es.lead_meshes(assets, 1)) == 86, "a serial export's lead owns every mesh")
    check(not any(es.is_level_bound(a) for i in range(3) for a in es.worker_meshes(assets, i, 3)),
          "no worker (empty map, no level) may be handed a spline or terrain bake")
    check(all(a["kind"] == "static_mesh" for a in lead),
          "materials and skeletal meshes are not the mesh stage's to hand out")

    sizes = [len(es.worker_meshes(assets, i, 3)) for i in range(3)]
    check(max(sizes) - min(sizes) <= 1, "striped slices must be even, got %r" % sizes)
    first = es.worker_meshes(assets, 0, 3)
    check([a["guid"] for a in first] == [a["guid"] for a in standalone[0::3]],
          "slices are striped across manifest order so a run of heavy meshes spreads out")
    try:
        es.worker_meshes(assets, 3, 3)
        check(False, "a worker index outside the pool must raise")
    except es.SliceError:
        pass

    # --- 3. merging proves completeness ------------------------------------------
    def records(meshes):
        return [{"guid": a["guid"], "relative_path": a["o3de_relative_path"]} for a in meshes]


    sets = [("lead", records(es.lead_meshes(assets, 2)))] + [
        ("worker %d" % (i + 1), records(es.worker_meshes(assets, i, 2))) for i in range(2)]
    check(len(es.merge_records(assets, sets)) == 86, "a complete wide export merges to every mesh")

    short = sets[:2]                                   # worker 2 never reported
    try:
        es.merge_records(assets, short)
        check(False, "a worker's missing records must fail the merge, not shrink the level")
    except es.SliceError as error:
        check("exported by nobody" in str(error), "the merge must name the hole: %s" % error)

    doubled = sets + [("worker 3", records(es.worker_meshes(assets, 0, 2))[:1])]
    try:
        es.merge_records(assets, doubled)
        check(False, "a mesh reported by two editors must fail the merge")
    except es.SliceError as error:
        check("both" in str(error), "the merge must name both owners: %s" % error)

    # --- 4. the check keeps its teeth outside the editor -------------------------
    def fake_readers(bounds):
        return (lambda path: False, None, lambda path: bounds[os.path.basename(path)])


    good = {"ue_bounds_min": [-10.0, 0.0, 0.0], "ue_bounds_max": [0.0, 5.0, 3.0],
            "relative_path": "a.fbx"}
    exact = fake_readers({"a.fbx": {"min": [-10.0, 0.0, 0.0], "max": [0.0, 5.0, 3.0]}})
    mirrored = fake_readers({"a.fbx": {"min": [0.0, 0.0, 0.0], "max": [10.0, 5.0, 3.0]}})
    check(export_verify.check_record(good, "root", readers=exact) is None,
          "an exact file must pass")
    message = export_verify.check_record(good, "root", readers=mirrored)
    check(message is not None and "mirrored" in message,
          "a file mirrored in X must fail with the mirror explanation, got %r" % (message,))


    def broken_reader(path):
        raise IOError("truncated")


    unreadable = export_verify.check_record(
        good, "root", readers=(lambda p: False, None, broken_reader))
    check(unreadable is not None and "could not be read" in unreadable,
          "an unreadable file must FAIL the check, never be skipped")

    # the real reader path through the process pool: a missing file fails in a child
    temp = tempfile.mkdtemp(prefix="ueo3de_verify_")
    checked, found = export_verify.verify_records(
        [dict(good, relative_path="missing_%d.fbx" % i) for i in range(6)], temp, processes=3)
    check(checked == 6 and len(found) == 6,
          "every record must be checked across the pool, and every missing file "
          "must fail (checked %d, failed %d)" % (checked, len(found)))
    check(export_verify.verify_records([], temp) == (0, []),
          "an empty record list is 'nothing checked', which the CLI turns into a FAIL")


if __name__ == "__main__":
    # Windows multiprocessing starts each pool child by RE-IMPORTING this
    # script: a test body at module level would re-run in every child and
    # spawn a pool of its own. Only the parent runs the checks.
    run()
    print("")
    print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
    sys.exit(1 if failures else 0)
