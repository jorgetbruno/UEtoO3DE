"""
camera_build.py — camera entities: the Camera component (plan M9).

Pure planning half + thin editor half, the light_build pattern.

UE's `field_of_view` is HORIZONTAL degrees; O3DE's Camera component takes
VERTICAL. The conversion needs the aspect ratio, which the manifest carries
alongside the raw number, so `vertical_fov_deg` is testable offline:

    fov_v = 2 * atan(tan(fov_h / 2) / aspect)

UE has no per-camera near/far (they are project settings), so the O3DE
defaults stay untouched.
"""

import math

CAMERA_COMPONENT = "Camera"
FOV_PROPERTY = "Controller|Configuration|Field of view"


def vertical_fov_deg(fov_horizontal_deg, aspect_ratio):
    """UE horizontal FOV -> vertical FOV, both degrees."""
    fov_h = math.radians(float(fov_horizontal_deg))
    aspect = float(aspect_ratio)
    if aspect <= 0:
        raise ValueError("aspect ratio must be positive, got %r" % aspect_ratio)
    return math.degrees(2.0 * math.atan(math.tan(fov_h / 2.0) / aspect))


def plan_camera(camera, entity_name):
    """{'component': 'Camera', 'properties': [(path, value)]} or None."""
    fov = vertical_fov_deg(camera["fov_horizontal_deg"], camera["aspect_ratio"])
    return {
        "component": CAMERA_COMPONENT,
        "properties": [(FOV_PROPERTY, float(fov))],
    }


def author_camera(entity_id, plan, entity_name, resolve_component_type):
    import azlmbr.bus as bus
    import azlmbr.editor as editor

    from .prefab_build import PrefabBuildError

    type_id = resolve_component_type(plan["component"])
    outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
    if not outcome or not outcome.IsSuccess():
        raise PrefabBuildError("%s: AddComponentsOfType(Camera) failed: %s"
                               % (entity_name,
                                  outcome.GetError() if outcome else "no outcome"))
    pair = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()
    for path, value in plan["properties"]:
        result = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', pair, path, value)
        if not result or not result.IsSuccess():
            raise PrefabBuildError("%s: could not set %s: %s"
                                   % (entity_name, path,
                                      result.GetError() if result else "no outcome"))
