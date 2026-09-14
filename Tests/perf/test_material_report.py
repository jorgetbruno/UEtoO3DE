"""
test_material_report.py — the import names the materials that fell back to the default.

Pure: no editor. Run: python Tests/perf/test_material_report.py

WHY THIS EXISTS. A material that does not convert leaves every entity using it
on the backend's default material. On NYC1950 the only trace was the export's
`MAT_EXPR_UNSUPPORTED x223`, a tally no one could act on, while 262 entities
rendered white, the Landscape and 249 building trims among them. The import
now warns once per failed material with the number of entities it affects.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts"))

from ueimporter import importer, report  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def entity(i, *guids):
    return {"id": "e%d" % i, "name": "E%d" % i,
            "mesh": {"asset_guid": "m", "material_slots": [
                {"index": n, "material_guid": g} for n, g in enumerate(guids)]}}


document = {
    "assets": [
        {"kind": "material", "guid": "ok", "name": "MI_Brick", "ue_path": "/Game/MI_Brick",
         "material_data": {"properties": {}}},
        {"kind": "material", "guid": "metal", "name": "MI_Metal2", "ue_path": "/Game/MI_Metal2",
         "material_data": None},
        {"kind": "material", "guid": "land", "name": "MI_Landscape", "ue_path": "/Game/MI_Landscape"},
        {"kind": "material", "guid": "unused", "name": "MI_Unused", "ue_path": "/Game/MI_Unused",
         "material_data": None},
    ],
    "entities": [entity(1, "ok", "metal"), entity(2, "metal", "metal"), entity(3, "metal"),
                 entity(4, "land"), entity(5, "ok"), {"id": "e6", "name": "NoMesh"}],
}

rows = importer.unconverted_materials(document)
check(rows == [("MI_Metal2", "/Game/MI_Metal2", 3), ("MI_Landscape", "/Game/MI_Landscape", 1)],
      "failed materials must be listed with their entity counts, most-used first; got %r" % (rows,))
check(all(name != "MI_Unused" for name, _path, _n in rows),
      "a failed material no entity uses is not worth a warning")
check(all(name != "MI_Brick" for name, _path, _n in rows),
      "a converted material must never be reported as a fallback")
counts = dict((name, n) for name, _path, n in rows)
check(counts.get("MI_Metal2") == 3,
      "an entity using one failed material in two slots counts once, not twice")
check(importer.unconverted_materials({"assets": [], "entities": []}) == [],
      "a manifest with nothing failed reports nothing")
check("MAT_DEFAULT_MATERIAL" in report.CODES,
      "MAT_DEFAULT_MATERIAL must be a registered report code")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
