"""
test_chunk.py — splitting a level too big for one prefab, without losing anything.

Pure: no editor. Run: python Tests/perf/test_chunk.py   (exit code is the verdict)

WHY THIS EXISTS. Measured on a 44,504-entity marketplace city: 4,000 entities
import in 126 s at a 4.8 GB peak, and 12,000 kill the editor outright during
`saving prefab` -- no assert, no log line, exit 0xC0000409. A level that size
has to arrive as several prefabs, so `importer.chunk_of` partitions it.

THE PROPERTY THAT MATTERS is not "the chunks are about the same size". It is
that the partition **loses nothing and splits no subtree**, because both
failures are silent:

  * an entity in no chunk simply is not in the level, and nothing counts it;
  * a child separated from its parent still imports -- it lands at the level
    root instead of where it belongs. A building's windows at the origin, in a
    prefab that saved without a single warning.

So every assertion below is about coverage and containment, and each has a
control that would fail if the partition drifted to a degenerate answer (all
entities in one bin, or entities silently dropped).
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


def make(tree):
    """`tree` is {id: parent_id} -> a manifest-shaped document."""
    return {"schema_version": 7, "level": {"name": "T"}, "assets": [],
            "entities": [{"id": i, "name": "e%s" % i, "parent_id": p}
                         for i, p in tree.items()]}


def parent_of(document):
    return {e["id"]: e["parent_id"] for e in document["entities"]}


def split(document, count):
    return [importer.chunk_of(document, i, count) for i in range(count)]


def assert_partition(document, count, label):
    chunks = split(document, count)
    all_ids = [e["id"] for e in document["entities"]]
    seen = [e["id"] for chunk in chunks for e in chunk["entities"]]

    check(sorted(seen) == sorted(all_ids),
          "%s: chunks do not reproduce the entity set (%d entities in, %d out, "
          "%d duplicated)" % (label, len(all_ids), len(seen),
                              len(seen) - len(set(seen))))
    check(len(seen) == len(set(seen)),
          "%s: an entity appears in more than one chunk" % label)

    # Containment: every non-root entity's parent is in the SAME chunk.
    for number, chunk in enumerate(chunks):
        ids = {e["id"] for e in chunk["entities"]}
        for entity in chunk["entities"]:
            if entity["parent_id"] is None:
                continue
            check(entity["parent_id"] in ids,
                  "%s: chunk %d holds entity %r but not its parent %r -- it "
                  "would import at the level root"
                  % (label, number, entity["id"], entity["parent_id"]))
    return chunks


# --- a deep chain: the whole level is ONE subtree ---------------------------
# It cannot be split without cutting a parent from a child, so a correct
# partition puts all of it in one chunk and leaves the rest empty. A splitter
# that "balanced" this would be wrong.
chain = make({1: None, 2: 1, 3: 2, 4: 3, 5: 4})
chunks = assert_partition(chain, 3, "single deep chain")
non_empty = [c for c in chunks if c["entities"]]
check(len(non_empty) == 1 and len(non_empty[0]["entities"]) == 5,
      "single deep chain: expected one chunk of 5, got %r"
      % [len(c["entities"]) for c in chunks])

# --- many singletons: should spread evenly ---------------------------------
flat = make({i: None for i in range(1, 13)})
chunks = assert_partition(flat, 4, "12 singletons")
check(sorted(len(c["entities"]) for c in chunks) == [3, 3, 3, 3],
      "12 singletons into 4 chunks should be 3 each, got %r"
      % sorted(len(c["entities"]) for c in chunks))

# --- mixed: a few big families and a crowd of singletons --------------------
tree = {}
next_id = 1
for family in range(4):                      # 4 families of 1 parent + 5 kids
    root = next_id
    tree[root] = None
    next_id += 1
    for _ in range(5):
        tree[next_id] = root
        next_id += 1
for _ in range(20):                          # 20 loose props
    tree[next_id] = None
    next_id += 1
mixed = make(tree)
chunks = assert_partition(mixed, 5, "4 families + 20 singletons")
sizes = sorted(len(c["entities"]) for c in chunks)
check(sizes[-1] - sizes[0] <= 6,
      "chunks are badly unbalanced: %r" % sizes)

# The control for the balance check: if everything landed in one bin the
# containment and coverage tests above would still pass, so assert it did not.
check(all(c["entities"] for c in chunks),
      "some chunk came out empty while others were full -- the partition is "
      "not distributing work")

# --- n == 1 is the identity ------------------------------------------------
whole = importer.chunk_of(mixed, 0, 1)
check(len(whole["entities"]) == len(mixed["entities"]),
      "chunk 1/1 dropped entities: %d of %d"
      % (len(whole["entities"]), len(mixed["entities"])))

# --- more chunks than roots -------------------------------------------------
chunks = assert_partition(flat, 20, "12 singletons into 20 chunks")
check(sum(1 for c in chunks if not c["entities"]) == 8,
      "12 roots into 20 chunks should leave 8 empty, got %d"
      % sum(1 for c in chunks if not c["entities"]))

# --- determinism: the same manifest must split the same way every time ------
# A chunk that moved between runs would make re-import meaningless: the ledger
# is per prefab, so an entity that changed chunks would look deleted in one and
# new in another.
first = [[e["id"] for e in c["entities"]] for c in split(mixed, 5)]
second = [[e["id"] for e in c["entities"]] for c in split(mixed, 5)]
check(first == second, "chunk_of is not deterministic across calls")

# Order of the entity list must not change the partition either -- the same
# level re-exported with entities in a different order should chunk the same.
shuffled = {"schema_version": 7, "level": {"name": "T"}, "assets": [],
            "entities": list(reversed(mixed["entities"]))}
reordered = [sorted(e["id"] for e in c["entities"]) for c in split(shuffled, 5)]
original = [sorted(e["id"] for e in c["entities"]) for c in split(mixed, 5)]
check(sorted(reordered) == sorted(original),
      "reordering the manifest changed the partition:\n  %r\n  %r"
      % (sorted(original), sorted(reordered)))

# --- the original document must not be mutated ------------------------------
before = len(mixed["entities"])
importer.chunk_of(mixed, 0, 3)
check(len(mixed["entities"]) == before,
      "chunk_of mutated the document it was given (%d -> %d entities)"
      % (before, len(mixed["entities"])))

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
