"""
test_m9_pure.py — pure tests for the M9 planning halves (no editor, ~instant).

The two pieces that fail INVISIBLY when wrong are proven as math:
  * camera_build.vertical_fov_deg -- a wrong conversion just renders a
    slightly-off framing nobody measures;
  * decal_build.compose_projection_rotation -- a wrong remap projects the
    decal sideways, which on a flat wall still LOOKS like a decal.
"""

import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
sys.path.insert(0, GEM_SCRIPTS)

from ueimporter import camera_build, decal_build  # noqa: E402

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


def mat_vec(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


RY_MINUS_90 = [[0, 0, -1], [0, 1, 0], [1, 0, 0]]


def test_fov_conversion():
    # The probe camera: 90 deg horizontal at 16:9 -> ~58.72 vertical.
    fov = camera_build.vertical_fov_deg(90.0, 16.0 / 9.0)
    check(abs(fov - 58.7155) < 0.01, "90/16:9 gave %.4f, expected ~58.72" % fov)
    # Square aspect: horizontal == vertical, exactly.
    check(close(camera_build.vertical_fov_deg(72.0, 1.0), 72.0, 1e-9),
          "square aspect must be the identity")
    # Monotonic in aspect: wider screen -> smaller vertical FOV.
    check(camera_build.vertical_fov_deg(90.0, 2.0)
          < camera_build.vertical_fov_deg(90.0, 1.5),
          "vertical FOV must shrink as the aspect widens")
    try:
        camera_build.vertical_fov_deg(90.0, 0.0)
        check(False, "aspect 0 must raise")
    except ValueError:
        pass


def quat_samples():
    """Unit quaternions covering all axes and sign cases.

    -30 degrees about Z is Fixture_02's Decal_01 (the yawed canary); the
    tilted axes catch a remap that only works in the ground plane.
    """
    samples = [(0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 0.0)]
    for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 2, 3)):
        norm = math.sqrt(sum(c * c for c in axis))
        unit = [c / norm for c in axis]
        for angle_deg in (-30, 30, 90, 150, 222.5):
            half = math.radians(angle_deg) / 2.0
            s = math.sin(half)
            samples.append((unit[0] * s, unit[1] * s, unit[2] * s,
                            math.cos(half)))
    return samples


def test_decal_rotation_is_right_multiplied_ry_minus_90():
    for q in quat_samples():
        composed = decal_build.compose_projection_rotation(q)
        expected = mat_mul(rotation_matrix(q), RY_MINUS_90)
        actual = rotation_matrix(composed)
        ok = all(abs(actual[i][j] - expected[i][j]) <= 1e-9
                 for i in range(3) for j in range(3))
        check(ok, "compose_projection_rotation(%r) is not R(q)*Ry(-90)" % (q,))
        check(composed[3] >= 0.0, "w >= 0 canonicalization broke on %r" % (q,))


def test_projection_axis_lands_on_plus_x():
    # The identity decal: Atom projects along local -Z; after the remap that
    # must be the frame's +X (where UE projected, Lane A applied).
    composed = decal_build.compose_projection_rotation([0.0, 0.0, 0.0, 1.0])
    direction = mat_vec(rotation_matrix(composed), [0.0, 0.0, -1.0])
    check(all(close(direction[i], [1.0, 0.0, 0.0][i], 1e-9) for i in range(3)),
          "local -Z must land on +X, got %r" % (direction,))


def test_projection_direction_survives_any_rotation():
    """The property that actually matters, for ROTATED decals.

    The identity check above cannot distinguish a correct remap from one
    that happens to work only at identity. For every entity rotation, the
    authored rotation's Atom -Z image must equal the manifest rotation's +X
    image -- i.e. the decal keeps projecting exactly where UE projected it,
    whatever the actor's yaw. (Verified by hand against Fixture_02's
    yawed-30 Decal_01: exact to machine epsilon; the ~3e-7 seen when
    reading the value back from the manifest is that file's 6-decimal float
    rounding, not the algebra.)

    The FOOTPRINT axes are asserted the same way: Atom local X must land on
    the frame axis carrying the UE z half-extent and Atom local Y on the y
    one -- a swapped pair keeps the projection correct while stretching the
    decal the wrong way across the surface.
    """
    for q in quat_samples():
        authored = decal_build.compose_projection_rotation(q)
        frame, remapped = rotation_matrix(q), rotation_matrix(authored)
        for atom_axis, frame_axis, label in (
                ([0.0, 0.0, -1.0], [1.0, 0.0, 0.0], "projection (-Z -> +X)"),
                ([1.0, 0.0, 0.0], [0.0, 0.0, 1.0], "footprint X -> frame Z"),
                ([0.0, 1.0, 0.0], [0.0, 1.0, 0.0], "footprint Y -> frame Y")):
            got, want = mat_vec(remapped, atom_axis), mat_vec(frame, frame_axis)
            check(all(close(a, b, 1e-12) for a, b in zip(got, want)),
                  "%s wrong for rotation %r: %r vs %r"
                  % (label, [round(v, 4) for v in q],
                     [round(v, 6) for v in got], [round(v, 6) for v in want]))


def test_scaled_decal_permutes_its_scale_with_its_extents():
    """UE sizes a decal as DecalSize * ActorScale in UE axes, so the SCALE
    must take the same permutation the extents take: UE x (depth) belongs on
    Atom Z, UE z on Atom X. Leaving the scale in UE order stretched a
    non-uniformly scaled decal along the wrong axis and gave it the wrong
    projection depth -- invisible on the unscaled canary."""
    transform = {"translation": [0.0, 0.0, 0.0],
                 "rotation": [0.0, 0.0, 0.0, 1.0],
                 "scale": [2.0, 3.0, 5.0]}          # sx, sy, sz all distinct
    half = [0.64, 1.28, 1.92]                        # hx (depth), hy, hz
    got = decal_build.corrected_local_transform(transform, half)["scale"]
    # Atom X <- UE z: 2*hz*sz | Atom Y <- UE y: 2*hy*sy | Atom Z <- UE x: 2*hx*sx
    want = [2 * 1.92 * 5.0, 2 * 1.28 * 3.0, 2 * 0.64 * 2.0]
    check(all(close(a, b, 1e-9) for a, b in zip(got, want)),
          "scaled decal volume %r != %r" % (got, want))


def test_corrected_transform_scale_mapping():
    transform = {"translation": [1.0, 2.0, 3.0],
                 "rotation": [0.0, 0.0, 0.0, 1.0],
                 "scale": [1.0, 1.0, 1.0]}
    corrected = decal_build.corrected_local_transform(
        transform, [0.64, 1.28, 1.92])
    # (hx, hy, hz) -> (2hz, 2hy, 2hx): footprint X covers UE z, Y covers y,
    # depth Z covers x.
    check(all(close(a, b, 1e-9) for a, b in zip(
        corrected["scale"], [3.84, 2.56, 1.28])),
        "scale mapping wrong: %r" % (corrected["scale"],))
    check(corrected["translation"] == transform["translation"],
          "translation must not move")
    check(transform["scale"] == [1.0, 1.0, 1.0], "input was mutated")


def main():
    for test in (test_fov_conversion,
                 test_decal_rotation_is_right_multiplied_ry_minus_90,
                 test_projection_axis_lands_on_plus_x,
                 test_projection_direction_survives_any_rotation,
                 test_scaled_decal_permutes_its_scale_with_its_extents,
                 test_corrected_transform_scale_mapping):
        test()
    if failures:
        print("RESULT: FAIL (%d)" % len(failures))
        return 1
    print("RESULT: PASS (M9 pure tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
