"""
test_scale.py — WHO applies an entity's scale to its colliders.

Pure: no editor. Run: python Tests/perf/test_scale.py  (exit code is the verdict)

THE DEFECT THIS PINS. `physics_build` multiplied every collider dimension and
offset by the entity's world scale, on the premise that collider components sit
outside the transform's scale. Both shipped backends apply that scale
themselves — measured, `Tests/o3de/probe_scale_matrix.py` reads each collider's
world AABB in game mode and every scaled/unscaled ratio is 2.000, for
dimensions and offsets, primitives and cooked mesh assets. So the multiply
SQUARED the collision: a 2x actor collided at 4x, a 3x actor at 9x. On the
converted siege map, 1,924 of 3,290 collidable entities are scaled.

WHY NO SUITE CAUGHT IT — and it is not "the fixtures are all at scale 1",
which is the easy answer and is false. Fixture_01 has four scaled entities,
three with collision. What it does not have is any assertion that MEASURES a
scaled collider: the rest-height tests drop probes they build themselves,
unscaled, and the one scaled collider they touch is a floor at (10, 10, 1)
whose collision was ten times too wide in X and Y — invisible to every
assertion, all of which read the height a ball rests at. That is the whole
reason this file exists: it tests scale ≠ 1 as data, on the axes the error is
on. The editor-side companion is `Tests/m3b/m3b_scale_acceptance.py`, which
authors the same entity through the real adapter and measures the AABB the
physics engine actually produces.

WHAT IS DELIBERATELY TESTED IN BOTH DIRECTIONS. Every assertion here has a
mirror that must FAIL if the capability flag is read backwards: authoring with
`CAP_SCALE_ENGINE_APPLIED` must produce UNSCALED numbers, and authoring without
it must produce SCALED ones. A test that only checked one side would pass with
the condition inverted, which is exactly how `UEO3DE_PHYSX_DECOMPOSE` once
turned itself on when told "off".
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import physics_build  # noqa: E402
from ueimporter.adapters import base  # noqa: E402
from ueimporter.adapters import jolt  # noqa: E402
from ueimporter.adapters import physx  # noqa: E402
from ueimporter.report import Report  # noqa: E402

# The knob under test; scrub it so the suite tests one world.
os.environ.pop("UEO3DE_BAKE_SCALE", None)

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# --- 1. both shipped adapters advertise it ----------------------------------
# Not a formality: `physics_build` bakes the scale for any backend that does
# not advertise, so an adapter that forgets the flag silently squares every
# scaled entity's collision again.
for adapter_class in (jolt.JoltBackendAdapter, physx.PhysXBackendAdapter):
    instance = adapter_class()
    check(base.CAP_SCALE_ENGINE_APPLIED in instance.capabilities(),
          "%s does not advertise CAP_SCALE_ENGINE_APPLIED; the importer would "
          "bake the entity scale into colliders the engine also scales"
          % adapter_class.__name__)


# --- 2. the override parses in the direction it says ------------------------
for text in ("1", "on", "true", "yes", "enabled", "ON", " True "):
    check(physics_build.bake_scale_override(text) is True,
          "UEO3DE_BAKE_SCALE=%r should force baking ON; got %r"
          % (text, physics_build.bake_scale_override(text)))
for text in ("0", "off", "false", "no", "none", "disabled", "OFF"):
    check(physics_build.bake_scale_override(text) is False,
          "UEO3DE_BAKE_SCALE=%r should force baking OFF; got %r"
          % (text, physics_build.bake_scale_override(text)))
check(physics_build.bake_scale_override("") is None,
      "an unset UEO3DE_BAKE_SCALE should defer to the adapter, not decide")
for text in ("maybe", "2", "-1", "trueish"):
    try:
        physics_build.bake_scale_override(text)
        check(False, "UEO3DE_BAKE_SCALE=%r was accepted; an unrecognised value "
                     "must raise, never fall back to a direction" % text)
    except ValueError:
        pass


class FakeAdapter(object):
    """Records every authoring call WITH its geometry; capabilities per test."""

    def __init__(self, caps):
        self._caps = set(caps)
        self.calls = []

    def name(self):
        return "fake"

    def capabilities(self):
        return set(self._caps)

    def add_static_body(self, entity_id, layer=None):
        self.calls.append(("static_body",))

    def add_dynamic_body(self, entity_id, **kw):
        self.calls.append(("dynamic_body",))

    def add_box_collider(self, entity_id, half_extents, local_offset=None,
                         local_rotation=None, material=None, layer=None):
        self.calls.append(("box",
                           [round(v, 6) for v in half_extents],
                           None if local_offset is None
                           else [round(v, 6) for v in local_offset]))

    def add_sphere_collider(self, entity_id, radius, local_offset=None,
                            material=None, layer=None):
        self.calls.append(("sphere", round(radius, 6),
                           None if local_offset is None
                           else [round(v, 6) for v in local_offset]))

    def add_capsule_collider(self, entity_id, radius, height, local_offset=None,
                             local_rotation=None, material=None, layer=None):
        self.calls.append(("capsule", round(radius, 6), round(height, 6)))

    def add_mesh_collider(self, entity_id, convex, material=None, layer=None,
                          asset_id=None):
        self.calls.append(("mesh", bool(convex), asset_id))

    def make_trigger(self, entity_id):
        self.calls.append(("trigger",))


ENGINE_SCALES = {base.CAP_SHAPE_BOX, base.CAP_SHAPE_SPHERE,
                 base.CAP_SHAPE_CAPSULE, base.CAP_SCALE_ENGINE_APPLIED}
IMPORTER_SCALES = {base.CAP_SHAPE_BOX, base.CAP_SHAPE_SPHERE,
                   base.CAP_SHAPE_CAPSULE}


# --- 3. collider_scale, the one decision ------------------------------------
world = [2.0, 2.0, 3.0]
check(physics_build.collider_scale(FakeAdapter(ENGINE_SCALES), world) == [1.0, 1.0, 1.0],
      "a backend that scales its own colliders must be handed identity; "
      "anything else squares the collision")
check(physics_build.collider_scale(FakeAdapter(IMPORTER_SCALES), world) == world,
      "a backend that does NOT scale its colliders must still get the entity "
      "scale baked in, or scaled entities collide at their unscaled size")
check(physics_build.collider_scale(FakeAdapter(ENGINE_SCALES), world,
                                   override=True) == world,
      "UEO3DE_BAKE_SCALE=1 must force baking back on for a gem build that "
      "predates engine-applied scale")
check(physics_build.collider_scale(FakeAdapter(IMPORTER_SCALES), world,
                                   override=False) == [1.0, 1.0, 1.0],
      "UEO3DE_BAKE_SCALE=0 must forbid baking even when the adapter asks for it")


# --- 4. the same entity, authored both ways ---------------------------------
def physics_block(**overrides):
    block = {"has_collision": True, "is_trigger": False,
             "simulates_physics": False, "kinematic": False,
             "collision_profile": "", "ccd": False,
             "enable_gravity": True, "linear_damping": 0.0,
             "angular_damping": 0.0, "mass_override": False, "mass_kg": None,
             "shapes": [], "shapes_from_asset": None}
    block.update(overrides)
    return block


def entity(shapes, scale):
    return {"name": "Scaled", "physics": physics_block(shapes=shapes),
            "transform": {"world": {"scale": list(scale)}}}


def author(adapter, item):
    report = Report()
    physics_build.author_entity_physics(adapter, "eid", item, {}, report, {})
    return adapter.calls, [record["code"] for record in report.records()]


BOX = {"type": "box", "half_extents": [1.0, 0.5, 0.25],
       "offset": [0.0, 0.0, 2.0]}
SPHERE = {"type": "sphere", "radius": 0.5, "offset": [1.0, 0.0, 0.0]}

# 4a. engine-applied: the authored numbers reach the adapter UNTOUCHED.
calls, codes = author(FakeAdapter(ENGINE_SCALES), entity([BOX], [2.0, 2.0, 2.0]))
box_calls = [call for call in calls if call[0] == "box"]
check(box_calls == [("box", [1.0, 0.5, 0.25], [0.0, 0.0, 2.0])],
      "a 2x entity on a scale-applying backend must be authored at its "
      "MANIFEST size and offset (the engine does the doubling); got %r"
      % (box_calls,))

# 4b. importer-applied: the same entity, doubled — the control that proves 4a
#     is measuring the capability and not just "the code stopped scaling".
calls, codes = author(FakeAdapter(IMPORTER_SCALES), entity([BOX], [2.0, 2.0, 2.0]))
box_calls = [call for call in calls if call[0] == "box"]
check(box_calls == [("box", [2.0, 1.0, 0.5], [0.0, 0.0, 4.0])],
      "a 2x entity on a backend that ignores scale must be authored DOUBLED, "
      "dimensions and offset alike; got %r" % (box_calls,))

# 4c. non-uniform scale on a sphere: an approximation the importer has to make
#     and report — but ONLY when it is the one applying the scale. Left to the
#     engine there is nothing to approximate and nothing to warn about.
calls, codes = author(FakeAdapter(IMPORTER_SCALES),
                      entity([SPHERE], [1.0, 1.0, 3.0]))
sphere_calls = [call for call in calls if call[0] == "sphere"]
check(sphere_calls == [("sphere", 1.5, [1.0, 0.0, 0.0])],
      "a sphere under non-uniform scale, baked by the importer, takes the "
      "largest axis; got %r" % (sphere_calls,))
check("PHYS_SHAPE_APPROXIMATED" in codes,
      "baking a non-uniform scale into a sphere is an approximation and must "
      "be reported; codes were %r" % (codes,))

calls, codes = author(FakeAdapter(ENGINE_SCALES),
                      entity([SPHERE], [1.0, 1.0, 3.0]))
sphere_calls = [call for call in calls if call[0] == "sphere"]
check(sphere_calls == [("sphere", 0.5, [1.0, 0.0, 0.0])],
      "a sphere on a scale-applying backend is authored at its manifest "
      "radius, non-uniform scale or not; got %r" % (sphere_calls,))
check("PHYS_SHAPE_APPROXIMATED" not in codes,
      "nothing was approximated when the engine applies the scale itself, so "
      "PHYS_SHAPE_APPROXIMATED is a false alarm; codes were %r" % (codes,))

# 4d. scale 1 — where the two worlds are indistinguishable, and where every
#     unscaled fixture entity lives. Pinned so it stays that way.
for caps in (ENGINE_SCALES, IMPORTER_SCALES):
    calls, _codes = author(FakeAdapter(caps), entity([BOX], [1.0, 1.0, 1.0]))
    box_calls = [call for call in calls if call[0] == "box"]
    check(box_calls == [("box", [1.0, 0.5, 0.25], [0.0, 0.0, 2.0])],
          "at scale 1 both worlds must author identical geometry (which is why "
          "an unscaled fixture cannot catch the defect); got %r" % (box_calls,))

# 4e. the degenerate-dimension clamp still fires on the unscaled numbers.
FLAT = {"type": "box", "half_extents": [1.0, 1.0, 0.0]}
calls, codes = author(FakeAdapter(ENGINE_SCALES), entity([FLAT], [4.0, 4.0, 4.0]))
box_calls = [call for call in calls if call[0] == "box"]
check(box_calls and box_calls[0][1][2] == physics_build.MIN_DIMENSION,
      "a zero-thickness plane must still be clamped when the scale is left to "
      "the engine; got %r" % (box_calls,))
check("PHYS_SHAPE_APPROXIMATED" in codes,
      "the clamp must still be reported; codes were %r" % (codes,))

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)
