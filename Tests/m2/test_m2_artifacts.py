"""
test_m2_artifacts.py — the M2 acceptance checks that need no editor.

    "Mirror check: assert the F mesh's world-space bounding box extents and its
     vertex centroid offset match the UE reference -- this is the assertion
     that catches a handedness inversion."
    "Assert one FBX per unique mesh GUID (dedup works)."

The mirror check runs against the **exported FBX**, in centimetres, because
that is the artifact SceneAPI consumes and the last point in the chain where
geometry is observable. Its vertex positions must equal the UE reference with
Lane A's basis map applied -- same reflection the transforms get. If the
exporter ever stops mirroring (or starts mirroring twice) the F mesh's centroid
lands on the wrong side of Y and this fails.

Scope, stated plainly: this does not read vertices back out of the O3DE
*product*. O3DE 26.05 reflects no bounds API to Python (`BoundsRequestBus` has
no binding -- measured in M0) and the product's `.azbuffer` is compressed, so
there is no supported way to do it yet. What covers the remaining step is the
`.assetinfo` assertion below (the scale rule is present and correct), AP
reporting zero failures, and `m2_acceptance.py` confirming the model loads and
reports geometry. Recorded in LANE_B.md as the one link measured indirectly.

Run:  python Tests/m2/test_m2_artifacts.py
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "Tests", "lib"))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts"))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

import fbx_reader  # noqa: E402
from ueimporter import assetinfo, manifest_io  # noqa: E402
from ueo3de import lane_a  # noqa: E402

MANIFEST_PATH = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
EXPORT_ASSETS = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "Assets")
UE_REFERENCE = os.path.join(REPO_ROOT, "Exports", "LaneB", "SM_LetterF.ue_reference.json")
DEFAULT_PROJECT = r"C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"

# The FBX is still in centimetres; only the .assetinfo scales it.
POSITION_TOLERANCE_CM = 1e-3

failures = []


def fail(message):
    failures.append(str(message))
    print("FAIL: " + str(message))


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def test_manifest_declares_both_lanes(document):
    """The importer refuses a manifest without both lane rules; prove it says so."""
    units = document["units"]
    check(units.get("lane_a_rule") == manifest_io.EXPECTED_LANE_A_RULE,
          "units.lane_a_rule is %r" % units.get("lane_a_rule"))
    check(units.get("lane_b_rule") == manifest_io.EXPECTED_LANE_B_RULE,
          "units.lane_b_rule is %r -- without it the importer cannot know the "
          "geometry carries the reflection" % units.get("lane_b_rule"))


def test_mirror_check(document):
    """The F mesh's exported geometry equals Lane A applied to the UE reference."""
    with open(UE_REFERENCE, "r") as handle:
        reference = json.load(handle)

    asset = next((a for a in document["assets"]
                  if a["ue_path"] == "/Game/Meshes/SM_LetterF"), None)
    if not check(asset is not None, "manifest has no SM_LetterF mesh asset"):
        return
    fbx_path = os.path.join(EXPORT_ASSETS, asset["o3de_relative_path"])
    if not check(os.path.exists(fbx_path), "exported FBX missing: " + fbx_path):
        return

    stats = fbx_reader.vertex_stats(fbx_path)

    # Lane A in centimetres: the basis map without the metre conversion.
    def to_o3de_cm(vector):
        return [component * 100.0 for component in lane_a.convert_position(vector)]

    expected_centroid = to_o3de_cm(reference["centroid"])
    corner_a = to_o3de_cm(reference["bounds_min"])
    corner_b = to_o3de_cm(reference["bounds_max"])
    expected_min = [min(corner_a[i], corner_b[i]) for i in range(3)]
    expected_max = [max(corner_a[i], corner_b[i]) for i in range(3)]

    print("  UE reference bounds   (cm): %s .. %s"
          % ([round(v, 3) for v in reference["bounds_min"]],
             [round(v, 3) for v in reference["bounds_max"]]))
    print("  Lane A predicts       (cm): %s .. %s"
          % ([round(v, 3) for v in expected_min], [round(v, 3) for v in expected_max]))
    print("  exported FBX bounds   (cm): %s .. %s  (%d control points)"
          % ([round(v, 3) for v in stats["min"]], [round(v, 3) for v in stats["max"]],
             stats["count"]))

    # Bounds are comparable directly: the extremes are the same points however
    # the vertices are counted.
    for index, axis in enumerate("xyz"):
        check(abs(stats["min"][index] - expected_min[index]) <= POSITION_TOLERANCE_CM,
              "F mesh bounds min.%s is %.4f, expected %.4f"
              % (axis, stats["min"][index], expected_min[index]))
        check(abs(stats["max"][index] - expected_max[index]) <= POSITION_TOLERANCE_CM,
              "F mesh bounds max.%s is %.4f, expected %.4f"
              % (axis, stats["max"][index], expected_max[index]))

    # Centroids are NOT comparable as absolute positions: the UE reference
    # averages 93 render vertices (duplicated at UV and normal seams) while the
    # FBX stores 28 unique control points, so the two means differ even when
    # the geometry is identical. What a mirror flips -- and what survives the
    # difference in vertex sets -- is the DIRECTION of the centroid's offset
    # from the bounding-box centre, so that is what is asserted.
    def offset_from_centre(centroid, low, high):
        return [centroid[i] - (low[i] + high[i]) * 0.5 for i in range(3)]

    ue_offset = offset_from_centre(reference["centroid"],
                                   reference["bounds_min"], reference["bounds_max"])
    expected_offset = [component * 100.0 for component
                       in lane_a.convert_position(ue_offset)]
    fbx_offset = offset_from_centre(stats["centroid"], stats["min"], stats["max"])
    print("  UE centroid offset    (cm): %s" % [round(v, 4) for v in ue_offset])
    print("  Lane A predicts       (cm): %s" % [round(v, 4) for v in expected_offset])
    print("  exported FBX offset   (cm): %s" % [round(v, 4) for v in fbx_offset])

    for index, axis in enumerate("xyz"):
        # Only axes where the mesh is meaningfully asymmetric carry a signal.
        if abs(expected_offset[index]) <= 1.0:
            fail("the F mesh is symmetric about %s (offset %.4f cm); a mirror "
                 "across that plane would be undetectable"
                 % (axis.upper(), expected_offset[index]))
            continue
        check(fbx_offset[index] * expected_offset[index] > 0.0,
              "F mesh centroid offset on %s is %.4f, Lane A predicts %.4f "
              "(opposite sign) -- the geometry is mirrored about %s"
              % (axis.upper(), fbx_offset[index], expected_offset[index], axis.upper()))

    settings = stats["global_settings"]
    unit_scale = settings.get("UnitScaleFactor")
    check(unit_scale and abs(float(unit_scale[0]) - 1.0) < 1e-6,
          "FBX UnitScaleFactor is %r; the .assetinfo assumes centimetres"
          % (unit_scale,))


