"""
reimport.py — importing the same level twice without losing the user's work.

The difference between a demo and a tool: a level gets re-exported because one
actor moved, and the import has to know that. The manifest already carries the
identity needed -- every entity's `id` is a uuid5 of its UE actor path (M1), so
it survives re-export unchanged as long as the actor is not renamed.

Three things must hold, and they are what this module computes:

  * entities are MATCHED, not duplicated -- by manifest id;
  * actors deleted in UE disappear, and the fact is reported;
  * entities the user edited by hand in O3DE are reported as CONFLICTS and
    keep the user's values, rather than being silently reverted to what UE
    says. Silently discarding someone's manual fixes is the worst possible
    behaviour for a tool that is meant to be run repeatedly.

**Everything here works in prefab space, never in manifest space.** The saved
`.prefab` records `Transform Data` as `Translate` / `Rotate` (euler DEGREES) /
`UniformScale`, while the manifest carries quaternions -- and the two do not
correspond entity-for-entity anyway: a skeletal entity's authored rotation has
M8's Rz180 composed into it, and lights carry their own orientation fixup. So
a "did the user move this?" check written against manifest quaternions would
have to reproduce every one of those transformations to avoid crying wolf.
Comparing the prefab against a ledger of what the prefab said last time needs
no conversion at all and cannot drift out of sync with the authoring code.

The ledger lives next to the prefab as `<prefab-stem>.ueimport.json`.
"""

import json
import os

LEDGER_VERSION = 1
LEDGER_SUFFIX = ".ueimport.json"

# Float tolerance for "did this change?". The prefab writer round-trips these
# through text, and a re-import authors bit-identical values from the same
# manifest, so this only has to absorb formatting noise -- not real edits.
# 1e-4 m is a tenth of a millimetre; no one nudges an entity by that much.
EPSILON = 1e-4

IDENTITY = {"translate": [0.0, 0.0, 0.0],
            "rotate": [0.0, 0.0, 0.0],
            "uniform_scale": 1.0}


def ledger_path_for(prefab_path):
    """`.../Fixture_01.prefab` -> `.../Fixture_01.ueimport.json`."""
    root, _ext = os.path.splitext(str(prefab_path))
    return root + LEDGER_SUFFIX


# ---------------------------------------------------------------------------
# reading a saved prefab
# ---------------------------------------------------------------------------

def _transform_of(entity):
    """Pull `Transform Data` out of an entity's components.

    O3DE omits default values on save, so an entity at the origin has no
    `Translate` key at all -- and an absent key means identity, not missing
    data. Treating absence as "unknown" would report every unmoved entity as
    a conflict.
    """
    for component in (entity.get("Components") or {}).values():
        if "TransformComponent" not in str(component.get("$type", "")):
            continue
        data = component.get("Transform Data") or {}
        return {
            "translate": [float(v) for v in data.get("Translate", IDENTITY["translate"])],
            "rotate": [float(v) for v in data.get("Rotate", IDENTITY["rotate"])],
            "uniform_scale": float(data.get("UniformScale", IDENTITY["uniform_scale"])),
        }
    return dict(IDENTITY)


def read_prefab(prefab_path, duplicates=None):
    """`{entity name: transform}` for a saved prefab. `{}` if there is none.

    Pass a `set` as `duplicates` to learn which names appeared more than once.
    That matters more than it looks: a saved prefab contains a level-root
    entity named after the LEVEL alongside the actor entities, so an actor
    whose UE label equals the level name collapses onto the root here, and
    whichever one the dict keeps is decided by iteration order. Callers that
    write back through these names must refuse to touch a duplicated one.
    """
    if not os.path.isfile(prefab_path):
        return {}
    with open(prefab_path, "r") as handle:
        document = json.load(handle)
    out = {}
    for entity in (document.get("Entities") or {}).values():
        name = entity.get("Name")
        if name is None:
            continue
        if name in out and duplicates is not None:
            duplicates.add(name)
        out[name] = _transform_of(entity)
    return out


def transforms_equal(left, right, epsilon=EPSILON):
    if left is None or right is None:
        return left is right
    for key in ("translate", "rotate"):
        a, b = left.get(key) or [], right.get(key) or []
        if len(a) != len(b):
            return False
        if any(abs(float(x) - float(y)) > epsilon for x, y in zip(a, b)):
            return False
    return abs(float(left.get("uniform_scale", 1.0))
               - float(right.get("uniform_scale", 1.0))) <= epsilon


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------

