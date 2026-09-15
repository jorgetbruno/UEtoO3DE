"""
test_slot_labels.py — a material goes on the slot whose label is EXACTLY its name.

Pure: no editor. Run: python Tests/perf/test_slot_labels.py

WHY THIS EXISTS. O3DE's FindMaterialAssignmentId matches a slot label by
substring. On Eastern Province's SM_Temple_Roof_01 (slots MI_Roof,
MI_Roof_Border, MI_WoodTrim), "MI_Roof" resolved to MI_Roof_Border. The roof
material landed on the border, the border material overwrote it, and the roof
kept the model default on all 57 instances. There was no warning, because
every lookup "succeeded". Stable ids below are the ones measured on that model.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts"))

from ueimporter.prefab_build import slot_row  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# SM_Temple_Roof_01: rows in model order.
rows = [4181915757, 1661686102, 2572555935]
labels = {"MI_Roof": 4181915757, "MI_Roof_Border": 1661686102, "MI_WoodTrim": 2572555935}

check(slot_row("MI_Roof", labels, rows) == 0, "MI_Roof must be row 0, not the border that contains its name")
check(slot_row("MI_Roof_Border", labels, rows) == 1, "MI_Roof_Border is row 1")
check(slot_row("MI_WoodTrim", labels, rows) == 2, "MI_WoodTrim is row 2")
assigned = {slot_row(name, labels, rows) for name in labels}
check(assigned == {0, 1, 2}, "three materials must reach three distinct rows; got %r" % assigned)
check(slot_row("MI_Roof_B", labels, rows) is None, "a prefix of a label is not that label")
check(slot_row("Roof", labels, rows) is None, "a substring of a label is not that label")

# The FBX name-dedup probe: MI_X_1 must not match MI_X_10, and a row already
# claimed is skipped.
dedup = {"MI_X": 10, "MI_X_1": 11, "MI_X_10": 12}
dedup_rows = [12, 10, 11]
check(slot_row("MI_X_1", dedup, dedup_rows) == 2, "MI_X_1 is its own row, not MI_X_10's")
check(slot_row("MI_X_1", dedup, dedup_rows, used_rows={2}) is None, "a used row is never handed out twice")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
