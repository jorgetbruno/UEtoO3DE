"""
test_m9_artifacts.py — M9 offline artifact assertions (no editor).

Fixture_02 gives every stretch feature exactly one actor, so each assertion
names its canary:

  Foliage_ISM   5 instance entities sharing ONE mesh asset, one rotated and
                one scaled (a symmetric instance set would let a transform
                bug pass) + ACTOR_INSTANCES_EXPANDED;
  SplineArch    a child entity over a '#spline' asset + SPLINE_BAKED; the
                exported FBX must carry the DEFORMED geometry (bent bounds);
  LodMesh       references SM_TwoLod + LOD_FLATTENED; the FBX carries the
                asymmetric LOD0 (letterF), not the cube LOD1;
  Decal_01      a decal block with material/extents/sort order +
                DECAL_MATERIAL_APPROX;
  Cam_Main      a camera block with the raw horizontal FOV + aspect.

Usage: python test_m9_artifacts.py [export_dir]
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "Tests", "lib"))

import fbx_reader  # noqa: E402

EXPORT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    REPO_ROOT, "Exports", "Fixture_02")

failures = []


def fail(message):
    failures.append(message)
    print("FAIL: " + message)


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def main():
    manifest_path = os.path.join(EXPORT_DIR, "manifest.json")
    if not check(os.path.exists(manifest_path),
                 "manifest missing at %s -- export Fixture_02 first"
                 % manifest_path):
        return
    with open(manifest_path) as handle:
        document = json.load(handle)
    check(document["schema_version"] == 7,
          "schema_version %r != 7" % document["schema_version"])

    entities = {e["name"]: e for e in document["entities"]}
    assets = {a["guid"]: a for a in document["assets"]}
    warning_index = {(w["code"], w["subject"]) for w in document["warnings"]}
    codes = {w["code"] for w in document["warnings"]}

    # --- foliage instances ---
    parent = entities.get("Foliage_ISM")
    if check(parent is not None, "Foliage_ISM missing"):
        children = [e for e in document["entities"]
                    if e["parent_id"] == parent["id"]]
        check(len(children) == 5,
              "Foliage_ISM expanded to %d entities, expected 5" % len(children))
        guids = {e["mesh"]["asset_guid"] for e in children if "mesh" in e}
        check(len(guids) == 1,
              "instances must share ONE mesh asset, got %d" % len(guids))
        rotated = [e for e in children
                   if abs(e["transform"]["world"]["rotation"][2]) > 0.2]
        check(len(rotated) >= 1, "no rotated instance survived the expansion")
        scaled = [e for e in children
                  if abs(e["transform"]["world"]["scale"][2] - 1.5) < 1e-3]
        check(len(scaled) == 1, "the z-scaled instance is missing")
        check("ACTOR_INSTANCES_EXPANDED" in codes,
              "ACTOR_INSTANCES_EXPANDED not reported")
        check("INSTANCES_TRUNCATED" not in codes,
              "5 instances must not trip the ceiling")

    # --- spline bake ---
    spline_children = [e for e in document["entities"]
                       if e.get("mesh") and
                       assets.get(e["mesh"]["asset_guid"], {}).get(
                           "ue_path", "").endswith("#spline")]
    if check(len(spline_children) == 1,
             "expected exactly 1 spline-baked entity, got %d"
             % len(spline_children)):
        spline_asset = assets[spline_children[0]["mesh"]["asset_guid"]]
        check(spline_asset["collision"]["source"] == "none",
              "spline bake must use the render-mesh collider path")
        check("SPLINE_BAKED" in codes, "SPLINE_BAKED not reported")
        fbx = os.path.join(EXPORT_DIR, "Assets",
                           spline_asset["o3de_relative_path"])
        if check(os.path.exists(fbx), "spline FBX missing: %s" % fbx):
            stats = fbx_reader.vertex_stats(fbx)
            z_span = stats["max"][2] - stats["min"][2]
            x_span = stats["max"][0] - stats["min"][0]
            check(z_span > 400.0,
                  "spline FBX z span %.1f; the 450 cm bend is missing" % z_span)
            check(x_span > 300.0,
                  "spline FBX x span %.1f; the geometry is NOT deformed -- "
                  "the bake exported the straight source" % x_span)

    # --- LODs ---
    lod = entities.get("LodMesh")
    if check(lod is not None, "LodMesh missing"):
        asset = assets.get(lod["mesh"]["asset_guid"])
        check(asset is not None and asset["ue_path"].endswith("SM_TwoLod"),
              "LodMesh must reference SM_TwoLod")
        check(("LOD_FLATTENED", "/Game/Meshes/SM_TwoLod") in warning_index,
              "LOD_FLATTENED not reported for SM_TwoLod")
        if asset is not None:
            fbx = os.path.join(EXPORT_DIR, "Assets", asset["o3de_relative_path"])
            if check(os.path.exists(fbx), "SM_TwoLod FBX missing"):
                stats = fbx_reader.vertex_stats(fbx)
                # LOD0 is the letterF: Y-asymmetric (the nub). The cube LOD1
                # would be perfectly symmetric -- this is the check that the
                # bake took LOD0.
                check(abs(abs(stats["min"][1]) - abs(stats["max"][1])) > 1.0,
                      "SM_TwoLod FBX is Y-symmetric; the bake took the wrong LOD")

    # --- decal ---
    decal = entities.get("Decal_01")
    if check(decal is not None, "Decal_01 missing"):
        check(decal["kind"] == "decal", "Decal_01 kind %r" % decal["kind"])
        block = decal.get("decal") or {}
        check(block.get("sort_order") == 7, "decal sort_order %r != 7"
              % block.get("sort_order"))
        expected = [0.64, 1.28, 1.92]
        got = block.get("half_extents_m") or []
        check(len(got) == 3 and all(abs(a - b) < 1e-4
                                    for a, b in zip(got, expected)),
              "decal half extents %r != %r" % (got, expected))
        material = assets.get(block.get("material_guid"))
        check(material is not None and material["kind"] == "material",
              "decal material does not resolve")
        check(("DECAL_MATERIAL_APPROX", "Decal_01") in warning_index,
              "DECAL_MATERIAL_APPROX not reported")

    # --- camera ---
    camera = entities.get("Cam_Main")
    if check(camera is not None, "Cam_Main missing"):
        check(camera["kind"] == "camera", "Cam_Main kind %r" % camera["kind"])
        block = camera.get("camera") or {}
        check(abs(block.get("fov_horizontal_deg", 0) - 72.0) < 1e-3,
              "camera fov %r != 72" % block.get("fov_horizontal_deg"))
        check(block.get("aspect_ratio", 0) > 1.0,
              "camera aspect ratio missing")

    check(not any(w["severity"] == "error" for w in document["warnings"]),
          "manifest carries error-severity warnings")


main()
if failures:
    print("RESULT: FAIL (%d)" % len(failures))
    raise SystemExit(1)
print("RESULT: PASS (M9 artifacts)")
