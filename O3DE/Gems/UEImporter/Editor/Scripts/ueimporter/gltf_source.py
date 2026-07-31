"""
gltf_source.py — make a UE-exported glTF addressable by a `.assetinfo`.

PURE (json + os only). Two facts, both measured against UE 5.8 and O3DE 26.05,
and both invisible until an Asset Processor job fails:

  1. **UE writes the mesh node UNNAMED.** Its glTF exporter names only the
     MESH (`meshes[i].name`), leaving `nodes[i].name` absent. SceneAPI selects
     by NODE, so there is nothing to select by name and the graph falls back to
     a synthesised `nodes[0]`. Naming the node ourselves is the fix, and the
     file is ours to write: `name_mesh_nodes` does it.

  2. **A glTF scene graph has no `RootNode`.** FBX graphs are rooted at a node
     literally called `RootNode`, so a selection path reads
     `RootNode.<node>` -- that is measured and correct for FBX. A glTF's root
     has an EMPTY path and the mesh sits directly beneath it, so the same
     path names nothing. `node_path` returns the right shape per format.

Both were found by dumping the graph from inside the Scene Builder
(`Tests/o3de/gltf_manifest_script.py`) after four static guesses were rejected
with the same unhelpful warning:

    SceneAPI: MeshGroup <name> wasn't found in the list of selected nodes.

With the node named and the prefix dropped, a UE glTF produces both
`<stem>.gltf.azmodel` and `<stem>.gltf.joltmesh` -- render and cooked physics
-- with zero AP errors. See LANE_C_GLTF.md.
"""

import json
import os

GLTF_EXTENSIONS = (".gltf",)
# .glb is the same JSON inside a binary container; naming its nodes means
# rewriting chunk 0 and its length, which is NOT done here. Staging must not
# hand a .glb to `name_mesh_nodes` believing it was patched, so the extension
# is deliberately absent from the tuple above rather than silently accepted.
GLB_EXTENSIONS = (".glb",)


def is_gltf(path):
    """Is this a glTF source whose node names this module can fix?"""
    return str(path).lower().endswith(GLTF_EXTENSIONS)


def is_glb(path):
    return str(path).lower().endswith(GLB_EXTENSIONS)


def node_path(node_name, source_path):
    """The scene-graph path a `.assetinfo` must name for this source.

    FBX: `RootNode.<node>`. glTF: `<node>`, because its graph root is unnamed.
    """
    if is_gltf(source_path) or is_glb(source_path):
        return node_name
    return "RootNode." + node_name


def root_path(source_path):
    """What to put in `unselectedNodes` to mean "the root".

    FBX names its root; a glTF root has no name, so there is nothing to
    unselect and the list stays empty.
    """
    return [] if (is_gltf(source_path) or is_glb(source_path)) else ["RootNode"]


def name_mesh_nodes(path, node_name):
    """Give every mesh-bearing node in a `.gltf` the name `node_name`.

    Returns the number of nodes renamed. Raises on a `.glb`, which needs
    container-aware rewriting this module does not do -- silently returning 0
    would leave an unaddressable file that fails much later, in an AP job.
    """
    if is_glb(path):
        raise ValueError(
            "%s is a .glb: its JSON lives in a binary chunk and naming its "
            "nodes needs container-aware rewriting, which is not implemented. "
            "Export .gltf, or add GLB support here -- do not stage it "
            "unpatched, the mesh group will select nothing." % path)
    if not is_gltf(path):
        raise ValueError("%s is not a .gltf" % path)

    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)

    renamed = 0
    for node in document.get("nodes", []):
        if "mesh" in node and node.get("name") != node_name:
            node["name"] = node_name
            renamed += 1

    if renamed:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, separators=(",", ":"))
    return renamed


def mesh_node_count(path):
    """How many mesh-bearing nodes the file has.

    More than one means "name them all the same" is wrong -- the selection
    would be ambiguous -- and the caller should say so rather than produce a
    sidecar that silently picks one.
    """
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    return sum(1 for node in document.get("nodes", []) if "mesh" in node)
