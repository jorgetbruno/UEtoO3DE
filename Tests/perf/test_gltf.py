"""
test_gltf.py — addressing a glTF's nodes, which is not how FBX does it.

Pure: no editor. Run: python Tests/perf/test_gltf.py

TWO MEASURED FACTS underpin every assertion here, both found by dumping the
scene graph from inside the Scene Builder after four static guesses were
rejected (LANE_C_GLTF.md):

  1. UE's glTF exporter leaves the mesh node UNNAMED -- only `meshes[i].name`
     is set -- and SceneAPI selects by NODE.
  2. A glTF scene graph has NO `RootNode`. Its root path is empty and the mesh
     sits directly beneath it, so `RootNode.<node>` -- correct for FBX --
     names nothing. The graph dump read:

         node                        content=False   <- root, empty path
         node nodes[0]               content=True    <- the mesh

With the node named and the prefix dropped, a UE glTF produced BOTH
`sm_letterf.gltf.azmodel` and `sm_letterf.gltf.joltmesh` with zero AP errors.

The FBX path must not move. Its selection is byte-pinned by test_pxmesh.py,
and every assertion below that touches FBX is here to catch a glTF change that
leaks into it.
"""

import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import assetinfo  # noqa: E402
from ueimporter import gltf_source  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# --- 1. which formats are recognised -----------------------------------------
for path in ("a/b.gltf", "A/B.GLTF", "x.gltf"):
    check(gltf_source.is_gltf(path), "%r should be recognised as glTF" % path)
for path in ("a/b.fbx", "a/b.glb", "a/b.gltf.assetinfo", "a/bgltf"):
    check(not gltf_source.is_gltf(path), "%r must NOT be treated as glTF" % path)
check(gltf_source.is_glb("a/b.glb") and gltf_source.is_glb("A/B.GLB"),
      ".glb must be recognised -- it needs different handling, not none")

# --- 2. the path shape, per format -------------------------------------------
check(gltf_source.node_path("Mesh", "a/b.fbx") == "RootNode.Mesh",
      "an FBX graph is rooted at RootNode; that path is measured and must not "
      "change")
check(gltf_source.node_path("Mesh", "a/b.gltf") == "Mesh",
      "a glTF graph's root is UNNAMED, so the path carries no prefix")
check(gltf_source.node_path("Mesh", "a/b.glb") == "Mesh",
      "a .glb is the same graph shape as a .gltf")
check(gltf_source.node_path("Mesh", None) == "RootNode.Mesh",
      "an unknown source must default to the FBX shape: that is what every "
      "existing caller means, and silently switching it would break them")

check(gltf_source.root_path("a/b.fbx") == ["RootNode"],
      "the FBX physics group unselects the root by name")
check(gltf_source.root_path("a/b.gltf") == [],
      "a glTF root has no name to unselect")

# --- 3. naming the mesh nodes -------------------------------------------------
work = tempfile.mkdtemp(prefix="ueo3de_gltf_")


def write_gltf(name, nodes, meshes=None):
    path = os.path.join(work, name)
    document = {"asset": {"version": "2.0"}, "nodes": nodes,
                "meshes": meshes if meshes is not None else [{"name": "M"}]}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    return path


# UE's actual shape: one mesh node, no name.
path = write_gltf("unnamed.gltf", [{"mesh": 0}])
check(gltf_source.mesh_node_count(path) == 1, "one mesh node expected")
check(gltf_source.name_mesh_nodes(path, "SM_Thing") == 1,
      "the unnamed mesh node should have been renamed")
with open(path, encoding="utf-8") as handle:
    check(json.load(handle)["nodes"][0]["name"] == "SM_Thing",
          "the node name must actually reach the file")
check(gltf_source.name_mesh_nodes(path, "SM_Thing") == 0,
      "renaming to the same name twice must be a no-op, or every restage "
      "rewrites the file and re-fingerprints it in the Asset Processor")

# Nodes without a mesh are transforms and must be left alone.
path = write_gltf("mixed.gltf", [{"name": "Pivot"}, {"mesh": 0}])
check(gltf_source.mesh_node_count(path) == 1,
      "a transform-only node is not a mesh node")
gltf_source.name_mesh_nodes(path, "SM_Thing")
with open(path, encoding="utf-8") as handle:
    nodes = json.load(handle)["nodes"]
check(nodes[0]["name"] == "Pivot",
      "a node with no mesh must keep its name; renaming it would move the "
      "hierarchy out from under the selection")
check(nodes[1]["name"] == "SM_Thing", "the mesh node should be named")

# Two mesh nodes: naming them alike would make the selection ambiguous.
path = write_gltf("two.gltf", [{"mesh": 0}, {"mesh": 1}],
                  meshes=[{"name": "A"}, {"name": "B"}])
check(gltf_source.mesh_node_count(path) == 2,
      "both mesh nodes must be counted -- staging refuses this case rather "
      "than picking one silently")

# .glb must refuse loudly rather than pretend.
glb = os.path.join(work, "thing.glb")
with open(glb, "wb") as handle:
    handle.write(b"glTF\x02\x00\x00\x00")
try:
    gltf_source.name_mesh_nodes(glb, "SM_Thing")
    check(False, "naming nodes in a .glb must raise: its JSON is in a binary "
                 "chunk, and returning 0 would leave an unaddressable file "
                 "that fails later in an AP job")
except ValueError:
    pass

# --- 4. the sidecar the two formats produce ----------------------------------
physics = {"method": "trimesh", "elements": 0, "decompose_hulls": None}
fbx = assetinfo.build("g", "N", physics=physics, backends=("jolt",),
                      source_path="x/g.fbx")
gltf = assetinfo.build("g", "N", physics=physics, backends=("jolt",),
                       source_path="x/g.gltf")

check(fbx["values"][0]["nodeSelectionList"]["selectedNodes"] == ["RootNode.N"],
      "FBX render selection must be unchanged")
check(fbx["values"][1]["NodeSelectionList"] == {
          "selectedNodes": ["RootNode.N"], "unselectedNodes": ["RootNode"]},
      "FBX physics selection must be unchanged")
check(gltf["values"][0]["nodeSelectionList"]["selectedNodes"] == ["N"],
      "glTF render selection carries no RootNode prefix")
check(gltf["values"][1]["NodeSelectionList"] == {
          "selectedNodes": ["N"], "unselectedNodes": []},
      "glTF physics selection carries no prefix and unselects nothing")

default = assetinfo.build("g", "N", physics=physics, backends=("jolt",))
check(default["values"] == fbx["values"],
      "omitting source_path must produce exactly the FBX document -- every "
      "existing caller relies on it, and test_pxmesh byte-pins those bytes")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
