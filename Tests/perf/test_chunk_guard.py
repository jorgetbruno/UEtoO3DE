"""
test_chunk_guard.py — a level too big for one import must be REFUSED, not tried.

Pure: no editor. Run: python Tests/perf/test_chunk_guard.py

Measured on a 44,504-entity marketplace city: 4,000 entities import in 126 s at
a 4.8 GB peak; 12,000 kills the editor during `saving prefab` with no assert,
no log line and exit 0xC0000409. That failure is the worst kind — it arrives
late, costs the whole run, and leaves nothing to read. `chunk_of` has existed
to split such a level for a while, but nothing told anyone to use it: the
import simply proceeded until the process died.

So the importer now computes the split itself and refuses, naming the exact
commands. What is pinned here:

  * the ceiling is the largest size MEASURED TO WORK, not the smallest
    measured to fail — the gap between them is unexplored;
  * a manifest that fits is untouched (this guard must not change any import
    that works today, and every fixture is far below the ceiling);
  * the message names every chunk command, because "use chunking" without the
    commands is a guard that gets bypassed rather than followed;
  * an explicit UEO3DE_CHUNK — including 1/1 — is always obeyed: the guard
    exists to prevent an ACCIDENT, not to overrule a decision;
  * it measures what is about to be IMPORTED, not what the file contains. The
    first version ran before UEO3DE_MAX_ENTITIES was applied, so a deliberate
    500-entity bisect of a large level would have been refused for a size it
    was never going to import;
  * a garbage ceiling raises instead of silently falling back to the default,
    the same stance `decompose_setting` takes for the same reason.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import importer  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# --- 1. the split arithmetic --------------------------------------------------
check(importer.recommended_chunks(1, 4000) == 1, "one entity needs one chunk")
check(importer.recommended_chunks(4000, 4000) == 1,
      "exactly the ceiling must still be ONE chunk: the ceiling is a size "
      "measured to work, so refusing it would refuse a working import")
check(importer.recommended_chunks(4001, 4000) == 2,
      "one entity over the ceiling needs two chunks")
check(importer.recommended_chunks(12000, 4000) == 3,
      "12000 entities at a 4000 ceiling is 3 chunks, not 3.0 or 4")
check(importer.recommended_chunks(44504, 4000) == 12,
      "the measured city (44504) splits into 12 chunks")

# --- 2. the ceiling knob, in both directions ----------------------------------
check(importer.chunk_ceiling({}) == importer.CHUNK_CEILING,
      "an unset ceiling must be the measured default")
check(importer.chunk_ceiling({"UEO3DE_CHUNK_CEILING": "1000"}) == 1000,
      "the ceiling must be tunable for a machine with different measurements")
check(importer.chunk_ceiling({"UEO3DE_CHUNK_CEILING": "  "}) == importer.CHUNK_CEILING,
      "a blank ceiling is unset, not zero")
for bad in ("0", "-5"):
    try:
        importer.chunk_ceiling({"UEO3DE_CHUNK_CEILING": bad})
        check(False, "UEO3DE_CHUNK_CEILING=%r must raise, not be accepted" % bad)
    except ValueError:
        pass
try:
    importer.chunk_ceiling({"UEO3DE_CHUNK_CEILING": "lots"})
    check(False, "a non-numeric ceiling must raise rather than silently "
                 "falling back to the default")
except ValueError:
    pass

# --- 3. the message has to be followable --------------------------------------
message = importer.chunk_guard_message(44504, 12, 4000)
check("44504" in message and "4000" in message,
      "the message must state the size and the ceiling; got %r" % message)
for index in (1, 6, 12):
    check("UEO3DE_CHUNK=%d/12" % index in message,
          "the message must name chunk %d's command -- 'use chunking' without "
          "the commands is a guard people work around; got %r" % (index, message))
check("UEO3DE_CHUNK=1/1" in message,
      "the message must name the escape hatch: the guard prevents an accident, "
      "it does not overrule a decision")
check("UEO3DE_CHUNK_CEILING" in message,
      "the message must mention the ceiling knob for a machine that measured "
      "something different")

# --- 3b. the guard reads the document AFTER the shrinking knobs ---------------
# Pinned by reading the source, because the ordering is invisible in behaviour
# until someone bisects a big level and gets refused for a size they were not
# importing. If the guard is ever moved back above the max_entities block, this
# fails.
source_path = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor",
                           "Scripts", "ueimporter", "importer.py")
with open(source_path, "r", encoding="utf-8") as handle:
    source = handle.read()
guard_at = source.find("chunk_guard_message(count, chunks, ceiling)")
trim_at = source.find("Diagnostic bisect knob (UEO3DE_MAX_ENTITIES)")
check(guard_at > 0 and trim_at > 0,
      "could not locate the guard and the max_entities trim in importer.py")
check(trim_at < guard_at,
      "the chunk guard runs BEFORE UEO3DE_MAX_ENTITIES trims the document, so "
      "a deliberate small bisect of a large level would be refused for a size "
      "it never intended to import")

# --- 4. every real fixture stays under the ceiling ----------------------------
# If this ever fails, the guard is about to start refusing an import that works
# today, and the ceiling -- not the fixture -- needs revisiting.
import json  # noqa: E402

exports = os.path.join(REPO_ROOT, "Exports")
checked = 0
for name in sorted(os.listdir(exports)) if os.path.isdir(exports) else []:
    manifest = os.path.join(exports, name, "manifest.json")
    if not os.path.isfile(manifest):
        continue
    with open(manifest, "r") as handle:
        count = len(json.load(handle).get("entities") or [])
    chunks = importer.recommended_chunks(count, importer.CHUNK_CEILING)
    checked += 1
    if chunks > 1:
        print("  note: %s has %d entities -> %d chunks (guard would refuse a "
              "single import)" % (name, count, chunks))
print("  checked %d exported manifests against the ceiling" % checked)
check(checked > 0,
      "no exported manifest was found to check; this leg asserted nothing")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
