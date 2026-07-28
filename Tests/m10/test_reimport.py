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


def test_a_renamed_and_hand_edited_entity_is_still_preserved():
    """The narrow case that made preservation a silent no-op.

    An entity's manifest `name` is label-derived while its `id` is a uuid5 of
    the actor PATH, so relabelling an actor in UE keeps the id and changes the
    name. The conflict is then found in the current prefab under the OLD name
    but must be written back into the rebuilt prefab under the NEW one.
    Patching by the old name found nothing, preserved nothing, and still
    reported the conflict -- telling the user their edit was kept while it was
    being discarded.
    """
    before = make_manifest([("id-a", "OldName")])
    prefab = make_prefab({"OldName": {"translate": [1.0, 0.0, 0.0]}})
    path = write_temp(prefab)
    try:
        ledger = reimport.build_ledger(before, path)
    finally:
        os.unlink(path)

    # The user then moves it in O3DE...
    edited = write_temp(make_prefab({"OldName": {"translate": [1.0, 0.0, 7.0]}}))
    try:
        current = reimport.read_prefab(edited)
    finally:
        os.unlink(edited)

    # ...and the actor is relabelled in UE, keeping its id.
    after = make_manifest([("id-a", "NewName")])
    result = reimport.plan(ledger, after, current)
    check(len(result["conflicts"]) == 1,
          "the hand edit must still be detected across a rename: %r"
          % (result["conflicts"],))
    if not result["conflicts"]:
        return
    conflict = result["conflicts"][0]
    check(conflict.get("new_name") == "NewName",
          "the conflict must carry the name the rebuild will use, got %r"
          % (conflict.get("new_name"),))

    # The rebuilt prefab uses the NEW name.
    rebuilt = write_temp(make_prefab({"NewName": {"translate": [1.0, 0.0, 0.0]}}))
    try:
        patched = reimport.preserve_conflicts(rebuilt, result["conflicts"])
        restored = reimport.read_prefab(rebuilt)
    finally:
        os.unlink(rebuilt)
    check(patched == ["NewName"],
          "expected to patch the renamed entity, patched %r" % (patched,))
    check(restored.get("NewName", {}).get("translate") == [1.0, 0.0, 7.0],
          "the edit was reported as kept but not restored: %r"
          % (restored.get("NewName"),))


def test_preserve_reports_what_it_could_not_patch():
    """A conflict naming an entity that is not in the rebuilt prefab must come
    back as un-patched, so the caller can say so instead of counting it as
    preserved."""
    rebuilt = write_temp(make_prefab({"StillHere": None}))
    try:
        patched = reimport.preserve_conflicts(rebuilt, [{
            "id": "id-x", "name": "Vanished", "new_name": "Vanished",
            "authored": dict(reimport.IDENTITY),
            "current": {"translate": [5.0, 0.0, 0.0], "rotate": [0.0, 0.0, 0.0],
                        "uniform_scale": 1.0},
        }])
    finally:
        os.unlink(rebuilt)
    check(patched == [],
          "patching an entity that is not in the prefab must report nothing "
          "patched, got %r" % (patched,))


