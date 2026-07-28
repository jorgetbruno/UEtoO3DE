"""
prefab_diff.py -- compare two saved prefabs by CONTENT, not by bytes.

Why this exists
---------------
The settle before serialization is the largest single cost of a real import,
and the tempting way to test a shorter one is "did the prefab still save?".
That test is worthless here, because the thing the settle guards against does
not throw. A mesh collider's bake result is serialized INTO the prefab:

    EditorJoltMeshColliderComponent
      ShapeConfiguration
        CookedData: "SFZDSgEAAABdAAAA..."   <- base64 of the baked mesh

Serialize before that bake finishes and you get an entity with an empty or
missing CookedData -- a collider that silently does nothing at runtime, in a
file that saved perfectly and reported no error. Material assets are the same
shape of risk one level down. So a shorter settle has to be judged on whether
the CONTENT is identical, and that is what this compares.

Entity ids are freshly minted per import, so nothing can be compared by id.
Entities are keyed by NAME, and duplicated names are compared as a multiset
rather than being silently collapsed -- a saved prefab really does contain a
level-root entity named after the level alongside the actor entities, so name
collisions are a normal occurrence, not a corner case (reimport.read_prefab
carries the same warning for the same reason).

Usage:
    python Tests/perf/prefab_diff.py A.prefab B.prefab [--verbose]

Exit code 0 iff the two are equivalent. Prints a per-category breakdown of
what differs, because "they differ" is not an answer -- 12 entities missing
CookedData and 12 entities with a different transform are different findings.
"""

import json
import os
import sys

# Every collider component whose baked data lands in the prefab. Both backends,
# because M3b's whole point is that the two are compared on the same content.
COOKED_PATH = ("ShapeConfiguration", "CookedData")


def _cooked_len(component):
    node = component
    for key in COOKED_PATH:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return len(node) if isinstance(node, str) else None


def _asset_ids(node, out):
    """Collect every assetId guid anywhere under `node`, in document order."""
    if isinstance(node, dict):
        asset_id = node.get("assetId")
        if isinstance(asset_id, dict) and "guid" in asset_id:
            out.append(str(asset_id["guid"]) + ":" + str(asset_id.get("subId", 0)))
        for value in node.values():
            _asset_ids(value, out)
    elif isinstance(node, list):
        for value in node:
            _asset_ids(value, out)
    return out


def fingerprint(entity):
    """What this entity IS, independent of the ids minted for this run."""
    components = entity.get("Components") or {}
    types = []
    cooked = {}
    assets = {}
    for component in components.values():
        if not isinstance(component, dict):
            continue
        kind = str(component.get("$type", "?"))
        types.append(kind)
        size = _cooked_len(component)
        if size is not None:
            # Same component type can appear more than once on an entity.
            cooked.setdefault(kind, []).append(size)
        ids = _asset_ids(component, [])
        if ids:
            assets.setdefault(kind, []).extend(sorted(ids))
    transform = None
    for component in components.values():
        if isinstance(component, dict) and "Transform Data" in component:
            transform = component["Transform Data"]
    return {
        "types": sorted(types),
        "cooked": {k: sorted(v) for k, v in cooked.items()},
        "assets": {k: sorted(v) for k, v in assets.items()},
        "transform": transform,
    }


def summarize(path):
    """`{name: [fingerprint, ...]}` -- a LIST per name, so duplicates survive."""
    with open(path, "r") as handle:
        document = json.load(handle)
    out = {}
    for entity in (document.get("Entities") or {}).values():
        name = entity.get("Name")
        if name is None:
            continue
        out.setdefault(name, []).append(fingerprint(entity))
    return out


def _key(fp):
    return json.dumps(fp, sort_keys=True)


def compare(left, right):
    """Categorized differences between two `summarize` results."""
    result = {
        "only_in_left": sorted(set(left) - set(right)),
        "only_in_right": sorted(set(right) - set(left)),
        "count_differs": [],      # name present in both, different multiplicity
        "cooked_differs": [],     # (name, left sizes, right sizes) -- THE risk
        "assets_differ": [],
        "types_differ": [],
        "transform_differs": [],
    }
    for name in sorted(set(left) & set(right)):
        a, b = left[name], right[name]
        if len(a) != len(b):
            result["count_differs"].append((name, len(a), len(b)))
            continue
        # Multiset match: pair identical fingerprints off first, then report
        # only what is genuinely unpaired.
        remaining = list(b)
        for fp in a:
            match = next((i for i, other in enumerate(remaining)
                          if _key(other) == _key(fp)), None)
            if match is not None:
                remaining.pop(match)
                continue
            # Unpaired: attribute the difference to a category. Compare against
            # the first leftover, which is the only honest guess available.
            other = remaining.pop(0) if remaining else {}
            if fp.get("cooked") != other.get("cooked"):
                result["cooked_differs"].append(
                    (name, fp.get("cooked"), other.get("cooked")))
            if fp.get("types") != other.get("types"):
                result["types_differ"].append(name)
            if fp.get("assets") != other.get("assets"):
                result["assets_differ"].append(name)
            if fp.get("transform") != other.get("transform"):
                result["transform_differs"].append(name)
    return result


def total_differences(result):
    return sum(len(v) for v in result.values())


def cooked_summary(summary):
    """(entities with cooked data, total colliders, colliders with 0 bytes)."""
    entities = colliders = empty = 0
    for fingerprints in summary.values():
        for fp in fingerprints:
            sizes = [s for v in fp["cooked"].values() for s in v]
            if not sizes:
                continue
            entities += 1
            colliders += len(sizes)
            empty += sum(1 for s in sizes if s == 0)
    return entities, colliders, empty


def main(argv):
    verbose = "--verbose" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if len(paths) != 2:
        print(__doc__)
        return 2
    for path in paths:
        if not os.path.isfile(path):
            print("missing: " + path)
            return 2

    left, right = summarize(paths[0]), summarize(paths[1])
    for path, summary in zip(paths, (left, right)):
        entities, colliders, empty = cooked_summary(summary)
        print("%-40s %5d entities, %5d with cooked colliders (%d colliders, "
              "%d EMPTY)" % (os.path.basename(path), sum(len(v) for v in summary.values()),
                             entities, colliders, empty))

    result = compare(left, right)
    print("")
    for key in sorted(result):
        values = result[key]
        if not values:
            continue
        print("%-20s %d" % (key, len(values)))
        if verbose:
            for value in values[:20]:
                print("    " + str(value)[:200])
    total = total_differences(result)
    print("")
    print("VERDICT: " + ("EQUIVALENT" if total == 0
                         else "%d DIFFERENCES" % total))
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
