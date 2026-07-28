"""
test_m1_acceptance.py — the M1 acceptance test (plan v2.2).

    - Export Fixture_01 -> validator passes.
    - Diff against committed golden file Fixture_01.expected.json with float
      tolerance (1e-4 m, 1e-3 deg). Golden file is regenerated only by an
      explicit, reviewed commit.
    - Assert: all scale components positive; the F mesh's transform is not
      mirrored; the rotated-child world transform matches its UE world
      transform; physics cube simulates_physics: true; kinematic actor
      kinematic: true; trigger box is_trigger: true.

Runs in a plain Python 3 interpreter against the manifest produced by
`Tests/ue/export_fixture_manifest.py`; it does not launch the editor itself, so
`Tests/m1/run_m1.bat` chains the two and CI asserts on the exit code
(plan constraint 10 -- never on console text).

The golden is diffed with `generator` excluded: the engine version string
carries a changelist number that moves with every UE hotfix, and a test that
fails on that trains people to regenerate the golden without reading it, which
defeats its purpose. The engine's major.minor is asserted separately instead.

Run:     python Tests/m1/test_m1_acceptance.py
Update:  python Tests/m1/test_m1_acceptance.py --update-golden   (review the diff!)
"""

import argparse
import json
import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE_ROOT = os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                            "UEO3DEExporter", "Content", "Python")
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ueo3de import lane_a, manifest as manifest_module  # noqa: E402
import validate_manifest  # noqa: E402

MANIFEST_PATH = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "golden", "Fixture_01.expected.json")
UE_REFERENCE_PATH = os.path.join(REPO_ROOT, "Exports", "LaneB",
                                 "SM_LetterF.ue_reference.json")
RESULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "results", "m1_acceptance_result.txt")

EXPECTED_ENGINE_PREFIX = "5.8."

# Plan M1: 1e-4 m on lengths, 1e-3 degrees on angles. Rotations are carried as
# quaternions, and 1e-3 deg of rotation moves a quaternion component by
# sin(0.5e-3 deg) ~= 8.7e-6, so rotation arrays get the tighter bound.
LENGTH_TOLERANCE = 1e-4
ROTATION_TOLERANCE = 1e-5

# Excluded from the golden diff; asserted separately.
GOLDEN_IGNORED_TOP_LEVEL = ("generator",)

_log = []
_failures = []


def log(message):
    _log.append(str(message))
    print(message)


def fail(message):
    _failures.append(str(message))
    log("FAIL: " + str(message))


def check(condition, message):
    if not condition:
        fail(message)
    return condition


# ---------------------------------------------------------------------------
# golden diff
# ---------------------------------------------------------------------------

def _tolerance_for(path):
    return ROTATION_TOLERANCE if "rotation" in path else LENGTH_TOLERANCE


def diff(actual, expected, path="$", differences=None):
    """Structural diff with numeric tolerance; returns a list of strings."""
    if differences is None:
        differences = []

    if isinstance(expected, bool) or isinstance(actual, bool):
        if actual != expected:
            differences.append("%s: %r != %r" % (path, actual, expected))
        return differences

    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        tolerance = _tolerance_for(path)
        if math.isnan(actual) or math.isnan(expected) or abs(actual - expected) > tolerance:
            differences.append("%s: %r != %r (tolerance %g)"
                               % (path, actual, expected, tolerance))
        return differences

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            differences.append("%s: expected an object, got %s" % (path, type(actual).__name__))
            return differences
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                differences.append("%s.%s: missing (golden has %r)" % (path, key, expected[key]))
            elif key not in expected:
                differences.append("%s.%s: unexpected (%r)" % (path, key, actual[key]))
            else:
                diff(actual[key], expected[key], "%s.%s" % (path, key), differences)
        return differences

    if isinstance(expected, list):
        if not isinstance(actual, list):
            differences.append("%s: expected an array, got %s" % (path, type(actual).__name__))
            return differences
        if len(actual) != len(expected):
            differences.append("%s: %d items, golden has %d" % (path, len(actual), len(expected)))
            return differences
        for index in range(len(expected)):
            diff(actual[index], expected[index], "%s[%d]" % (path, index), differences)
        return differences

    if actual != expected:
        differences.append("%s: %r != %r" % (path, actual, expected))
    return differences


