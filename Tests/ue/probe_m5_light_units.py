"""
probe_m5_light_units.py — M5: what UE's light intensity UNITS actually mean.

The plan calls out that intensity is not a single factor: UE5 lights carry an
`ELightUnits` enum (Unitless / Candelas / Lumens / EV) and Atom's local lights
accept only Candela and Lumen. So the importer must CONVERT, and the
conversion factors must be measured, not recalled.

UE converts the stored intensity when the units property changes (the same
behaviour Atom's "Intensity mode" has). That makes the factors directly
measurable: set a known value in candelas, switch units, read what UE wrote.

Also records, for the same lights:
  * inner/outer cone angle semantics (half-angle from the axis?) by reading
    what UE stores for a known cone;
  * whether a directional light exposes an intensity-units property at all
    (the exporter claims it does not).

Output: Tests/ue/results/probe_m5_light_units.txt
Run:    run_ue_python.bat probe_m5_light_units.py
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m5_light_units.txt"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_M5_UNITS] " + str(msg))


UNIT_NAMES = ["UNITLESS", "CANDELAS", "LUMENS", "EV"]


def units_enum():
    """The ELightUnits enum members that exist in this build."""
    found = {}
    enum_type = getattr(unreal, "LightUnits", None)
    if enum_type is None:
        out("unreal.LightUnits does not exist")
        return found
    for name in UNIT_NAMES:
        member = getattr(enum_type, name, None)
        if member is not None:
            found[name] = member
    return found


def measure(component, label, units, base_units="CANDELAS", base_value=100.0):
    """Set `base_value` in `base_units`, then switch units and read back."""
    out("  --- %s ---" % label)
    if base_units not in units:
        out("    base unit %s unavailable" % base_units)
        return
    component.set_editor_property("intensity_units", units[base_units])
    component.set_editor_property("intensity", base_value)
    stored = component.get_editor_property("intensity")
    out("    base: %.6f %s" % (stored, base_units))
    for name, member in units.items():
        component.set_editor_property("intensity_units", units[base_units])
        component.set_editor_property("intensity", base_value)
        component.set_editor_property("intensity_units", member)
        converted = component.get_editor_property("intensity")
        ratio = (converted / base_value) if base_value else float("nan")
        out("    %-9s -> %.6f   (x %.6f, inverse %.6f)"
            % (name, converted, ratio, (1.0 / ratio) if ratio else float("nan")))


def main():
    units = units_enum()
    out("ELightUnits members present: %r" % sorted(units))

    world = unreal.EditorLevelLibrary.get_editor_world()
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    point = actor_sub.spawn_actor_from_class(
        unreal.PointLight, unreal.Vector(0.0, 0.0, 5000.0))
    spot = actor_sub.spawn_actor_from_class(
        unreal.SpotLight, unreal.Vector(200.0, 0.0, 5000.0))
    directional = actor_sub.spawn_actor_from_class(
        unreal.DirectionalLight, unreal.Vector(400.0, 0.0, 5000.0))

    try:
        out("")
        out("=== intensity unit conversions (UE does the conversion) ===")
        measure(point.point_light_component, "PointLight", units)
        measure(spot.spot_light_component, "SpotLight", units)

        out("")
        out("=== directional light: is there a units property? ===")
        dlc = directional.light_component
        try:
            value = dlc.get_editor_property("intensity_units")
            out("  directional intensity_units = %r" % value)
        except Exception as exc:
            out("  directional has NO intensity_units property (%s)"
                % type(exc).__name__)
        dlc.set_editor_property("intensity", 5.0)
        out("  directional intensity reads %.6f (lux by definition)"
            % dlc.get_editor_property("intensity"))

        out("")
        out("=== cone angle semantics ===")
        slc = spot.spot_light_component
        slc.set_editor_property("inner_cone_angle", 15.0)
        slc.set_editor_property("outer_cone_angle", 30.0)
        out("  wrote inner=15 outer=30, reads inner=%.4f outer=%.4f"
            % (slc.get_editor_property("inner_cone_angle"),
               slc.get_editor_property("outer_cone_angle")))
        # UE clamps the outer cone to 80 degrees; a half-angle interpretation
        # is the only one where 80 is a sane maximum (a full angle would cap
        # the cone at 40 degrees wide, which UE's own gizmo contradicts).
        slc.set_editor_property("outer_cone_angle", 100.0)
        out("  wrote outer=100, reads outer=%.4f (UE clamp)"
            % slc.get_editor_property("outer_cone_angle"))

        out("")
        out("=== attenuation radius + source radius round-trip ===")
        plc = point.point_light_component
        plc.set_editor_property("attenuation_radius", 600.0)
        plc.set_editor_property("source_radius", 12.0)
        out("  point attenuation_radius=%.3f source_radius=%.3f"
            % (plc.get_editor_property("attenuation_radius"),
               plc.get_editor_property("source_radius")))
    finally:
        for actor in (point, spot, directional):
            actor_sub.destroy_actor(actor)
        del world


status = "PASS"
try:
    main()
except Exception:
    out("FATAL: " + traceback.format_exc())
    status = "FAIL"

_lines.append("RESULT: " + status)
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w") as handle:
    handle.write("\n".join(_lines) + "\n")
print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)
