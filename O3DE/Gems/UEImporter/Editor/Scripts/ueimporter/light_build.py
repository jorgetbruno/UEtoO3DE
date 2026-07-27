"""
light_build.py — UE lights → Atom light components (plan M5).

`plan_light` is PURE: it turns a manifest `light` block into an ordered list of
component properties plus the warnings the conversion owes the report, with no
editor imported. `author_light` is the thin half that talks to `azlmbr`. The
split is not tidiness -- it is what lets `Tests/m5/test_light_build.py` assert
the two facts that are otherwise invisible until a level looks wrong:

  * the CONVERSION arithmetic (UE units → candela), and
  * the property ORDER.

Order is load-bearing. Writing "Intensity mode" CONVERTS the stored intensity
on the Directional Light component: measured in
`Tests/o3de/probe_m5_lights2.py`, writing intensity 5.0 and then switching the
mode to Lux stores **80.0**, while mode-then-intensity stores 5.0. Every
directional light in a level would be 16x too bright, uniformly, which reads
as "the exposure is off" rather than as a bug. The plan is therefore an
ordered list and the test asserts the mode precedes the intensity.

--------------------------------------------------------------------------
Enum values (read from the 26.05 SDK headers, confirmed live by the probes)
--------------------------------------------------------------------------
`PhotometricUnit`  (Atom/Feature/CoreLights/PhotometricValue.h)
    0 Lumen  1 Candela  2 Lux  3 Nit  4 Ev100Luminance  5 Ev100Illuminance
`LightType`        (AtomLyIntegration/.../AreaLightComponentConfig.h)
    0 Unknown  1 Sphere  2 SpotDisk  3 Capsule  4 Quad  5 Polygon
    6 SimplePoint  7 SimpleSpot
`LightAttenuationRadiusMode` (.../CoreLightsConstants.h)  0 Explicit 1 Automatic

Atom's LOCAL lights accept only Candela and Lumen (`GetValidPhotometricUnits`:
Nit/Ev100 need a shape component), so every UE unit is converted to **candela**
and the mode pinned to Candela. The directional light is the exception: it
takes lux, which is exactly what UE gives it.

--------------------------------------------------------------------------
Intensity conversion (from UE's own arithmetic, not from a textbook)
--------------------------------------------------------------------------
`ULocalLightComponent::GetUnitsConversionFactor` (Engine/Private/Components/
LocalLightComponent.cpp) composes a source factor with a target factor. Read
against `TargetUnits = Candelas`, and with `EV100ToLuminance` (RenderUtils.h,
`2^EV` with an implicit 1 m^2 surface, as UE's own comment describes):

    candelas   -> v                       (identity)
    lumens     -> v / (2*pi*(1 - cos t))  t = the cone half-angle; a point
                                          light is the full sphere, t = 180
                                          deg, which gives the familiar 4*pi
    ev         -> 2^v
    unitless   -> v * 16/10000            UE's own factor; "unitless" has no
                                          photometric meaning, so this is an
                                          approximation and says so
    nits       -> v                       area-dependent in UE ("no scale
                                          factor (it depends on the light's
                                          area)"); the implicit 1 m^2 surface
                                          is the same assumption UE makes for
                                          EV. UE 5.8's ELightUnits has no Nits
                                          member, so this path is unreachable
                                          from this exporter and exists only
                                          so the schema's enum has no hole.
"""

import math

# --- Atom enums -----------------------------------------------------------
PHOTOMETRIC_LUMEN = 0
PHOTOMETRIC_CANDELA = 1
PHOTOMETRIC_LUX = 2
PHOTOMETRIC_NIT = 3
PHOTOMETRIC_EV100_LUMINANCE = 4
PHOTOMETRIC_EV100_ILLUMINANCE = 5

LIGHT_TYPE_UNKNOWN = 0
LIGHT_TYPE_SPHERE = 1
LIGHT_TYPE_SPOT_DISK = 2
LIGHT_TYPE_SIMPLE_POINT = 6
LIGHT_TYPE_SIMPLE_SPOT = 7

RADIUS_EXPLICIT = 0
RADIUS_AUTOMATIC = 1

# --- component names (resolve-or-fail through prefab_build) ---------------
LIGHT_COMPONENT = "Light"
DIRECTIONAL_COMPONENT = "Directional Light"

