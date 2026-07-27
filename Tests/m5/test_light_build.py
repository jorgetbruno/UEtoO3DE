"""
test_light_build.py — the M5 conversion and ordering rules, offline.

No editor: `light_build.plan_light` is pure, so the two things that are
invisible until a level looks wrong can be asserted here instead of by eye.

  * **Arithmetic.** UE's units → candela, checked against the closed forms
    UE's own `GetUnitsConversionFactor` implies (lumens over the cone's solid
    angle, 2^EV, the 16/10000 unitless factor), not against numbers this code
    produced earlier.
  * **Order.** "Intensity mode" must precede "Intensity" in every plan.
    Measured consequence of getting it wrong (probe_m5_lights2): a directional
    light written intensity-first stores 80.0 lux instead of 5.0 -- uniformly,
    across the whole level, which looks like an exposure choice rather than a
    bug.

Run:  python Tests/m5/test_light_build.py
"""

import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts"))

from ueimporter import light_build  # noqa: E402
from ueimporter.report import CODES as REPORT_CODES  # noqa: E402

failures = []


def fail(message):
    failures.append(str(message))
    print("FAIL: " + str(message))


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def close(actual, expected, tolerance=1e-6):
    return abs(float(actual) - float(expected)) <= tolerance


def paths_of(plan):
    return [path for path, _kind, _value in plan["properties"]]


def value_at(plan, wanted):
    for path, _kind, value in plan["properties"]:
        if path == wanted:
            return value
    return None


# ---------------------------------------------------------------------------
# intensity conversion
# ---------------------------------------------------------------------------

def test_candelas_are_identity():
    value, approximated = light_build.to_candela(12.5, "candelas")
    check(close(value, 12.5), "candelas must pass through unchanged, got %r" % value)
    check(not approximated, "candelas is exact, not an approximation")


def test_lumens_point_is_full_sphere():
    """A point light spreads its flux over 4*pi steradians."""
    value, approximated = light_build.to_candela(1256.6370614, "lumens")
    check(close(value, 100.0, 1e-4),
          "1256.637 lm over a sphere is 100 cd, got %r" % value)
    check(not approximated, "lumens is an exact conversion")


def test_lumens_spot_uses_the_cone():
    """A spot concentrates the same flux into its cone: 2*pi*(1-cos t)."""
    outer = 30.0
    solid_angle = 2.0 * math.pi * (1.0 - math.cos(math.radians(outer)))
    value, _ = light_build.to_candela(1000.0, "lumens", outer)
    check(close(value, 1000.0 / solid_angle, 1e-6),
          "1000 lm in a 30 deg cone should be %r cd, got %r"
          % (1000.0 / solid_angle, value))

    # The same flux in a narrower cone MUST be brighter; a conversion that
    # ignored the cone (using 4*pi for everything) would return the same
    # number for both and pass a single-value check.
    narrow, _ = light_build.to_candela(1000.0, "lumens", 10.0)
    wide, _ = light_build.to_candela(1000.0, "lumens", 60.0)
    check(narrow > wide * 4.0,
          "a 10 deg cone must be far brighter than a 60 deg one for equal "
          "flux (got %r vs %r); the cone angle is being ignored" % (narrow, wide))
    sphere, _ = light_build.to_candela(1000.0, "lumens")
    check(wide > sphere,
          "a 60 deg cone must beat the full sphere for equal flux "
          "(got %r vs %r)" % (wide, sphere))


def test_ev_is_power_of_two():
    for ev, expected in ((0.0, 1.0), (5.0, 32.0), (-2.0, 0.25)):
        value, approximated = light_build.to_candela(ev, "ev")
        check(close(value, expected), "EV %r -> %r cd, got %r" % (ev, expected, value))
        check(not approximated, "EV has a defined conversion (2^EV)")


def test_unitless_is_flagged_as_approximate():
    value, approximated = light_build.to_candela(10000.0, "unitless")
    check(close(value, 16.0), "10000 unitless is 16 cd by UE's factor, got %r" % value)
    check(approximated,
          "unitless has no photometric meaning and must be flagged approximate")


