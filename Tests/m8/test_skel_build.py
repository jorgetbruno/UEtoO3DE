"""
test_skel_build.py — pure tests for the M8 skeletal planning half.

The Rz180 composition is the piece that fails INVISIBLY when wrong (every
character faces backwards, nothing errors), so it is proven here as a MATRIX
identity -- R(compose_rz180(q)) == R(q) * Rz(180) -- across a quaternion
sample, not just spot values. Runs in a plain interpreter, ~instant.
"""

import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
sys.path.insert(0, GEM_SCRIPTS)

from ueimporter import skel_build  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def rotation_matrix(q):
    x, y, z, w = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def mat_close(a, b, tol=1e-9):
    return all(abs(a[i][j] - b[i][j]) <= tol for i in range(3) for j in range(3))


RZ180 = [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]


def quat_samples():
    """A spread of unit quaternions covering all axes and sign cases."""
    samples = [(0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 0.0)]
    for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 2, 3)):
        norm = math.sqrt(sum(c * c for c in axis))
        unit = [c / norm for c in axis]
        for angle_deg in (30, 45, 90, 135, 180, 222.5, 270):
            half = math.radians(angle_deg) / 2.0
            s = math.sin(half)
            samples.append((unit[0] * s, unit[1] * s, unit[2] * s,
                            math.cos(half)))
    return samples


def test_compose_is_right_multiplied_rz180():
    for q in quat_samples():
        composed = skel_build.compose_rz180(q)
        # The identity that matters: rotating BY the composed quaternion is
        # the original rotation followed IN THE LOCAL FRAME by a 180-deg yaw,
        # i.e. R(q') = R(q) * Rz180 (column-vector convention).
        expected = mat_mul(rotation_matrix(q), RZ180)
        actual = rotation_matrix(composed)
        check(mat_close(actual, expected),
              "compose_rz180(%r) is not R(q)*Rz180" % (q,))
        check(composed[3] >= 0.0,
              "compose_rz180(%r) broke the w >= 0 canonicalization" % (q,))


def test_compose_twice_is_identity():
    for q in quat_samples():
        twice = skel_build.compose_rz180(skel_build.compose_rz180(q))
        expected = rotation_matrix(q)
        check(mat_close(rotation_matrix(twice), expected),
              "compose_rz180 twice is not the identity on %r" % (q,))


def test_corrected_transform_touches_only_rotation():
    transform = {"translation": [1.0, 2.0, 3.0],
                 "rotation": [0.0, 0.0, 0.0, 1.0],
                 "scale": [2.0, 2.0, 2.0]}
    corrected = skel_build.corrected_local_transform(transform)
    check(corrected["translation"] == transform["translation"],
          "correction moved the translation")
    check(corrected["scale"] == transform["scale"],
          "correction changed the scale")
    check(corrected["rotation"] == [0.0, 0.0, 1.0, 0.0],
          "identity rotation did not become Rz180: %r" % corrected["rotation"])
    check(transform["rotation"] == [0.0, 0.0, 0.0, 1.0],
          "input transform was mutated")


def test_plan_with_animation():
    plan = skel_build.plan_skeletal(
        {"asset_guid": "g", "animation_guid": "a", "loop": True, "play": False,
         "material_slots": []}, "E")
    names = [c[0] for c in plan["components"]]
    check(names == ["Actor", "Simple Motion"],
          "unexpected component list: %r" % names)
    properties = dict(plan["components"][1][1])
    check(properties.get("Configuration|Motion") == "motion_asset",
          "motion property missing from the plan")
    check(properties.get("Configuration|Play on active") is False,
          "play=False was not planned")
    check(properties.get("Configuration|Loop motion") is True,
          "loop=True was not planned")


def test_plan_without_animation_has_no_simple_motion():
    plan = skel_build.plan_skeletal(
        {"asset_guid": "g", "animation_guid": None, "loop": False,
         "play": False, "material_slots": []}, "E")
    names = [c[0] for c in plan["components"]]
    check(names == ["Actor"],
          "bind-pose entity should get the Actor component only: %r" % names)


def main():
    for test in (test_compose_is_right_multiplied_rz180,
                 test_compose_twice_is_identity,
                 test_corrected_transform_touches_only_rotation,
                 test_plan_with_animation,
                 test_plan_without_animation_has_no_simple_motion):
        test(),
    if failures:
        print("RESULT: FAIL (%d)" % len(failures))
        return 1
    print("RESULT: PASS (skel_build pure tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