def _without_ignored(document):
    return {k: v for k, v in document.items() if k not in GOLDEN_IGNORED_TOP_LEVEL}


# ---------------------------------------------------------------------------
# property assertions
# ---------------------------------------------------------------------------

def by_name(document):
    return {entity["name"]: entity for entity in document["entities"]}


def assert_all_scales_positive(document):
    """Plan constraint 6: handedness lives in the rotation, never in the scale."""
    for entity in document["entities"]:
        for space in ("world", "local"):
            scale = entity["transform"][space]["scale"]
            if any(component <= 0.0 for component in scale):
                fail("%s: %s scale %r has a non-positive component"
                     % (entity["name"], space, scale))
    for asset in document["assets"]:
        bounds = asset.get("bounds_local")
        if bounds and any(bounds["max"][i] < bounds["min"][i] for i in range(3)):
            fail("%s: bounds_local is inverted (%r > %r)"
                 % (asset["ue_path"], bounds["min"], bounds["max"]))


def _linear_matrix(rotation, scale):
    """3x3 columns of the rotation-times-scale part of a transform."""
    columns = []
    for axis in range(3):
        basis = [0.0, 0.0, 0.0]
        basis[axis] = scale[axis]
        columns.append(lane_a.quat_rotate(rotation, basis))
    return columns


def _determinant(columns):
    a, b, c = columns
    return (a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0]))


def assert_letter_f_not_mirrored(document):
    """The asymmetric canary mesh: its transform must not flip orientation.

    Boxes, spheres and cylinders are all mirror-symmetric, so SM_LetterF is the
    only actor in the fixture whose mirroring is visible at all (plan M0). Two
    things are asserted: the actor transform is orientation-preserving
    (determinant > 0), and the mesh's own bounds still match the UE-side
    measurement from S0.2 after Lane A -- which is what would catch an axis
    flip introduced in the exporter rather than in the transform.
    """
    entities = by_name(document)
    entity = entities.get("SM_LetterF")
    if not check(entity is not None, "fixture is missing SM_LetterF"):
        return

    for space in ("world", "local"):
        transform = entity["transform"][space]
        determinant = _determinant(_linear_matrix(transform["rotation"], transform["scale"]))
        check(determinant > 0.0,
              "SM_LetterF %s transform is mirrored (determinant %r)" % (space, determinant))

    assets = {asset["guid"]: asset for asset in document["assets"]}
    mesh_asset = assets.get(entity.get("mesh", {}).get("asset_guid"))
    if not check(mesh_asset is not None, "SM_LetterF has no mesh asset entry"):
        return

    if not os.path.exists(UE_REFERENCE_PATH):
        fail("S0.2 UE reference is missing: " + UE_REFERENCE_PATH)
        return
    with open(UE_REFERENCE_PATH, "r") as handle:
        reference = json.load(handle)

    corner_a = lane_a.convert_position(reference["bounds_min"])
    corner_b = lane_a.convert_position(reference["bounds_max"])
    expected_min = [min(corner_a[i], corner_b[i]) for i in range(3)]
    expected_max = [max(corner_a[i], corner_b[i]) for i in range(3)]

    bounds = mesh_asset["bounds_local"]
    for index, axis in enumerate("xyz"):
        if abs(bounds["min"][index] - expected_min[index]) > LENGTH_TOLERANCE:
            fail("SM_LetterF bounds min.%s is %r, UE reference converts to %r"
                 % (axis, bounds["min"][index], expected_min[index]))
        if abs(bounds["max"][index] - expected_max[index]) > LENGTH_TOLERANCE:
            fail("SM_LetterF bounds max.%s is %r, UE reference converts to %r"
                 % (axis, bounds["max"][index], expected_max[index]))

    # If the canary were ever symmetric about a plane, a mirror across that
    # plane would pass every assertion above while changing the level. The
    # check is per-axis for that reason: the mesh built in M0 was asymmetric in
    # Z only, so it could not have caught the left-right flip that Lane A's
    # basis map exists to get right.
    centroid = lane_a.convert_position(reference["centroid"])
    center = [(expected_min[i] + expected_max[i]) * 0.5 for i in range(3)]
    for index, axis in enumerate("xyz"):
        offset = abs(centroid[index] - center[index])
        check(offset > 0.01,
              "SM_LetterF is symmetric about %s (centroid offset %.4f m); a "
              "mirror across that plane would be undetectable"
              % (axis.upper(), offset))


