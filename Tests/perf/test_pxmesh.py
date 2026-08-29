"""
test_pxmesh.py — the cooked-physics-mesh pipeline, everything testable offline.

Pure: no editor. Run: python Tests/perf/test_pxmesh.py  (exit code is the verdict)

WHAT THIS COVERS. On PhysX there is no render-mesh collider bake; the importer
instead writes a PhysX mesh group into each FBX's `.assetinfo` sidecar, waits
for the Asset Processor's cooked `.pxmesh` product, and authors a mesh
collider referencing it. Three layers of that are plain data transforms and
are pinned here:

  1. the sidecar document (`assetinfo.build` + `physics_for_asset`) -- schema
     quirks included, because they are load-bearing: the group $type is
     distinguished from the render group's ONLY by UUID, the node list member
     is spelled `NodeSelectionList` (capital N) unlike the render group's,
     and the export mode field is literally `export method` with a space,
     numeric. All verified against an editor-saved sidecar; a "cleanup" of
     any of them produces a group the Asset Processor silently ignores.
  2. the product path + project gating (`staging`) -- the Jolt test project
     has no PhysX gem and must keep byte-identical sidecars.
  3. the authoring decision (`physics_build.author_entity_physics`) -- which
     entities get ONE cooked-asset collider, which fall back to per-element
     AABB boxes, and which are reported as gaps. The collapse-vs-boxes split
     is the exact shape of a bug that shipped once already (see
     test_convex.py); here it is pinned at the author level.

THE STABILITY CONSTRAINT that is easy to break and impossible to see in a
diff: the `.pxmesh` product's sub-id derives from the mesh group's `id`, and
the render group's azmodel sub-id derives from its OMITTED id. So the physx
group id must be identical on every regeneration, and the render group must
never gain one. Either regression orphans every existing asset reference.
"""

import json
import os
import re
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import assetinfo  # noqa: E402
from ueimporter import physics_build  # noqa: E402
from ueimporter import staging  # noqa: E402
from ueimporter.adapters import base  # noqa: E402
from ueimporter.report import Report  # noqa: E402

# The env knob changes decomposition; scrub it so the suite tests one world.
for _name in ("UEO3DE_DECOMPOSE", "UEO3DE_PHYSX_DECOMPOSE"):
    os.environ.pop(_name, None)

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# --- 1a. the render group must not change AT ALL ----------------------------
# Byte-pinned against the pre-cooked-mesh output. The azmodel product sub-id
# derives from the group id the AP assigns when the sidecar OMITS one; a
# render group that gained an id (or any reordering) re-fingerprints every
# staged FBX and can orphan every Model reference in every existing prefab.
baseline = json.dumps(assetinfo.build("sm_thing", "SM_Thing"),
                      separators=(",", ":"))
expected_baseline = (
    '{"values":[{"$type":"{07B356B7-3635-40B5-878A-FAC4EFD5AD86} MeshGroup",'
    '"name":"sm_thing","nodeSelectionList":{"selectedNodes":["RootNode.SM_Thing"],'
    '"unselectedNodes":[]},"rules":{"rules":[{"$type":"MaterialRule"},'
    '{"$type":"{6E796AC8-1484-4909-860A-6D3F22A7346F} LodRule"}]}}]}')
check(baseline == expected_baseline,
      "build() without physics no longer matches the shipped sidecar bytes; "
      "every staged FBX would re-fingerprint and re-process:\n  got  %s\n  want %s"
      % (baseline, expected_baseline))

# --- 1b. the physx group, field by field ------------------------------------
convex_plan = {"method": "convex", "elements": 3, "decompose_hulls": None}
document = assetinfo.build("sm_thing", "SM_Thing", physics=convex_plan)
values = document["values"]
check(len(values) == 2, "physics build should carry 2 groups, got %d" % len(values))
check(json.dumps(values[0], separators=(",", ":")) in expected_baseline,
      "adding the physx group ALTERED the render group -- azmodel sub-ids churn")

group = values[1]
# The UUID is spelled out here independently of the constant: a typo'd or
# "corrected" constant must fail loudly, not agree with itself.
check(group.get("$type") == "{5B03C8E6-8CEE-4DA0-A7FA-CD88689DD45B} MeshGroup",
      "physx group $type is %r; the AP matches the UUID, not the display name"
      % group.get("$type"))
check("export method" in group and group["export method"] == 1,
      "convex export must serialize as {'export method': 1} (space, numeric); "
      "got %r" % {k: v for k, v in group.items() if "method" in k.lower()})
check("NodeSelectionList" in group and "nodeSelectionList" not in group,
      "the physx group's node list member is 'NodeSelectionList' (capital N); "
      "the lowercase spelling belongs to the render group only")
check(group["NodeSelectionList"]["selectedNodes"] == ["RootNode.SM_Thing"],
      "physx group selects %r" % group["NodeSelectionList"]["selectedNodes"])
