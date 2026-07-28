"""
test_m8_artifacts.py — M8 offline artifact assertions (no editor).

Three layers, each catching what the one above cannot:

  1. MANIFEST: schema 6 + the skeletal Lane B rule; the SK_Canary skeletal
     asset (40 bones, name table consistent); the three canary entities
     (Wave animated+looping, RootMotion warned, Bind motionless); no physics
     block on any skeletal entity.
  2. FBX INTERMEDIATES: the skeletal FBX carries skin (Deformer) and NO
     animation curves; each animation FBX carries curves and NO geometry;
     the skeletal FBX is mirror-Y(source) -- the native exporter's stage-2
     negation with no bake stage, checked against the UE-side reference
     bounds captured by add_m8_skeletal.py.
  3. PRODUCTS (staged + AP-processed by run_m2): the .actor product carries
     every manifest bone name (the plan's bone-count assertion, byte-level:
     EMotionFX exposes no bus to Python in 26.05); the .motion products
     carry joint tracks; and the skinned azmodel position buffer proves the
     SKELETAL Lane B rule the permanent M2 way -- the FBX Y extremes appear
     NEGATED (/100) in the product and the Z extremes appear unchanged, so a
     sign regression in either stage fails here, not in a screenshot.

Usage: python test_m8_artifacts.py [project_root] [export_dir]
"""

import json
import os
import struct
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "Tests", "lib"))

import fbx_reader  # noqa: E402

DEFAULT_PROJECT = r"C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"

PROJECT = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROJECT
EXPORT_DIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    REPO_ROOT, "Exports", "Fixture_01")
REFERENCE_PATH = os.path.join(REPO_ROOT, "Tests", "m8", "skel_reference.json")

SKEL_RELATIVE = "uetoo3de/game/skeletal/sk_canary.fbx"
CACHE_DIR = os.path.join(PROJECT, "Cache", "pc", "assets",
                         "uetoo3de", "game", "skeletal")

failures = []


def fail(message):
    failures.append(message)
    print("FAIL: " + message)


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def float_hits_near(blob, value, ulps=2):
    base = struct.unpack("<I", struct.pack("<f", value))[0]
    return sum(blob.count(struct.pack("<I", base + delta))
               for delta in range(-ulps, ulps + 1))


def load_manifest():
    path = os.path.join(EXPORT_DIR, "manifest.json")
    if not os.path.exists(path):
        fail("manifest missing: %s (run the fixture export first)" % path)
        return None
    with open(path) as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------