def assert_hierarchy_composes(document):
    """The rotated child under the rotated parent must land where UE put it.

    UE reported the child's world transform directly; the exporter converted
    that and the child's parent-relative transform independently. If Lane A
    were not a homomorphism the two would disagree, which is exactly how a
    handedness bug shows up in a hierarchy.
    """
    entities = by_name(document)
    child = entities.get("RotatedChild_Sphere")
    parent = entities.get("RotatedParent_Cube")
    if not check(child is not None and parent is not None,
                 "fixture is missing the rotated parent/child pair"):
        return
    check(child["parent_id"] == parent["id"],
          "RotatedChild_Sphere is not attached to RotatedParent_Cube")

    composed = lane_a.compose(parent["transform"]["world"], child["transform"]["local"])
    world = child["transform"]["world"]

    for index, axis in enumerate("xyz"):
        if abs(composed["translation"][index] - world["translation"][index]) > LENGTH_TOLERANCE:
            fail("RotatedChild_Sphere world translation.%s is %r; parent o local gives %r"
                 % (axis, world["translation"][index], composed["translation"][index]))

    expected = composed["rotation"]
    if expected[3] < 0.0:
        expected = [-component for component in expected]
    for index, axis in enumerate("xyzw"):
        if abs(expected[index] - world["rotation"][index]) > ROTATION_TOLERANCE:
            fail("RotatedChild_Sphere world rotation.%s is %r; parent o local gives %r"
                 % (axis, world["rotation"][index], expected[index]))

    # A composition test on an identity parent proves nothing.
    check(any(abs(component) > 1e-3 for component in parent["transform"]["world"]["rotation"][:3]),
          "RotatedParent_Cube is not actually rotated; the composition test is vacuous")
    check(any(abs(component) > 1e-3 for component in child["transform"]["local"]["rotation"][:3]),
          "RotatedChild_Sphere is not actually rotated relative to its parent")


def assert_physics_flags(document):
    entities = by_name(document)

    cube = entities.get("Cube_Dynamic")
    if check(cube is not None, "fixture is missing Cube_Dynamic"):
        physics = cube.get("physics")
        if check(physics is not None, "Cube_Dynamic has no physics block"):
            check(physics["simulates_physics"] is True,
                  "Cube_Dynamic simulates_physics is %r" % physics["simulates_physics"])
            check(physics["kinematic"] is False,
                  "Cube_Dynamic must not also be kinematic")

    kinematic = entities.get("Cube_Kinematic")
    if check(kinematic is not None, "fixture is missing Cube_Kinematic"):
        physics = kinematic.get("physics")
        if check(physics is not None, "Cube_Kinematic has no physics block"):
            check(physics["kinematic"] is True,
                  "Cube_Kinematic kinematic is %r" % physics["kinematic"])
            check(physics["simulates_physics"] is False,
                  "Cube_Kinematic must not simulate")

    trigger = entities.get("TriggerBox_01")
    if check(trigger is not None, "fixture is missing TriggerBox_01"):
        physics = trigger.get("physics")
        if check(physics is not None, "TriggerBox_01 has no physics block"):
            check(physics["is_trigger"] is True,
                  "TriggerBox_01 is_trigger is %r" % physics["is_trigger"])
            check(trigger["kind"] == "trigger",
                  "TriggerBox_01 kind is %r" % trigger["kind"])
            check(len(physics["shapes"]) == 1,
                  "TriggerBox_01 should own exactly one shape, has %d"
                  % len(physics["shapes"]))

    # Render-only actors must carry no physics at all (plan M3 mapping table).
    for name in ("Light_Point", "Light_Spot", "Light_Directional"):
        entity = entities.get(name)
        if entity is not None and "physics" in entity:
            fail("%s has a physics block but does not collide" % name)