check(group["NodeSelectionList"]["unselectedNodes"] == ["RootNode"],
      "physx group should unselect RootNode (mirrors the editor-saved file), "
      "got %r" % group["NodeSelectionList"]["unselectedNodes"])
check("DecomposeMeshes" not in group and "ConvexDecompositionParams" not in group,
      "decomposition params must be absent when not requested")

trimesh_doc = assetinfo.build("sm_thing", "SM_Thing",
                              physics={"method": "trimesh", "elements": 0,
                                       "decompose_hulls": None})
check(trimesh_doc["values"][1]["export method"] == 0,
      "trimesh export must serialize as {'export method': 0}, got %r"
      % trimesh_doc["values"][1]["export method"])

try:
    assetinfo.build("sm_thing", "SM_Thing", physics={"method": "primitive"})
    check(False, "an unknown physics method must raise, not silently emit")
except ValueError:
    pass

# --- 1b-bis. the JOLT group, whose schema differs in ways that fail quietly --
# Same shape as PhysX's group, three differences that a copy-paste would get
# wrong and no test would notice: the $type is written WITHOUT the type uuid
# (that is what the editor itself writes, and unlike "MeshGroup" the name
# "JoltMeshGroup" is unambiguous), the export mode DEFAULTS TO CONVEX rather
# than triangle mesh (so an omitted field means opposite things per backend),
# and the group id comes from a different namespace so the two products cannot
# collide in a project carrying both gems.
jolt_doc = assetinfo.build("sm_thing", "SM_Thing", physics=convex_plan,
                           backends=("jolt",))
jolt_group = jolt_doc["values"][1]
check(jolt_group.get("$type") == "JoltMeshGroup",
      "the Jolt group $type must be the bare class name as the editor writes "
      "it, got %r" % jolt_group.get("$type"))
check(jolt_group.get("export method") == 1,
      "a Jolt convex cook must serialize 'export method': 1, got %r"
      % jolt_group.get("export method"))
check(assetinfo.build("sm_thing", "SM_Thing",
                      physics={"method": "trimesh", "elements": 0,
                               "decompose_hulls": None},
                      backends=("jolt",))["values"][1]["export method"] == 0,
      "a Jolt trimesh cook must serialize 'export method': 0 -- omitting it "
      "would mean CONVEX on this backend")
check(jolt_group.get("NodeSelectionList", {}).get("selectedNodes")
      == ["RootNode.SM_Thing"],
      "the Jolt group's node selection is wrong: %r" % jolt_group.get("NodeSelectionList"))
check(jolt_group.get("id") != group.get("id"),
      "the Jolt and PhysX groups share an id; in a project with both gems "
      "their products would be asked to share a sub-id")

# Both gems present -> both groups, in a fixed order so sidecar bytes do not
# depend on how project.json happens to list them.
both = assetinfo.build("sm_thing", "SM_Thing", physics=convex_plan,
                       backends=("physx", "jolt"))
check([v.get("$type") for v in both["values"]]
      == [assetinfo.MESH_GROUP_TYPE, assetinfo.PHYSX_MESH_GROUP_TYPE,
          assetinfo.JOLT_MESH_GROUP_TYPE],
      "a two-gem project's sidecar should carry render + physx + jolt in that "
      "order, got %r" % [v.get("$type") for v in both["values"]])
try:
    assetinfo.build("sm_thing", "SM_Thing", physics=convex_plan,
                    backends=("bullet",))
    check(False, "an unknown backend must raise rather than emit nothing")
except ValueError:
    pass

# --- 1c. the group id: present, well-formed, STABLE -------------------------
# THE LITERAL PIN, and it is the only assertion here that can catch the one
# forbidden change. Everything else in this section (regex, build-vs-build
# equality, cross-name inequality, determinism) is satisfied by ANY
# deterministic derivation, and both canonical runners restage before testing
# -- so a rewritten derivation regenerates the sidecars it is then compared
# against, and every suite stays green while every already-imported prefab's
# collider references are orphaned (the .pxmesh sub-id derives from this id).
# The value below is therefore part of the on-disk contract, not an
# implementation detail: changing it is a breaking change to shipped prefabs.
EXPECTED_IDS = {
    "sm_thing": "{966932BA-8136-5D0B-A9BC-47696065391C}",
    "sm_rock": "{629CC37E-755F-5EBB-97D6-FF839D331414}",
    "sm_barrel": "{5EB355F4-E963-5387-A0D7-103CA19A4297}",
}
for name, expected_id in sorted(EXPECTED_IDS.items()):
    check(assetinfo.physx_group_id(name) == expected_id,
          "physx_group_id(%r) is %s, expected %s. The .pxmesh sub-id derives "
          "from this string, so changing the derivation re-points every cooked "
          "collider in every prefab already imported. If this is intentional, "
          "it needs a migration, not a new literal."
          % (name, assetinfo.physx_group_id(name), expected_id))

group_id = group.get("id")
check(bool(group_id) and re.match(r"^\{[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}\}$",
                                  str(group_id)),
      "physx group id %r is not an uppercase braced UUID" % group_id)
