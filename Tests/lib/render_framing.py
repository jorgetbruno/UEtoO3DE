"""
render_framing.py — where to put the camera so a render check sees the level.

Pure. Used by Tests/m6/m6_level_renders.py.

THE GAP THIS CLOSES. The level-render check instantiated the prefab and
captured from wherever the viewport camera already was. On a city whose
geometry sits hundreds of metres from the origin (NYC1950 in Phoenix) that
camera looked at empty sky: the "level" capture was byte-identical to the
empty control, and the check still said PASS -- it proved the capture path
works and nothing about the level.

So the camera is aimed from the level's own extent, and the verdict compares
two captures from THE SAME POSE, one with the prefab and one without. A level
that contributes no pixels to its own framing fails, whatever the reason.
"""

import math

# Below this fraction of changed pixels, a capture with the level and one
# without it are the same picture. NYC's framed street differs from its empty
# framing by far more; a mis-aimed camera differs by capture noise alone.
MIN_CHANGED_FRACTION = 0.02


def _percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values")
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def robust_bounds(points, low=0.05, high=0.95):
    """(min xyz, max xyz) of the 5th..95th percentile of each axis.

    Percentiles, not extremes: one stray entity at the origin (a sky sphere, a
    container) must not drag the camera away from where the level is.
    """
    points = [tuple(p) for p in points]
    if not points:
        raise ValueError("no points to frame")
    lo = tuple(_percentile([p[i] for p in points], low) for i in range(3))
    hi = tuple(_percentile([p[i] for p in points], high) for i in range(3))
    return lo, hi


def framing(points, min_size=10.0):
    """Camera pose {'position': (x, y, z), 'pitch': degrees, 'target': (x, y, z)}.

    The camera looks along +Y (yaw 0, which is how the editor's
    set_current_view_rotation reads: validated by aiming at an NYC street),
    stands back far enough to hold the robust extent, and looks down at the
    centre.
    """
    lo, hi = robust_bounds(points)
    centre = tuple((lo[i] + hi[i]) / 2.0 for i in range(3))
    size = max(hi[0] - lo[0], hi[1] - lo[1], min_size)
    back = size * 0.9 + 5.0
    up = size * 0.45 + 3.0 + (hi[2] - lo[2]) * 0.5
    position = (centre[0], centre[1] - back, centre[2] + up)
    pitch = -math.degrees(math.atan2(up, back))
    return {"position": position, "pitch": pitch, "target": centre, "size": size}


def verdict_changed(changed_fraction, minimum=MIN_CHANGED_FRACTION):
    """None when the level visibly contributed to its own framing, else why not."""
    if changed_fraction < minimum:
        return ("the capture with the level differs from the same framing without "
                "it in only %.2f%% of pixels (need %.0f%%): the level contributes "
                "nothing to what the camera sees"
                % (changed_fraction * 100.0, minimum * 100.0))
    return None
