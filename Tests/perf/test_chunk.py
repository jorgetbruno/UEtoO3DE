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


# --- an oversized subtree is split by its direct children ----------------------
# Measured on NYC_Level_WC: an InstancedFoliageActor root with 13,964 flat
# children (foliage instances expanded into entities) landed whole in one
# 13,965-entity chunk, against a crash boundary near 12,000. A root larger
# than the ceiling is split by its direct children's subtrees; the root rides
# along in every piece so no child loses its parent.
os.environ["UEO3DE_CHUNK_CEILING"] = "4000"
big = {"entities": [{"id": "foliage", "parent_id": None, "name": "InstancedFoliageActor"}]
       + [{"id": "inst%05d" % i, "parent_id": "foliage", "name": "i"} for i in range(9000)]
       + [{"id": "lone%d" % i, "parent_id": None, "name": "l"} for i in range(50)]}
chunks_big = split(big, 3)
child_ids = [e["id"] for c in chunks_big for e in c["entities"] if e["id"] != "foliage"]
check(sorted(child_ids) == sorted(e["id"] for e in big["entities"] if e["id"] != "foliage"),
      "every child of an oversized root must appear exactly once across chunks")
check(len(child_ids) == len(set(child_ids)), "no child may be duplicated")
for c in chunks_big:
    ids = {e["id"] for e in c["entities"]}
    check(all(e["parent_id"] in ids for e in c["entities"] if e["parent_id"]),
          "a child's parent must be present in the child's own chunk")
    check(len(c["entities"]) <= 4000 + 1,
          "no chunk may exceed the ceiling (plus the duplicated root); got %d"
          % len(c["entities"]))
check(sum(1 for c in chunks_big if any(e["id"] == "foliage" for e in c["entities"])) >= 3,
      "the oversized root must ride along in every piece")
del os.environ["UEO3DE_CHUNK_CEILING"]

# --- spatial order: each chunk is a compact patch of the map -------------------
# Measured on NYC_Level_WC (51,776 entities, 13 chunks): the size order spread
# every chunk over the whole map -- "the nearest part" was every thirteenth
# building, and one finished street needed all thirteen prefabs. The spatial
# order walks the roots along a Hilbert curve and cuts it into contiguous
# runs. The property: a chunk's XY extent is a FRACTION of the level's, and
# the partition guarantees above hold unchanged.