again = assetinfo.build("sm_thing", "SM_Thing", physics=convex_plan)
check(again["values"][1]["id"] == group_id,
      "the physx group id changed between two builds of the same file -- the "
      ".pxmesh sub-id derives from it, so every prefab reference would break")
check(assetinfo.build("sm_other", "SM_Other",
                      physics=convex_plan)["values"][1]["id"] != group_id,
      "two different meshes share a physx group id")
check("id" not in values[0],
      "the RENDER group gained an id; its azmodel sub-id derives from the "
      "AP-assigned one and every existing model reference would break")

# Full-document determinism: staging rewrites sidecars on every restage, and
# AP fingerprints the bytes -- nondeterminism here means every restage
# re-processes every FBX.
check(json.dumps(document, separators=(",", ":"))
      == json.dumps(assetinfo.build("sm_thing", "SM_Thing", physics=convex_plan),
                    separators=(",", ":")),
      "build() with physics is not byte-deterministic")

# --- 1d. decomposition params (the v2 schema) -------------------------------
decomposed = assetinfo.build(
    "sm_thing", "SM_Thing",
    physics={"method": "convex", "elements": 340, "decompose_hulls": 64})
dgroup = decomposed["values"][1]
check(dgroup.get("DecomposeMeshes") is True,
      "decompose_hulls should set DecomposeMeshes, got %r"
      % dgroup.get("DecomposeMeshes"))
check(dgroup.get("ConvexDecompositionParams") == {"MaxConvexHulls": 64},
      "ConvexDecompositionParams must use the v2 field names (the engine's "
      "sample scene_data.py writes stale V-HACD-3 keys that are silently "
      "dropped); got %r" % dgroup.get("ConvexDecompositionParams"))

# --- 2a. physics_for_asset: the decision table ------------------------------
def asset(kind="static_mesh", source="simple", shapes=None):
    return {"kind": kind, "collision": {"source": source, "shapes": shapes or []}}

convex3 = [{"type": "convex"}] * 3
check(assetinfo.physics_for_asset(asset(shapes=convex3), decompose=0)
      == {"method": "convex", "elements": 3, "decompose_hulls": None,
          "hull_nodes": False},
      "3 convex elements should plan a convex cook")
check(assetinfo.physics_for_asset(asset(source="none"))
      == {"method": "trimesh", "elements": 0, "decompose_hulls": None,
          "hull_nodes": False},
      "source 'none' (no simple collision / complex-as-simple) should plan a "
      "trimesh cook")
check(assetinfo.physics_for_asset(asset(shapes=[{"type": "box"}])) is None,
      "a box-only asset needs no cooked mesh; primitives author faithfully")
check(assetinfo.physics_for_asset(asset(kind="texture", source="none")) is None,
      "only static meshes get physx groups")
check(assetinfo.physics_for_asset({"kind": "static_mesh"}) is None,
      "an asset without a collision block must plan nothing")

# Decomposition: off by default, capped at the element count, never for a
# single element (one hull IS the undcomposed answer).
check(assetinfo.physics_for_asset(asset(shapes=convex3),
                                  decompose=1)["decompose_hulls"] == 3,
      "decompose=1 should cap hulls at the element count for small counts")
many = [{"type": "convex"}] * 340
check(assetinfo.physics_for_asset(asset(shapes=many),
                                  decompose=1)["decompose_hulls"] == 64,
      "decompose=1 should cap hulls at 64 by default")
check(assetinfo.physics_for_asset(asset(shapes=many),
                                  decompose=200)["decompose_hulls"] == 200,
      "a numeric decompose value should raise the hull cap")
check(assetinfo.physics_for_asset(asset(shapes=[{"type": "convex"}]),
                                  decompose=1)["decompose_hulls"] is None,
      "a single convex element must never decompose")
os.environ["UEO3DE_DECOMPOSE"] = "1"
try:
    check(assetinfo.physics_for_asset(asset(shapes=convex3))["decompose_hulls"] == 3,
          "decompose=None should read UEO3DE_DECOMPOSE")
finally:
    os.environ.pop("UEO3DE_DECOMPOSE", None)

# The knob gates BOTH backends but was named for PhysX, so the old name stays
# working -- and an alias nobody asserts is an alias that quietly stops
# working. Both names, and which one wins when both are set.
check(assetinfo.decompose_env_value({"UEO3DE_DECOMPOSE": "64"}) == "64",
      "the backend-neutral name must be read")
check(assetinfo.decompose_env_value({"UEO3DE_PHYSX_DECOMPOSE": "32"}) == "32",
      "the historical PhysX-named knob must keep working; it is in shipped docs")
check(assetinfo.decompose_env_value(
          {"UEO3DE_DECOMPOSE": "64", "UEO3DE_PHYSX_DECOMPOSE": "32"}) == "64",
      "with both set the backend-neutral name must win, or the deprecated one "
      "silently overrides the current one")
