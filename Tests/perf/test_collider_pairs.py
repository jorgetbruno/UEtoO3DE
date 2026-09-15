"""
test_collider_pairs.py — every added collider is configured on ITSELF.

Pure: no editor (the EditorComponentAPIBus is faked). Run: python Tests/perf/test_collider_pairs.py

WHY THIS EXISTS. The Jolt adapter added a component and then looked it up again
with GetComponentOfType, which returns whichever component of that type the
entity holds. A UE body with three box elements became three Jolt Box Colliders
on one entity, and each new box's dimensions could land on an earlier one: some
boxes were sized twice, the rest stayed default 1 m cubes at the origin. On
NYC1950 ~120 bodies per chunk were affected, and two imports of one manifest
broke DIFFERENT boxes, because the lookup follows random component ids. It
showed up only because a parallel import's prefabs were diffed against a serial
import's. The fake below reproduces that lookup (it returns the first-added
component), so a regression to it fails here.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts"))

from ueimporter.adapters import jolt, physx  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


class Outcome:
    def __init__(self, value):
        self.value = value

    def IsSuccess(self):
        return True

    def GetValue(self):
        return self.value


class FakeEditor:
    """Components per entity and the properties set on each one."""

    def __init__(self):
        self.components = {}          # entity -> [(type_id, pair)]
        self.properties = {}          # pair -> {path: value}
        self.next_pair = 0

    def EditorComponentAPIBus(self, _broadcast, call, *args):
        if call == "AddComponentsOfType":
            entity, types = args
            added = []
            for type_id in types:
                self.next_pair += 1
                pair = "pair%d" % self.next_pair
                self.components.setdefault(entity, []).append((type_id, pair))
                self.properties[pair] = {}
                added.append(pair)
            return Outcome(added)
        if call == "GetComponentOfType":
            entity, type_id = args
            # The engine's answer is "one of them"; the first is the worst case.
            return Outcome(next(p for t, p in self.components[entity] if t == type_id))
        if call == "SetComponentProperty":
            pair, path, value = args
            self.properties[pair][path] = value
            return Outcome(None)
        raise AssertionError("unexpected bus call %r" % call)


class FakeBus:
    Broadcast = "Broadcast"


class FakeEntity:
    def __init__(self, name):
        self.name = name

    def ToString(self):
        return self.name


def adapter(cls, fake):
    instance = cls()
    instance._bus = lambda: (FakeBus, fake)
    instance._vector3 = lambda v: tuple(float(x) for x in v)
    instance._quaternion = lambda q: tuple(float(x) for x in q)
    return instance


# --- Jolt: three boxes on one body ------------------------------------------------
fake = FakeEditor()
a = adapter(jolt.JoltBackendAdapter, fake)
a._type_ids = {jolt._COLLIDER_BOX: "BoxType"}
body = FakeEntity("SM_NYCB_436")
boxes = [([9.2, 3.5, 10.8], [-9.0, -6.2, 9.8]),
         ([7.8, 3.5, 10.8], [-7.5, -3.3, 9.8]),
         ([8.1, 3.9, 5.5], [-8.0, -5.3, 23.9])]
pairs = [a.add_box_collider(body, half, local_offset=offset) for half, offset in boxes]
check(len(set(pairs)) == 3, "each Jolt box add must return its own component; got %r" % pairs)
for (half, offset), pair in zip(boxes, pairs):
    props = fake.properties[pair]
    check(props.get(jolt._P_BOX_DIMENSIONS) == tuple(2.0 * v for v in half),
          "box %s must carry its own dimensions; has %r" % (pair, props))
    check(props.get(jolt._P_OFFSET) == tuple(offset),
          "box %s must carry its own offset; has %r" % (pair, props))
check(a._colliders_by_entity["SM_NYCB_436"] == pairs,
      "make_trigger must reach all three boxes, not one of them three times")

# --- PhysX: the same contract (it was fixed there first) -----------------------------
fake = FakeEditor()
p = adapter(physx.PhysXBackendAdapter, fake)
p._type_ids = {"PhysX Primitive Collider": "PrimType"}
first = p._add_component(body, "PhysX Primitive Collider")
second = p._add_component(body, "PhysX Primitive Collider")
check(first != second, "each PhysX primitive add must return its own component")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