def test_a_hand_edit_survives_MORE_THAN_ONE_reimport():
    """The bug this exists for: preservation that works once and then loses the
    edit in silence.

    The ledger records what the import AUTHORED, not what the file ends up as.
    Written the other way round -- from the file after the edit was patched
    back -- the second re-import sees file == ledger, reports no conflict, and
    the rebuild quietly replaces the user's edit with UE's value. One
    re-import looks perfect; two lose the data. This walks three runs.
    """
    manifest = make_manifest([("id-a", "A")])
    authored = {"A": {"translate": [1.0, 0.0, 0.0]}}
    edit = [1.0, 0.0, 9.0]

    def run(current_transforms, authored_translate):
        """One import: rebuild to `authored_translate`, ledger from the REBUILD,
        then patch conflicts back. Mirrors importer.import_level's order."""
        rebuilt_path = write_temp(make_prefab({"A": {"translate": authored_translate}}))
        try:
            plan = reimport.plan(run.ledger, manifest, current_transforms)
            run.ledger = reimport.build_ledger(manifest, rebuilt_path)
            reimport.preserve_conflicts(rebuilt_path, plan["conflicts"])
            return plan, reimport.read_prefab(rebuilt_path)
        finally:
            os.unlink(rebuilt_path)

    run.ledger = None

    # Run 1: first import. Nothing to compare against.
    _plan, state = run({}, [1.0, 0.0, 0.0])
    check(state["A"]["translate"] == [1.0, 0.0, 0.0], "run 1 authored wrongly")

    # The user moves it in O3DE.
    state["A"]["translate"] = list(edit)

    # Run 2: UE has not changed. The edit must be detected and kept.
    plan2, state = run(state, [1.0, 0.0, 0.0])
    check(len(plan2["conflicts"]) == 1, "run 2 did not detect the hand edit")
    check(state["A"]["translate"] == edit,
          "run 2 lost the hand edit: %r" % (state["A"]["translate"],))

    # Run 3: still unchanged in UE. THIS is where writing the ledger from the
    # patched file used to lose the edit without a word.
    plan3, state = run(state, [1.0, 0.0, 0.0])
    check(len(plan3["conflicts"]) == 1,
          "run 3 reported no conflict, so the edit is about to be silently "
          "overwritten -- the ledger is recording the patched file instead of "
          "what the import authored")
    check(state["A"]["translate"] == edit,
          "run 3 lost the hand edit: %r" % (state["A"]["translate"],))


def test_an_actor_named_after_the_level_never_moves_the_level_root():
    """Found by adversarial review, and reproduced: the saved prefab contains a
    level-root entity named after the LEVEL, sitting in `Entities` alongside
    the actors. `read_prefab` keys by name, so an actor whose UE label equals
    the level name collapses onto the root — and `preserve_conflicts` then
    wrote that actor's edited transform into EVERY entity carrying the name,
    including the root, which is the parent of every manifest root. The whole
    prefab was offset on instantiation, reported only as a WARN whose text
    claimed the conflict had not been reported at all.
    """
    manifest = make_manifest([("id-a", "Fixture_01")])
    manifest["level"]["name"] = "Fixture_01"

    # A prefab holding BOTH the level root and an actor of the same name.
    prefab = {"ContainerEntity": {"Id": "ContainerEntity", "Name": "Root"},
              "Entities": {}}
    for index, (entity_id, translate) in enumerate(
            (("Entity_[7777]", None), ("Entity_[8888]", [5.0, 0.0, 0.0]))):
        component = {"$type": "{27F1E1A1-8D9D-4C3B-BD3A-AFB9762449C0} TransformComponent",
                     "Id": 900 + index}
        if translate:
            component["Transform Data"] = {"Translate": list(translate)}
        prefab["Entities"][entity_id] = {
            "Id": entity_id, "Name": "Fixture_01",
            "Components": {"TransformComponent": component}}

    path = write_temp(prefab)
    try:
        duplicates = set()
        current = reimport.read_prefab(path, duplicates=duplicates)
        check(duplicates == {"Fixture_01"},
              "read_prefab must report the duplicated name, got %r" % (duplicates,))
        ledger = reimport.build_ledger(manifest, path)
        result = reimport.plan(ledger, manifest, current,
                              prefab_duplicates=duplicates)
        check("Fixture_01" in result["name_collisions"],
              "an actor sharing the level's name must be flagged: %r"
              % (result["name_collisions"],))
        check(result["conflicts"] == [],
              "an ambiguous name must raise NO conflict — there is no way to "
              "write it back safely. Got %r" % (result["conflicts"],))

        # Second line of defence: even handed a conflict directly,
        # preserve_conflicts must refuse rather than fan the value out.
        patched = reimport.preserve_conflicts(path, [{
            "id": "id-a", "name": "Fixture_01", "new_name": "Fixture_01",
            "authored": dict(reimport.IDENTITY),
            "current": {"translate": [5.0, 0.0, 9.0], "rotate": [0.0, 0.0, 0.0],
                        "uniform_scale": 1.0}}])
        after = json.load(open(path))
        root = after["Entities"]["Entity_[7777]"]["Components"]["TransformComponent"]
    finally:
        os.unlink(path)
    check(patched == [],
          "a duplicated name must not be patched at all, patched %r" % (patched,))
    check("Transform Data" not in root,
          "THE LEVEL ROOT WAS MOVED (%r) — every entity in the prefab is now "
          "offset" % (root.get("Transform Data"),))


