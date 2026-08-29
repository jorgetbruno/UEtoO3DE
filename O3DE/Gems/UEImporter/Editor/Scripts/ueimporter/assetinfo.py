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

from . import gltf_source

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

# JoltPhysics::Pipeline::JoltMeshGroup, the Jolt gem's equivalent, cooking a
# `.joltmesh` product. Written WITHOUT the type UUID because that is what the
# editor itself writes (verified against a hand-saved sm_carriage sidecar):
# unlike "MeshGroup", the name "JoltMeshGroup" is unambiguous, so SceneAPI
# resolves the bare form. The schema mirrors PhysX's field for field --
# "NodeSelectionList", the space in "export method", numeric export mode --
# with two differences that matter:
#
#   * the export mode DEFAULTS TO CONVEX here (PhysX defaults to triangle
#     mesh), so an omitted field does not mean the same thing on both. This
#     writer always emits it rather than relying on either default.
#   * `DecomposeMeshes` only takes effect on a convex export (the gem's
#     GetDecomposeMeshes() is `GetExportAsConvex() && m_decomposeMeshes`), and
#     its parameter block is Jolt's own -- MaxConvexHulls / Resolution /
#     MaxNumVerticesPerConvexHull / Concavity, NOT PhysX's v2 field set.
JOLT_MESH_GROUP_TYPE = "JoltMeshGroup"
# Total LOD nodes a sidecar selects (LOD0 + LodRule entries); measured limit.
LOD_NODES_MAX = 5
JOLT_EXPORT_TRIMESH = 0
JOLT_EXPORT_CONVEX = 1

BACKENDS = ("physx", "jolt")


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


def jolt_group_id(group_name):
    """The same, for the Jolt group -- and deliberately a DIFFERENT namespace.

    `JoltMeshExporter` derives its product uuid exactly as PhysX does
    (`AZ::Uuid::CreateName(group.GetId().ToString())`), so the same stability
    rule applies. A distinct namespace string keeps the two groups' ids apart
    in a project that carries both gems, where one sidecar can hold both
    groups and two products would otherwise be asked to share a sub-id.
    """
    return "{%s}" % str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   "ueimporter:joltmesh:" + group_name)).upper()


def group_id(group_name, backend):
    if backend == "physx":
        return physx_group_id(group_name)
    if backend == "jolt":
        return jolt_group_id(group_name)
    raise ValueError("unknown physics backend %r" % (backend,))


_DECOMPOSE_OFF = ("0", "off", "false", "no", "none", "disabled")
_DECOMPOSE_ON = ("1", "on", "true", "yes", "enabled")
# The knob was named for PhysX because PhysX was the only backend that cooked
# meshes when it landed. It has gated BOTH backends since Jolt's mesh colliders
# moved onto assets -- `_physics_group` writes DecomposeMeshes for jolt too --
# so the PhysX-specific name is now a lie that would send someone hunting for a
# Jolt equivalent that does not exist. The new name is preferred; the old one
# still works, because it is documented in shipped READMEs.
_DECOMPOSE_ENV = ("UEO3DE_DECOMPOSE", "UEO3DE_PHYSX_DECOMPOSE")


def decompose_env_value(environ=None):
    """The decomposition knob's raw value, preferring the backend-neutral name."""
    environ = os.environ if environ is None else environ
    for name in _DECOMPOSE_ENV:
        value = environ.get(name, "")
        if str(value).strip():
            return value
    return ""


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
        value = decompose_env_value()
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
            "UEO3DE_DECOMPOSE=%r is not understood. Use a hull count "
            "(e.g. 64), 1/on/true to enable with the default cap, or "
            "0/off/false to disable." % (value,))
    return number if number > 0 else 0


_COLLISION_MODES = ("single", "vhacd", "ue")
_COLLISION_ENV = "UEO3DE_COLLISION"