def build_ledger(document, prefab_path):
    """Record what this import produced, keyed by manifest entity id.

    Read back from the SAVED prefab rather than from what we meant to author:
    the point of the ledger is to describe the file the user will edit.
    """
    by_name = read_prefab(prefab_path)
    entities = {}
    for item in document.get("entities") or []:
        name = item.get("name")
        entities[item["id"]] = {
            "name": name,
            "transform": by_name.get(name) or dict(IDENTITY),
            "present": name in by_name,
        }
    return {
        "ledger_version": LEDGER_VERSION,
        "prefab": os.path.basename(str(prefab_path)),
        "level": (document.get("level") or {}).get("name"),
        "schema_version": document.get("schema_version"),
        "entities": entities,
    }


def write_ledger(prefab_path, ledger):
    path = ledger_path_for(prefab_path)
    with open(path, "w") as handle:
        json.dump(ledger, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def load_ledger(prefab_path):
    path = ledger_path_for(prefab_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as handle:
            ledger = json.load(handle)
    except Exception:
        return None
    if ledger.get("ledger_version") != LEDGER_VERSION:
        return None
    return ledger


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------

def plan(ledger, document, current_transforms=None, prefab_duplicates=None):
    """Diff a previous import against a new manifest.

    `ledger`             from `load_ledger`, or None for a first import
    `document`           the new manifest
    `current_transforms` `read_prefab()` of the prefab as it stands NOW --
                         which is where hand edits live

    Returns lists of ids, plus `conflicts` carrying the values needed to put
    the user's edit back after the rebuild.
    """
    result = {"first_import": ledger is None, "added": [], "removed": [],
              "updated": [], "unchanged": [], "conflicts": [],
              "name_collisions": [], "unmatched": []}

    new_by_id = {}
    seen_names = {}
    # The prefab also contains a level-root entity named after the LEVEL, which
    # no manifest entity corresponds to. An actor sharing that name shadows it
    # in the by-name lookup, so the ledger would record the root's identity
    # transform for that actor and report a conflict on every import from then
    # on. Rare, but silently wrong rather than loudly wrong.
    level_name = (document.get("level") or {}).get("name")
    for item in document.get("entities") or []:
        new_by_id[item["id"]] = item
        name = item.get("name")
        if name in seen_names or (level_name and name == level_name):
            result["name_collisions"].append(name)
        seen_names[name] = item["id"]

    if ledger is None:
        result["added"] = sorted(new_by_id)
        return result

    old = ledger.get("entities") or {}
    for entity_id in sorted(new_by_id):
        if entity_id not in old:
            result["added"].append(entity_id)
    for entity_id in sorted(old):
        if entity_id not in new_by_id:
            result["removed"].append({"id": entity_id,
                                      "name": old[entity_id].get("name")})

    if current_transforms is None:
        current_transforms = {}

    # A name that is ambiguous cannot be used to write anything back, so the
    # entities carrying it are excluded from conflict detection entirely --
    # which is what REIMPORT_NAME_COLLISION has always claimed happens. Before
    # this, they went through the normal path and `preserve_conflicts` then
    # wrote one entity's edited transform into EVERY entity sharing the name.
    # With the level root among them (it is named after the level), that
    # translated the whole prefab.
    ambiguous = set(result["name_collisions"]) | set(prefab_duplicates or ())

    for entity_id in sorted(set(old) & set(new_by_id)):
        record = old[entity_id]
        name = record.get("name")
        authored = record.get("transform")
        current = None if name in ambiguous else current_transforms.get(name)
        # A name that vanished from the prefab is not an edit -- the user may
        # have deleted the entity, or the prefab may simply not have been read.
        # Either way there is nothing to preserve, so it is not a conflict.
        new_name = new_by_id[entity_id].get("name")
        if new_name in ambiguous:
            current = None  # the write-back target is ambiguous too
        elif current is None and current_transforms and name not in ambiguous:
            # The ledger knows this entity, the prefab has no entity of that
            # name, and the prefab is not empty: it was renamed or deleted in
            # O3DE. Either way its hand edits cannot be matched and are about
            # to be replaced. That used to happen without a word.
            result["unmatched"].append({"id": entity_id, "name": name})
        if current is not None and not transforms_equal(authored, current):
            result["conflicts"].append({
                "id": entity_id,
                # `name` is what the entity was called when the edit was made,
                # which is how it is found in the CURRENT prefab. `new_name` is
                # what the rebuild will call it, which is how it must be found
                # afterwards to put the edit back. They differ whenever the
                # actor was renamed in UE, and patching by the old name then
                # silently preserves nothing while still reporting a conflict.
                "name": name,
                "new_name": new_name,
                "authored": authored,
                "current": current,
            })
        if new_name != name:
            result["updated"].append(entity_id)
        else:
            result["unchanged"].append(entity_id)
    return result


# ---------------------------------------------------------------------------
# putting hand edits back
# ---------------------------------------------------------------------------

def preserve_conflicts(prefab_path, conflicts):
    """Write the user's transforms back into a freshly rebuilt prefab.

    Done as a JSON patch on the saved file rather than during authoring, for
    one reason: the prefab records euler degrees and the authoring path takes
    quaternions, so re-authoring would need a conversion whose convention is
    one more thing to get wrong. Here the value goes back exactly as it came
    out.

    Returns the names actually patched.
    """
    if not conflicts or not os.path.isfile(prefab_path):
        return []
    with open(prefab_path, "r") as handle:
        document = json.load(handle)

    # Match on the name the REBUILT prefab uses. `new_name` is absent only for
    # conflicts built by older callers, where the two are the same anyway.
    wanted = {}
    for conflict in conflicts:
        target = conflict.get("new_name") or conflict.get("name")
        if target:
            wanted[target] = conflict["current"]
    # How many entities carry each wanted name? A name on two entities cannot
    # be written back safely -- `plan` already refuses to raise conflicts for
    # ambiguous names, and this is the second line of defence, because the
    # failure it prevents is severe: the loop below has its `break` on the
    # COMPONENT loop, so without this check one entity's edited transform is
    # written into every entity sharing its name. When the level root shares
    # the name (it is named after the level), that offsets the entire prefab.
    counts = {}
    for entity in (document.get("Entities") or {}).values():
        name = entity.get("Name")
        if name in wanted:
            counts[name] = counts.get(name, 0) + 1
    for name, count in counts.items():
        if count > 1:
            del wanted[name]

    patched = []
    for entity in (document.get("Entities") or {}).values():
        name = entity.get("Name")
        if name not in wanted:
            continue
        target = wanted.pop(name)   # once per name, never fanned out
        for component in (entity.get("Components") or {}).values():
            if "TransformComponent" not in str(component.get("$type", "")):
                continue
            data = component.setdefault("Transform Data", {})
            # Write only what differs from the O3DE default, matching how the
            # prefab writer itself serializes: leaving an explicit identity
            # behind would make the next diff see a change that is not one.
            _set_or_clear(data, "Translate", target["translate"],
                          IDENTITY["translate"])
            _set_or_clear(data, "Rotate", target["rotate"], IDENTITY["rotate"])
            _set_or_clear(data, "UniformScale", target["uniform_scale"],
                          IDENTITY["uniform_scale"])
            if not data:
                component.pop("Transform Data", None)
            patched.append(name)
            break

    if patched:
        with open(prefab_path, "w") as handle:
            json.dump(document, handle, indent=4)
            handle.write("\n")
    return patched


def _set_or_clear(data, key, value, default):
    if isinstance(value, (list, tuple)):
        differs = any(abs(float(v) - float(d)) > EPSILON
                      for v, d in zip(value, default))
        value = [float(v) for v in value]
    else:
        differs = abs(float(value) - float(default)) > EPSILON
        value = float(value)
    if differs:
        data[key] = value
    else:
        data.pop(key, None)


def summarize(plan_result):
    """One line for the log and the summary dialog."""
    if plan_result.get("first_import"):
        return "first import: %d entities" % len(plan_result["added"])
    return ("re-import: %d added, %d removed, %d renamed, %d unchanged, "
            "%d hand-edited" % (len(plan_result["added"]),
                                len(plan_result["removed"]),
                                len(plan_result["updated"]),
                                len(plan_result["unchanged"]),
                                len(plan_result["conflicts"])))