check(assetinfo.decompose_env_value(
          {"UEO3DE_DECOMPOSE": "  ", "UEO3DE_PHYSX_DECOMPOSE": "32"}) == "32",
      "an EMPTY new-name variable must not mask a set old-name one -- that is "
      "how `set UEO3DE_DECOMPOSE=` in a shell would silently disable the knob "
      "someone had configured")
check(assetinfo.decompose_env_value({}) == "",
      "neither set means unset, not an error")
check(assetinfo.physics_for_asset(asset(shapes=convex3))["decompose_hulls"] is None,
      "with the env unset, decomposition must default OFF (a single hull is "
      "what the editor's own Add PhysXMesh produces)")

# --- 2a-bis. the knob must not turn ON when a human writes OFF ---------------
# An earlier version parsed this with int() and mapped ValueError to 1, so
# every word meaning "off" enabled V-HACD -- silently, and since the value
# changes sidecar bytes, it re-fingerprinted every multi-convex FBX into a
# minutes-per-mesh cook. Directionality is the whole assertion.
for text in ("0", "off", "OFF", "false", "False", "no", "none", "disabled", "",
             "  ", "-3"):
    check(assetinfo.decompose_setting(text) == 0,
          "UEO3DE_DECOMPOSE=%r must mean OFF, got %r"
          % (text, assetinfo.decompose_setting(text)))
for text in ("1", "on", "true", "YES", "enabled"):
    check(assetinfo.decompose_setting(text) == 1,
          "UEO3DE_DECOMPOSE=%r must mean ON, got %r"
          % (text, assetinfo.decompose_setting(text)))
check(assetinfo.decompose_setting("128") == 128,
      "a numeric hull cap must survive parsing")
for text in ("maybe", "yes please", "64k"):
    try:
        assetinfo.decompose_setting(text)
        check(False, "UEO3DE_PHYSX_DECOMPOSE=%r must RAISE rather than guess a "
                     "direction (guessing 'on' costs a whole-project recook)" % text)
    except ValueError:
        pass
os.environ["UEO3DE_PHYSX_DECOMPOSE"] = "off"
try:
    check(assetinfo.physics_for_asset(asset(shapes=many))["decompose_hulls"] is None,
          "UEO3DE_PHYSX_DECOMPOSE=off must leave decomposition off end to end")
finally:
    os.environ.pop("UEO3DE_PHYSX_DECOMPOSE", None)

# --- 2b. physics_in_sidecar reads back what write() wrote -------------------
scratch = tempfile.mkdtemp(prefix="ueo3de_pxmesh_")
fbx = os.path.join(scratch, "sm_thing.fbx")
sidecar = assetinfo.write(fbx, "SM_Thing", physics=convex_plan)
check(assetinfo.physics_in_sidecar(sidecar) == {"method": "convex"},
      "physics_in_sidecar failed to read back a convex group")
# A decomposing group (ue/vhacd staging) reports its cap, so the import
# report can tell a decomposed product from a whole-mesh hull.
sidecar = assetinfo.write(fbx, "SM_Thing",
                          physics={"method": "convex", "elements": 34,
                                   "decompose_hulls": 34})
check(assetinfo.physics_in_sidecar(sidecar) == {"method": "convex", "decompose_hulls": 34},
      "physics_in_sidecar must carry the decomposition cap of a decomposing group, got %r"
      % (assetinfo.physics_in_sidecar(sidecar),))
sidecar = assetinfo.write(fbx, "SM_Thing",
                          physics={"method": "trimesh", "elements": 0,
                                   "decompose_hulls": None})
check(assetinfo.physics_in_sidecar(sidecar) == {"method": "trimesh"},
      "physics_in_sidecar failed to read back a trimesh group")
sidecar = assetinfo.write(fbx, "SM_Thing")
check(assetinfo.physics_in_sidecar(sidecar) is None,
      "a render-only sidecar must read back as no physics group")
check(assetinfo.physics_in_sidecar(os.path.join(scratch, "missing.assetinfo")) is None,
      "a missing sidecar must read back as None, not raise")
broken = os.path.join(scratch, "broken.assetinfo")
with open(broken, "w") as handle:
    handle.write("{not json")
check(assetinfo.physics_in_sidecar(broken) is None,
      "a malformed sidecar must read back as None, not raise")

# --- 3a. product paths ------------------------------------------------------
# Both builders name the product from the FULL source filename, .fbx included
# (verified in both caches: sm_rock.fbx.pxmesh, sm_carriage.fbx.joltmesh).
check(staging.pxmesh_product_path_for("uetoo3de/a/B.fbx", "assets")
      == "assets/uetoo3de/a/b.fbx.pxmesh",
      "pxmesh product path: got %r"
      % staging.pxmesh_product_path_for("uetoo3de/a/B.fbx", "assets"))
check(staging.physics_product_path_for("uetoo3de/a/B.fbx", "assets", "jolt")
      == "assets/uetoo3de/a/b.fbx.joltmesh",
      "joltmesh product path: got %r"
      % staging.physics_product_path_for("uetoo3de/a/B.fbx", "assets", "jolt"))
