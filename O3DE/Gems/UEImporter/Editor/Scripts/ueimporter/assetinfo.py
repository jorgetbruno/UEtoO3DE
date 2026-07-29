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
import uuid

MESH_GROUP_TYPE = "{07B356B7-3635-40B5-878A-FAC4EFD5AD86} MeshGroup"
LOD_RULE_TYPE = "{6E796AC8-1484-4909-860A-6D3F22A7346F} LodRule"

# PhysX::Pipeline::MeshGroup. The display name is ALSO "MeshGroup" -- only the
# UUID distinguishes it from Atom's render group above, so the full string is
# load-bearing. Verified against an editor-saved sidecar (FBX Settings ->
# Add PhysXMesh on sm_rock.fbx, 26.05) and the PhysX5.Editor.Gem.dll string
# table. Two schema quirks from that file, reproduced deliberately: the node
# list member is "NodeSelectionList" (capital N, unlike the render group's
# "nodeSelectionList"), and the export mode field is literally "export method"
# with a space, serialized as a NUMBER.
PHYSX_MESH_GROUP_TYPE = "{5B03C8E6-8CEE-4DA0-A7FA-CD88689DD45B} MeshGroup"
PHYSX_EXPORT_TRIMESH = 0   # static-only at runtime; a dynamic body rejects it
PHYSX_EXPORT_CONVEX = 1


def physx_group_id(group_name):
    """A stable per-file id for the PhysX mesh group.

    The Asset Processor derives the `.pxmesh` product's sub-id from this id
    (SHA1 name-uuid of its string, first four bytes), so it must be IDENTICAL
    on every regeneration of the same sidecar -- a churned id would give the
    product a new asset id and silently orphan every prefab that references
    the old one. uuid5 of the group name is deterministic across runs and
    machines. Never change this derivation once sidecars are in the wild.
    """
    return "{%s}" % str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   "ueimporter:pxmesh:" + group_name)).upper()


_DECOMPOSE_OFF = ("0", "off", "false", "no", "none", "disabled")
_DECOMPOSE_ON = ("1", "on", "true", "yes", "enabled")


def decompose_setting(value=None):
    """Parse the decomposition knob into 0 (off) or a positive hull cap.

    Wrong-way-round env parsing deserves its own function and its own test.
    An earlier version did `int(value)` and mapped ValueError to 1, so every
    word a person types to turn something OFF -- "off", "false", "no" --
    turned V-HACD decomposition ON, silently, and re-fingerprinted every
    multi-convex FBX into a minutes-per-mesh cook.

    Anything unrecognized RAISES rather than guessing a direction: this
    setting changes sidecar bytes, so a typo that silently means "on" costs an
    Asset Processor pass over the whole project. `settle_frames` has the same
    stance (a bare `int()` that throws on garbage).
    """
    if value is None:
        value = os.environ.get("UEO3DE_PHYSX_DECOMPOSE", "")
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return value if value > 0 else 0
    text = str(value).strip().lower()
    if not text or text in _DECOMPOSE_OFF:
        return 0
    if text in _DECOMPOSE_ON:
        return 1
    try:
        number = int(text)
    except ValueError:
        raise ValueError(
            "UEO3DE_PHYSX_DECOMPOSE=%r is not understood. Use a hull count "
            "(e.g. 64), 1/on/true to enable with the default cap, or "
            "0/off/false to disable." % (value,))
    return number if number > 0 else 0


def physics_for_asset(asset, decompose=None):
    """What PhysX mesh group (if any) this manifest asset's sidecar carries.

    Decided from the ASSET's own collision data alone -- never from which
    entities reference it -- because chunked imports restage with a sliced
    entity list, and a sidecar whose content depended on the chunk would
    flip between chunk runs, re-fingerprint the source, and delete the
    `.pxmesh` product the previous chunk's prefab already references.

      * convex element(s), source "simple"  -> convex cook of the render mesh
        (what physics_build substitutes for UE's decomposition, matching the
        Jolt whole-mesh-hull semantics)
      * source "none" (no simple collision / UE complex-as-simple, which the
        manifest cannot tell apart) -> triangle-mesh cook, usable on static
        and kinematic bodies
      * primitives only (box/sphere/capsule) -> None; they author faithfully
        as primitive colliders and need no cooked asset

    `decompose` mirrors UEO3DE_PHYSX_DECOMPOSE (read here when None), parsed by
    `decompose_setting`: enables V-HACD decomposition for multi-element convex
    assets, hull count capped at the element count (UE's own decomposition
    size) or at the value when it is a number > 1. Off by default: a single
    hull is what the editor's own Add PhysXMesh produces, and V-HACD at cook
    time is minutes-per-mesh territory on dense geometry.
    """
    if asset.get("kind") != "static_mesh":
        return None
    collision = asset.get("collision") or {}
    shapes = collision.get("shapes") or []
    convex_count = sum(1 for shape in shapes if shape.get("type") == "convex")
    if collision.get("source") == "simple" and convex_count:
        decompose = decompose_setting(decompose)
        hulls = None
        if decompose and convex_count > 1:
            cap = decompose if decompose > 1 else 64
            hulls = min(convex_count, cap)
        return {"method": "convex", "elements": convex_count,
                "decompose_hulls": hulls}
    if collision.get("source") == "none":
        return {"method": "trimesh", "elements": 0, "decompose_hulls": None}
    return None


