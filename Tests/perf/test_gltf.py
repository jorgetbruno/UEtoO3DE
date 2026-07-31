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
import shutil
import struct
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
    check(not gltf_source.is_gltf(path),
          "%r must NOT be the JSON container -- is_gltf names the CONTAINER, "
          "and reading a .glb as text would fail" % path)
check(gltf_source.is_glb("a/b.glb") and gltf_source.is_glb("A/B.GLB"),
      ".glb must be recognised as the binary container")
for path in ("a/b.gltf", "a/b.glb", "A/B.GLTF", "A/B.GLB"):
    check(gltf_source.is_gltf_source(path),
          "%r is glTF whatever the container: the SCENE GRAPH is the same, "
          "and that is what the selection path depends on" % path)
for path in ("a/b.fbx", "a/b.png", None):
    check(not gltf_source.is_gltf_source(path),
          "%r is not a glTF source" % (path,))

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

# --- 3b. the SAME rule inside the binary container ----------------------------
#
# Run against UE 5.8's OWN .glb, not a synthetic one. A hand-built container
# would only prove this code agrees with itself; the whole point of the GLB
# path is that it survives the bytes UE actually writes -- space-padded JSON
# chunk, NUL-padded BIN chunk, and a buffer whose byteLength EXCLUDES that pad.
#
# It lives in Tests/ue/data (TRACKED) and not in the probe's results directory,
# which `Tests/**/results/` ignores: a suite whose only fixture is ignored
# output passes here and fails on a fresh clone.
FIXTURE = os.path.join(REPO_ROOT, "Tests", "ue", "data", "SM_LetterF.glb")

if not os.path.exists(FIXTURE):
    check(False, "missing %s -- it is committed, so this means the working "
                 "tree is incomplete, not that a probe needs re-running"
                 % FIXTURE)
else:
    glb = os.path.join(work, "SM_LetterF.glb")
    shutil.copyfile(FIXTURE, glb)          # never mutate the fixture
    original = open(FIXTURE, "rb").read()

    before = gltf_source.read_glb_chunks(glb)
    check([kind for kind, _ in before] == [0x4E4F534A, 0x004E4942],
          "UE's .glb should be a JSON chunk then a BIN chunk; got %r"
          % ([hex(k) for k, _ in before],))
    bin_before = [data for kind, data in before if kind == 0x004E4942][0]

    check(gltf_source.mesh_node_count(glb) == 1,
          "the fixture has one mesh node, read through the container")
    check(gltf_source.load_document(glb)["nodes"][0].get("name") is None,
          "UE leaves the node UNNAMED in .glb exactly as in .gltf -- if this "
          "ever fails, the naming step is no longer the fix and the sidecar "
          "should use UE's own name")

    check(gltf_source.name_mesh_nodes(glb, "SM_LetterF") == 1,
          "the unnamed mesh node in the .glb should have been renamed")
    check(gltf_source.load_document(glb)["nodes"][0]["name"] == "SM_LetterF",
          "the name must survive the rewrite and be readable back")
    check(gltf_source.name_mesh_nodes(glb, "SM_LetterF") == 0,
          "a second naming pass must be a no-op for .glb too, or every "
          "restage rewrites 143 KB and re-fingerprints it in the AP")

    # The container must still be a valid container, and the payload must be
    # untouched. A wrong chunk length or a dropped pad byte produces a file
    # that still LOOKS like a .glb and fails deep inside an importer.
    after = gltf_source.read_glb_chunks(glb)      # re-parses, so it validates
    check([kind for kind, _ in after] == [0x4E4F534A, 0x004E4942],
          "the rewritten .glb must keep both chunks, in order")
    bin_after = [data for kind, data in after if kind == 0x004E4942][0]
    check(bin_after == bin_before,
          "the BIN chunk must come through BYTE-IDENTICAL, padding included: "
          "this module changes one node name and has no business re-deriving "
          "a %d-byte buffer" % len(bin_before))

    blob = open(glb, "rb").read()
    magic, version, declared = struct.unpack_from("<4sII", blob, 0)
    check(magic == b"glTF" and version == 2, "header must survive the rewrite")
    check(declared == len(blob),
          "the header's total length must match the file on disk: %d declared, "
          "%d written" % (declared, len(blob)))
    json_len = struct.unpack_from("<I", blob, 12)[0]
    check(json_len % 4 == 0,
          "the JSON chunk must stay 4-byte aligned; got %d" % json_len)
    check(blob[20:20 + json_len].endswith(b"}") or
          blob[20:20 + json_len].rstrip(b"\x20").endswith(b"}"),
          "the JSON chunk must be padded with SPACES -- a NUL-padded JSON "
          "chunk parses here but is invalid to a strict reader")
    check(len(blob) != len(original),
          "sanity: naming a node changes the file, so this test is not "
          "silently comparing a file to itself")

# A truncated or mislabelled container must fail while the bytes are in hand,
# not as an unexplained AP error two steps later.
for name, payload in (
        ("short.glb", b"glTF\x02\x00"),
        ("badmagic.glb", b"GLTF" + struct.pack("<II", 2, 12)),
        ("badlen.glb", b"glTF" + struct.pack("<II", 2, 999)),
        ("runaway.glb", b"glTF" + struct.pack("<II", 2, 24)
                        + struct.pack("<II", 900, 0x4E4F534A) + b"{}  ")):
    bad = os.path.join(work, name)
    with open(bad, "wb") as handle:
        handle.write(payload)
    try:
        gltf_source.read_glb_chunks(bad)
        check(False, "%s is not a valid container and must raise" % name)
    except ValueError:
        pass

# A .fbx is not a glTF source in either container.
try:
    gltf_source.load_document(os.path.join(work, "nope.fbx"))
    check(False, "loading a .fbx as a glTF document must raise")
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
