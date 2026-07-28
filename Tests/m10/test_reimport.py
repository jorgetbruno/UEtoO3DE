"""
test_reimport.py — the incremental re-import diff, offline (plan M10).

Pure: no editor, no O3DE, no manifest export. It runs against real saved
prefabs from the test project when they are there, and against hand-built
fixtures otherwise, so it is fast enough to run on every edit.

The property that matters is narrow and easy to get wrong in a way no green
suite would notice: **an entity nobody touched must not be reported as a
conflict, and an entity someone moved must be**. A conflict detector that
fires on everything and one that fires on nothing both produce a passing
import; only the tests below tell them apart.

Run: python Tests/m10/test_reimport.py       (exit code is the verdict)
"""

import copy
import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import reimport  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def make_prefab(entities):
    """A minimal saved-prefab document. `entities` is {name: transform-or-None}."""
    document = {"ContainerEntity": {"Id": "ContainerEntity", "Name": "Root"},
                "Entities": {}}
    for index, (name, transform) in enumerate(entities.items()):
        component = {"$type": "{27F1E1A1-8D9D-4C3B-BD3A-AFB9762449C0} TransformComponent",
                     "Id": 1000 + index}
        if transform is not None:
            data = {}
            if transform.get("translate"):
                data["Translate"] = list(transform["translate"])
            if transform.get("rotate"):
                data["Rotate"] = list(transform["rotate"])
            if transform.get("uniform_scale", 1.0) != 1.0:
                data["UniformScale"] = transform["uniform_scale"]
            if data:
                component["Transform Data"] = data
        document["Entities"]["Entity_[%d]" % index] = {
            "Id": "Entity_[%d]" % index,
            "Name": name,
            "Components": {"TransformComponent": component},
        }
    return document


def make_manifest(entities):
    """`entities` is [(id, name, ...)] -> a manifest-shaped document."""
    return {
        "schema_version": 7,
        "level": {"name": "TestLevel"},
        "entities": [{"id": entity_id, "name": name, "parent_id": None}
                     for entity_id, name in entities],
        "assets": [],
    }


def write_temp(document, suffix=".prefab"):
    handle = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
    json.dump(document, handle, indent=4)
    handle.close()
    return handle.name


# ---------------------------------------------------------------------------

def test_absent_transform_keys_are_identity_not_missing():
    """O3DE omits defaults on save. An entity at the origin has no Translate
    key at all -- read as 'unknown', every unmoved entity becomes a conflict."""
    document = make_prefab({"AtOrigin": None,
                            "Moved": {"translate": [1.0, 2.0, 3.0]}})
    path = write_temp(document)
    try:
        transforms = reimport.read_prefab(path)
    finally:
        os.unlink(path)
    check(transforms["AtOrigin"] == reimport.IDENTITY,
          "an entity with no Transform Data must read as identity, got %r"
          % (transforms["AtOrigin"],))
    check(transforms["Moved"]["translate"] == [1.0, 2.0, 3.0],
          "translate not read: %r" % (transforms["Moved"],))
    check(transforms["Moved"]["uniform_scale"] == 1.0,
          "absent UniformScale must default to 1.0")


def test_first_import_has_no_ledger_and_no_conflicts():
    manifest = make_manifest([("id-a", "A"), ("id-b", "B")])
    result = reimport.plan(None, manifest, {})
    check(result["first_import"], "ledger None must mean first import")
    check(sorted(result["added"]) == ["id-a", "id-b"],
          "every entity is new on a first import, got %r" % (result["added"],))
    check(result["conflicts"] == [],
          "a first import cannot have hand edits to detect")


def test_untouched_entities_are_not_conflicts():
    """The canary. If this fails, everything is a conflict and the feature is
    noise the user will learn to ignore."""
    manifest = make_manifest([("id-a", "A"), ("id-b", "B")])
    prefab = make_prefab({"A": {"translate": [1.0, 0.0, 0.0]},
                          "B": None})
    path = write_temp(prefab)
    try:
        ledger = reimport.build_ledger(manifest, path)
        current = reimport.read_prefab(path)
    finally:
        os.unlink(path)
    result = reimport.plan(ledger, manifest, current)
    check(result["conflicts"] == [],
          "nothing was edited, but %d conflicts were reported: %r"
          % (len(result["conflicts"]), result["conflicts"]))
    check(len(result["unchanged"]) == 2,
          "both entities should be unchanged, got %r" % (result["unchanged"],))