def test_manifest_contract(document):
    check(document["schema_version"] >= 6,
          "schema_version %r < 6 (predates the skeletal contract)"
          % document["schema_version"])
    check(document["units"].get("lane_b_skeletal_rule")
          == "native_y_scene_rz180_entity_rz180",
          "lane_b_skeletal_rule is %r" % document["units"].get("lane_b_skeletal_rule"))

    assets = {a["name"]: a for a in document["assets"]}
    canary = assets.get("SK_Canary")
    if not check(canary is not None, "SK_Canary skeletal asset missing"):
        return
    check(canary["kind"] == "skeletal_mesh", "SK_Canary kind %r" % canary["kind"])
    check(canary.get("bone_count") == 40,
          "bone_count %r != 40 (the imported Quaternius rig)" % canary.get("bone_count"))
    names = canary.get("bone_names") or []
    check(len(names) == canary.get("bone_count"),
          "bone_names length %d != bone_count" % len(names))
    for bone in ("Root", "Foot_L", "Foot_R", "CharacterArmature"):
        check(bone in names, "bone %r missing from bone_names" % bone)

    animations = {a["name"]: a for a in document["assets"]
                  if a["kind"] == "animation"}
    check("Anim_Walk_RM" in animations, "Anim_Walk_RM animation asset missing")
    check("SK_CanaryCharacterArmature_Wave" in animations,
          "Wave animation asset missing")
    if "Anim_Walk_RM" in animations:
        check(animations["Anim_Walk_RM"].get("root_motion") is True,
              "Anim_Walk_RM.root_motion is not True")
    if "SK_CanaryCharacterArmature_Wave" in animations:
        check(animations["SK_CanaryCharacterArmature_Wave"].get("root_motion") is False,
              "Wave.root_motion should be False")
        check(animations["SK_CanaryCharacterArmature_Wave"].get("duration_seconds", 0) > 1.0,
              "Wave duration suspiciously short")

    entities = {e["name"]: e for e in document["entities"]}
    for name in ("SkelWave", "SkelRootMotion", "SkelBind"):
        entity = entities.get(name)
        if not check(entity is not None, "%s entity missing" % name):
            continue
        check(entity["kind"] == "skeletal_mesh", "%s kind %r" % (name, entity["kind"]))
        check("physics" not in entity,
              "%s carries a physics block; skeletal physics is a documented "
              "drop (SKEL_PHYSICS_DROPPED)" % name)
        skeletal = entity.get("skeletal") or {}
        check(skeletal.get("asset_guid") == canary["guid"],
              "%s does not reference SK_Canary" % name)
    if "SkelWave" in entities:
        skeletal = entities["SkelWave"]["skeletal"]
        check(skeletal["animation_guid"] is not None, "SkelWave has no animation")
        check(skeletal["loop"] is True and skeletal["play"] is True,
              "SkelWave loop/play flags wrong: %r/%r"
              % (skeletal["loop"], skeletal["play"]))
    if "SkelBind" in entities:
        check(entities["SkelBind"]["skeletal"]["animation_guid"] is None,
              "SkelBind should be motionless (Actor component only)")

    warning_index = {(w["code"], w["subject"]) for w in document["warnings"]}
    check(("ANIM_ROOT_MOTION_DROPPED", "SkelRootMotion") in warning_index,
          "ANIM_ROOT_MOTION_DROPPED not reported for SkelRootMotion")
    for name in ("SkelWave", "SkelRootMotion", "SkelBind"):
        check(("SKEL_PHYSICS_DROPPED", name) in warning_index,
              "SKEL_PHYSICS_DROPPED not reported for %s" % name)


def test_fbx_intermediates(document):
    assets_root = os.path.join(EXPORT_DIR, "Assets")
    by_kind = {}
    for asset in document["assets"]:
        by_kind.setdefault(asset["kind"], []).append(asset)

    for asset in by_kind.get("skeletal_mesh", []):
        path = os.path.join(assets_root, asset["o3de_relative_path"])
        if not check(os.path.exists(path), "missing FBX %s" % path):
            continue
        with open(path, "rb") as handle:
            blob = handle.read()
        check(blob.count(b"Deformer") > 0,
              "%s carries no skin deformers" % asset["name"])
        check(blob.count(b"AnimationCurveNode") == 0,
              "%s (a MESH export) carries animation curves; the .actor "
              "builder would embed a take" % asset["name"])

    for asset in by_kind.get("animation", []):
        path = os.path.join(assets_root, asset["o3de_relative_path"])
        if not check(os.path.exists(path), "missing FBX %s" % path):
            continue
        with open(path, "rb") as handle:
            blob = handle.read()
        check(blob.count(b"AnimationCurveNode") > 0,
              "%s carries no animation curves" % asset["name"])
        check(blob.count(b"Deformer") == 0,
              "%s (an ANIMATION export) carries skin deformers; "
              "export_preview_mesh leaked the mesh in" % asset["name"])

    # Mirror-Y check against the UE-side truth captured at canary time.
    if not os.path.exists(REFERENCE_PATH):
        fail("skel_reference.json missing (run add_m8_skeletal.py)")
        return
    with open(REFERENCE_PATH) as handle:
        reference = json.load(handle)
    bind = reference["canaries"]["SkelBind"]
    location = bind["actor_location_cm"]
    origin = bind["world_bounds_origin_cm"]
    extent = bind["world_bounds_extent_cm"]
    source_min = [origin[i] - extent[i] - location[i] for i in range(3)]
    source_max = [origin[i] + extent[i] - location[i] for i in range(3)]
    expected_min = [source_min[0], -source_max[1], source_min[2]]
    expected_max = [source_max[0], -source_min[1], source_max[2]]

    stats = fbx_reader.vertex_stats(os.path.join(assets_root, SKEL_RELATIVE))
    deltas = [max(abs(stats["min"][i] - expected_min[i]),
                  abs(stats["max"][i] - expected_max[i])) for i in range(3)]
    check(max(deltas) < 2.0,
          "sk_canary.fbx is not mirror-Y(source): fbx %s..%s vs expected "
          "%s..%s -- the native exporter's negation moved or a bake appeared"
          % ([round(v, 1) for v in stats["min"]],
             [round(v, 1) for v in stats["max"]],
             [round(v, 1) for v in expected_min],
             [round(v, 1) for v in expected_max]))


