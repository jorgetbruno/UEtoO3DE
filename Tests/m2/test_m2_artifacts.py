"""
test_m2_artifacts.py — the M2 acceptance checks that need no editor.

    "Mirror check: assert the F mesh's world-space bounding box extents and its
     vertex centroid offset match the UE reference -- this is the assertion
     that catches a handedness inversion."
    "Assert one FBX per unique mesh GUID (dedup works)."

The mirror/scale check runs against the **product position buffers in the AP
cache** -- the final artifact the Mesh component renders, after all three
pipeline stages. Asserting any earlier artifact has already failed twice:

  * asserting the FBX (M2, first attempt) missed that SceneAPI negates Y a
    third time, so a "correct" FBX produced a mirrored product;
  * asserting nothing about product scale let a doubled /100 through until a
    human noticed a bench was 100x too small next to the shader ball.

O3DE reflects no bounds API to Python and the buffers are AZ object streams,
so the product is read by FLOAT BYTE PATTERN: the buffer embeds the raw
little-endian float32 vertex data, and searching for the exact byte encodings
of known coordinates is immune to the surrounding serialization. The fixture
makes this precise -- the engine cube's corners are exactly +/-0.5 m, and the
F mesh's nub gives Y values that exist on only one side of zero.

The FBX is still checked, but for what it actually is now: a verbatim-UE
intermediate (the bake and UE's export negation cancel; see LANE_B.md).

Run:  python Tests/m2/test_m2_artifacts.py [project_path]
"""

import json
import os
import struct
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "Tests", "lib"))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts"))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

import fbx_reader  # noqa: E402
from ueimporter import assetinfo, manifest_io  # noqa: E402

MANIFEST_PATH = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
EXPORT_ASSETS = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "Assets")
UE_REFERENCE = os.path.join(REPO_ROOT, "Exports", "LaneB", "SM_LetterF.ue_reference.json")
DEFAULT_PROJECT = r"C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"

POSITION_TOLERANCE_CM = 1e-3

failures = []


def fail(message):
    failures.append(str(message))
    print("FAIL: " + str(message))


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def _float_hits(data, value):
    """Occurrences of the exact little-endian float32 encoding of `value`."""
    return data.count(struct.pack("<f", value))


def _position_buffer(project, relative_fbx_path, stem):
    """Path of the LOD0 position .azbuffer for a staged FBX, or None."""
    folder = os.path.join(project, "Cache", "pc", "assets",
                          os.path.dirname(relative_fbx_path)).replace("\\", "/")
    if not os.path.isdir(folder):
        return None
    for name in os.listdir(folder):
        if name.startswith(stem) and "position" in name and name.endswith(".azbuffer") \
                and not name.startswith("default_"):
            return os.path.join(folder, name)
    return None


# ---------------------------------------------------------------------------

def test_manifest_declares_both_lanes(document):
    units = document["units"]
    check(units.get("lane_a_rule") == manifest_io.EXPECTED_LANE_A_RULE,
          "units.lane_a_rule is %r" % units.get("lane_a_rule"))
    check(units.get("lane_b_rule") == manifest_io.EXPECTED_LANE_B_RULE,
          "units.lane_b_rule is %r" % units.get("lane_b_rule"))


def test_fbx_is_verbatim_intermediate(document):
    """The FBX equals the UE source: bake and export negations cancelled.

    If this fails while the product test passes, a stage moved -- find out
    which before trusting either.
    """
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
    print("  UE reference bounds (cm): %s .. %s"
          % (reference["bounds_min"], reference["bounds_max"]))
    print("  exported FBX bounds (cm): %s .. %s"
          % ([round(v, 3) for v in stats["min"]], [round(v, 3) for v in stats["max"]]))
    for index, axis in enumerate("xyz"):
        check(abs(stats["min"][index] - reference["bounds_min"][index]) <= POSITION_TOLERANCE_CM,
              "FBX bounds min.%s is %.4f, UE source has %.4f"
              % (axis, stats["min"][index], reference["bounds_min"][index]))
        check(abs(stats["max"][index] - reference["bounds_max"][index]) <= POSITION_TOLERANCE_CM,
              "FBX bounds max.%s is %.4f, UE source has %.4f"
              % (axis, stats["max"][index], reference["bounds_max"][index]))