def test_a_moved_entity_is_a_conflict():
    """The other half of the canary: detection that never fires is worthless."""
    manifest = make_manifest([("id-a", "A"), ("id-b", "B")])
    prefab = make_prefab({"A": {"translate": [1.0, 0.0, 0.0]}, "B": None})
    path = write_temp(prefab)
    try:
        ledger = reimport.build_ledger(manifest, path)
    finally:
        os.unlink(path)
    edited = make_prefab({"A": {"translate": [1.0, 0.0, 5.0]}, "B": None})
    edited_path = write_temp(edited)
    try:
        current = reimport.read_prefab(edited_path)
    finally:
        os.unlink(edited_path)
    result = reimport.plan(ledger, manifest, current)
    check(len(result["conflicts"]) == 1,
          "exactly one entity moved; got %d conflicts" % len(result["conflicts"]))
    if result["conflicts"]:
        conflict = result["conflicts"][0]
        check(conflict["name"] == "A", "wrong entity flagged: %r" % conflict["name"])
        check(conflict["current"]["translate"] == [1.0, 0.0, 5.0],
              "the conflict must carry the USER's value to put back, got %r"
              % (conflict["current"],))
        check(conflict["authored"]["translate"] == [1.0, 0.0, 0.0],
              "the conflict must carry what the import authored, got %r"
              % (conflict["authored"],))


def test_rotation_and_scale_edits_count_too():
    manifest = make_manifest([("id-a", "A")])
    prefab = make_prefab({"A": {"rotate": [0.0, 0.0, 90.0]}})
    path = write_temp(prefab)
    try:
        ledger = reimport.build_ledger(manifest, path)
    finally:
        os.unlink(path)
    for edit, label in (({"rotate": [0.0, 0.0, 91.0]}, "rotation"),
                        ({"rotate": [0.0, 0.0, 90.0], "uniform_scale": 2.0},
                         "scale")):
        edited_path = write_temp(make_prefab({"A": edit}))
        try:
            current = reimport.read_prefab(edited_path)
        finally:
            os.unlink(edited_path)
        result = reimport.plan(ledger, manifest, current)
        check(len(result["conflicts"]) == 1,
              "a %s edit must be a conflict, got %r" % (label, result["conflicts"]))


def test_float_noise_below_epsilon_is_not_an_edit():
    manifest = make_manifest([("id-a", "A")])
    prefab = make_prefab({"A": {"translate": [1.0, 2.0, 3.0]}})
    path = write_temp(prefab)
    try:
        ledger = reimport.build_ledger(manifest, path)
    finally:
        os.unlink(path)
    noisy = write_temp(make_prefab({"A": {"translate": [1.0 + 1e-7, 2.0, 3.0]}}))
    try:
        current = reimport.read_prefab(noisy)
    finally:
        os.unlink(noisy)
    result = reimport.plan(ledger, manifest, current)
    check(result["conflicts"] == [],
          "1e-7 m of serialization noise must not read as a hand edit")


def test_added_and_removed_are_matched_by_id_not_by_name():
    """Renaming an actor in UE changes its id (the id is a uuid5 of the actor
    path), so a rename legitimately reads as remove+add. What must NOT happen
    is a MOVED actor reading as remove+add -- that would duplicate it."""
    before = make_manifest([("id-a", "A"), ("id-b", "B")])
    prefab = make_prefab({"A": None, "B": None})
    path = write_temp(prefab)
    try:
        ledger = reimport.build_ledger(before, path)
    finally:
        os.unlink(path)

    moved = copy.deepcopy(before)
    moved["entities"][0]["name"] = "A"           # same id, same name, moved in UE
    result = reimport.plan(ledger, moved, {})
    check(result["added"] == [] and result["removed"] == [],
          "a moved actor must be matched, not re-added: added=%r removed=%r"
          % (result["added"], result["removed"]))

    after = make_manifest([("id-a", "A"), ("id-c", "C")])
    result = reimport.plan(ledger, after, {})
    check(result["added"] == ["id-c"], "new actor not reported: %r" % (result["added"],))
    check([r["id"] for r in result["removed"]] == ["id-b"],
          "deleted actor not reported: %r" % (result["removed"],))


def test_preserve_conflicts_puts_the_users_value_back():
    rebuilt = make_prefab({"A": {"translate": [9.0, 9.0, 9.0]}, "B": None})
    path = write_temp(rebuilt)
    try:
        patched = reimport.preserve_conflicts(path, [{
            "id": "id-a", "name": "A",
            "authored": {"translate": [9.0, 9.0, 9.0], "rotate": [0.0, 0.0, 0.0],
                         "uniform_scale": 1.0},
            "current": {"translate": [1.0, 2.0, 3.0], "rotate": [0.0, 0.0, 45.0],
                        "uniform_scale": 2.0},
        }])
        after = reimport.read_prefab(path)
    finally:
        os.unlink(path)
    check(patched == ["A"], "expected to patch A, patched %r" % (patched,))
    check(after["A"]["translate"] == [1.0, 2.0, 3.0],
          "translate not restored: %r" % (after["A"],))
    check(after["A"]["rotate"] == [0.0, 0.0, 45.0],
          "rotate not restored: %r" % (after["A"],))
    check(after["A"]["uniform_scale"] == 2.0,
          "scale not restored: %r" % (after["A"],))
    check(after["B"] == reimport.IDENTITY, "an untouched entity was modified")