try:
    staging.physics_product_path_for("a/b.fbx", "assets", "bullet")
    check(False, "an unknown backend must raise rather than invent an extension")
except staging.StagingError:
    pass

# --- 3b. project gating -----------------------------------------------------
def project_with_gems(gems):
    root = tempfile.mkdtemp(prefix="ueo3de_proj_")
    with open(os.path.join(root, "project.json"), "w") as handle:
        json.dump({"gem_names": gems}, handle)
    assets = os.path.join(root, "Assets")
    os.makedirs(assets)
    return assets

check(staging.project_has_physx_gem(project_with_gems(["Atom", "PhysX5"])),
      "a project with the PhysX5 gem (26.05's name) must gate ON")
check(staging.project_has_physx_gem(project_with_gems(["PhysX"])),
      "a project with the plain PhysX gem name must gate ON")
check(staging.project_has_physx_gem(project_with_gems([{"name": "PhysX5"}])),
      "dict-shaped gem entries must be handled")
check(not staging.project_has_physx_gem(project_with_gems(["JoltPhysics"])),
      "a Jolt-only project must gate PhysX OFF -- its AP has no serializer "
      "for the physx group $type and could never cook the product")

# Per-backend gating: each gem enables its own group and only its own.
check(staging.project_physics_backends(project_with_gems(["JoltPhysics"]))
      == ("jolt",),
      "a JoltPhysics project must cook jolt groups only, got %r"
      % (staging.project_physics_backends(project_with_gems(["JoltPhysics"])),))
check(staging.project_physics_backends(project_with_gems(["PhysX5"]))
      == ("physx",),
      "a PhysX5 project must cook physx groups only")
check(staging.project_physics_backends(
          project_with_gems(["PhysX5", "JoltPhysics"])) == ("physx", "jolt"),
      "a project with both gems must cook both, in a fixed order")
check(staging.project_physics_backends(project_with_gems(["Atom"])) == (),
      "a project with neither physics gem must cook nothing")
check(not staging.project_has_physx_gem(
          os.path.join(tempfile.mkdtemp(prefix="ueo3de_none_"), "Assets")),
      "a missing project.json must gate OFF, not raise")

# The override, which exists because O3DE activates gems TRANSITIVELY: a
# project listing only a game gem that depends on PhysX runs the PhysX backend
# while the literal scan says no, and the importer's "restage to fix" advice
# would then re-run the same scan forever.
jolt_project = project_with_gems(["JoltPhysics"])
physx_project = project_with_gems(["PhysX5"])
os.environ["UEO3DE_PHYSX_COOK"] = "1"
try:
    check(staging.project_has_physx_gem(jolt_project),
          "UEO3DE_PHYSX_COOK=1 must force cooking ON for a project whose "
          "gem_names do not name PhysX (transitive activation)")
finally:
    os.environ.pop("UEO3DE_PHYSX_COOK", None)
os.environ["UEO3DE_PHYSX_COOK"] = "0"
try:
    check(not staging.project_has_physx_gem(physx_project),
          "UEO3DE_PHYSX_COOK=0 must force cooking OFF even with PhysX listed")
finally:
    os.environ.pop("UEO3DE_PHYSX_COOK", None)
os.environ["UEO3DE_PHYSX_COOK"] = "perhaps"
try:
    staging.project_has_physx_gem(physx_project)
    check(False, "an unparseable UEO3DE_PHYSX_COOK must raise, not be ignored")
except staging.StagingError:
    pass
finally:
    os.environ.pop("UEO3DE_PHYSX_COOK", None)

# --- 3c. the BYTES on disk, which is what the Asset Processor fingerprints ---
# Sections 1a/1c compare build()'s dict re-serialized by this test; the AP sees
# whatever write() puts on disk. Two writes of the same input must be
# byte-identical or every restage re-processes every FBX.
byte_dir = tempfile.mkdtemp(prefix="ueo3de_bytes_")
byte_fbx = os.path.join(byte_dir, "sm_thing.fbx")
first = open(assetinfo.write(byte_fbx, "SM_Thing", physics=convex_plan), "rb").read()
second = open(assetinfo.write(byte_fbx, "SM_Thing", physics=convex_plan), "rb").read()
check(first == second,
      "two writes of the same sidecar produced different BYTES; every restage "
      "would re-fingerprint and re-process every FBX")
check(first == json.dumps(document, separators=(",", ":")).encode("ascii"),
      "write() did not put the compact two-group document on disk verbatim "
      "(whitespace, key order, encoding or a trailing newline drifted); got %r"
      % first[:160])
plain_bytes = open(assetinfo.write(byte_fbx, "SM_Thing"), "rb").read()
check(plain_bytes == expected_baseline.encode("ascii"),
      "a physics-free sidecar's BYTES drifted from the shipped form: %r"
      % plain_bytes)