def collision_mode(environ=None):
    """UEO3DE_COLLISION -> how a multi-hull UE collision is represented.

      single  one convex hull over the whole render mesh (the original
              behaviour; concavities such as truck beds fill in)
      vhacd   V-HACD decomposition at cook time, hull count capped by the
              element count (or UEO3DE_DECOMPOSE when that is a number);
              cooks from LOD1 when a chain exists, or dense Nanite sources
              turn into minutes per mesh
      ue      UE's own hull elements, exported as a UCX_ node and re-split
              at cook time into at most UE's element count -- measured on
              RetroCars: tow truck 10 of 10, pickup 9 of 10, box truck 5
              of 10 (V-HACD merges elements that overlap), products 4-8 KB
              against 5.7 MB for the whole-mesh hull; falls back to
              `single` for a file that carries no UCX_ node and for `#mx`
              mirrored variants

    Unset means `single`. Anything else raises: this changes sidecar bytes
    for every mesh in the level, so a typo must not silently pick a mode.
    """
    environ = os.environ if environ is None else environ
    value = str(environ.get(_COLLISION_ENV, "")).strip().lower()
    if not value:
        return "single"
    if value in _COLLISION_MODES:
        return value
    raise ValueError("%s=%r is not one of %s"
                     % (_COLLISION_ENV, value, ", ".join(_COLLISION_MODES)))


def physics_for_asset(asset, decompose=None, mode=None):
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

    `decompose` mirrors UEO3DE_DECOMPOSE (read here when None), parsed by
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
        mode = collision_mode() if mode is None else mode
        decompose = decompose_setting(decompose)
        if mode == "vhacd" and not decompose:
            decompose = 1
        hulls = None
        if decompose and convex_count > 1:
            cap = decompose if decompose > 1 else 64
            hulls = min(convex_count, cap)
        # A mirrored variant (`#mx`) is baked under a reflection, which a
        # CoordinateSystemRule cannot express (it rotates); its hulls would
        # land mirrored, so it keeps the whole-mesh hull.
        mirrored = "#mx" in str(asset.get("ue_path", ""))
        return {"method": "convex", "elements": convex_count,
                "decompose_hulls": hulls,
                "hull_nodes": mode == "ue" and not mirrored}
    if collision.get("source") == "none":
        return {"method": "trimesh", "elements": 0, "decompose_hulls": None,
                "hull_nodes": False}
    return None


def _physics_group(group_name, fbx_node_name, physics, backend,
                   source_path=None, node_paths=None):
    """One backend's physics mesh group for `build`.

    The two gems' groups are the same shape -- node list, numeric
    `export method`, optional decomposition -- but not the same schema, and
    the differences are the kind that fail silently: PhysX's group is
    identified by UUID (its display name collides with Atom's render group)
    while Jolt's is written bare, PhysX's export mode defaults to triangle
    mesh while Jolt's defaults to convex, and the decomposition parameter
    blocks share only one field name. So each backend gets its literals from
    its own gem, and neither inherits the other's defaults by omission.
    """
    method = physics["method"]
    if method not in ("convex", "trimesh"):
        raise ValueError("unknown physics method %r" % (method,))
    convex = method == "convex"
    group = {
        "$type": PHYSX_MESH_GROUP_TYPE if backend == "physx" else JOLT_MESH_GROUP_TYPE,
        "id": group_id(group_name, backend),
        "name": group_name,
        # Capital N here, lowercase in the render group -- both gems spell it
        # this way and the other spelling is silently ignored. The PATH shape
        # is source-format dependent: an FBX graph is rooted at `RootNode`, a
        # glTF graph's root is unnamed (measured, see gltf_source).
        # Several selected nodes on a CONVEX group cook one hull per node
        # (JoltMeshExporter iterates its per-node export data for convex
        # groups and only merges for triangle-mesh/primitive groups; PhysX
        # behaves the same) -- which is how UE's hull elements arrive as
        # separate shapes in one cooked asset.
        "NodeSelectionList": {
            "selectedNodes": list(node_paths) if node_paths else
                             [gltf_source.node_path(fbx_node_name, source_path)],
            "unselectedNodes": gltf_source.root_path(source_path),
        },
        # Always explicit: omitting it would mean triangle mesh on PhysX and
        # convex on Jolt.
        "export method": ((PHYSX_EXPORT_CONVEX if convex else PHYSX_EXPORT_TRIMESH)
                          if backend == "physx"
                          else (JOLT_EXPORT_CONVEX if convex else JOLT_EXPORT_TRIMESH)),
    }
    if node_paths:
        # The hull nodes are UE's elements copied VERBATIM onto the baked
        # asset (KConvexElem's transform is protected from Python, so they
        # cannot be baked like the render geometry, which carries the
        # exporter's diag(-1,-1,1) = 180-degree yaw). SceneAPI converts both
        # identically, so the hulls arrive yawed 180 degrees relative to the
        # render mesh; this rule turns the physics group -- and only it --
        # back into alignment. The mesh exporters honour it through
        # DetermineWorldTransform(scene, node, ruleContainer). Quaternion
        # [x, y, z, w] for a half-turn about Z.
        group["rules"] = {"rules": [{
            "$type": "CoordinateSystemRule",
            "useAdvancedData": True,
            "originNodeName": "",
            "rotation": [0.0, 0.0, 1.0, 0.0],
            "translation": [0.0, 0.0, 0.0],
            "scale": 1.0,
        }]}
    if physics.get("decompose_hulls"):
        group["DecomposeMeshes"] = True
        if backend == "physx":
            # ConvexDecompositionParams v2 field names (26.05). The engine's
            # own scene_data.py sample writes stale V-HACD-3 keys that are
            # silently dropped on load -- do not copy from it.
            group["ConvexDecompositionParams"] = {
                "MaxConvexHulls": int(physics["decompose_hulls"]),
            }
        else:
            # Jolt's own params (JoltConvexDecompositionParams): the only
            # field name shared with PhysX's block is MaxConvexHulls.
            group["ConvexDecompositionParams"] = {
                "MaxConvexHulls": int(physics["decompose_hulls"]),
            }
    return group