def test_product_scale_and_mirror(document, project):
    """THE Lane B assertion: the final product carries negate-Y at 1/100.

    Cube: every corner is exactly +/-0.5 m. Wrong outcomes are equally exact:
    +/-50 (units not converted), +/-0.005 (a scale rule stacked on the unit
    conversion -- the bug a user caught by eye).

    F mesh: UE nub Y in [+12.5, +37.5] cm -> correct product Y extreme is
    -0.375 m and +0.375 must not exist. A net-zero mirror (the second M2 bug)
    produces exactly the opposite signature.
    """
    # --- cube ---
    buffer_path = _position_buffer(project, "uetoo3de/engine/basicshapes/cube.fbx", "cube")
    if not check(buffer_path is not None, "no product position buffer for the cube"):
        return
    data = open(buffer_path, "rb").read()
    plus = _float_hits(data, 0.5)
    minus = _float_hits(data, -0.5)
    print("  cube product:    +0.5 x%d  -0.5 x%d  (+50 x%d  +0.005 x%d)"
          % (plus, minus, _float_hits(data, 50.0), _float_hits(data, 0.005)))
    check(plus >= 24 and minus >= 24,
          "cube product does not contain +/-0.5 m corners (+%d/-%d); scale is wrong"
          % (plus, minus))
    check(_float_hits(data, 50.0) == 0 and _float_hits(data, -50.0) == 0,
          "cube product contains +/-50: units were not converted")
    check(_float_hits(data, 0.005) == 0 and _float_hits(data, -0.005) == 0,
          "cube product contains +/-0.005: a scale rule is stacked on the unit "
          "conversion (the 100x-too-small bug)")

    # --- F mesh mirror ---
    buffer_path = _position_buffer(project, "uetoo3de/game/meshes/sm_letterf.fbx", "sm_letterf")
    if not check(buffer_path is not None, "no product position buffer for SM_LetterF"):
        return
    data = open(buffer_path, "rb").read()
    correct = _float_hits(data, -0.375)
    mirrored = _float_hits(data, 0.375)
    print("  letterf product: -0.375 x%d  +0.375 x%d" % (correct, mirrored))
    check(correct > 0,
          "F mesh product has no vertex at Y = -0.375 m; the nub is missing or "
          "the geometry is not negate-Y")
    check(mirrored == 0,
          "F mesh product has vertices at Y = +0.375 m: the geometry is MIRRORED "
          "(net-zero Y negation; a bake or conversion stage is missing/doubled)")


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

    used = [entity["mesh"]["asset_guid"] for entity in document["entities"]
            if "mesh" in entity]
    check(len(used) > len(set(used)),
          "no mesh is referenced by more than one entity; the dedup assertion "
          "would pass even if dedup were broken")
    print("  %d FBX files for %d mesh references across %d entities"
          % (len(on_disk), len(set(used)), len(used)))


def test_assetinfo_sidecars(document, project):
    """Every staged FBX has the sidecar contract: node selection + LodRule +
    MaterialRule, and NO CoordinateSystemRule (SceneAPI owns both conversions)."""
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
              "%s: selectedNodes is %r, expected %r" % (relative_path, nodes, [expected_node]))

        rules = {rule["$type"]: rule for rule in group["rules"]["rules"]}
        check("CoordinateSystemRule" not in rules,
              "%s: carries a CoordinateSystemRule; SceneAPI already converts "
              "units and axes, and a scale rule stacks a second /100 on top "
              "(the 100x-too-small bug)" % relative_path)
        check(assetinfo.LOD_RULE_TYPE in rules,
              "%s: the LodRule is missing; the AP job fails without it" % relative_path)
        check("MaterialRule" in rules, "%s: the MaterialRule is missing" % relative_path)
    print("  %d sidecars verified" % len(manifest_io.static_mesh_assets(document)))