def assert_coverage(document):
    """Nothing may be silently dropped (plan constraint 9)."""
    entities = by_name(document)
    expected_names = {
        "Fixture_Floor", "Prim_Box", "Prim_Sphere", "Prim_Cylinder", "SM_LetterF",
        "SM_TwoTone", "RotatedParent_Cube", "RotatedChild_Sphere", "Cube_Dynamic",
        "Cube_Kinematic", "TriggerBox_01", "Light_Point", "Light_Spot",
        "Light_Directional", "Light_Point_Lumens", "Light_Spot_Lumens",
        "Light_Point_EV", "Light_Point_Unitless",
        "Atmo_SkyLight", "Atmo_HeightFog", "Atmo_SkyAtmosphere", "PPV_01",
        "MirroredF", "RotationFold_Box", "BP_Like_Props",
        "BP_Like_Props.StaticMesh", "BP_Like_Props.StaticMesh1",
        "SkelWave", "SkelRootMotion", "SkelBind",
    }
    missing = sorted(expected_names - set(entities))
    check(not missing, "fixture actors missing from the manifest: %r" % missing)

    # Every actor the exporter did not fully map must say so in warnings[].
    # An environment actor that carries an `environment` block IS mapped (M6),
    # so only the ones without a payload still owe a record.
    coded = {(record["subject"], record["code"]) for record in document["warnings"]}
    for name, entity in entities.items():
        if entity["kind"] == "unknown" or (
                entity["kind"] == "environment" and "environment" not in entity):
            has_record = any(subject == name for subject, _code in coded)
            check(has_record,
                  "%s is unmapped (kind=%s) but produced no warnings[] record"
                  % (name, entity["kind"]))

    check(not any(record["severity"] == "error" for record in document["warnings"]),
          "manifest carries error-severity warnings: %r"
          % [r["code"] for r in document["warnings"] if r["severity"] == "error"])

    # Dedup by asset GUID: three actors share the engine cube, two share the sphere.
    mesh_assets = [a for a in document["assets"] if a["kind"] == "static_mesh"]
    paths = [a["ue_path"] for a in mesh_assets]
    check(len(paths) == len(set(paths)), "static mesh assets are not deduplicated")
    used = [e["mesh"]["asset_guid"] for e in document["entities"] if "mesh" in e]
    check(len(used) > len(set(used)),
          "no mesh is shared between actors; the dedup assertion is vacuous")


def assert_skeletal_canaries(document):
    """The M8 canaries, at the manifest level.

    SkelWave: single-node Wave -> a skeletal block referencing an animation
    asset with root_motion False. SkelRootMotion: its (duplicated) anim has
    root_motion True and the export says so in warnings[]. SkelBind: no
    animation at all -- the Actor-only path must survive, or a fixture edit
    could quietly turn every skeletal import into the animated shape only.
    The deep product checks live in Tests/m8/test_m8_artifacts.py.
    """
    entities = by_name(document)
    assets = {asset["guid"]: asset for asset in document["assets"]}

    wave = entities.get("SkelWave")
    if check(wave is not None, "fixture is missing SkelWave"):
        check(wave["kind"] == "skeletal_mesh", "SkelWave kind %r" % wave["kind"])
        skeletal = wave.get("skeletal") or {}
        mesh_asset = assets.get(skeletal.get("asset_guid"))
        check(mesh_asset is not None and mesh_asset["kind"] == "skeletal_mesh",
              "SkelWave does not reference a skeletal_mesh asset")
        if mesh_asset is not None:
            check(mesh_asset.get("bone_count", 0) > 0
                  and len(mesh_asset.get("bone_names") or [])
                  == mesh_asset.get("bone_count"),
                  "SkelWave's mesh asset bone table is inconsistent")
        animation = assets.get(skeletal.get("animation_guid"))
        check(animation is not None and animation["kind"] == "animation",
              "SkelWave does not reference an animation asset")
        if animation is not None:
            check(animation.get("root_motion") is False,
                  "SkelWave's animation unexpectedly has root motion")

    root_motion = entities.get("SkelRootMotion")
    if check(root_motion is not None, "fixture is missing SkelRootMotion"):
        animation = assets.get((root_motion.get("skeletal") or {}).get("animation_guid"))
        check(animation is not None and animation.get("root_motion") is True,
              "SkelRootMotion's animation does not carry the root-motion flag")
        coded = {(record["code"], record["subject"])
                 for record in document["warnings"]}
        check(("ANIM_ROOT_MOTION_DROPPED", "SkelRootMotion") in coded,
              "root motion dropped without an ANIM_ROOT_MOTION_DROPPED record")

    bind = entities.get("SkelBind")
    if check(bind is not None, "fixture is missing SkelBind"):
        skeletal = bind.get("skeletal") or {}
        check(skeletal.get("animation_guid") is None,
              "SkelBind must be motionless (the Actor-only path)")
        check("physics" not in bind,
              "skeletal entities must not carry physics blocks "
              "(SKEL_PHYSICS_DROPPED)")