def build(group_name, fbx_node_name, physics=None):
    """The `.assetinfo` document for a single-mesh FBX.

    `physics` is None or a `physics_for_asset` plan; when present, a PhysX
    mesh group rides along and the Asset Processor cooks a `.pxmesh` product
    beside the azmodel. The render group stays byte-identical either way --
    in particular it keeps its OMITTED id (the AP assigns one), because
    adding an id now would change the azmodel product's sub-id and break
    every existing model reference.
    """
    if not group_name:
        raise ValueError("group_name is required")
    if not fbx_node_name:
        raise ValueError("fbx_node_name is required; without it the node "
                         "selection path cannot be built and the AP job fails")
    values = [{
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
    if physics is not None:
        method = physics["method"]
        if method not in ("convex", "trimesh"):
            raise ValueError("unknown physics method %r" % (method,))
        group = {
            "$type": PHYSX_MESH_GROUP_TYPE,
            "id": physx_group_id(group_name),
            "name": group_name,
            "NodeSelectionList": {
                "selectedNodes": ["RootNode." + fbx_node_name],
                "unselectedNodes": ["RootNode"],
            },
            "export method": (PHYSX_EXPORT_CONVEX if method == "convex"
                              else PHYSX_EXPORT_TRIMESH),
        }
        if physics.get("decompose_hulls"):
            # ConvexDecompositionParams v2 field names (26.05). The engine's
            # own scene_data.py sample writes stale V-HACD-3 keys that are
            # silently dropped on load -- do not copy from it.
            group["DecomposeMeshes"] = True
            group["ConvexDecompositionParams"] = {
                "MaxConvexHulls": int(physics["decompose_hulls"]),
            }
        values.append(group)
    return {"values": values}


def group_name_for(relative_path):
    """Mesh group name derived from the FBX's own file name.

    `uetoo3de/game/meshes/sm_letterf.fbx` -> `sm_letterf`.
    """
    base = os.path.basename(relative_path)
    stem, _dot, _extension = base.partition(".")
    return stem


def physics_in_sidecar(sidecar_path):
    """What PhysX mesh group the sidecar ON DISK actually carries.

    The importer decides whether to wait for a `.pxmesh` product from THIS,
    never from `physics_for_asset` alone: the sidecar may predate cooked-mesh
    support, or have been staged into a project without the PhysX gem, and in
    both cases the Asset Processor was never asked to cook -- waiting would
    burn the timeout once per asset and then fall back anyway.

    Returns None or `{"method": "convex"|"trimesh"}`.
    """
    try:
        with open(sidecar_path, "r") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    for group in document.get("values") or []:
        if not isinstance(group, dict):
            continue
        if group.get("$type") != PHYSX_MESH_GROUP_TYPE:
            continue
        method = group.get("export method")
        if method == PHYSX_EXPORT_CONVEX:
            return {"method": "convex"}
        if method == PHYSX_EXPORT_TRIMESH:
            return {"method": "trimesh"}
        return None
    return None


def write(fbx_path, fbx_node_name, physics=None):
    """Write `<fbx_path>.assetinfo` next to the FBX. Returns the sidecar path."""
    document = build(group_name_for(fbx_path), fbx_node_name, physics=physics)
    sidecar_path = fbx_path + ".assetinfo"
    directory = os.path.dirname(sidecar_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(sidecar_path, "w") as handle:
        json.dump(document, handle, separators=(",", ":"))
    return sidecar_path
