"""
lane_a.py — Lane A: transform conversion, UE world space -> O3DE world space.

PURE MATH. Imports nothing from `unreal`, so the unit tests run in a plain
Python interpreter with no editor in the loop.

--------------------------------------------------------------------------
The convention (plan v2.2, global constraint 6, Lane A)
--------------------------------------------------------------------------
UE:   centimeters, Z-up, LEFT-handed.  +X forward, +Y right, +Z up.
O3DE: meters,      Z-up, RIGHT-handed. +X right,   +Y forward, +Z up.

The two systems have OPPOSITE handedness, so the numeric map between them
must have determinant -1. This is not a stylistic choice: a determinant +1
map (including "copy the numbers unchanged" and including any pure rotation)
applied across a handedness change produces a scene that renders MIRRORED.
That is precisely the bug `SM_LetterF` exists to catch.

The chosen basis map is **negate Y**, which is what the plan means by
"handedness is corrected in the rotation (negate the appropriate quaternion
components)":

    B = diag(1, -1, 1) * (1/100)

    position:   (x, y, z)_cm      -> ( x/100, -y/100,  z/100)_m
    rotation:   (x, y, z, w)_quat -> (-x,      y,      -z,     w)
    scale:      (sx, sy, sz)      -> (sx, sy, sz)          [unchanged]

The rotation rule is conjugation R' = B R B^-1. Because B is improper
(det -1), conjugation maps a rotation about axis `a` by angle `theta` to a
rotation about `B a` by `-theta`; in quaternion terms that is exactly
"negate x and z, keep y and w". `test_lane_a.py` asserts this numerically
rather than trusting the derivation: for every test rotation it checks

    convert_position(R_ue * v) == R_o3de * convert_position(v)

Scale is untouched, so the plan's "every exported scale component is
positive" invariant holds by construction for any UE actor that does not
itself carry a negative scale. UE actors that DO carry one cannot be
represented (negative scale inverts winding and is invalid on Jolt
colliders), so `convert_scale` reports them and exports the absolute value.

--------------------------------------------------------------------------
Consequence worth knowing (documented in MAPPING.md)
--------------------------------------------------------------------------
Negating Y maps UE's forward (+X) onto O3DE's +X, which is O3DE's RIGHT.
The ported level is therefore faithful in shape and mirror-free, but yawed
90 degrees relative to O3DE's forward convention. Nothing in v1 scope
(meshes, lights, physics, terrain) depends on that convention. The
alternative -- swapping X and Y, which also has determinant -1 and keeps
forward on forward -- is a strictly larger change (it permutes scale
components too) and is not what the plan specifies.

--------------------------------------------------------------------------
Lane B must use the SAME basis map
--------------------------------------------------------------------------
Mesh geometry has to undergo the same reflection as the transforms, or the
meshes end up mirrored relative to their own placement. See LANE_B.md: the
S0.2 contract currently covers units only, and closing that gap is an M2
obligation.
"""

CM_TO_M = 0.01

# Only floats within this distance of zero are snapped, purely so the emitted
# JSON never carries -0.0 (which compares equal to 0.0 but diffs as text).
_ZERO_SNAP = 0.0


def _clean(value):
    """Normalize -0.0 to 0.0 so golden-file text diffs stay stable."""
    value = float(value)
    if value == _ZERO_SNAP:
        return 0.0
    return value


def convert_position(vec_cm):
    """UE position in centimeters -> O3DE position in meters."""
    x, y, z = vec_cm
    return [_clean(x * CM_TO_M), _clean(-y * CM_TO_M), _clean(z * CM_TO_M)]


def convert_vector(vec_cm):
    """Alias for a direction/offset in centimeters (same map as a position)."""
    return convert_position(vec_cm)


def convert_length(value_cm):
    """A scalar distance (radius, extent, attenuation) in cm -> meters."""
    return _clean(value_cm * CM_TO_M)


def convert_quat(quat_xyzw):
    """UE rotation quaternion -> O3DE rotation quaternion.

    Conjugation by B = diag(1, -1, 1): negate x and z. The result is
    canonicalized to w >= 0 so that q and -q (which denote the same
    rotation) never produce a spurious golden-file diff.
    """
    x, y, z, w = quat_xyzw
    out = [-float(x), float(y), -float(z), float(w)]
    if out[3] < 0.0:
        out = [-c for c in out]
    return [_clean(c) for c in out]


def convert_scale(scale):
    """UE scale -> O3DE scale (unchanged; dimensionless).

    Returns (converted, negative_axes) where `negative_axes` lists the axis
    names that were negative in UE. A non-empty list is a data-loss event the
    caller must report as `XFORM_NEGATIVE_SCALE` -- negative scale inverts
    triangle winding and is not valid on Jolt colliders, so it cannot be
    carried across.
    """
    negative = [axis for axis, value in zip("xyz", scale) if value < 0.0]
    return [_clean(abs(float(v))) for v in scale], negative