def assert_two_tone_slots(document):
    """The per-slot canary must actually be multi-material in the manifest.

    If a fixture edit ever collapsed SM_TwoTone to one slot (or one material),
    every downstream per-slot check would pass vacuously -- same failure shape
    as a symmetric mirror canary."""
    entities = by_name(document)
    entity = entities.get("SM_TwoTone")
    if not check(entity is not None, "fixture is missing SM_TwoTone"):
        return
    slots = entity.get("mesh", {}).get("material_slots", [])
    check(len(slots) == 2, "SM_TwoTone has %d slots, expected 2" % len(slots))
    guids = [slot.get("material_guid") for slot in slots]
    check(all(guids) and len(set(guids)) == 2,
          "SM_TwoTone slots do not carry two distinct materials: %r" % guids)

    # The actor OVERRIDES a slot, so its effective material differs from the
    # mesh asset's own. That difference is the whole point of the canary: the
    # FBX carries the ASSET's material names, so an importer that matches a
    # slot by its effective material name finds nothing. Without an override
    # the two names coincide and the bug is invisible -- which is exactly how
    # it reached a real level and silently un-styled 97 trees.
    assets = {asset["guid"]: asset for asset in document["assets"]}
    mesh_asset = assets.get(entity["mesh"]["asset_guid"], {})
    asset_slot_names = mesh_asset.get("material_slot_material_names") or []
    check(len(asset_slot_names) == 2,
          "SM_TwoTone's mesh asset should record 2 slot material names, got %r"
          % (asset_slot_names,))
    effective = [assets[guid]["name"] for guid in guids]
    check(asset_slot_names != effective,
          "SM_TwoTone's effective materials %r match its asset's slot materials "
          "%r; the actor's material override is gone and the label-vs-override "
          "distinction is untested" % (effective, asset_slot_names))