# --- 4. author_entity_physics: who gets the cooked collider -----------------
class RecordingAdapter(object):
    """Records every authoring call; capabilities set per test."""

    def __init__(self, caps):
        self._caps = set(caps)
        self.calls = []

    def name(self):
        return "fake"

    def capabilities(self):
        return set(self._caps)

    def add_static_body(self, entity_id, layer=None):
        self.calls.append(("static_body",))

    def add_dynamic_body(self, entity_id, **kw):
        self.calls.append(("dynamic_body", bool(kw.get("kinematic"))))

    def add_box_collider(self, entity_id, half_extents, local_offset=None,
                         local_rotation=None, material=None, layer=None):
        self.calls.append(("box", tuple(round(v, 6) for v in half_extents)))

    def add_sphere_collider(self, entity_id, radius, local_offset=None,
                            material=None, layer=None):
        self.calls.append(("sphere", round(radius, 6)))

    def add_capsule_collider(self, entity_id, radius, height, local_offset=None,
                             local_rotation=None, material=None, layer=None):
        self.calls.append(("capsule", round(radius, 6), round(height, 6)))

    def add_mesh_collider(self, entity_id, convex, material=None, layer=None,
                          asset_id=None):
        self.calls.append(("mesh", bool(convex), asset_id))

    def make_trigger(self, entity_id):
        self.calls.append(("trigger",))


COOKED_CAPS = {base.CAP_SHAPE_BOX, base.CAP_SHAPE_SPHERE,
               base.CAP_SHAPE_CAPSULE, base.CAP_SHAPE_MESH_COOKED}
BAKING_CAPS = {base.CAP_SHAPE_BOX, base.CAP_SHAPE_CONVEX, base.CAP_SHAPE_TRIMESH}


def physics_block(**overrides):
    block = {"has_collision": True, "is_trigger": False,
             "simulates_physics": False, "kinematic": False,
             "collision_profile": "", "ccd": False,
             "enable_gravity": True, "linear_damping": 0.0,
             "angular_damping": 0.0, "mass_override": False, "mass_kg": None,
             "shapes": [], "shapes_from_asset": None}
    block.update(overrides)
    return block


def entity(physics, mesh_guid=None, name="Thing"):
    item = {"name": name, "physics": physics,
            "transform": {"world": {"scale": [1.0, 1.0, 1.0]}}}
    if mesh_guid:
        item["mesh"] = {"asset_guid": mesh_guid}
    return item


def author(adapter, item, assets, cooked):
    report = Report()
    summary = physics_build.author_entity_physics(
        adapter, "eid", item, assets, report, {}, cooked_mesh_ids=cooked)
    return summary, [r["code"] for r in report.records()]


CONVEX_ELEM = {"type": "convex", "vertex_count": 8,
               "aabb_min": [-1.0, -1.0, -1.0], "aabb_max": [1.0, 1.0, 1.0]}
ASSETS = {"g1": {"guid": "g1", "ue_path": "/Game/X", "collision":
                 {"source": "simple", "shapes": [dict(CONVEX_ELEM)] * 3}},
          "g2": {"guid": "g2", "ue_path": "/Game/Y", "collision":
                 {"source": "none", "shapes": []}}}

# Cooked asset available: ONE mesh collider carrying the asset id, no boxes.
adapter = RecordingAdapter(COOKED_CAPS)
_s, codes = author(adapter, entity(physics_block(shapes_from_asset="g1")),
                   ASSETS, {"g1": {"asset_id": "AID-1", "method": "convex"}})
mesh_calls = [c for c in adapter.calls if c[0] == "mesh"]
box_calls = [c for c in adapter.calls if c[0] == "box"]
check(mesh_calls == [("mesh", True, "AID-1")] and not box_calls,
      "3 convex elements + a cooked asset should author exactly one mesh "
      "collider with the asset id; got mesh=%r box=%r" % (mesh_calls, box_calls))
check(codes.count("PHYS_SHAPE_APPROXIMATED") == 1,
      "the collapse must still be reported once (concavities fill in), got %r"
      % codes)

# No cooked asset: the old per-element AABB boxes, NOT a collapse. This is
# the author-level pin on the regression test_convex.py documents.
adapter = RecordingAdapter(COOKED_CAPS)
_s, codes = author(adapter, entity(physics_block(shapes_from_asset="g1")),
                   ASSETS, {})
box_calls = [c for c in adapter.calls if c[0] == "box"]
mesh_calls = [c for c in adapter.calls if c[0] == "mesh"]
check(len(box_calls) == 3 and not mesh_calls,
      "without a cooked asset each convex element must keep its own AABB box; "
      "got box=%d mesh=%d" % (len(box_calls), len(mesh_calls)))

# A cooked TRIMESH id must not satisfy a convex element (wrong geometry).
adapter = RecordingAdapter(COOKED_CAPS)
_s, _codes = author(adapter, entity(physics_block(shapes_from_asset="g1")),
                    ASSETS, {"g1": {"asset_id": "AID-T", "method": "trimesh"}})