def test_one_fbx_per_unique_mesh_guid(document):
    mesh_assets = manifest_io.static_mesh_assets(document)
    guids = [asset["guid"] for asset in mesh_assets]
    check(len(guids) == len(set(guids)), "manifest repeats a static mesh GUID")

    on_disk = []
    for root, _dirs, files in os.walk(EXPORT_ASSETS):
        on_disk.extend(os.path.join(root, name) for name in files if name.endswith(".fbx"))
    check(len(on_disk) == len(mesh_assets),
          "%d FBX files on disk for %d unique mesh GUIDs (dedup broken)"
          % (len(on_disk), len(mesh_assets)))

    for asset in mesh_assets:
        path = os.path.join(EXPORT_ASSETS, asset["o3de_relative_path"])
        check(os.path.exists(path), "missing FBX for %s: %s" % (asset["ue_path"], path))

    # The check is only meaningful if some mesh is genuinely shared.
    used = [entity["mesh"]["asset_guid"] for entity in document["entities"]
            if "mesh" in entity]
    check(len(used) > len(set(used)),
          "no mesh is referenced by more than one entity; the dedup assertion "
          "would pass even if dedup were broken")
    print("  %d FBX files for %d mesh references across %d entities"
          % (len(on_disk), len(set(used)), len(used)))