def assert_negative_scale_canaries(document):
    """The negative-scale fidelity paths, at the manifest level (M4.5).

    MirroredF (UE scale (-1,1,1), odd negatives): positive exported scale, a
    mesh reference to the `#mx` mirrored variant, and a variant asset whose
    bounds/shapes are the mirror of the base's.
    RotationFold_Box (UE scale (1,-2,-0.5), even negatives): positive scale
    (1,2,0.5), the 180 folded into the rotation, and the BASE mesh -- a
    rotation needs no variant.
    BP_Like_Props: an unmapped-class actor whose two StaticMeshComponents
    export as child entities (the component-extraction path).
    """
    entities = by_name(document)
    assets = {asset["guid"]: asset for asset in document["assets"]}

    mirrored = entities.get("MirroredF")
    if check(mirrored is not None, "fixture is missing MirroredF"):
        asset = assets.get(mirrored.get("mesh", {}).get("asset_guid"))
        check(asset is not None and asset["ue_path"].endswith("#mx"),
              "MirroredF must reference the #mx variant, got %r"
              % (asset and asset["ue_path"]))
        check(asset is not None and asset["fbx_node_name"].endswith("_MX"),
              "the variant's FBX node must be <name>_MX")
        base = next((a for a in document["assets"]
                     if a["ue_path"] == "/Game/Meshes/SM_LetterF"), None)
        if asset is not None and base is not None:
            check(abs(asset["bounds_local"]["min"][0]
                      + base["bounds_local"]["max"][0]) < 1e-6,
                  "variant bounds are not the X-mirror of the base's")
            check(asset["material_slot_material_names"]
                  == base["material_slot_material_names"],
                  "the variant must carry the base's slot material names")
        rotation = mirrored["transform"]["world"]["rotation"]
        check(abs(rotation[3]) > 0.999,
              "(-1,1,1) folds with SIGMA_rot = identity; rotation should be "
              "identity, got %r" % (rotation,))

    fold = entities.get("RotationFold_Box")
    if check(fold is not None, "fixture is missing RotationFold_Box"):
        asset = assets.get(fold.get("mesh", {}).get("asset_guid"))
        check(asset is not None and "#" not in asset["ue_path"],
              "an even-negative scale is a rotation and must use the BASE mesh")
        scale = fold["transform"]["world"]["scale"]
        check(all(abs(component - expected) < 1e-5
                  for component, expected in zip(scale, (1.0, 2.0, 0.5))),
              "RotationFold_Box scale should be (1,2,0.5), got %r" % (scale,))
        rotation = fold["transform"]["world"]["rotation"]
        check(abs(abs(rotation[0]) - 1.0) < 1e-5,
              "(1,-2,-0.5) folds to Rx(180); |rotation.x| should be 1, got %r"
              % (rotation,))

    props = entities.get("BP_Like_Props")
    if check(props is not None, "fixture is missing BP_Like_Props"):
        for child_name in ("BP_Like_Props.StaticMesh", "BP_Like_Props.StaticMesh1"):
            child = entities.get(child_name)
            if not check(child is not None, "missing extracted entity " + child_name):
                continue
            check(child["parent_id"] == props["id"],
                  "%s must parent to the BP actor's entity" % child_name)
            check(child["kind"] == "static_mesh" and "mesh" in child,
                  "%s must be a mesh entity" % child_name)
        codes = {(record["subject"], record["code"]) for record in document["warnings"]}
        check(("BP_Like_Props", "ACTOR_COMPONENTS_EXTRACTED") in codes,
              "component extraction must be reported")


def assert_light_units_covered(document):
    """Every intensity-unit mode M5 converts must exist in the fixture.

    M5's conversions are per-unit and each has its own arithmetic; a fixture
    that only carried candelas would let three of the four paths rot
    untested while every suite stayed green."""
    units = {entity["light"]["intensity_units"]
             for entity in document["entities"] if "light" in entity}
    for wanted in ("candelas", "lumens", "ev", "unitless", "lux"):
        check(wanted in units,
              "no fixture light uses intensity units %r; M5's conversion for "
              "it is untested (present: %r)" % (wanted, sorted(units)))

    # A lumens SPOT is what proves the cone's solid angle is used rather than
    # the full sphere -- a point light in lumens cannot tell the two apart.
    spots = [entity for entity in document["entities"]
             if entity.get("light", {}).get("type") == "spot"
             and entity["light"]["intensity_units"] == "lumens"]
    check(spots, "no spot light uses lumens; the cone solid-angle conversion "
                 "would be untested end to end")