def test_preserving_an_identity_transform_clears_the_keys():
    """If the user moved an entity back to the origin, the patched prefab must
    not keep an explicit `Translate: [0,0,0]`. O3DE omits defaults, so leaving
    one behind makes the NEXT diff see a change that did not happen."""
    rebuilt = make_prefab({"A": {"translate": [9.0, 0.0, 0.0]}})
    path = write_temp(rebuilt)
    try:
        reimport.preserve_conflicts(path, [{
            "id": "id-a", "name": "A",
            "authored": {"translate": [9.0, 0.0, 0.0], "rotate": [0.0, 0.0, 0.0],
                         "uniform_scale": 1.0},
            "current": dict(reimport.IDENTITY),
        }])
        with open(path, "r") as handle:
            document = json.load(handle)
        after = reimport.read_prefab(path)
    finally:
        os.unlink(path)
    component = list(document["Entities"].values())[0]["Components"]["TransformComponent"]
    check("Transform Data" not in component or not component["Transform Data"],
          "an identity transform must serialize as absent, got %r"
          % (component.get("Transform Data"),))
    check(after["A"] == reimport.IDENTITY, "round-trip broken: %r" % (after["A"],))


def test_ledger_round_trip_and_version_gate():
    manifest = make_manifest([("id-a", "A")])
    prefab = make_prefab({"A": {"translate": [1.0, 0.0, 0.0]}})
    path = write_temp(prefab)
    try:
        ledger = reimport.build_ledger(manifest, path)
        reimport.write_ledger(path, ledger)
        loaded = reimport.load_ledger(path)
        check(loaded is not None, "ledger did not round-trip")
        check(loaded["entities"]["id-a"]["transform"]["translate"] == [1.0, 0.0, 0.0],
              "ledger lost the transform: %r" % (loaded["entities"],))

        # A ledger from a future/older format must be ignored rather than
        # misread -- an incremental import driven by a ledger it does not
        # understand is worse than a clean one.
        stale = dict(ledger)
        stale["ledger_version"] = reimport.LEDGER_VERSION + 1
        reimport.write_ledger(path, stale)
        check(reimport.load_ledger(path) is None,
              "a ledger with an unknown version must be refused")
    finally:
        for candidate in (path, reimport.ledger_path_for(path)):
            if os.path.exists(candidate):
                os.unlink(candidate)


def test_ledger_path_is_beside_the_prefab():
    check(reimport.ledger_path_for("/a/b/Fixture_01.prefab")
          == "/a/b/Fixture_01" + reimport.LEDGER_SUFFIX,
          "ledger path wrong: %r" % reimport.ledger_path_for("/a/b/Fixture_01.prefab"))


def test_against_a_real_saved_prefab():
    """The fixtures above are my own idea of the format. This one is O3DE's."""
    candidates = [
        r"C:/Users/jorge/O3DE/Projects/UEtoO3DETest-Jolt/Prefabs/Fixture_01.prefab",
    ]
    path = next((c for c in candidates if os.path.isfile(c)), None)
    if path is None:
        print("  (skipped: no real prefab available)")
        return
    transforms = reimport.read_prefab(path)
    check(len(transforms) > 20,
          "expected a populated prefab, read %d entities" % len(transforms))
    identity_count = sum(1 for t in transforms.values() if t == reimport.IDENTITY)
    check(identity_count > 0,
          "a real prefab should contain entities whose transform is omitted "
          "entirely; none were read as identity, so the omission handling is "
          "not being exercised")
    manifest_path = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r") as handle:
            document = json.load(handle)
        ledger = reimport.build_ledger(document, path)
        missing = [record["name"] for record in ledger["entities"].values()
                   if not record["present"]]
        check(not missing,
              "%d manifest entities have no entity of that name in the saved "
              "prefab, so the name-based match is not sound here: %r"
              % (len(missing), missing[:5]))
        result = reimport.plan(ledger, document, transforms)
        check(result["conflicts"] == [],
              "a ledger built from a prefab must report no conflicts against "
              "that same prefab; got %r" % (result["conflicts"][:3],))


def main():
    for test in (test_absent_transform_keys_are_identity_not_missing,
                 test_first_import_has_no_ledger_and_no_conflicts,
                 test_untouched_entities_are_not_conflicts,
                 test_a_moved_entity_is_a_conflict,
                 test_rotation_and_scale_edits_count_too,
                 test_float_noise_below_epsilon_is_not_an_edit,
                 test_added_and_removed_are_matched_by_id_not_by_name,
                 test_preserve_conflicts_puts_the_users_value_back,
                 test_preserving_an_identity_transform_clears_the_keys,
                 test_ledger_round_trip_and_version_gate,
                 test_ledger_path_is_beside_the_prefab,
                 test_against_a_real_saved_prefab):
        print("- " + test.__name__)
        test()
    if failures:
        print("RESULT: FAIL (%d)" % len(failures))
        return 1
    print("RESULT: PASS (M10 re-import diff)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
