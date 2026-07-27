"""
assetinfo.py — generate the SceneAPI `.assetinfo` sidecar for an exported FBX.

PURE. The contract this implements was measured in M0 spike S0.2 and is written
down in LANE_B.md; the shape below matches the file that was verified to produce
a correct product on disk, byte for byte in structure.

Why each piece is here (all learned the hard way -- do not "simplify"):

  * `scale: 0.01` in an **advanced** `CoordinateSystemRule`. SceneAPI applies
    no unit conversion of its own, so the FBX's centimetre values would be read
    as metres and the model would come in 100x too large. In advanced mode
    `scale` is a plain float.
  * Node paths are `RootNode.<NodeName>`, dot-separated, with the FBX root
    prefix included. A wrong path fails the job outright with "No valid
    ModelLodAssets have been added" -- which is at least loud. The node name
    comes from the manifest (`assets[].fbx_node_name`) rather than being
    guessed from the file name.
  * The `LodRule` must be present, and bare -- no `nodeSelectionList` member --
    or the job fails the same way.

Note that the reflection Lane B needs is NOT here: `CoordinateSystemRule`
offers a rotation and one scalar scale, and neither can express a
determinant -1 map. The exporter bakes the mirror into the FBX geometry
instead, and records that it did so as `units.lane_b_rule`.
"""

import json
import os

MESH_GROUP_TYPE = "{07B356B7-3635-40B5-878A-FAC4EFD5AD86} MeshGroup"
LOD_RULE_TYPE = "{6E796AC8-1484-4909-860A-6D3F22A7346F} LodRule"

# UE exports centimetres; SceneAPI does not convert units (measured, S0.2).
CM_TO_M_SCALE = 0.01


def build(group_name, fbx_node_name, scale=CM_TO_M_SCALE):
    """The `.assetinfo` document for a single-mesh FBX."""
    if not group_name:
        raise ValueError("group_name is required")
    if not fbx_node_name:
        raise ValueError("fbx_node_name is required; without it the node "
                         "selection path cannot be built and the AP job fails")
    return {
        "values": [{
            "$type": MESH_GROUP_TYPE,
            "name": group_name,
            "nodeSelectionList": {
                "selectedNodes": ["RootNode." + fbx_node_name],
                "unselectedNodes": [],
            },
            "rules": {
                "rules": [
                    {"$type": "MaterialRule"},
                    {
                        "$type": "CoordinateSystemRule",
                        "useAdvancedData": True,
                        "originNodeName": "",
                        "scale": scale,
                    },
                    {"$type": LOD_RULE_TYPE},
                ]
            },
        }]
    }


def group_name_for(relative_path):
    """Mesh group name derived from the FBX's own file name.

    `uetoo3de/game/meshes/sm_letterf.fbx` -> `sm_letterf`, matching the
    verified S0.2 sidecar.
    """
    base = os.path.basename(relative_path)
    stem, _dot, _extension = base.partition(".")
    return stem


def write(fbx_path, fbx_node_name, scale=CM_TO_M_SCALE):
    """Write `<fbx_path>.assetinfo` next to the FBX. Returns the sidecar path."""
    document = build(group_name_for(fbx_path), fbx_node_name, scale)
    sidecar_path = fbx_path + ".assetinfo"
    directory = os.path.dirname(sidecar_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(sidecar_path, "w") as handle:
        json.dump(document, handle, separators=(",", ":"))
    return sidecar_path