def test_assetinfo_sidecars(document, project):
    """Every staged FBX has the sidecar LANE_B.md specifies."""
    project_assets = os.path.join(project, "Assets")
    for asset in manifest_io.static_mesh_assets(document):
        relative_path = asset["o3de_relative_path"]
        sidecar = os.path.join(project_assets, relative_path + ".assetinfo")
        if not check(os.path.exists(sidecar), "missing sidecar: " + sidecar):
            continue
        with open(sidecar, "r") as handle:
            document_json = json.load(handle)

        values = document_json.get("values") or []
        if not check(len(values) == 1, "%s: expected one mesh group" % relative_path):
            continue
        group = values[0]

        nodes = group["nodeSelectionList"]["selectedNodes"]
        expected_node = "RootNode." + asset["fbx_node_name"]
        check(nodes == [expected_node],
              "%s: selectedNodes is %r, expected %r (a wrong path fails the AP "
              "job with 'No valid ModelLodAssets have been added')"
              % (relative_path, nodes, [expected_node]))

        rules = {rule["$type"]: rule for rule in group["rules"]["rules"]}
        coordinate_rule = rules.get("CoordinateSystemRule")
        if check(coordinate_rule is not None,
                 "%s: no CoordinateSystemRule; the model would import 100x too "
                 "large" % relative_path):
            check(coordinate_rule.get("useAdvancedData") is True,
                  "%s: CoordinateSystemRule is not in advanced mode" % relative_path)
            check(abs(coordinate_rule.get("scale", 0) - assetinfo.CM_TO_M_SCALE) < 1e-9,
                  "%s: scale is %r, expected %r"
                  % (relative_path, coordinate_rule.get("scale"), assetinfo.CM_TO_M_SCALE))
        check(assetinfo.LOD_RULE_TYPE in rules,
              "%s: the LodRule is missing; the AP job fails without it" % relative_path)
        check("MaterialRule" in rules, "%s: the MaterialRule is missing" % relative_path)
    print("  %d sidecars verified" % len(manifest_io.static_mesh_assets(document)))


def test_import_report():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "results", "m2_import_report.json")
    if not check(os.path.exists(path), "import report missing: " + path):
        return
    with open(path, "r") as handle:
        report = json.load(handle)

    errors = [record for record in report["warnings"] if record["severity"] == "error"]
    check(not errors, "import report contains errors: %r" % [r["code"] for r in errors])

    counters = report["counters"]
    check(counters.get("entities_created") == 16,
          "import created %r entities, expected 16" % counters.get("entities_created"))
    check(counters.get("assets_waited_for") == 5,
          "import waited for %r assets, expected 5" % counters.get("assets_waited_for"))

    # The fixture has two non-uniformly scaled actors; if that record ever
    # disappears, either the fixture changed or the scale silently collapsed.
    codes = {record["code"] for record in report["warnings"]}
    check("XFORM_NONUNIFORM_SCALE_COMPONENT" in codes,
          "no non-uniform scale was reported; Fixture_Floor (10,10,1) and "
          "Prim_Box (2,1,0.5) both need one")
    print("  counters: %r" % counters)


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROJECT

    if not os.path.exists(MANIFEST_PATH):
        print("FAIL: manifest not found: %s (run the UE export first)" % MANIFEST_PATH)
        return 1
    document = manifest_io.load(MANIFEST_PATH)

    for name, test in (
            ("manifest declares both lanes", lambda: test_manifest_declares_both_lanes(document)),
            ("mirror check (F mesh vs UE reference)", lambda: test_mirror_check(document)),
            ("one FBX per unique mesh GUID", lambda: test_one_fbx_per_unique_mesh_guid(document)),
            ("assetinfo sidecars", lambda: test_assetinfo_sidecars(document, project)),
            ("import report", test_import_report),
    ):
        before = len(failures)
        print("== %s ==" % name)
        test()
        print("  %s" % ("ok" if len(failures) == before else "FAILED"))

    print("")
    if failures:
        print("RESULT: FAIL (%d failure(s))" % len(failures))
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
