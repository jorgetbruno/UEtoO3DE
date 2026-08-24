"""test_lod_chain.py -- the LOD chain's sidecar contract, offline.

Pure: no editor, no UE. Run: python Tests/perf/test_lod_chain.py

THE MEASURED MECHANISM this pins (every link probed live before any code):

  * a `level_of_detail=True` FBX wraps the mesh in an FbxLODGroup; SceneAPI
    flattens it to `RootNode.<name>` with `<name>_LOD<i>` children (scene
    dump via ScriptProcessorRule on lod_probe_car.fbx);
  * WITHOUT a sidecar the AP fragments that into one single-LOD model per
    node -- four azmodels from one car (measured);
  * WITH a MeshGroup selecting `..._LOD0` and a LodRule whose
    `nodeSelectionList[i]` selects `..._LOD(i+1)`, the AP produces ONE
    azmodel with FOUR azlods, index buffers halving with the tri chain
    (81,699 / 41,077 / 20,761 / 10,609 bytes);
  * the M0 rule still holds for single-mesh files: their LodRule must be
    present and BARE, and their bytes are pinned by test_pxmesh.
"""

import os
import struct
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import assetinfo, staging  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# --- 1. the FBX LOD-node scanner ----------------------------------------------
def fake_fbx(names, extra=b""):
    """A binary blob shaped like FBX name storage: length-delimited strings."""
    body = b"Kaydara FBX Binary  \x00" + extra
    for name in names:
        encoded = name.encode("ascii")
        body += struct.pack("<B", len(encoded)) + encoded + b"\x00\x01\x02"
    path = os.path.join(tempfile.mkdtemp(prefix="ueo3de_lod_"), "mesh.fbx")
    with open(path, "wb") as handle:
        handle.write(body)
    return path


chain = fake_fbx(["SM_Car_24a_LodGroup", "SM_Car_24a_LOD0", "SM_Car_24a_LOD1",
                  "SM_Car_24a_LOD2", "SM_Car_24a_LOD3", "MI_Car_24a"])
check(staging.fbx_lod_nodes(chain, "SM_Car_24a")
      == ["SM_Car_24a_LOD0", "SM_Car_24a_LOD1", "SM_Car_24a_LOD2",
          "SM_Car_24a_LOD3"],
      "a four-LOD chain must be detected in order")

single = fake_fbx(["SM_LetterF"])
check(staging.fbx_lod_nodes(single, "SM_LetterF") == [],
      "a single-mesh FBX has no chain -- its sidecar must stay byte-identical")

lonely = fake_fbx(["SM_Thing_LOD0"])
check(staging.fbx_lod_nodes(lonely, "SM_Thing") == [],
      "LOD0 alone is not a chain; one entry would write an empty LodRule "
      "selection and fail the AP job")

hole = fake_fbx(["SM_Thing_LOD0", "SM_Thing_LOD2"])
check(staging.fbx_lod_nodes(hole, "SM_Thing") == [],
      "a hole in the chain (LOD0 + LOD2, no LOD1) is not something this "
      "pipeline wrote and must not be trusted")

# `_LOD1` must not match inside another identifier.
tricky = fake_fbx(["SM_Thing_LOD0suffix", "SM_Thing_LOD0", "SM_Thing_LOD1x"])
check(staging.fbx_lod_nodes(tricky, "SM_Thing") == [],
      "identifier-embedded matches (SM_Thing_LOD1x) must not count")

check(staging.fbx_lod_nodes("whatever.glb", "N") == [],
      "only .fbx files are scanned -- the glb container keeps its "
      "single-mesh contract")

# --- 2. the sidecar the chain produces -----------------------------------------
physics = {"method": "trimesh", "elements": 0, "decompose_hulls": None}
lods = ["SM_Car_24a_LOD%d" % i for i in range(4)]
doc = assetinfo.build("sm_car_24a", "SM_Car_24a", physics=physics,
                      backends=("jolt",), source_path="x/sm_car_24a.fbx",
                      lod_nodes=lods)

render = doc["values"][0]
check(render["nodeSelectionList"]["selectedNodes"]
      == ["RootNode.SM_Car_24a.SM_Car_24a_LOD0"],
      "the render group must select LOD0 through the LODGroup path; got %r"
      % render["nodeSelectionList"]["selectedNodes"])

lod_rules = [r for r in render["rules"]["rules"]
             if r.get("$type") == assetinfo.LOD_RULE_TYPE]
check(len(lod_rules) == 1, "exactly one LodRule")
selections = lod_rules[0].get("nodeSelectionList")
check(selections == [
    {"selectedNodes": ["RootNode.SM_Car_24a.SM_Car_24a_LOD%d" % i],
     "unselectedNodes": []} for i in (1, 2, 3)],
    "the LodRule must select LOD1..3, one selection list per LOD, in the "
    "measured schema; got %r" % (selections,))

jolt = doc["values"][1]
check(jolt["NodeSelectionList"]["selectedNodes"]
      == ["RootNode.SM_Car_24a.SM_Car_24a_LOD0"],
      "the physics group cooks from LOD0's node -- the full geometry, the "
      "same collider the flattened export produced; got %r"
      % jolt["NodeSelectionList"]["selectedNodes"])

# --- 3. without lod_nodes, nothing moves ---------------------------------------
plain = assetinfo.build("sm_car_24a", "SM_Car_24a", physics=physics,
                        backends=("jolt",), source_path="x/sm_car_24a.fbx")
check(plain["values"][0]["nodeSelectionList"]["selectedNodes"]
      == ["RootNode.SM_Car_24a"],
      "no lod_nodes -> the original selection, byte-compatible with every "
      "existing sidecar (test_pxmesh pins the exact bytes)")
bare = [r for r in plain["values"][0]["rules"]["rules"]
        if r.get("$type") == assetinfo.LOD_RULE_TYPE]
check(bare == [{"$type": assetinfo.LOD_RULE_TYPE}],
      "the single-mesh LodRule stays BARE -- the M0 rule: a populated list "
      "on a single-mesh file fails the job")

try:
    assetinfo.build("g", "N", lod_nodes=["N_LOD0"])
    check(False, "a one-entry lod_nodes must raise, not write an empty "
                 "LodRule selection")
except ValueError:
    pass

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