# --- property paths (probe_m5_lights.py dumped the whole surface) ---------
# The local and directional components differ by one capital letter in the
# shadow path; probe_m5_lights2.py asserts the local path is REJECTED on the
# directional component, so this is a real distinction and not a typo.
P_COLOR = "Controller|Configuration|Color"
P_INTENSITY = "Controller|Configuration|Intensity"
P_INTENSITY_MODE = "Controller|Configuration|Intensity mode"
P_LIGHT_TYPE = "Controller|Configuration|Light type"
P_RADIUS_MODE = "Controller|Configuration|Attenuation radius|Mode"
P_RADIUS = "Controller|Configuration|Attenuation radius|Radius"
P_SHADOW_LOCAL = "Controller|Configuration|Shadows|Enable shadow"
P_SHADOW_DIRECTIONAL = "Controller|Configuration|Shadow|Enable Shadow"
P_SHUTTERS_ENABLE = "Controller|Configuration|Shutters|Enable shutters"
P_SHUTTERS_INNER = "Controller|Configuration|Shutters|Inner angle"
P_SHUTTERS_OUTER = "Controller|Configuration|Shutters|Outer angle"

# `SupportsShadows()` is SpotDisk | Sphere | SimpleSpot -- SimplePoint is NOT
# in the list (AreaLightComponentConfig.cpp). The config FIELD exists for
# every type, so writing it succeeds and reads back true while doing nothing:
# a property assertion alone would be fooled. Hence the explicit set.
TYPES_SUPPORTING_SHADOWS = (LIGHT_TYPE_SPHERE, LIGHT_TYPE_SPOT_DISK,
                            LIGHT_TYPE_SIMPLE_SPOT)

UNITLESS_TO_CANDELA = 16.0 / 10000.0

# Value kinds, so the plan stays free of azlmbr types.
KIND_INT = "int"
KIND_FLOAT = "float"
KIND_BOOL = "bool"
KIND_COLOR = "color"


class LightBuildError(Exception):
    pass


def to_candela(intensity, units, outer_cone_deg=None):
    """UE intensity in `units` → candela. Returns (value, approximated).

    `outer_cone_deg` is the UE spot light's OUTER cone half-angle; None means
    a point light, i.e. the full sphere.
    """
    value = float(intensity)
    units = (units or "").lower()

    if units == "candelas":
        return value, False
    if units == "lumens":
        if outer_cone_deg is None:
            solid_angle = 4.0 * math.pi
        else:
            half_angle = math.radians(max(0.0, min(180.0, float(outer_cone_deg))))
            solid_angle = 2.0 * math.pi * (1.0 - math.cos(half_angle))
        if solid_angle <= 0.0:
            # A zero-width cone concentrates finite flux into no solid angle;
            # the limit is unbounded, so refuse rather than divide by zero.
            return value, True
        return value / solid_angle, False
    if units == "ev":
        return math.pow(2.0, value), False
    if units == "unitless":
        return value * UNITLESS_TO_CANDELA, True
    if units == "nits":
        return value, True
    return value, True