def test_products(document):
    if not os.path.isdir(CACHE_DIR):
        fail("cache products missing at %s -- run Tests/m2/run_m2.bat first "
             "(stage + AssetProcessor); an M8 suite that passes without "
             "products would hide exactly what it exists to catch" % CACHE_DIR)
        return

    actor_path = os.path.join(CACHE_DIR, "sk_canary.actor")
    if not check(os.path.exists(actor_path),
                 ".actor product missing: %s" % actor_path):
        return
    with open(actor_path, "rb") as handle:
        actor_blob = handle.read()
    canary = next(a for a in document["assets"] if a["name"] == "SK_Canary")
    missing = [name for name in canary["bone_names"]
               if actor_blob.count(name.encode("ascii")) == 0]
    check(not missing,
          "the .actor product is missing %d of %d manifest bones: %s -- the "
          "skeleton did not survive the FBX or the wrong scene rules ran"
          % (len(missing), len(canary["bone_names"]), missing[:8]))

    long_names = [n for n in canary["bone_names"] if len(n) > 5]
    for stem in ("sk_canarycharacterarmature_wave", "anim_walk_rm"):
        motion_path = os.path.join(CACHE_DIR, stem + ".motion")
        if not check(os.path.exists(motion_path),
                     ".motion product missing: %s" % motion_path):
            continue
        with open(motion_path, "rb") as handle:
            motion_blob = handle.read()
        present = sum(1 for name in long_names
                      if motion_blob.count(name.encode("ascii")) > 0)
        check(present >= len(long_names) // 2,
              "%s.motion carries joint tracks for only %d of %d joints"
              % (stem, present, len(long_names)))

    # The permanent skeletal Lane B byte check (the M2 technique): product
    # position floats = FBX floats with Y NEGATED and Z KEPT, /100.
    buffer_path = None
    for name in sorted(os.listdir(CACHE_DIR)):
        if name.startswith("sk_canary_lod") and "position" in name \
                and name.endswith(".azbuffer"):
            buffer_path = os.path.join(CACHE_DIR, name)
    if not check(buffer_path is not None,
                 "sk_canary position buffer missing in %s" % CACHE_DIR):
        return
    with open(buffer_path, "rb") as handle:
        product_blob = handle.read()
    stats = fbx_reader.vertex_stats(
        os.path.join(EXPORT_DIR, "Assets", SKEL_RELATIVE))

    for label, fbx_value, sign in (
            ("Y min", stats["min"][1], -1.0),
            ("Y max", stats["max"][1], -1.0),
            ("Z min", stats["min"][2], 1.0),
            ("Z max", stats["max"][2], 1.0)):
        expected = sign * fbx_value / 100.0
        wrong = -expected
        check(float_hits_near(product_blob, expected) >= 1,
              "skeletal product: expected %s pattern %.6f absent -- SceneAPI's "
              "conversion of skinned geometry changed; re-measure LANE_B.md M8"
              % (label, expected))
        check(float_hits_near(product_blob, wrong, ulps=0) == 0,
              "skeletal product: the WRONG-sign %s pattern %.6f is present"
              % (label, wrong))


def main():
    document = load_manifest()
    if document is not None:
        test_manifest_contract(document)
        test_fbx_intermediates(document)
        test_products(document)
    if failures:
        print("RESULT: FAIL (%d)" % len(failures))
        return 1
    print("RESULT: PASS (M8 artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