def test_stale_instance_removal():
    """A level holding an instance of the prefab being rewritten breaks the save.

    Pure file I/O, so it is tested here rather than in the editor. The bug it
    guards is expensive: `CreatePrefabInMemory` answers with an opaque
    "unknown exception", which reads exactly like an asset-streaming race and
    was twice misdiagnosed as one (the level's own entity count and content
    genuinely affect *when* it trips). Only instances of THIS prefab may be
    removed -- the level's other content is none of the importer's business.
    """
    import json as json_module
    import shutil
    import tempfile

    from ueimporter import prefab_build

    root = tempfile.mkdtemp(prefix="ueo3de_stale_")
    try:
        level_dir = os.path.join(root, "Levels", "DefaultLevel")
        os.makedirs(level_dir)
        level_file = os.path.join(level_dir, "DefaultLevel.prefab")
        with open(level_file, "w") as handle:
            json_module.dump({
                "ContainerEntity": {"Id": "ContainerEntity"},
                "Entities": {"Entity_1": {"Name": "SomethingElse"}},
                "Instances": {
                    "Instance_[1]": {"Source": "Prefabs/L_Overview.prefab"},
                    "Instance_[2]": {"Source": "Prefabs/SomeoneElses.prefab"},
                },
            }, handle)

        target = os.path.join(root, "Prefabs", "L_Overview.prefab")
        removed = prefab_build.detach_conflicting_instances(
            root, "DefaultLevel", target)
        check(removed == 1, "expected 1 stale instance removed, got %r" % removed)

        with open(level_file, "r") as handle:
            after = json_module.load(handle)
        sources = [value["Source"] for value in (after.get("Instances") or {}).values()]
        check(sources == ["Prefabs/SomeoneElses.prefab"],
              "only the conflicting instance may be removed; instances left: %r"
              % sources)
        check("Entity_1" in after.get("Entities", {}),
              "the level's own entities must be left alone")

        # Idempotent, and silent when there is nothing to do.
        again = prefab_build.detach_conflicting_instances(
            root, "DefaultLevel", target)
        check(again == 0, "second pass should find nothing, got %r" % again)
        check(prefab_build.detach_conflicting_instances(
                  root, "NoSuchLevel", target) == 0,
              "a missing level file must be handled, not raised")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("  conflicting instance removed, unrelated content untouched")


def test_two_tone_slots(document, project):
    """Per-slot material fidelity, at both artifact levels (M4).

    FBX: the two-slot canary must carry BOTH material names -- they are the
    azmodel slot labels the importer matches on. A single-name FBX means the
    bake flattened the slots again.

    Prefab: the SM_TwoTone entity's own subtree must reference both
    .azmaterial products. The default-slot mechanism can only ever carry one
    material per entity, so two distinct hints inside one entity is the
    signature of per-slot assignment having landed.

    The two lists deliberately DIFFER: the actor overrides slot 1, so the FBX
    carries the mesh asset's materials (PBR + ORM) while the prefab must carry
    the effective ones (PBR + Masked). An importer that matched slots by the
    effective material name would find no label and assign nothing -- the bug
    L_Showcase exposed on 97 trees.
    """
    asset = next((a for a in document["assets"]
                  if a["ue_path"] == "/Game/Meshes/SM_TwoTone"), None)
    if not check(asset is not None, "manifest has no SM_TwoTone mesh asset"):
        return
    fbx_path = os.path.join(EXPORT_ASSETS, asset["o3de_relative_path"])
    if not check(os.path.exists(fbx_path), "exported FBX missing: " + fbx_path):
        return
    data = open(fbx_path, "rb").read()
    for name in (b"M_Fixture_PBR", b"M_Fixture_ORM"):
        check(data.count(name) > 0,
              "SM_TwoTone FBX does not carry material %r; the bake flattened "
              "the slots" % name.decode())
    check(data.count(b"WorldGridMaterial") == 0,
          "SM_TwoTone FBX carries the bake's default WorldGridMaterial slot")

    prefab_path = os.path.join(project, "Prefabs", "Fixture_01.prefab")
    if not check(os.path.exists(prefab_path), "prefab missing: " + prefab_path):
        return
    with open(prefab_path, "r") as handle:
        prefab = json.load(handle)
    subtree = None
    for key, entity in (prefab.get("Entities") or {}).items():
        if entity.get("Name") == "SM_TwoTone":
            subtree = json.dumps(entity)
            break
    if not check(subtree is not None, "prefab has no SM_TwoTone entity"):
        return
    # The EFFECTIVE materials, after the actor's slot-1 override.
    for hint in ("m_fixture_pbr.azmaterial", "m_fixture_masked.azmaterial"):
        check(hint in subtree,
              "SM_TwoTone's prefab entity does not reference %s; per-slot "
              "assignment did not land" % hint)
    check("m_fixture_orm.azmaterial" not in subtree,
          "SM_TwoTone's prefab entity references the mesh asset's ORM material; "
          "the actor's slot-1 override was ignored")
    print("  FBX carries the asset's material names; prefab carries the "
          "effective (overridden) ones")