def plan_light(light, subject):
    """Turn a manifest `light` block into an authoring plan.

    Returns `(plan, warnings)`:
      plan     {"component": str, "properties": [(path, kind, value), ...]}
               or None when the light has no v1 mapping
      warnings [(code, detail), ...] for the importer's report
    """
    warnings = []
    kind = (light.get("type") or "unknown").lower()
    colour = light.get("color_linear") or [1.0, 1.0, 1.0]
    cast_shadows = bool(light.get("cast_shadows", True))

    if light.get("use_temperature"):
        warnings.append((
            "LIGHT_TEMPERATURE_DROPPED",
            "UE colour temperature %.0f K is not represented on Atom's light "
            "components; the light keeps its RGB colour only"
            % float(light.get("temperature_k", 6500.0))))

    if kind == "directional":
        # UE directional intensity is lux and carries no units enum (the
        # exporter defaults it to "lux"); Atom's directional light takes lux
        # directly, so no conversion -- only the mode-before-intensity order.
        units = (light.get("intensity_units") or "lux").lower()
        intensity = float(light.get("intensity", 0.0))
        if units != "lux":
            intensity, approximated = to_candela(intensity, units)
            warnings.append((
                "LIGHT_INTENSITY_APPROX",
                "directional light carried %r rather than lux; converted to "
                "%.4f and applied as lux" % (units, intensity)))
        properties = [
            (P_INTENSITY_MODE, KIND_INT, PHOTOMETRIC_LUX),
            (P_INTENSITY, KIND_FLOAT, intensity),
            (P_COLOR, KIND_COLOR, colour),
            (P_SHADOW_DIRECTIONAL, KIND_BOOL, cast_shadows),
        ]
        return {"component": DIRECTIONAL_COMPONENT, "properties": properties}, warnings

    if kind not in ("point", "spot"):
        warnings.append((
            "LIGHT_TYPE_UNSUPPORTED",
            "UE light type %r has no v1 mapping; imported as a transform-only "
            "entity" % kind))
        return None, warnings

    is_spot = kind == "spot"
    light_type = LIGHT_TYPE_SIMPLE_SPOT if is_spot else LIGHT_TYPE_SIMPLE_POINT
    outer_cone = light.get("outer_cone_angle_deg") if is_spot else None

    units = (light.get("intensity_units") or "candelas").lower()
    intensity, approximated = to_candela(
        light.get("intensity", 0.0), units, outer_cone)
    if approximated:
        warnings.append((
            "LIGHT_INTENSITY_APPROX",
            "UE units %r have no exact photometric meaning; %r was converted "
            "to %.4f cd using UE's own internal factor"
            % (units, light.get("intensity"), intensity)))

    properties = [
        (P_LIGHT_TYPE, KIND_INT, light_type),
        # Mode BEFORE intensity, always: see the module docstring.
        (P_INTENSITY_MODE, KIND_INT, PHOTOMETRIC_CANDELA),
        (P_INTENSITY, KIND_FLOAT, intensity),
        (P_COLOR, KIND_COLOR, colour),
    ]

    # Attenuation radius: Atom defaults to Automatic (derived from intensity);
    # UE always carries an explicit radius and artists tune it, so the import
    # is faithful to UE and pins Explicit. DIVERGENCES.md carries the
    # reasoning and the plan assumption it revises.
    radius = light.get("attenuation_radius")
    if radius is not None:
        properties.append((P_RADIUS_MODE, KIND_INT, RADIUS_EXPLICIT))
        properties.append((P_RADIUS, KIND_FLOAT, float(radius)))
        warnings.append((
            "LIGHT_RADIUS_EXPLICIT",
            "UE attenuation radius %.3f m applied explicitly; Atom would "
            "otherwise derive the influence radius from the intensity"
            % float(radius)))

    if is_spot:
        properties.append((P_SHUTTERS_ENABLE, KIND_BOOL, True))
        properties.append((P_SHUTTERS_INNER, KIND_FLOAT,
                           float(light.get("inner_cone_angle_deg", 0.0))))
        properties.append((P_SHUTTERS_OUTER, KIND_FLOAT,
                           float(light.get("outer_cone_angle_deg", 0.0))))

    if light_type in TYPES_SUPPORTING_SHADOWS:
        properties.append((P_SHADOW_LOCAL, KIND_BOOL, cast_shadows))
    elif cast_shadows:
        # Deliberately NOT written: the field accepts the value and reads it
        # back while the light type ignores it, which would make the report
        # and any read-back assertion claim a shadow that does not exist.
        warnings.append((
            "LIGHT_SHADOWS_UNSUPPORTED",
            "UE point light casts shadows, but Atom's SimplePoint light type "
            "does not support them (Sphere does, at the cost of a separate "
            "shape component); the light is imported without shadows"))

    source_radius = light.get("source_radius")
    if source_radius:
        warnings.append((
            "LIGHT_SOURCE_RADIUS_DROPPED",
            "UE source radius %.3f m makes this an area light; imported as a "
            "punctual light, so soft shadow/specular width is lost"
            % float(source_radius)))

    return {"component": LIGHT_COMPONENT, "properties": properties}, warnings


def author_light(entity_id, plan, entity_name, resolve_component_type):
    """Add the planned component and write its properties, in order."""
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.math as azmath

    type_id = resolve_component_type(plan["component"])
    outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
    if not outcome or not outcome.IsSuccess():
        raise LightBuildError("%s: AddComponentsOfType(%s) failed"
                              % (entity_name, plan["component"]))
    pair = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()

    for path, kind, value in plan["properties"]:
        if kind == KIND_COLOR:
            payload = azmath.Color(float(value[0]), float(value[1]),
                                   float(value[2]), 1.0)
        elif kind == KIND_INT:
            payload = int(value)
        elif kind == KIND_BOOL:
            payload = bool(value)
        else:
            payload = float(value)
        set_outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', pair, path, payload)
        if not set_outcome or not set_outcome.IsSuccess():
            raise LightBuildError("%s: setting %s failed" % (entity_name, path))
    return pair