def build(group_name, fbx_node_name, physics=None, backends=("physx",),
          source_path=None, lod_nodes=None, hull_nodes=None):
    """The `.assetinfo` document for a single-mesh FBX.

    `physics` is None or a `physics_for_asset` plan; when present, one physics
    mesh group per entry in `backends` rides along and the Asset Processor
    cooks a `.pxmesh` / `.joltmesh` product beside the azmodel. A project
    carrying both gems gets both groups -- which backend the level is
    eventually imported with is not staging's decision to make, and writing
    only one would silently decide it.

    The render group stays byte-identical either way -- in particular it keeps
    its OMITTED id (the AP assigns one), because adding an id now would change
    the azmodel product's sub-id and break every existing model reference.
    """
    if not group_name:
        raise ValueError("group_name is required")
    if not fbx_node_name:
        raise ValueError("fbx_node_name is required; without it the node "
                         "selection path cannot be built and the AP job fails")
    # LOD CHAINS (measured on lod_probe_car.fbx, one azmodel + four azlods):
    # a `level_of_detail=True` FBX wraps the mesh in an FbxLODGroup that
    # SceneAPI flattens to `RootNode.<name>` with `<name>_LOD<i>` children.
    # The render group then selects LOD0's node and the LodRule -- populated
    # here, still bare everywhere else, exactly as the M0 note requires for
    # single-mesh files -- selects one node per further LOD. Without a
    # sidecar the AP fragments the group into one single-LOD model per node
    # (measured), which is the failure the old level_of_detail=False comment
    # predicted.
    render_selected = gltf_source.node_path(fbx_node_name, source_path)
    physics_node = fbx_node_name
    lod_rule = {"$type": LOD_RULE_TYPE}
    if lod_nodes and len(lod_nodes) > LOD_NODES_MAX:
        # A sixth LOD crashed Atom's ModelAssetCreator::AddLodAsset on the
        # two NYC meshes that carried one (0xC0000005 in the AssetBuilder);
        # five is what every shipped chain has. Extra nodes stay in the
        # file, unselected.
        lod_nodes = list(lod_nodes)[:LOD_NODES_MAX]
    if lod_nodes:
        if len(lod_nodes) < 2:
            raise ValueError("lod_nodes needs at least LOD0 and LOD1; got %r"
                             % (lod_nodes,))
        base = gltf_source.node_path(fbx_node_name, source_path)
        render_selected = "%s.%s" % (base, lod_nodes[0])
        physics_node = "%s.%s" % (fbx_node_name, lod_nodes[0])
        lod_rule = {
            "$type": LOD_RULE_TYPE,
            "nodeSelectionList": [
                {"selectedNodes": ["%s.%s" % (base, node)],
                 "unselectedNodes": []}
                for node in lod_nodes[1:]
            ],
        }

    values = [{
        "$type": MESH_GROUP_TYPE,
        "name": group_name,
        "nodeSelectionList": {
            "selectedNodes": [render_selected],
            "unselectedNodes": [],
        },
        "rules": {
            "rules": [
                {"$type": "MaterialRule"},
                lod_rule,
            ]
        },
    }]
    if physics is not None:
        node_paths = None
        if physics.get("hull_nodes") and hull_nodes:
            # UE's hull elements as UCX_ node(s), siblings of the mesh node
            # under the root: the physics group selects them and the render
            # group never sees them (it selects by explicit path). UE writes
            # every element into ONE node (measured), which alone would cook
            # back into one hull -- so when the asset has several elements
            # the group also decomposes, capped at that count: V-HACD over a
            # ~100-vertex cloud of convex pieces recovers the disjoint ones
            # and merges overlapping ones (measured: 10 -> 10, 9, 5 across
            # the fleet) in milliseconds, unlike the 100k-triangle render
            # mesh.
            root = gltf_source.root_path(source_path)
            prefix = (root[0] + ".") if root else ""
            node_paths = [prefix + hull for hull in hull_nodes]
            if len(hull_nodes) == 1 and physics.get("elements", 0) > 1:
                physics = dict(physics, decompose_hulls=physics["elements"])
        elif physics.get("decompose_hulls") and lod_nodes and len(lod_nodes) > 1:
            # V-HACD on the full Nanite source is minutes per mesh; LOD1
            # (20% of the source under the exporter's default budgets) keeps
            # every concavity a hull can represent at a fraction of the cost.
            physics_node = "%s.%s" % (fbx_node_name, lod_nodes[1])
        for backend in backends:
            if backend not in BACKENDS:
                raise ValueError("unknown physics backend %r" % (backend,))
            values.append(_physics_group(group_name, physics_node, physics,
                                         backend, source_path=source_path,
                                         node_paths=node_paths))
    return {"values": values}