def test_two_entities_sharing_a_name_are_never_cross_patched():
    """The other half of the same defect: one entity's edit written into a
    second entity nobody touched, while the length-based guard stayed silent
    because two names patched equalled two conflicts reported."""
    manifest = make_manifest([("id-a", "Box"), ("id-b", "Box")])
    prefab = {"ContainerEntity": {"Id": "ContainerEntity", "Name": "Root"},
              "Entities": {
                  "Entity_[1]": {"Id": "Entity_[1]", "Name": "Box", "Components": {
                      "TransformComponent": {
                          "$type": "{27F1E1A1-8D9D-4C3B-BD3A-AFB9762449C0} TransformComponent",
                          "Id": 1, "Transform Data": {"Translate": [1.0, 0.0, 0.0]}}}},
                  "Entity_[2]": {"Id": "Entity_[2]", "Name": "Box", "Components": {
                      "TransformComponent": {
                          "$type": "{27F1E1A1-8D9D-4C3B-BD3A-AFB9762449C0} TransformComponent",
                          "Id": 2, "Transform Data": {"Translate": [2.0, 0.0, 9.0]}}}}}}
    path = write_temp(prefab)
    try:
        duplicates = set()
        current = reimport.read_prefab(path, duplicates=duplicates)
        ledger = reimport.build_ledger(manifest, path)
        result = reimport.plan(ledger, manifest, current,
                              prefab_duplicates=duplicates)
        check(result["conflicts"] == [],
              "duplicated names must raise no conflicts, got %r"
              % (result["conflicts"],))
        patched = reimport.preserve_conflicts(path, [{
            "id": "id-a", "name": "Box", "new_name": "Box",
            "authored": {"translate": [1.0, 0.0, 0.0], "rotate": [0.0, 0.0, 0.0],
                         "uniform_scale": 1.0},
            "current": {"translate": [2.0, 0.0, 9.0], "rotate": [0.0, 0.0, 0.0],
                        "uniform_scale": 1.0}}])
        after = json.load(open(path))
        first = after["Entities"]["Entity_[1]"]["Components"]["TransformComponent"]
    finally:
        os.unlink(path)
    check(patched == [], "an ambiguous name must not be patched: %r" % (patched,))
    check(first["Transform Data"]["Translate"] == [1.0, 0.0, 0.0],
          "an entity nobody edited was overwritten with another's transform: %r"
          % (first["Transform Data"],))


def test_renaming_an_entity_in_O3DE_is_reported_not_silent():
    """Entities are matched back to the prefab by name, so renaming one in
    O3DE severs the link and its hand edits are replaced. That is a real
    limitation and cannot be fixed by matching alone — but it must not happen
    quietly, which is what it did until the review pointed it out."""
    manifest = make_manifest([("id-a", "Wall"), ("id-b", "Floor")])
    path = write_temp(make_prefab({"Wall": {"translate": [1.0, 0.0, 0.0]},
                                   "Floor": None}))
    try:
        ledger = reimport.build_ledger(manifest, path)
    finally:
        os.unlink(path)

    renamed = write_temp(make_prefab({"Wall_MyEdit": {"translate": [1.0, 0.0, 9.0]},
                                      "Floor": None}))
    try:
        current = reimport.read_prefab(renamed)
    finally:
        os.unlink(renamed)

    result = reimport.plan(ledger, manifest, current)
    check([u["name"] for u in result["unmatched"]] == ["Wall"],
          "an entity renamed in O3DE must be reported as unmatched, got %r"
          % (result["unmatched"],))
    check(result["conflicts"] == [],
          "a renamed entity cannot be matched, so it cannot be a conflict: %r"
          % (result["conflicts"],))
    # The untouched entity must NOT be dragged into it.
    check("Floor" not in [u["name"] for u in result["unmatched"]],
          "an entity that is still present was reported as unmatched")


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
                 test_a_renamed_and_hand_edited_entity_is_still_preserved,
                 test_preserve_reports_what_it_could_not_patch,
                 test_a_hand_edit_survives_MORE_THAN_ONE_reimport,
                 test_an_actor_named_after_the_level_never_moves_the_level_root,
                 test_two_entities_sharing_a_name_are_never_cross_patched,
                 test_renaming_an_entity_in_O3DE_is_reported_not_silent,
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
