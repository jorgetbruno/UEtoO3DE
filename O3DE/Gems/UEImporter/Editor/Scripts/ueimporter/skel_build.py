"""
skel_build.py — skeletal entities: Actor + Simple Motion (plan M8).

Split like light_build: `compose_rz180` / `corrected_local_transform` /
`plan_skeletal` are PURE (testable in a plain interpreter, mutation-tested in
Tests/m8), `author_skeletal` is the thin editor half.

The Rz180: skeletal geometry ships through UE's NATIVE FBX exporter -- no
GeometryScript bake stage exists for skinned meshes -- so its O3DE products
carry LaneA * Rz180 instead of Lane A (LANE_B.md, M8; measured at the skinned
azmodel buffer level). The correction is one lossless local yaw-180 composed
into the entity rotation: q' = q * (0,0,1,0). Getting this wrong does not
error anywhere -- every character just faces backwards -- which is why the
manifest carries `lane_b_skeletal_rule` and manifest_io refuses a mismatch.

Component recipe (measured, Tests/o3de/probe_m8_emfx.py):
  Actor           'Actor asset'                      <- .actor product
  Simple Motion   'Configuration|Motion'             <- .motion product
                  'Configuration|Play on active'     <- manifest play
                  'Configuration|Loop motion'        <- manifest loop
No EMotionFX bus is reflected to Python in 26.05, so these properties are the
entire authoring surface; playback is asserted by the M8 acceptance through
frame captures, not joint queries.
"""

# The frame correction itself, as a quaternion: a 180-degree yaw. Self-
# inverse, which is why compose_rz180 applied twice is the identity.
RZ180 = (0.0, 0.0, 1.0, 0.0)

ACTOR_COMPONENT = "Actor"
SIMPLE_MOTION_COMPONENT = "Simple Motion"
ACTOR_ASSET_PROPERTY = "Actor asset"
MOTION_PROPERTY = "Configuration|Motion"
PLAY_ON_ACTIVE_PROPERTY = "Configuration|Play on active"
LOOP_PROPERTY = "Configuration|Loop motion"


def compose_rz180(rotation_xyzw):
    """q * (0,0,1,0), canonicalized to w >= 0 (Lane A's convention).

    Right-multiplication rotates about the ENTITY's OWN Z axis -- the frame
    the product geometry is yawed in -- not the world's. Hamilton product
    with q2 = (0,0,1,0) collapses to a component shuffle.
    """
    x, y, z, w = (float(c) for c in rotation_xyzw)
    qx, qy, qz, qw = y, -x, w, -z
    if qw < 0:
        qx, qy, qz, qw = -qx, -qy, -qz, -qw
    return [qx, qy, qz, qw]


def corrected_local_transform(transform):
    """The manifest local transform with the skeletal Rz180 composed in."""
    corrected = dict(transform)
    corrected["rotation"] = compose_rz180(transform["rotation"])
    return corrected


def _quat_mul(a, b):
    ax, ay, az, aw = (float(c) for c in a)
    bx, by, bz, bw = (float(c) for c in b)
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def _rotate(q, v):
    x, y, z, w = (float(c) for c in q)
    vx, vy, vz = (float(c) for c in v)
    # v + 2w(q_vec x v) + 2 q_vec x (q_vec x v)
    tx, ty, tz = 2.0 * (y * vz - z * vy), 2.0 * (z * vx - x * vz), 2.0 * (x * vy - y * vx)
    return [vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx)]


def counter_correct_child(child_local, correction_xyzw, parent_scale_ratio=1.0):
    """Undo a parent's frame correction for ONE child's local transform.

    A skeletal Rz180 (or a decal's Ry(-90)) compensates for how THAT
    entity's own product geometry was baked -- but O3DE composes
    child_world = parent_world * child_local, so the correction also swings
    every descendant around the parent's origin. Left-multiplying the
    child's local transform by the correction's INVERSE cancels exactly
    that: rotation C^-1*L, translation C^-1 applied to L, and (when the
    parent's correction changed a UNIFORM scale) the position and scale
    divided by that ratio. Net child world transform is unchanged.

    Non-uniform parent scale needs no ratio: O3DE carries it on
    EditorNonUniformScaleComponent, which does not reach children at all
    (DIVERGENCES.md, XFORM_NONUNIFORM_SCALE_NOT_INHERITED).
    """
    x, y, z, w = (float(c) for c in correction_xyzw)
    inverse = [-x, -y, -z, w]                      # unit quaternion inverse
    ratio = float(parent_scale_ratio) or 1.0

    out = dict(child_local)
    out["rotation"] = _quat_mul(inverse, child_local["rotation"])
    rotated = _rotate(inverse, child_local["translation"])
    out["translation"] = [component / ratio for component in rotated]
    out["scale"] = [component / ratio for component in child_local["scale"]]
    return out


def plan_skeletal(skeletal, entity_name):
    """A deterministic authoring plan for one skeletal entity.

    Returns {"components": [(component_name, [(property, asset_key or
    value)])]} where asset placeholders are the strings "actor_asset" /
    "motion_asset" -- the author substitutes real AssetIds, keeping this half
    pure. No animation -> no Simple Motion component at all: a motionless
    Simple Motion would read as a configured component that plays nothing.
    """
    components = [(ACTOR_COMPONENT, [(ACTOR_ASSET_PROPERTY, "actor_asset")])]
    if skeletal.get("animation_guid"):
        components.append((SIMPLE_MOTION_COMPONENT, [
            (MOTION_PROPERTY, "motion_asset"),
            (PLAY_ON_ACTIVE_PROPERTY, bool(skeletal.get("play", True))),
            (LOOP_PROPERTY, bool(skeletal.get("loop", True))),
        ]))
    return {"components": components}


def author_skeletal(entity_id, plan, actor_asset_id, motion_asset_id,
                    entity_name, resolve_component_type):
    """Editor half: add the planned components and set their properties."""
    import azlmbr.bus as bus
    import azlmbr.editor as editor

    from .prefab_build import PrefabBuildError

    substitutions = {"actor_asset": actor_asset_id, "motion_asset": motion_asset_id}
    for component_name, properties in plan["components"]:
        type_id = resolve_component_type(component_name)
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
        if not outcome or not outcome.IsSuccess():
            raise PrefabBuildError(
                "%s: AddComponentsOfType(%s) failed: %s"
                % (entity_name, component_name,
                   outcome.GetError() if outcome else "no outcome"))
        pair = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()
        for property_path, value in properties:
            resolved = substitutions.get(value, value) if isinstance(value, str) else value
            if resolved is None:
                raise PrefabBuildError(
                    "%s: %s.%s has no value (asset never waited for?)"
                    % (entity_name, component_name, property_path))
            result = editor.EditorComponentAPIBus(
                bus.Broadcast, 'SetComponentProperty', pair, property_path, resolved)
            if not result or not result.IsSuccess():
                raise PrefabBuildError(
                    "%s: could not set %s on %s: %s"
                    % (entity_name, property_path, component_name,
                       result.GetError() if result else "no outcome"))
