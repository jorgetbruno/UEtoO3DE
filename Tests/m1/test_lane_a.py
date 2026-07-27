"""
test_lane_a.py — property tests for the pure exporter modules (plan M1).

Runs in a plain Python 3 interpreter; no editor, no third-party packages.
These are the assertions that would catch a "simplification" of Lane A, which
is the plan's #1 known hard spot and the one bug that looks fine in every
screenshot until someone reads a sign in the level.

The load-bearing test is `test_orientation_is_reversed`. UE is left-handed and
O3DE is right-handed, so the numeric map between them MUST have determinant
-1. Anyone who "cleans up" `convert_position` into a plain divide-by-100
produces a level that is a perfect mirror of the original -- self-consistent,
geometrically valid, and wrong. That test fails the moment the determinant
turns positive.

Run:  python Tests/m1/test_lane_a.py
"""

import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE_ROOT = os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                            "UEO3DEExporter", "Content", "Python")
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from ueo3de import lane_a  # noqa: E402
from ueo3de import naming  # noqa: E402
from ueo3de.warnings import CODES, Warnings  # noqa: E402

EPSILON = 1e-9
_failures = []


def check(condition, message):
    if not condition:
        _failures.append(message)


def check_close(actual, expected, message, tolerance=1e-9):
    if isinstance(expected, (list, tuple)):
        if len(actual) != len(expected):
            _failures.append("%s: length %d != %d" % (message, len(actual), len(expected)))
            return
        for index, (a, e) in enumerate(zip(actual, expected)):
            if abs(a - e) > tolerance:
                _failures.append("%s: component %d is %r, expected %r"
                                 % (message, index, a, e))
        return
    if abs(actual - expected) > tolerance:
        _failures.append("%s: %r != %r" % (message, actual, expected))


def quat_from_axis_angle(axis, degrees):
    length = math.sqrt(sum(c * c for c in axis))
    axis = [c / length for c in axis]
    half = math.radians(degrees) * 0.5
    s = math.sin(half)
    return [axis[0] * s, axis[1] * s, axis[2] * s, math.cos(half)]


# A fixed, varied set — never random, so a failure is always reproducible.
TEST_ROTATIONS = [
    quat_from_axis_angle((0, 0, 1), 90),
    quat_from_axis_angle((0, 0, 1), -37),
    quat_from_axis_angle((0, 1, 0), 45),
    quat_from_axis_angle((1, 0, 0), 30),
    quat_from_axis_angle((1, 2, 3), 121),
    quat_from_axis_angle((-2, 0.5, 1), -64),
    quat_from_axis_angle((0.3, -0.7, 0.2), 179),
]

TEST_VECTORS = [
    [100.0, 0.0, 0.0], [0.0, 100.0, 0.0], [0.0, 0.0, 100.0],
    [150.0, -240.0, 37.5], [-1000.0, 2.5, -0.25], [12.0, 34.0, 56.0],
]


# ---------------------------------------------------------------------------

def test_basis_vectors():
    """UE's axes land where MAPPING.md says they land."""
    check_close(lane_a.convert_position([100.0, 0.0, 0.0]), [1.0, 0.0, 0.0],
                "UE forward (+X) -> O3DE +X")
    check_close(lane_a.convert_position([0.0, 100.0, 0.0]), [0.0, -1.0, 0.0],
                "UE right (+Y) -> O3DE -Y")
    check_close(lane_a.convert_position([0.0, 0.0, 100.0]), [0.0, 0.0, 1.0],
                "UE up (+Z) -> O3DE +Z")
    check_close(lane_a.convert_length(250.0), 2.5, "250 cm -> 2.5 m")


def test_orientation_is_reversed():
    """The basis map must have determinant -1 (UE is LH, O3DE is RH).

    A determinant of +1 -- which is what "just divide by 100" gives -- yields a
    mirrored level: internally consistent, and backwards.
    """
    ex = lane_a.convert_position([1.0, 0.0, 0.0])
    ey = lane_a.convert_position([0.0, 1.0, 0.0])
    ez = lane_a.convert_position([0.0, 0.0, 1.0])
    cross = [ey[1] * ez[2] - ey[2] * ez[1],
             ey[2] * ez[0] - ey[0] * ez[2],
             ey[0] * ez[1] - ey[1] * ez[0]]
    determinant = sum(ex[i] * cross[i] for i in range(3))
    check(determinant < 0.0,
          "basis map determinant is %r; must be negative or the port is mirrored"
          % determinant)