def test_degenerate_cone_does_not_divide_by_zero():
    value, approximated = light_build.to_candela(1000.0, "lumens", 0.0)
    check(value == value and abs(value) != float("inf"),
          "a zero-width cone must not produce inf/nan, got %r" % value)
    check(approximated, "a degenerate cone conversion must be flagged")


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------

POINT = {
    "type": "point", "intensity": 12.5, "intensity_units": "candelas",
    "color_linear": [1.0, 0.6, 0.3], "cast_shadows": True,
    "attenuation_radius": 6.0, "source_radius": 0.0,
    "temperature_k": 6500.0, "use_temperature": False,
}
SPOT = dict(POINT, type="spot", intensity=40.0,
            inner_cone_angle_deg=15.0, outer_cone_angle_deg=30.0,
            attenuation_radius=10.0)
DIRECTIONAL = {
    "type": "directional", "intensity": 5.0, "intensity_units": "lux",
    "color_linear": [1.0, 0.95, 0.85], "cast_shadows": True,
    "temperature_k": 6500.0, "use_temperature": False,
}


def test_mode_precedes_intensity_everywhere():
    """The ordering rule, on every light kind. See the module docstring."""
    for label, light in (("point", POINT), ("spot", SPOT),
                         ("directional", DIRECTIONAL)):
        plan, _warnings = light_build.plan_light(light, label)
        if not check(plan is not None, "%s produced no plan" % label):
            continue
        paths = paths_of(plan)
        check(light_build.P_INTENSITY_MODE in paths and light_build.P_INTENSITY in paths,
              "%s: plan is missing the intensity properties" % label)
        check(paths.index(light_build.P_INTENSITY_MODE)
              < paths.index(light_build.P_INTENSITY),
              "%s: intensity is written BEFORE the mode; on the directional "
              "component that silently rescales the value (5.0 lux -> 80.0)"
              % label)


def test_point_maps_to_simple_point_without_shadows():
    plan, warnings = light_build.plan_light(POINT, "Light_Point")
    codes = {code for code, _detail in warnings}
    check(plan["component"] == light_build.LIGHT_COMPONENT,
          "point light should use the %r component" % light_build.LIGHT_COMPONENT)
    check(value_at(plan, light_build.P_LIGHT_TYPE) == light_build.LIGHT_TYPE_SIMPLE_POINT,
          "point light should map to SimplePoint")
    check(value_at(plan, light_build.P_INTENSITY_MODE) == light_build.PHOTOMETRIC_CANDELA,
          "local lights only accept Candela/Lumen; the mode must be Candela")
    check(light_build.P_SHADOW_LOCAL not in paths_of(plan),
          "SimplePoint does not support shadows, so the flag must not be "
          "written (it would read back true and do nothing)")
    check("LIGHT_SHADOWS_UNSUPPORTED" in codes,
          "a shadow-casting point light must report the lost shadows, got %r" % codes)
    check(value_at(plan, light_build.P_RADIUS_MODE) == light_build.RADIUS_EXPLICIT,
          "UE's explicit attenuation radius must pin Atom to Explicit mode")
    check(close(value_at(plan, light_build.P_RADIUS), 6.0),
          "the attenuation radius must be UE's value in metres")


def test_spot_maps_to_simple_spot_with_shutters_and_shadows():
    plan, _warnings = light_build.plan_light(SPOT, "Light_Spot")
    check(value_at(plan, light_build.P_LIGHT_TYPE) == light_build.LIGHT_TYPE_SIMPLE_SPOT,
          "spot light should map to SimpleSpot")
    check(value_at(plan, light_build.P_SHUTTERS_ENABLE) is True,
          "the spot cone is the shutters; they must be enabled")
    check(close(value_at(plan, light_build.P_SHUTTERS_INNER), 15.0),
          "inner cone angle should map straight across")
    check(close(value_at(plan, light_build.P_SHUTTERS_OUTER), 30.0),
          "outer cone angle should map straight across")
    check(value_at(plan, light_build.P_SHADOW_LOCAL) is True,
          "SimpleSpot supports shadows, so the flag must be written")
    # Intensity is candelas here, so it must NOT be divided by the cone.
    check(close(value_at(plan, light_build.P_INTENSITY), 40.0),
          "candela intensity must not be re-scaled by the cone")