def assert_environment_covered(document):
    """Every M6 environment type, and a PPV that actually overrides something.

    UE applies a post-process setting only when its `override_*` flag is set,
    so the exporter carries overridden settings only. A fixture whose PPV
    overrode nothing would exercise none of that: the override filter, the
    mapped settings and the unmapped-setting report would all be untested
    against an empty dictionary.
    """
    blocks = {entity["name"]: entity["environment"]
              for entity in document["entities"] if "environment" in entity}
    kinds = {block["type"] for block in blocks.values()}
    for wanted in ("skylight", "fog", "post_process", "sky_atmosphere"):
        check(wanted in kinds,
              "no fixture actor exports environment type %r (present: %r)"
              % (wanted, sorted(kinds)))

    ppv = next((block for block in blocks.values()
                if block["type"] == "post_process"), None)
    if check(ppv is not None, "fixture has no post-process volume"):
        overrides = ppv.get("overrides") or {}
        check(len(overrides) >= 2,
              "the fixture PPV overrides %d settings; the override path needs "
              "at least a couple to be meaningful" % len(overrides))
        codes = {record["code"] for record in document["warnings"]}
        check("ENV_POSTPROCESS_UNMAPPED" in codes,
              "no overridden-but-unmapped post-process setting in the fixture; "
              "a regression that silently dropped them would look identical to "
              "correct behaviour")

    fog = next((block for block in blocks.values() if block["type"] == "fog"), None)
    if check(fog is not None, "fixture has no fog actor"):
        # start_distance is a length: it must have gone through Lane A (cm->m),
        # so the UE value of 500 cm must not still be 500.
        check(fog["start_distance"] < 100.0,
              "fog start_distance is %r; lengths are metres in the manifest, so "
              "a UE centimetre value was carried through unconverted"
              % fog["start_distance"])


def assert_generator(document):
    version = document["generator"]["engine_version"]
    check(version.startswith(EXPECTED_ENGINE_PREFIX),
          "engine is %r; the plan pins UE %s (no version-conditional code)"
          % (version, EXPECTED_ENGINE_PREFIX))
    check(document["schema_version"] == manifest_module.SCHEMA_VERSION,
          "schema_version drifted from the exporter's")


# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    parser.add_argument("--golden", default=GOLDEN_PATH)
    parser.add_argument("--update-golden", action="store_true",
                        help="overwrite the golden file from the current export")
    args = parser.parse_args(argv)

    if not os.path.exists(args.manifest):
        print("FAIL: manifest not found: %s (run Tests/ue/export_fixture_manifest.py)"
              % args.manifest)
        return 1
    with open(args.manifest, "r") as handle:
        document = json.load(handle)

    log("== 1. schema + referential validation ==")
    errors = validate_manifest.validate(document, validate_manifest.load_schema())
    for error in errors:
        fail("validator: " + error)
    if not errors:
        log("  ok   %d entities, %d assets, %d warnings"
            % (len(document["entities"]), len(document["assets"]),
               len(document["warnings"])))

    log("== 2. golden file diff ==")
    if args.update_golden:
        os.makedirs(os.path.dirname(args.golden), exist_ok=True)
        with open(args.golden, "w") as handle:
            handle.write(manifest_module.dumps(_without_ignored(document)))
        log("  golden REGENERATED: " + args.golden)
        log("  review the diff before committing -- it is the M1 contract")
    elif not os.path.exists(args.golden):
        fail("golden file missing: %s (regenerate with --update-golden)" % args.golden)
    else:
        with open(args.golden, "r") as handle:
            golden = json.load(handle)
        differences = diff(_without_ignored(document), golden)
        for difference in differences[:40]:
            fail("golden: " + difference)
        if len(differences) > 40:
            fail("golden: ... and %d more differences" % (len(differences) - 40))
        if not differences:
            log("  ok   matches %s" % os.path.relpath(args.golden, REPO_ROOT))

    log("== 3. property assertions ==")
    for name, assertion in (
            ("all scales positive", assert_all_scales_positive),
            ("F mesh not mirrored", assert_letter_f_not_mirrored),
            ("hierarchy composes", assert_hierarchy_composes),
            ("physics flags", assert_physics_flags),
            ("coverage + dedup", assert_coverage),
            ("two-tone slot canary", assert_two_tone_slots),
            ("negative-scale canaries", assert_negative_scale_canaries),
            ("skeletal canaries", assert_skeletal_canaries),
            ("light unit coverage", assert_light_units_covered),
            ("environment coverage", assert_environment_covered),
            ("generator pins", assert_generator),
    ):
        before = len(_failures)
        assertion(document)
        log("  %s %s" % ("ok  " if len(_failures) == before else "FAIL", name))

    status = "PASS" if not _failures else "FAIL"
    log("RESULT: %s (%d failure(s))" % (status, len(_failures)))

    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    with open(RESULT_PATH, "w") as handle:
        handle.write("\n".join(_log) + "\n")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
