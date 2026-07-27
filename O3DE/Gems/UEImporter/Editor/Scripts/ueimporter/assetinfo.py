"""
assetinfo.py — generate the SceneAPI `.assetinfo` sidecar for an exported FBX.

PURE. The sidecar pins the mesh group name and the node selection so product
naming is deterministic and stray nodes can never sneak into the model.

**It contains NO CoordinateSystemRule.** SceneAPI performs both conversions
itself, honouring the FBX header UE writes (`UnitScaleFactor` cm -> m, and the
declared axes, which negates Y into O3DE's frame). That is measured at the
byte level in the product position buffers -- the engine cube imports at
exactly +/-0.5 m with no rule at all (`Tests/m2/test_m2_artifacts.py`).

An earlier revision carried `CoordinateSystemRule {scale: 0.01}` per M0's
LANE_B measurement. That measurement was wrong -- its evidence was product
metadata and buffer ratios, never absolute floats -- and the rule stacked a
second /100 on top of the automatic unit conversion, shrinking every imported
mesh 100x. First caught by a human eyeballing a bench against the shader
ball. LANE_B.md carries the full correction.

Schema notes that remain true (learned in M0, do not "simplify"):

  * Node paths are `RootNode.<NodeName>` (dot-separated, FBX root prefix
    included). A wrong path fails the AP job with "No valid ModelLodAssets
    have been added".
  * The **LodRule must be present**, bare -- no `nodeSelectionList` member --
    or the job fails the same way.
"""

import json
import os

MESH_GROUP_TYPE = "{07B356B7-3635-40B5-878A-FAC4EFD5AD86} MeshGroup"
LOD_RULE_TYPE = "{6E796AC8-1484-4909-860A-6D3F22A7346F} LodRule"


def build(group_name, fbx_node_name):
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
                    {"$type": LOD_RULE_TYPE},
                ]
            },
        }]
    }


def group_name_for(relative_path):
    """Mesh group name derived from the FBX's own file name.

    `uetoo3de/game/meshes/sm_letterf.fbx` -> `sm_letterf`.
    """
    base = os.path.basename(relative_path)
    stem, _dot, _extension = base.partition(".")
    return stem


def write(fbx_path, fbx_node_name):
    """Write `<fbx_path>.assetinfo` next to the FBX. Returns the sidecar path."""
    document = build(group_name_for(fbx_path), fbx_node_name)
    sidecar_path = fbx_path + ".assetinfo"
    directory = os.path.dirname(sidecar_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(sidecar_path, "w") as handle:
        json.dump(document, handle, separators=(",", ":"))
    return sidecar_path