def test_spot_in_lumens_uses_its_own_cone():
    lumens_spot = dict(SPOT, intensity_units="lumens", intensity=1000.0)
    plan, _warnings = light_build.plan_light(lumens_spot, "Light_Spot_Lumens")
    solid_angle = 2.0 * math.pi * (1.0 - math.cos(math.radians(30.0)))
    check(close(value_at(plan, light_build.P_INTENSITY), 1000.0 / solid_angle, 1e-6),
          "a lumens spot must divide by ITS cone's solid angle")


def test_directional_uses_lux_and_its_own_shadow_path():
    plan, _warnings = light_build.plan_light(DIRECTIONAL, "Light_Directional")
    check(plan["component"] == light_build.DIRECTIONAL_COMPONENT,
          "directional light should use the Directional Light component")
    check(value_at(plan, light_build.P_INTENSITY_MODE) == light_build.PHOTOMETRIC_LUX,
          "UE directional intensity is lux; Atom takes lux directly")
    check(close(value_at(plan, light_build.P_INTENSITY), 5.0),
          "directional intensity must pass through unconverted")
    check(light_build.P_SHADOW_DIRECTIONAL in paths_of(plan),
          "the directional component's shadow path differs from the local one")
    check(light_build.P_SHADOW_LOCAL not in paths_of(plan),
          "the local shadow path is rejected by the directional component "
          "(measured in probe_m5_lights2)")
    check(light_build.P_RADIUS not in paths_of(plan),
          "a directional light has no attenuation radius")


def test_unsupported_type_is_reported_not_silent():
    rect = dict(POINT, type="rect")
    plan, warnings = light_build.plan_light(rect, "Light_Rect")
    check(plan is None, "a rect light has no v1 mapping and must produce no plan")
    check("LIGHT_TYPE_UNSUPPORTED" in {code for code, _d in warnings},
          "an unmapped light type must be reported, never dropped silently")


def test_extras_are_reported():
    warm = dict(POINT, use_temperature=True, temperature_k=3200.0)
    _plan, warnings = light_build.plan_light(warm, "Light_Warm")
    check("LIGHT_TEMPERATURE_DROPPED" in {code for code, _d in warnings},
          "colour temperature is dropped and must say so")

    area = dict(POINT, source_radius=0.25)
    _plan, warnings = light_build.plan_light(area, "Light_Area")
    check("LIGHT_SOURCE_RADIUS_DROPPED" in {code for code, _d in warnings},
          "a non-zero source radius is dropped and must say so")


def test_every_emitted_code_is_in_the_catalogue():
    """Constraint 9: codes are machine-readable and catalogued, not ad-hoc."""
    emitted = set()
    for light in (POINT, SPOT, DIRECTIONAL,
                  dict(POINT, type="rect"),
                  dict(POINT, use_temperature=True),
                  dict(POINT, source_radius=0.25),
                  dict(POINT, intensity_units="unitless"),
                  dict(DIRECTIONAL, intensity_units="ev")):
        _plan, warnings = light_build.plan_light(light, "x")
        emitted.update(code for code, _detail in warnings)
    unknown = sorted(emitted - set(REPORT_CODES))
    check(not unknown, "codes emitted but not catalogued in report.py: %r" % unknown)
    print("  codes exercised: %r" % sorted(emitted))


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        before = len(failures)
        test()
        print("  %s %s" % ("ok  " if len(failures) == before else "FAIL", test.__name__))

    print("")
    if failures:
        print("RESULT: FAIL (%d failure(s))" % len(failures))
        return 1
    print("RESULT: PASS (%d tests)" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