def test_import_report(document):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "results", "m2_import_report_Fixture_01.json")
    if not check(os.path.exists(path), "import report missing: " + path):
        return
    with open(path, "r") as handle:
        report = json.load(handle)

    errors = [record for record in report["warnings"] if record["severity"] == "error"]
    check(not errors, "import report contains errors: %r" % [r["code"] for r in errors])

    counters = report["counters"]
    # Derived from the manifest rather than pinned to a number: nothing may be
    # dropped, and adding a fixture actor should not require editing a magic
    # constant here (which is how a real drop gets normalised away).
    check(counters.get("entities_created") == len(document["entities"]),
          "import created %r entities, manifest has %d"
          % (counters.get("entities_created"), len(document["entities"])))
    # 6 mesh products + 5 converted materials (the 4 fixture PBR set plus
    # WorldGridMaterial, whose Multiply graph resolves through the texture-DFS
    # approximation); image products are dependencies of the material jobs and
    # are not waited on directly.
    check(counters.get("assets_waited_for") == 11,
          "import waited for %r assets, expected 11" % counters.get("assets_waited_for"))
    # SM_TwoTone is the only multi-material mesh: exactly its 2 slots go
    # through per-slot assignment, and both labels must match model slots.
    check(counters.get("material_slots_assigned") == 2,
          "per-slot assignment set %r slots, expected 2 (SM_TwoTone)"
          % counters.get("material_slots_assigned"))
    # Every manifest light must have produced a component (M5).
    expected_lights = sum(1 for item in document["entities"] if "light" in item)
    check(counters.get("lights_created") == expected_lights,
          "authored %r lights, manifest has %d"
          % (counters.get("lights_created"), expected_lights))

    codes = {record["code"] for record in report["warnings"]}
    check("XFORM_NONUNIFORM_SCALE_COMPONENT" in codes,
          "no non-uniform scale was reported; Fixture_Floor (10,10,1) and "
          "Prim_Box (2,1,0.5) both need one")
    for code in ("MAT_SLOT_UNMATCHED", "MAT_MODEL_NOT_READY",
                 "MAT_SLOT_LABEL_AMBIGUOUS"):
        check(code not in codes,
              "%s reported on the fixture; per-slot assignment degraded" % code)
    print("  counters: %r" % counters)


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROJECT

    if not os.path.exists(MANIFEST_PATH):
        print("FAIL: manifest not found: %s (run the UE export first)" % MANIFEST_PATH)
        return 1
    document = manifest_io.load(MANIFEST_PATH)

    for name, test in (
            ("manifest declares both lanes", lambda: test_manifest_declares_both_lanes(document)),
            ("FBX is the verbatim-UE intermediate", lambda: test_fbx_is_verbatim_intermediate(document)),
            ("PRODUCT scale + mirror (byte-level)", lambda: test_product_scale_and_mirror(document, project)),
            ("one FBX per unique mesh GUID", lambda: test_one_fbx_per_unique_mesh_guid(document)),
            ("assetinfo sidecars", lambda: test_assetinfo_sidecars(document, project)),
            ("stale prefab instance removal", test_stale_instance_removal),
            ("two-tone per-slot fidelity", lambda: test_two_tone_slots(document, project)),
            ("import report", lambda: test_import_report(document)),
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
