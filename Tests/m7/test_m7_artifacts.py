"""
test_m7_artifacts.py — M7 terrain checks that need no editor.

Runs against an export directory that CONTAINS A LANDSCAPE. The fixture
cannot: landscape creation is impossible in a scripted headless session (the
engine asserts `!IsRunningCommandlet()` on spawn -- measured,
probe_m7_create) and the editor-UI creation path cannot be automated, so M7's
artifact and acceptance tests take the export directory as an argument,
defaulting to Exports/L_Showcase. A missing directory is a hard FAIL: a
terrain suite that silently passes with no terrain in sight is worse than one
that demands its input.

Asserts:
  * the manifest carries exactly the terrain contract: a static_mesh entity
    at IDENTITY transform whose asset ue_path ends in '#terrain', collision
    source "none" (the importer's render-mesh triangle-collider path), one
    material slot;
  * terrain_samples.json exists with >= 5 O3DE-space points, all inside the
    terrain asset's converted bounds;
  * the terrain FBX exists and its bounds are the mirror-X of the sampled
    world bounds (the normal Lane B intermediate rule);
  * the heightmap TGA side artifact exists and parses.

Run:  python Tests/m7/test_m7_artifacts.py [export_dir]
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "Tests", "lib"))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

import fbx_reader  # noqa: E402
from ueo3de import tga  # noqa: E402

failures = []


def fail(message):
    failures.append(str(message))
    print("FAIL: " + str(message))


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def main():
    export_dir = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(REPO_ROOT, "Exports", "L_Showcase")
    manifest_path = os.path.join(export_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        print("FAIL: no manifest at %s -- M7's tests need an export that "
              "contains a Landscape (the fixture cannot; see the module "
              "docstring)" % manifest_path)
        return 1
    with open(manifest_path) as handle:
        document = json.load(handle)

    print("== terrain entity + asset contract ==")
    assets = {a["guid"]: a for a in document["assets"]}
    terrain_assets = [a for a in document["assets"]
                      if a["kind"] == "static_mesh"
                      and a["ue_path"].endswith("#terrain")]
    check(len(terrain_assets) == 1,
          "expected exactly one #terrain asset, found %d" % len(terrain_assets))
    if not terrain_assets:
        return 1
    asset = terrain_assets[0]
    check(asset["collision"]["source"] == "none" and not asset["collision"]["shapes"],
          "terrain collision must be source=none (the importer's render-mesh "
          "triangle collider path)")

    entity = next((e for e in document["entities"]
                   if e.get("mesh", {}).get("asset_guid") == asset["guid"]), None)
    if check(entity is not None, "no entity references the terrain asset"):
        world = entity["transform"]["world"]
        check(world["translation"] == [0.0, 0.0, 0.0]
              and world["rotation"][3] == 1.0
              and world["scale"] == [1.0, 1.0, 1.0],
              "the terrain entity must sit at IDENTITY (the mesh is baked in "
              "world space), got %r" % (world,))
        check(entity["kind"] == "static_mesh", "terrain entity kind")
        check("physics" in entity and entity["physics"]["has_collision"],
              "the terrain entity must carry a physics block (static body + "
              "mesh collider)")
        codes = {w["code"] for w in document["warnings"]}
        check("TERRAIN_BAKED_TO_MESH" in codes and "TERRAIN_LAYERS_FLATTENED" in codes,
              "terrain warnings missing: %r"
              % (codes & {"TERRAIN_BAKED_TO_MESH", "TERRAIN_LAYERS_FLATTENED"}))
    print("  ok" if not failures else "  FAILED")

    print("== terrain samples ==")
    samples_path = os.path.join(export_dir, "terrain_samples.json")
    if check(os.path.exists(samples_path), "terrain_samples.json missing"):
        with open(samples_path) as handle:
            samples = json.load(handle)["samples"]
        check(len(samples) >= 5, "expected >= 5 samples, got %d" % len(samples))
        bounds = asset["bounds_local"]
        for point in samples:
            inside = all(bounds["min"][axis] - 1.0 <= point[axis] <= bounds["max"][axis] + 1.0
                         for axis in range(3))
            check(inside, "sample %r is outside the terrain bounds %r..%r"
                  % (point, bounds["min"], bounds["max"]))
    print("  ok" if not failures else "  FAILED")

    print("== terrain FBX + heightmap ==")
    fbx_path = os.path.join(export_dir, "Assets", asset["o3de_relative_path"])
    if check(os.path.exists(fbx_path), "terrain FBX missing: " + fbx_path):
        stats = fbx_reader.vertex_stats(fbx_path)
        check(stats["count"] > 1000,
              "terrain FBX has only %d vertices" % stats["count"])
        # Normal-entry intermediate rule: FBX = mirror-X(world geometry). The
        # sampled world bounds live in the manifest CONVERTED; recover the UE
        # X extent from them: converted x == UE x / 100.
        expected_min_x = -asset["bounds_local"]["max"][0] * 100.0
        check(abs(stats["min"][0] - expected_min_x) < 200.0,
              "terrain FBX min.x %.0f is not the mirror of the converted "
              "bounds (%.0f); the terrain bake skipped the Lane B mirror"
              % (stats["min"][0], expected_min_x))

    heightmaps = [name for name in os.listdir(export_dir)
                  if name.endswith("_heightmap.tga")]
    if check(len(heightmaps) == 1, "expected one heightmap TGA, found %r" % heightmaps):
        image = tga.read(os.path.join(export_dir, heightmaps[0]))
        check(image["width"] > 50 and image["height"] > 50,
              "heightmap is implausibly small: %dx%d"
              % (image["width"], image["height"]))
    print("  ok" if not failures else "  FAILED")

    print("")
    if failures:
        print("RESULT: FAIL (%d failure(s))" % len(failures))
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
