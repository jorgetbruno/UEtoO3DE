"""
editor_physics.py — reading physics facts out of a running O3DE editor.

Only useful inside the editor (it imports `azlmbr` lazily, so importing this
module offline is harmless). It exists because four separate scripts had grown
their own copy of "ask the simulated body for its AABB", the copies drifted,
and the drift cost a debugging cycle: one copy handled only `GetMin()`/
`GetMax()` as methods, and on this build the Aabb proxy exposes `min`/`max` as
ATTRIBUTES while the method names exist and are None. That copy reported "no
simulated body" for every entity in a level whose colliders were all fine.

Two facts encoded here, both measured, both easy to get wrong:

  * the Aabb comes back as a PythonProxyObject and both spellings must be
    tried;
  * `GetAabb` REPLIES FOR ANY VALID ENTITY -- a light, a prefab container --
    so "the bus answered" is not evidence of collision. What distinguishes a
    body is a non-degenerate, finite box; AZ::Aabb's null value has min above
    max, which reads as a negative extent. `body_extents` is the predicate
    that knows the difference, and callers should prefer it to `body_aabb`.
"""

# Metres. `physics_build` clamps its own shapes at MIN_DIMENSION = 0.01, so
# anything under half of that is a shape that carries no geometry -- which is
# exactly what a collider serialized before its bake finished looks like.
DEFAULT_MIN_EXTENT = 0.005
DEFAULT_MAX_EXTENT = 10000.0

_BUS_NAMES = ("SimulatedBodyComponentRequestBus",
              "SimulatedBodyComponentRequestsBus",
              "RigidBodyRequestBus")


def aabb_corners(aabb):
    """(min, max) out of whichever shape the Aabb binding takes, else (None, None)."""
    if aabb is None:
        return None, None
    if all(callable(getattr(aabb, name, None)) for name in ("GetMin", "GetMax")):
        return aabb.GetMin(), aabb.GetMax()
    minimum = getattr(aabb, "min", None)
    maximum = getattr(aabb, "max", None)
    if minimum is not None and maximum is not None and hasattr(minimum, "x"):
        return minimum, maximum
    return None, None


def body_aabb_with_source(entity_id):
    """(min, max, bus name) — the bus name is worth logging in a probe, since
    which binding answered is the first thing to check when a reading looks
    wrong. Returns (None, None, why) when nothing answered readably."""
    import azlmbr.bus as bus

    try:
        import azlmbr.physics as physics
    except ImportError:
        return None, None, "azlmbr.physics not importable"

    tried = []
    for name in _BUS_NAMES:
        handler = getattr(physics, name, None)
        if handler is None:
            continue
        tried.append(name)
        try:
            aabb = handler(bus.Event, "GetAabb", entity_id)
        except Exception as error:  # noqa: BLE001 - a refusal means "try the next"
            tried[-1] += "(raised %s)" % type(error).__name__
            continue
        minimum, maximum = aabb_corners(aabb)
        if minimum is not None:
            return minimum, maximum, name
        tried[-1] += "(unreadable)" if aabb is not None else "(None)"
    return None, None, ", ".join(tried) or "no candidate bus exists"


def body_aabb(entity_id):
    """(min, max) of a GAME entity's simulated body, or (None, None).

    Answers for entities with no collision too -- see `body_extents`, which is
    almost always the one you want.
    """
    minimum, maximum, _source = body_aabb_with_source(entity_id)
    return minimum, maximum


def body_extents(entity_id, min_extent=DEFAULT_MIN_EXTENT,
                 max_extent=DEFAULT_MAX_EXTENT):
    """(size, centre) of a real simulated body, or None when there is none.

    "Real" means the AABB is finite and describes at least a SURFACE. An entity
    with no collision still answers the bus; it answers with a null box, whose
    min exceeds its max and so reads as a negative extent.

    A FLAT BODY IS A REAL BODY. This used to require every axis to be
    non-degenerate, which is wrong: a triangle-mesh collider cooked from a flat
    render mesh has zero thickness and collides perfectly well -- it is a
    surface, not an absence. Measured on SiegeOfPonthus, where `SM_Floor` is a
    genuinely flat 5.0 x 5.0 x 0.0 m plane in BOTH the FBX and glb exports:
    three floors were reported as "a body with no geometry" when the bodies
    were there and correct. Requiring two solid axes keeps the guard that
    matters (a null box, a point, a line) and stops calling a plane an absence.
    """
    minimum, maximum = body_aabb(entity_id)
    if minimum is None:
        return None
    size = [maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z]
    # A null AABB is min > max, so it shows up here as a NEGATIVE extent --
    # that, not "small", is what distinguishes no-collision from thin geometry.
    if min(size) < -min_extent or max(size) > max_extent:
        return None
    if sum(1 for extent in size if extent >= min_extent) < 2:
        return None                      # a point or a line is not a surface
    centre = [(maximum.x + minimum.x) * 0.5,
              (maximum.y + minimum.y) * 0.5,
              (maximum.z + minimum.z) * 0.5]
    return size, centre


def is_flat(size, min_extent=DEFAULT_MIN_EXTENT):
    """Does this body's size describe a zero-thickness surface?

    Worth reporting separately: a flat collider is legitimate geometry, but it
    is also what a solid shape collapses to when the importer had to fall back
    from a convex to the cooked RENDER mesh -- so it is a fidelity signal even
    though it is not a failure.
    """
    return sum(1 for extent in size if extent < min_extent) == 1


def quaternion_matrix(quat):
    """xyzw -> 3x3 rotation matrix."""
    x, y, z, w = quat
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def transformed_aabb_size(half_extents, matrix):
    """World AABB size of a box with `half_extents` under a 3x3 transform."""
    return [2.0 * sum(abs(matrix[i][j]) * half_extents[j] for j in range(3))
            for i in range(3)]


def scaled_rotated_aabb(half_extents, quat, scale, scale_in_shape_frame=False,
                        half_prescaled=False):
    """AABB size of a rotated box on a scaled entity — three distinct answers.

    Both backends apply entity scale in ENTITY space, outside the collider's
    rotation (measured): M = diag(scale) . R, which is the default here.

      * `scale_in_shape_frame` gives R . diag(scale) instead: the scale applied
        inside the shape's own frame. This is what the two conventions
        disagree about, and it is why a ROTATED collider is the only subject
        that can tell them apart -- an axis-aligned one reads the same either
        way.
      * `half_prescaled` models the IMPORTER's defect rather than an engine
        convention: the half extents multiplied per-axis in the shape frame
        before the engine scales again in entity space. Distinct from both of
        the above, and the value a regression of the scale fix would produce.
    """
    rotation = quaternion_matrix(quat)
    if scale_in_shape_frame:
        matrix = [[rotation[i][j] * scale[j] for j in range(3)] for i in range(3)]
    else:
        matrix = [[scale[i] * rotation[i][j] for j in range(3)] for i in range(3)]
    extents = [half_extents[j] * (scale[j] if half_prescaled else 1.0)
               for j in range(3)]
    return transformed_aabb_size(extents, matrix)