def test_rotation_equivariance():
    """convert(R_ue * v) == convert(R_ue) * convert(v), for every R and v.

    This is the whole justification for `convert_quat`'s "negate x and z". If
    the rule were wrong, rotated actors would land in the right position with
    the wrong orientation -- a bug that survives every position assertion.
    """
    for q_index, q_ue in enumerate(TEST_ROTATIONS):
        q_o3de = lane_a.convert_quat(q_ue)
        for v_index, v_ue in enumerate(TEST_VECTORS):
            rotated_then_converted = lane_a.convert_position(
                lane_a.quat_rotate(q_ue, v_ue))
            converted_then_rotated = lane_a.quat_rotate(
                q_o3de, lane_a.convert_position(v_ue))
            check_close(rotated_then_converted, converted_then_rotated,
                        "equivariance for rotation %d, vector %d" % (q_index, v_index),
                        tolerance=1e-9)


def test_conversion_is_a_homomorphism():
    """convert(parent o child) == convert(parent) o convert(child).

    Parent/child composition is where handedness bugs actually surface, so the
    conversion has to commute with composition or a rotated child under a
    rotated parent lands somewhere plausible but wrong.
    """
    parents = [
        {"translation": [0.0, 400.0, 50.0], "rotation": TEST_ROTATIONS[2], "scale": [1.0, 1.0, 1.0]},
        {"translation": [-120.0, 33.0, 7.0], "rotation": TEST_ROTATIONS[4], "scale": [2.0, 2.0, 2.0]},
    ]
    children = [
        {"translation": [150.0, 0.0, 50.0], "rotation": TEST_ROTATIONS[3], "scale": [1.0, 1.0, 1.0]},
        {"translation": [0.0, -75.0, 12.0], "rotation": TEST_ROTATIONS[5], "scale": [0.5, 0.5, 0.5]},
    ]

    def convert(transform):
        scale, _negative = lane_a.convert_scale(transform["scale"])
        return {
            "translation": lane_a.convert_position(transform["translation"]),
            "rotation": lane_a.convert_quat(transform["rotation"]),
            "scale": scale,
        }

    for p_index, parent in enumerate(parents):
        for c_index, child in enumerate(children):
            composed_in_ue = lane_a.compose(parent, child)
            # Composition is plain TRS algebra, so it is valid in either space;
            # only the numbers differ.
            via_ue = convert(composed_in_ue)
            via_o3de = lane_a.compose(convert(parent), convert(child))
            check_close(via_ue["translation"], via_o3de["translation"],
                        "composed translation %d/%d" % (p_index, c_index), 1e-9)
            # q and -q are the same rotation; compare after canonicalizing.
            expected = via_o3de["rotation"]
            if expected[3] < 0.0:
                expected = [-c for c in expected]
            check_close(via_ue["rotation"], expected,
                        "composed rotation %d/%d" % (p_index, c_index), 1e-9)


def test_quaternion_canonicalization():
    for q_ue in TEST_ROTATIONS:
        converted = lane_a.convert_quat(q_ue)
        check(converted[3] >= 0.0, "converted quaternion must have w >= 0")
        norm = math.sqrt(sum(c * c for c in converted))
        check_close(norm, 1.0, "converted quaternion stays unit-length", 1e-9)
    check_close(lane_a.convert_quat([0.0, 0.0, 0.0, 1.0]), [0.0, 0.0, 0.0, 1.0],
                "identity rotation converts to identity")


def test_scale_never_goes_negative():
    scale, negative = lane_a.convert_scale([2.0, 1.0, 0.5])
    check_close(scale, [2.0, 1.0, 0.5], "positive scale passes through unchanged")
    check(negative == [], "positive scale reports no negative axes")

    scale, negative = lane_a.convert_scale([1.0, -2.0, 3.0])
    check(all(c > 0.0 for c in scale), "negative scale is exported as absolute value")
    check(negative == ["y"], "negative scale reports which axis, got %r" % negative)


# ---------------------------------------------------------------------------

