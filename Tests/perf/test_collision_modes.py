"""test_collision_modes.py -- UEO3DE_COLLISION and the hull-node sidecar.

Pure: no editor, no UE. Run: python Tests/perf/test_collision_modes.py

Pins the collision selector and what each mode writes into the sidecar:

  single  the original whole-mesh convex; bytes pinned elsewhere
          (test_pxmesh) -- this file only checks the mode leaves the plan
          untouched
  vhacd   DecomposeMeshes with the element-count cap, cooking from LOD1
          when a chain exists (V-HACD on a Nanite source is minutes per mesh)
  ue      the physics group selects every UCX_ hull node, one hull per node
          (measured in JoltMeshExporter: convex groups cook each selected
          node separately; merging is triangle-mesh only), and the render
          group is untouched

and the UCX_ scanner: UE's `UCX_<mesh>_NN` naming, contiguous from 00,
identifier-boundary checked.
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


# --- 1. the selector -----------------------------------------------------------
check(assetinfo.collision_mode({}) == "single", "unset must mean single")
for spelling in ("single", "VHACD", " ue "):
    check(assetinfo.collision_mode({"UEO3DE_COLLISION": spelling})
          == spelling.strip().lower(), "%r must parse" % spelling)
try:
    assetinfo.collision_mode({"UEO3DE_COLLISION": "hulls"})
    check(False, "an unknown mode must raise, not pick a default")
except ValueError:
    pass

# --- 2. the plan per mode ------------------------------------------------------
asset = {"kind": "static_mesh",
         "collision": {"source": "simple",
                       "shapes": [{"type": "convex"}] * 7}}
single = assetinfo.physics_for_asset(asset, decompose=0, mode="single")
check(single == {"method": "convex", "elements": 7, "decompose_hulls": None,
                 "hull_nodes": False},
      "single: whole-mesh convex, no decomposition; got %r" % (single,))
vhacd = assetinfo.physics_for_asset(asset, decompose=0, mode="vhacd")
check(vhacd["decompose_hulls"] == 7 and not vhacd["hull_nodes"],
      "vhacd: decomposition capped at UE's element count; got %r" % (vhacd,))
capped = assetinfo.physics_for_asset(asset, decompose=4, mode="vhacd")
check(capped["decompose_hulls"] == 4,
      "a numeric UEO3DE_DECOMPOSE caps below the element count; got %r"
      % (capped,))
ue = assetinfo.physics_for_asset(asset, decompose=0, mode="ue")
check(ue["hull_nodes"] and ue["decompose_hulls"] is None,
      "ue: hull nodes wanted, no decomposition; got %r" % (ue,))
one = assetinfo.physics_for_asset(
    {"kind": "static_mesh", "collision": {"source": "simple",
                                          "shapes": [{"type": "convex"}]}},
    decompose=0, mode="vhacd")
check(one["decompose_hulls"] is None,
      "a single-element asset never decomposes, whatever the mode")

# --- 3. the UCX_ scanner -------------------------------------------------------
def fake_fbx(names):
    body = b"Kaydara FBX Binary  \x00"
    for name in names:
        encoded = name.encode("ascii")
        body += struct.pack("<B", len(encoded)) + encoded + b"\x00\x01\x02"
    path = os.path.join(tempfile.mkdtemp(prefix="ueo3de_ucx_"), "mesh.fbx")
    with open(path, "wb") as handle:
        handle.write(body)
    return path


hulls = fake_fbx(["SM_Truck", "UCX_SM_Truck_00", "UCX_SM_Truck_01",
                  "UCX_SM_Truck_02", "MI_Truck"])
check(staging.fbx_hull_nodes(hulls, "SM_Truck")
      == ["UCX_SM_Truck_00", "UCX_SM_Truck_01", "UCX_SM_Truck_02"],
      "three hull nodes must be found in order")
check(staging.fbx_hull_nodes(fake_fbx(["SM_Truck"]), "SM_Truck") == [],
      "no hull nodes -> []")
check(staging.fbx_hull_nodes(fake_fbx(["UCX_SM_Truck_00", "UCX_SM_Truck_02"]),
                             "SM_Truck") == ["UCX_SM_Truck_00"],
      "a hole ends the chain rather than skipping it")
check(staging.fbx_hull_nodes(fake_fbx(["UCX_SM_Truck_010"]), "SM_Truck") == [],
      "_010 must not satisfy _01 -- identifier boundary")
check(staging.fbx_hull_nodes("x.glb", "SM_Truck") == [],
      "only .fbx files are scanned")

# --- 4. the sidecar per mode ---------------------------------------------------
lods = ["SM_Truck_LOD%d" % i for i in range(3)]
hull_names = ["UCX_SM_Truck_00", "UCX_SM_Truck_01"]

doc_ue = assetinfo.build("sm_truck", "SM_Truck", physics=ue, backends=("jolt",),
                         source_path="x/sm_truck.fbx", lod_nodes=lods,
                         hull_nodes=hull_names)
render, jolt = doc_ue["values"]
check(render["nodeSelectionList"]["selectedNodes"]
      == ["RootNode.SM_Truck.SM_Truck_LOD0"],
      "ue: the render group must not change; got %r"
      % render["nodeSelectionList"]["selectedNodes"])
check(jolt["NodeSelectionList"]["selectedNodes"]
      == ["RootNode.UCX_SM_Truck_00", "RootNode.UCX_SM_Truck_01"],
      "ue: the physics group selects every hull node; got %r"
      % jolt["NodeSelectionList"]["selectedNodes"])
check(jolt["export method"] == assetinfo.JOLT_EXPORT_CONVEX
      and "DecomposeMeshes" not in jolt,
      "ue: convex, one hull per node, no decomposition")

doc_fallback = assetinfo.build("sm_truck", "SM_Truck", physics=ue,
                               backends=("jolt",), source_path="x/sm_truck.fbx",
                               lod_nodes=lods, hull_nodes=[])
check(doc_fallback["values"][1]["NodeSelectionList"]["selectedNodes"]
      == ["RootNode.SM_Truck.SM_Truck_LOD0"],
      "ue with no hull nodes in the file falls back to the whole-mesh hull")

doc_vhacd = assetinfo.build("sm_truck", "SM_Truck", physics=vhacd,
                            backends=("jolt",), source_path="x/sm_truck.fbx",
                            lod_nodes=lods)
jolt_vhacd = doc_vhacd["values"][1]
check(jolt_vhacd["NodeSelectionList"]["selectedNodes"]
      == ["RootNode.SM_Truck.SM_Truck_LOD1"],
      "vhacd on a chain cooks from LOD1; got %r"
      % jolt_vhacd["NodeSelectionList"]["selectedNodes"])
check(jolt_vhacd.get("DecomposeMeshes") is True
      and jolt_vhacd["ConvexDecompositionParams"]["MaxConvexHulls"] == 7,
      "vhacd: DecomposeMeshes with the element-count cap")

doc_single = assetinfo.build("sm_truck", "SM_Truck", physics=single,
                             backends=("jolt",), source_path="x/sm_truck.fbx",
                             lod_nodes=lods)
check(doc_single["values"][1]["NodeSelectionList"]["selectedNodes"]
      == ["RootNode.SM_Truck.SM_Truck_LOD0"],
      "single keeps cooking from LOD0 -- the bytes test_pxmesh pins")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