check([c for c in adapter.calls if c[0] == "box"] and
      not [c for c in adapter.calls if c[0] == "mesh"],
      "a trimesh cook must not be attached to convex elements; boxes substitute")

# --- COOKED BEATS BAKED, on a backend that can do both -----------------------
# Jolt's mesh colliders moved to cooked .joltmesh assets while keeping the
# render-mesh bake available under a second component, so it advertises BOTH
# routes. The cooked one must win wherever a product exists: it needs no
# tick-time bake (so no settle, and no PHYS_COLLIDER_NOT_BAKED risk) and it
# references one shared asset instead of copying geometry into every instance
# -- 315.7 MB of baked Jolt prefab versus 22.0 MB of PhysX asset references on
# the same level.
adapter = RecordingAdapter(BAKING_CAPS | {base.CAP_SHAPE_MESH_COOKED})
_s, _codes = author(adapter, entity(physics_block(shapes_from_asset="g1")),
                    ASSETS, {"g1": {"asset_id": "AID-1", "method": "convex"}})
check([c for c in adapter.calls if c[0] == "mesh"] == [("mesh", True, "AID-1")],
      "a backend with BOTH routes must prefer the cooked asset, got %r"
      % adapter.calls)

# ...and must still bake when there is no cooked product for that mesh, which
# is the case for anything staged before the group existed or whose cook
# failed. Losing this fallback would silently drop those entities to boxes.
adapter = RecordingAdapter(BAKING_CAPS | {base.CAP_SHAPE_MESH_COOKED})
_s, _codes = author(adapter, entity(physics_block(shapes_from_asset="g1")),
                    ASSETS, {})
check([c for c in adapter.calls if c[0] == "mesh"] == [("mesh", True, None)],
      "with no cooked product the render-mesh bake must still happen, got %r"
      % adapter.calls)

# A bake-only backend (an older Jolt gem) is untouched by cooked ids reaching
# it -- it cannot use them, and physics_build must not hand them over.
adapter = RecordingAdapter(BAKING_CAPS)
_s, _codes = author(adapter, entity(physics_block(shapes_from_asset="g1")),
                    ASSETS, {"g1": {"asset_id": "AID-1", "method": "convex"}})
check([c for c in adapter.calls if c[0] == "mesh"] == [("mesh", True, None)],
      "a bake-only backend must keep baking from the render mesh "
      "(no asset id), got %r" % adapter.calls)

# source 'none' + static body + cooked trimesh -> triangle-mesh collider.
adapter = RecordingAdapter(COOKED_CAPS)
_s, codes = author(adapter, entity(physics_block(shapes_from_asset="g2")),
                   ASSETS, {"g2": {"asset_id": "AID-2", "method": "trimesh"}})
check([c for c in adapter.calls if c[0] == "mesh"] == [("mesh", False, "AID-2")],
      "a static body with no simple collision should get the cooked triangle "
      "mesh, got %r" % adapter.calls)
check("PHYS_MESH_FROM_RENDER" in codes,
      "the render-mesh substitution must stay visible in the report")

# source 'none' + KINEMATIC body: triangle mesh is legal on kinematic actors.
adapter = RecordingAdapter(COOKED_CAPS)
_s, _codes = author(adapter, entity(physics_block(shapes_from_asset="g2",
                                                  kinematic=True)),
                    ASSETS, {"g2": {"asset_id": "AID-2", "method": "trimesh"}})
check([c for c in adapter.calls if c[0] == "mesh"] == [("mesh", False, "AID-2")],
      "a kinematic body should accept the cooked triangle mesh, got %r"
      % adapter.calls)

# source 'none' + DYNAMIC body: PhysX rejects trimesh on simulated actors at
# runtime with only a component warning -- authoring it would produce a
# silently non-colliding entity, so the gap must be reported instead.
adapter = RecordingAdapter(COOKED_CAPS)
_s, codes = author(adapter, entity(physics_block(shapes_from_asset="g2",
                                                 simulates_physics=True)),
                   ASSETS, {"g2": {"asset_id": "AID-2", "method": "trimesh"}})
check(not [c for c in adapter.calls if c[0] == "mesh"],
      "a dynamic body must NOT get a cooked triangle mesh, got %r" % adapter.calls)
check("PHYS_SHAPE_APPROXIMATED" in codes,
      "the dynamic-body trimesh gap must be reported, got %r" % codes)

# --- THE TRIGGER RESTRICTION, which the first version of this route missed ---
# PhysX refuses to raise the trigger flag on triangle-mesh geometry, so a
# trimesh cook on a trigger is a collider that reports healthy, verifies
# healthy (its .pxmesh reference DOES serialize), and never fires an overlap.
# The gate was `body != "dynamic"`, which admits "static+trigger". Before the
# cooked route existed that entity was a REPORTED gap; authoring it silently
# was strictly worse than both the gap and the box it replaced.
adapter = RecordingAdapter(COOKED_CAPS)
_s, codes = author(adapter,
                   entity(physics_block(shapes_from_asset="g2", is_trigger=True)),
                   ASSETS, {"g2": {"asset_id": "AID-2", "method": "trimesh"}})