def test_path_sanitization():
    cases = [
        ("/Game/Meshes/SM_LetterF.SM_LetterF", "uetoo3de/game/meshes/sm_letterf"),
        ("/Game/Meshes/SM_LetterF", "uetoo3de/game/meshes/sm_letterf"),
        ("/Engine/BasicShapes/Cube.Cube", "uetoo3de/engine/basicshapes/cube"),
        ("/Game/Foo Bar/My Asset!", "uetoo3de/game/foo_bar/my_asset"),
        ("/Game/x/CON", "uetoo3de/game/x/con_"),
    ]
    for ue_path, expected in cases:
        check(naming.sanitize_path(ue_path) == expected,
              "sanitize(%r) == %r, got %r"
              % (ue_path, expected, naming.sanitize_path(ue_path)))

    check(naming.sanitize_path("/Game/A/X") != naming.sanitize_path("/Game/B/X"),
          "different folders must not collapse onto one path")


def test_path_collisions_are_detected():
    registry = naming.PathRegistry()
    registry.claim("/Game/Foo Bar/Thing")
    # Both sanitize to game/foo_bar/thing -- a space and a '!' are both outside
    # the allowed set and both map to '_'. ('-' is allowed and survives, so
    # 'Foo-Bar' does NOT collide with 'Foo Bar'.) Silently overwriting one with
    # the other is exactly what the plan forbids.
    try:
        registry.claim("/Game/Foo!Bar/Thing")
    except naming.PathCollisionError:
        pass
    else:
        _failures.append("colliding sanitized paths were accepted")

    # Re-claiming the same asset is not a collision.
    try:
        registry.claim("/Game/Foo Bar/Thing")
    except naming.PathCollisionError:
        _failures.append("re-claiming the same asset was treated as a collision")


def test_guids_are_stable():
    # Pinned literals: if the namespace or the derivation ever changes, every
    # previously exported manifest silently stops matching and M10's
    # incremental re-import starts duplicating entities instead of updating.
    check(naming.asset_guid("/Game/Meshes/SM_LetterF.SM_LetterF")
          == naming.asset_guid("/Game/Meshes/SM_LetterF"),
          "object path and package path must yield the same GUID")
    check(naming.asset_guid("/Game/Meshes/SM_LetterF")
          == "5f55ab12-5954-51f0-8ef5-f1509568a6fd",
          "SM_LetterF GUID drifted: %r" % naming.asset_guid("/Game/Meshes/SM_LetterF"))
    check(naming.asset_guid("/Engine/BasicShapes/Cube")
          == "34b75cb0-9e38-5955-a258-4ee811136fdb",
          "Cube GUID drifted: %r" % naming.asset_guid("/Engine/BasicShapes/Cube"))
    check(naming.asset_guid("/Game/A") != naming.asset_guid("/Game/B"),
          "distinct assets must get distinct GUIDs")


def test_warning_catalogue():
    collector = Warnings()
    collector.add("PHYS_DEGENERATE_SHAPE", "subject", "detail")
    collector.add("PHYS_DEGENERATE_SHAPE", "subject", "detail")
    check(len(collector) == 1, "identical warnings are deduplicated")

    try:
        collector.add("NOT_A_CODE", "s", "d")
    except KeyError:
        pass
    else:
        _failures.append("an unknown warning code was accepted")

    for code, (severity, meaning) in CODES.items():
        check(code.isupper(), "warning code %r must be upper case" % code)
        check(severity in ("info", "warn", "error"), "bad severity on %r" % code)
        check(bool(meaning.strip()), "warning code %r has no meaning text" % code)


TESTS = [
    test_basis_vectors,
    test_orientation_is_reversed,
    test_rotation_equivariance,
    test_conversion_is_a_homomorphism,
    test_quaternion_canonicalization,
    test_scale_never_goes_negative,
    test_path_sanitization,
    test_path_collisions_are_detected,
    test_guids_are_stable,
    test_warning_catalogue,
]


def main():
    for test in TESTS:
        before = len(_failures)
        test()
        status = "ok  " if len(_failures) == before else "FAIL"
        print("  %s %s" % (status, test.__name__))

    if _failures:
        print()
        for failure in _failures:
            print("FAIL: " + failure)
        print("RESULT: FAIL (%d failure(s))" % len(_failures))
        return 1
    print("RESULT: PASS (%d tests)" % len(TESTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