# ---------------------------------------------------------------------------
# negative scale -> rotation (+ optional canonical mirror)   (M4.5 fidelity)
# ---------------------------------------------------------------------------
# A diagonal sign matrix commutes with any diagonal scale, so R*S factors as
#
#     R * S  =  (R * SIGMA_rot) * |S| * M
#
# where SIGMA = diag(sign(S)) = SIGMA_rot * M, SIGMA_rot is a 180-degree
# rotation (or identity) and M is either identity (det SIGMA = +1: the signs
# ARE a rotation, nothing is lost) or the canonical mirror about X,
# diag(-1,1,1) (det SIGMA = -1: a true reflection). The mirror cannot live in
# the transform -- O3DE has no negative scale -- so it is BAKED into a mirrored
# mesh variant and the entity keeps positive scale. Verified numerically for
# all eight sign patterns in test_lane_a.py, and at the FBX level by
# Tests/ue/probe_mirror_bake.py (centroid X flips exactly, signed volume keeps
# sign and magnitude, so winding needs no manual flip).
#
# sign pattern -> (quaternion of SIGMA_rot or None, needs mirrored mesh)
_SIGN_FOLDS = {
    (1, 1, 1):    (None, False),
    (1, -1, -1):  ((1.0, 0.0, 0.0, 0.0), False),   # Rx(180)
    (-1, 1, -1):  ((0.0, 1.0, 0.0, 0.0), False),   # Ry(180)
    (-1, -1, 1):  ((0.0, 0.0, 1.0, 0.0), False),   # Rz(180)
    (-1, 1, 1):   (None, True),                    # M alone
    (1, -1, 1):   ((0.0, 0.0, 1.0, 0.0), True),    # Rz(180) * M
    (1, 1, -1):   ((0.0, 1.0, 0.0, 0.0), True),    # Ry(180) * M
    (-1, -1, -1): ((1.0, 0.0, 0.0, 0.0), True),    # Rx(180) * M
}


def fold_scale_signs(quat_xyzw, scale):
    """Rewrite a UE-space (rotation, signed scale) with the signs folded out.

    Returns (quat', |scale|, mirrored). `mirrored` means the entity must
    reference the mirror-about-X mesh variant. Runs BEFORE Lane A conversion;
    the adjusted quaternion then converts like any other rotation.
    """
    signs = tuple(1 if float(value) >= 0.0 else -1 for value in scale)
    q_rot, mirrored = _SIGN_FOLDS[signs]
    if q_rot is None:
        quat = [float(component) for component in quat_xyzw]
    else:
        quat = quat_multiply(quat_xyzw, q_rot)
    return quat, [abs(float(value)) for value in scale], mirrored


def mirror_x_position(vec):
    """A converted-space position under the canonical mirror: negate x."""
    x, y, z = vec
    return [_clean(-float(x)), _clean(float(y)), _clean(float(z))]


def mirror_x_quat(quat_xyzw):
    """Conjugate a converted-space rotation by diag(-1,1,1).

    A reflection maps a rotation about axis `a` by theta to one about
    `M a` by -theta, which for M = diag(-1,1,1) is exactly "keep x, negate
    y and z". Canonicalized to w >= 0 like convert_quat.
    """
    x, y, z, w = quat_xyzw
    out = [float(x), -float(y), -float(z), float(w)]
    if out[3] < 0.0:
        out = [-c for c in out]
    return [_clean(c) for c in out]


# ---------------------------------------------------------------------------
# quaternion helpers (used by the exporter's self-check and by the tests)
# ---------------------------------------------------------------------------

def quat_multiply(a, b):
    """Hamilton product a*b, both (x, y, z, w). Applies b first, then a."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ]


def quat_rotate(quat_xyzw, vec):
    """Rotate `vec` by the unit quaternion `quat_xyzw`."""
    x, y, z, w = quat_xyzw
    vx, vy, vz = vec
    # t = 2 * (q_vec x v);  v' = v + w*t + q_vec x t
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return [
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    ]


def compose(parent, child):
    """Compose two O3DE-space TRS transforms: world = parent * child.

    Each transform is a dict with 'translation', 'rotation' (xyzw) and
    'scale'. This is the standard TRS composition and is only exact when the
    parent scale is uniform -- which is why the fixture puts its non-uniform
    scale on a childless actor.
    """
    ps, pr, pt = parent["scale"], parent["rotation"], parent["translation"]
    cs, cr, ct = child["scale"], child["rotation"], child["translation"]
    scaled = [ct[i] * ps[i] for i in range(3)]
    rotated = quat_rotate(pr, scaled)
    return {
        "translation": [pt[i] + rotated[i] for i in range(3)],
        "rotation": quat_multiply(pr, cr),
        "scale": [ps[i] * cs[i] for i in range(3)],
    }