def grid(side, spacing=10.0):
    """side x side singleton actors on a grid, with manifest transforms."""
    entities = []
    for gy in range(side):
        for gx in range(side):
            entities.append({"id": "g%02d_%02d" % (gx, gy), "name": "b", "parent_id": None,
                             "transform": {"world": {"translation": [gx * spacing, gy * spacing, 0.0],
                                                     "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]}}})
    return {"schema_version": 7, "level": {"name": "G"}, "assets": [], "entities": entities}


def extent(chunk):
    xs = [e["transform"]["world"]["translation"][0] for e in chunk["entities"]]
    ys = [e["transform"]["world"]["translation"][1] for e in chunk["entities"]]
    return (max(xs) - min(xs), max(ys) - min(ys)) if xs else (0.0, 0.0)


board = grid(16)                                   # 256 actors, 150 m square
spatial = [importer.chunk_of(board, i, 4, order="spatial") for i in range(4)]
by_size = [importer.chunk_of(board, i, 4, order="size") for i in range(4)]

check(sorted(len(c["entities"]) for c in spatial) == [64, 64, 64, 64],
      "spatial chunks of a uniform grid must be even; got %r"
      % sorted(len(c["entities"]) for c in spatial))
ids = sorted(e["id"] for c in spatial for e in c["entities"])
check(ids == sorted(e["id"] for e in board["entities"]),
      "the spatial partition must cover every entity exactly once")
for c in spatial:
    ex, ey = extent(c)
    check(ex <= 80.0 and ey <= 80.0,
          "a spatial chunk must be a compact patch (quadrant of a 150 m grid); "
          "got extent %.0f x %.0f m" % (ex, ey))
# CONTROL: the same grid under the size order spreads every chunk over the
# whole map -- if it did not, the spatial assertion above would prove nothing.
check(all(max(extent(c)) >= 140.0 for c in by_size),
      "control: the size order should spread each chunk over the whole grid")

# the environment knob selects it, and the default stays `size`
os.environ["UEO3DE_CHUNK_ORDER"] = "spatial"
try:
    via_env = [importer.chunk_of(board, i, 4) for i in range(4)]
    check([sorted(e["id"] for e in c["entities"]) for c in via_env]
          == [sorted(e["id"] for e in c["entities"]) for c in spatial],
          "UEO3DE_CHUNK_ORDER=spatial must select the spatial order")
    assert_partition(mixed, 4, "mixed, spatial order")
    # the oversized root rides along in every piece BY DESIGN, so it is the
    # one entity `assert_partition` must not hold to "exactly once"
    chunks_big_s = split(big, 3)
    child_ids_s = [e["id"] for c in chunks_big_s for e in c["entities"] if e["id"] != "foliage"]
    check(sorted(child_ids_s) == sorted(e["id"] for e in big["entities"] if e["id"] != "foliage"),
          "spatial order: every child of an oversized root must appear exactly once")
    for c in chunks_big_s:
        ids = {e["id"] for e in c["entities"]}
        check(all(e["parent_id"] in ids for e in c["entities"] if e["parent_id"]),
              "spatial order: a child's parent must be present in the child's own chunk")
        check(len(c["entities"]) <= 4000 + 1,
              "spatial order: no chunk may exceed the ceiling (plus the duplicated root); got %d"
              % len(c["entities"]))
    first = [sorted(e["id"] for e in c["entities"]) for c in split(mixed, 4)]
    second = [sorted(e["id"] for e in c["entities"]) for c in split(mixed, 4)]
    check(first == second, "the spatial order must be deterministic across calls")
finally:
    del os.environ["UEO3DE_CHUNK_ORDER"]
# --- the ceiling holds under the spatial order, whatever the walk looks like -
# Five 60-entity families in a row at a ceiling of 100: the size order packs
# them into 3 chunks (300/100), a contiguous walk cannot put two neighbours
# together without crossing 100, so it needs 5. The fill must never exceed
# the ceiling to "make it fit", and a count it cannot honour must be refused
# with the count it needs -- silently overflowing is how NYC's first spatial
# try put 5,982 entities in one chunk.
os.environ["UEO3DE_CHUNK_CEILING"] = "100"
row = {"entities": []}
for f in range(5):
    row["entities"].append({"id": "fam%d" % f, "parent_id": None, "name": "f",
                            "transform": {"world": {"translation": [f * 50.0, 0.0, 0.0]}}})
    row["entities"].extend({"id": "fam%d_%02d" % (f, k), "parent_id": "fam%d" % f, "name": "k",
                            "transform": {"world": {"translation": [f * 50.0 + k * 0.1, 0.0, 0.0]}}}
                           for k in range(59))
check(importer.recommended_chunks(len(row["entities"]), 100) == 3,
      "control: the size order recommends 3 chunks for 300 entities at 100")
check(importer.spatial_chunks(row, 100) == 5,
      "five 60-entity neighbours need 5 contiguous runs at a ceiling of 100; got %d"
      % importer.spatial_chunks(row, 100))
try:
    importer.chunk_of(row, 0, 3, order="spatial")
    check(False, "a count the spatial walk cannot honour must raise, not overflow")
except ValueError as error:
    check("5" in str(error) and "UEO3DE_CHUNK=i/5" in str(error),
          "the refusal must name the count the spatial order needs; got %r" % (str(error),))
five = [importer.chunk_of(row, i, 5, order="spatial") for i in range(5)]
check(all(len(c["entities"]) == 60 for c in five),
      "at the count it needs, each spatial chunk is one family; got %r"
      % [len(c["entities"]) for c in five])
check(all(len({e["parent_id"] for e in c["entities"] if e["parent_id"]}) == 1 for c in five),
      "no family may be split across spatial chunks")
# the same 300 with a roomier ceiling: contiguous AND even
os.environ["UEO3DE_CHUNK_CEILING"] = "200"
three = [importer.chunk_of(row, i, 3, order="spatial") for i in range(3)]
check(sorted(len(c["entities"]) for c in three) == [60, 120, 120],
      "at a ceiling of 200 the walk cuts into 120/120/60 and never over; got %r"
      % sorted(len(c["entities"]) for c in three))
del os.environ["UEO3DE_CHUNK_CEILING"]

# --- an oversized root's pieces are compact patches too -----------------------
# NYC's InstancedFoliageActor: 13,964 flat children cut into ~4,000-entity
# pieces in EXPORT order spanned 52% of the map each, against 25% for the
# ordinary chunks. Under the spatial order the children are walked along the
# curve before they are cut, so a piece is a patch of foliage, not a sample
# of all of it.
os.environ["UEO3DE_CHUNK_CEILING"] = "100"
meadow = {"entities": [{"id": "foliage", "parent_id": None, "name": "InstancedFoliageActor",
                        "transform": {"world": {"translation": [0.0, 0.0, 0.0]}}}]}
# 400 instances on a 20 x 20 grid, listed in a shuffled-but-deterministic order
cells = [(gx, gy) for gy in range(20) for gx in range(20)]
cells.sort(key=lambda c: (c[0] * 7 + c[1] * 13) % 400)
for n, (gx, gy) in enumerate(cells):
    meadow["entities"].append({"id": "inst%03d" % n, "parent_id": "foliage", "name": "i",
                               "transform": {"world": {"translation": [gx * 10.0, gy * 10.0, 0.0]}}})
needed = importer.spatial_chunks(meadow, 100)
pieces = [importer.chunk_of(meadow, i, needed, order="spatial") for i in range(needed)]
child_ids = [e["id"] for c in pieces for e in c["entities"] if e["id"] != "foliage"]
check(sorted(child_ids) == sorted(e["id"] for e in meadow["entities"] if e["id"] != "foliage"),
      "spatial pieces: every instance must appear exactly once")
for c in pieces:
    inst = [e for e in c["entities"] if e["id"] != "foliage"]
    xs = [e["transform"]["world"]["translation"][0] for e in inst]
    ys = [e["transform"]["world"]["translation"][1] for e in inst]
    check(len(c["entities"]) <= 101, "a piece may not exceed the ceiling plus its root")
    check(max(xs) - min(xs) <= 120.0 and max(ys) - min(ys) <= 120.0,
          "a spatial piece of an oversized root must be a compact patch (a Hilbert run is a blob, "
          "not a square: under two thirds of the 190 m meadow); got %.0f x %.0f m" % (max(xs) - min(xs), max(ys) - min(ys)))
# CONTROL: the same meadow cut in export order spans the whole meadow
loose = [importer.chunk_of(meadow, i, needed, order="size") for i in range(needed)]
check(any(max(e["transform"]["world"]["translation"][0] for e in c["entities"])
          - min(e["transform"]["world"]["translation"][0] for e in c["entities"]) >= 180.0
          for c in loose if len(c["entities"]) > 1),
      "control: export-order pieces should span the whole meadow")
del os.environ["UEO3DE_CHUNK_CEILING"]

check(importer.chunk_order({}) == "size", "the default chunk order must stay `size`")
check(importer.chunk_order({"UEO3DE_CHUNK_ORDER": " Spatial "}) == "spatial",
      "the knob must accept case and whitespace")
try:
    importer.chunk_order({"UEO3DE_CHUNK_ORDER": "nearest"})
    check(False, "a garbage UEO3DE_CHUNK_ORDER must raise, not fall back")
except ValueError:
    pass
# a transform-less manifest (placeholders, old exports) still partitions
bare = [importer.chunk_of(mixed, i, 4, order="spatial") for i in range(4)]
check(sorted(e["id"] for c in bare for e in c["entities"])
      == sorted(e["id"] for e in mixed["entities"]),
      "a manifest without transforms must still partition under the spatial order")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
