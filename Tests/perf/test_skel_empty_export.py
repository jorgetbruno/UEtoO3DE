"""
test_skel_empty_export.py — a skeletal mesh that exported no geometry leaves the manifest.

Pure: no editor. Run: python Tests/perf/test_skel_empty_export.py

WHY THIS EXISTS. UE's native skeletal FBX exporter writes no cloth-simulated
section. A character pack's armor part was all cloth, so its FBX held a
skeleton and no mesh, the Asset Processor built no .actor, and the import
waited 180 s for it and failed the level. The mesh is now dropped at export and
its entities keep their transforms, with a warning.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

from ueo3de import manifest, warnings  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


document = {
    "assets": [{"guid": "armor", "kind": "skeletal_mesh", "ue_path": "/Game/K/SKM_Armor"},
               {"guid": "body", "kind": "skeletal_mesh", "ue_path": "/Game/K/SKM_Body"}],
    "entities": [{"name": "Armor", "skeletal": {"asset_guid": "armor"}},
                 {"name": "Body", "skeletal": {"asset_guid": "body"}},
                 {"name": "Rock", "mesh": {"asset_guid": "rock"}}],
    "warnings": [],
}
dropped = manifest.drop_empty_skeletal_meshes(document, ["armor"])
check(dropped == ["/Game/K/SKM_Armor"], "the dropped asset is named; got %r" % dropped)
check([a["guid"] for a in document["assets"]] == ["body"], "only the empty mesh leaves the assets")
entities = {e["name"]: e for e in document["entities"]}
check("skeletal" not in entities["Armor"] and "Armor" in entities,
      "the entity stays, without a skeletal block the schema would reject as null")
check(entities["Body"]["skeletal"] == {"asset_guid": "body"}, "other skeletal entities are untouched")
check([w["code"] for w in document["warnings"]] == ["SKEL_MESH_EMPTY_EXPORT"]
      and document["warnings"][0]["subject"] == "Armor", "each affected entity is reported")
check("SKEL_MESH_EMPTY_EXPORT" in warnings.CODES, "the code is registered")
check(manifest.drop_empty_skeletal_meshes(document, []) == [], "nothing to drop changes nothing")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