check(not [c for c in adapter.calls if c[0] == "mesh"],
      "a TRIGGER must not receive a cooked triangle mesh (PhysX cannot use "
      "trimesh geometry as a trigger shape), got %r" % adapter.calls)
check("PHYS_SHAPE_APPROXIMATED" in codes,
      "the trigger/trimesh gap must be reported, not silent; got %r" % codes)
# A CONVEX cook is fine on a trigger -- PhysX supports convex trigger shapes,
# so the gate must not over-reject and cost triggers their real geometry.
adapter = RecordingAdapter(COOKED_CAPS)
_s, _codes = author(adapter,
                    entity(physics_block(shapes_from_asset="g1", is_trigger=True)),
                    ASSETS, {"g1": {"asset_id": "AID-1", "method": "convex"}})
check([c for c in adapter.calls if c[0] == "mesh"] == [("mesh", True, "AID-1")],
      "a trigger with a CONVEX cook should still get its cooked collider, got %r"
      % adapter.calls)
check(("trigger",) in adapter.calls,
      "make_trigger must still run for a trigger entity, got %r" % adapter.calls)

# The same hole existed in the authored==0 fallback; pin it there too.
adapter = RecordingAdapter(COOKED_CAPS)
_s, codes = author(adapter,
                   entity(physics_block(is_trigger=True), mesh_guid="g2"),
                   ASSETS, {"g2": {"asset_id": "AID-2", "method": "trimesh"}})
check(not [c for c in adapter.calls if c[0] == "mesh"],
      "the no-shapes fallback must not give a trigger a cooked triangle mesh, "
      "got %r" % adapter.calls)

# The gap message must name the real blocker: a cooked mesh EXISTS, so
# "cannot build a collider from a render mesh" would send the reader hunting
# for a missing asset that is sitting in the cache.
report = Report()
physics_build.author_entity_physics(
    RecordingAdapter(COOKED_CAPS), "eid",
    entity(physics_block(shapes_from_asset="g2", is_trigger=True)),
    ASSETS, report, {}, cooked_mesh_ids={"g2": {"asset_id": "AID-2",
                                                "method": "trimesh"}})
details = " ".join(r["detail"] for r in report.records())
check("TRIANGLE MESH" in details and "trigger" in details,
      "the trigger gap should say the cooked mesh is a triangle mesh and that a "
      "trigger cannot use it; got %r" % details)

# authored==0 fallback: entity has a render mesh whose asset cooked.
adapter = RecordingAdapter(COOKED_CAPS)
_s, codes = author(adapter, entity(physics_block(), mesh_guid="g2"),
                   ASSETS, {"g2": {"asset_id": "AID-2", "method": "trimesh"}})
check([c for c in adapter.calls if c[0] == "mesh"] == [("mesh", False, "AID-2")],
      "the no-shapes-anywhere fallback should use the cooked mesh, got %r"
      % adapter.calls)

# Entity-owned convex shapes have no backing asset: never the cooked route,
# never collapsed -- each element keeps its own box even when the entity's
# RENDER mesh happens to have a cooked product.
adapter = RecordingAdapter(COOKED_CAPS)
_s, _codes = author(adapter,
                    entity(physics_block(shapes=[dict(CONVEX_ELEM),
                                                 dict(CONVEX_ELEM)]),
                           mesh_guid="g1"),
                    ASSETS, {"g1": {"asset_id": "AID-1", "method": "convex"}})
box_calls = [c for c in adapter.calls if c[0] == "box"]
check(len(box_calls) == 2 and not [c for c in adapter.calls if c[0] == "mesh"],
      "entity-owned convex shapes must keep per-element boxes (no backing "
      "asset to cook); got %r" % adapter.calls)

# Mixed primitives + convex through the cooked route: the box element keeps
# its own dimensions, the convex elements become the one cooked collider.
mixed_assets = {"g3": {"guid": "g3", "ue_path": "/Game/Z", "collision": {
    "source": "simple",
    "shapes": [{"type": "box", "half_extents": [0.5, 0.5, 0.5],
                "offset": [0.0, 0.0, 1.0], "rotation": [0.0, 0.0, 0.0, 1.0]},
               dict(CONVEX_ELEM), dict(CONVEX_ELEM)]}}}
adapter = RecordingAdapter(COOKED_CAPS)
_s, _codes = author(adapter, entity(physics_block(shapes_from_asset="g3")),
                    mixed_assets, {"g3": {"asset_id": "AID-3", "method": "convex"}})
check([c for c in adapter.calls if c[0] == "box"] == [("box", (0.5, 0.5, 0.5))],
      "the box element must survive the cooked route with its own dimensions, "
      "got %r" % adapter.calls)
check([c for c in adapter.calls if c[0] == "mesh"] == [("mesh", True, "AID-3")],
      "the convex elements must become one cooked collider, got %r"
      % adapter.calls)

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