def group_name_for(relative_path):
    """Mesh group name derived from the FBX's own file name.

    `uetoo3de/game/meshes/sm_letterf.fbx` -> `sm_letterf`.
    """
    base = os.path.basename(relative_path)
    stem, _dot, _extension = base.partition(".")
    return stem


def physics_in_sidecar(sidecar_path, backend="physx"):
    """What physics mesh group the sidecar ON DISK actually carries.

    The importer decides whether to wait for a cooked product from THIS, never
    from `physics_for_asset` alone: the sidecar may predate cooked-mesh
    support, or have been staged into a project without the backend's gem, and
    in both cases the Asset Processor was never asked to cook -- waiting would
    burn the timeout once per asset and then fall back anyway.

    `backend` selects which group is being asked about, because a sidecar can
    legitimately carry both and they can disagree (a mesh whose Jolt group is
    convex and whose PhysX group is convex still cooks two different products).

    Returns None or `{"method": "convex"|"trimesh"}`.
    """
    wanted = PHYSX_MESH_GROUP_TYPE if backend == "physx" else JOLT_MESH_GROUP_TYPE
    convex_value = PHYSX_EXPORT_CONVEX if backend == "physx" else JOLT_EXPORT_CONVEX
    trimesh_value = PHYSX_EXPORT_TRIMESH if backend == "physx" else JOLT_EXPORT_TRIMESH
    try:
        with open(sidecar_path, "r") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    for group in document.get("values") or []:
        if not isinstance(group, dict):
            continue
        if group.get("$type") != wanted:
            continue
        method = group.get("export method")
        if method == convex_value:
            return {"method": "convex"}
        if method == trimesh_value:
            return {"method": "trimesh"}
        return None
    return None


def write(fbx_path, fbx_node_name, physics=None, backends=("physx",),
          lod_nodes=None, hull_nodes=None):
    """Write `<fbx_path>.assetinfo` next to the FBX. Returns the sidecar path."""
    document = build(group_name_for(fbx_path), fbx_node_name, physics=physics,
                     lod_nodes=lod_nodes, hull_nodes=hull_nodes,
                     backends=backends, source_path=fbx_path)
    sidecar_path = fbx_path + ".assetinfo"
    directory = os.path.dirname(sidecar_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(sidecar_path, "w") as handle:
        json.dump(document, handle, separators=(",", ":"))
    return sidecar_path
