"""
decal_build.py — decal entities: the Atom Decal component (plan M9).

Pure planning half + thin editor half.

Frames: a UE decal projects along its LOCAL +X with `decal_size` as
half-extents (x = projection depth); an Atom decal projects along the
entity's LOCAL -Z over a unit box scaled by the entity's (non-uniform)
scale. The remap is one local rotation Ry(-90) composed into the entity
rotation -- it takes local -Z onto local +X -- plus a scale of
(2*hz, 2*hy, 2*hx): decal-local X lands on the UE z half-extent, Y on y,
Z (depth) on x. Full extents because the unit box is 1 m per scaled axis.

The material is a converted StandardPBR asset, NOT an Atom decal material
type; the exporter already recorded DECAL_MATERIAL_APPROX, and Sort Key is
the one blend control that maps.
"""

DECAL_COMPONENT = "Decal"
MATERIAL_PROPERTY = "Controller|Configuration|Material"
SORT_KEY_PROPERTY = "Controller|Configuration|Sort Key"

# q for Ry(-90): (x, y, z, w) = (0, -sin45, 0, cos45).
_RY_MINUS_90 = (0.0, -0.7071067811865476, 0.0, 0.7071067811865476)


def compose_projection_rotation(rotation_xyzw):
    """q * Ry(-90), canonicalized to w >= 0: Atom's -Z projection lands on
    the frame UE projected along (+X after Lane A)."""
    x1, y1, z1, w1 = (float(c) for c in rotation_xyzw)
    x2, y2, z2, w2 = _RY_MINUS_90
    qx = w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2
    qy = w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2
    qz = w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2
    qw = w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2
    if qw < 0:
        qx, qy, qz, qw = -qx, -qy, -qz, -qw
    return [qx, qy, qz, qw]


def corrected_local_transform(transform, half_extents_m):
    """Manifest local transform remapped for the Atom decal volume."""
    hx, hy, hz = (abs(float(v)) for v in half_extents_m)
    corrected = dict(transform)
    corrected["rotation"] = compose_projection_rotation(transform["rotation"])
    base = transform.get("scale") or [1.0, 1.0, 1.0]
    corrected["scale"] = [base[0] * 2.0 * hz, base[1] * 2.0 * hy,
                          base[2] * 2.0 * hx]
    return corrected


def plan_decal(decal, entity_name):
    """{'component', 'properties': [(path, 'material_asset'|value)]}."""
    properties = [(SORT_KEY_PROPERTY, int(decal.get("sort_order", 0)))]
    if decal.get("material_guid"):
        properties.insert(0, (MATERIAL_PROPERTY, "material_asset"))
    return {"component": DECAL_COMPONENT, "properties": properties}


def author_decal(entity_id, plan, material_asset_id, entity_name,
                 resolve_component_type):
    import azlmbr.bus as bus
    import azlmbr.editor as editor

    from .prefab_build import PrefabBuildError

    type_id = resolve_component_type(plan["component"])
    outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
    if not outcome or not outcome.IsSuccess():
        raise PrefabBuildError("%s: AddComponentsOfType(Decal) failed: %s"
                               % (entity_name,
                                  outcome.GetError() if outcome else "no outcome"))
    pair = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()
    for path, value in plan["properties"]:
        resolved = material_asset_id if value == "material_asset" else value
        if resolved is None:
            raise PrefabBuildError(
                "%s: decal material asset missing (never waited for?)"
                % entity_name)
        result = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', pair, path, resolved)
        if not result or not result.IsSuccess():
            raise PrefabBuildError("%s: could not set %s: %s"
                                   % (entity_name, path,
                                      result.GetError() if result else "no outcome"))
