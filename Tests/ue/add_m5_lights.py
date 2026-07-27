"""
add_m5_lights.py — add the M5 intensity-unit lights to the EXISTING fixture.

Fixture_01 already covers the three light TYPES (point/spot/directional) but
only two intensity UNITS (candelas, lux). The plan's M5 acceptance is "per
light type and per intensity-unit mode", so this adds the rest of the enum:
lumens (point AND spot -- the spot divides by its own cone, the point by the
full sphere), EV, and unitless.

Intensities are chosen so the expected candela values are exact and readable,
which is what makes the acceptance assertions meaningful rather than
self-fulfilling:

    Light_Point_Lumens    1256.6370614 lm / (4*pi)          = 100 cd
    Light_Spot_Lumens     1000 lm / (2*pi*(1-cos 20deg))    = 2639.44... cd
    Light_Point_EV        2^5                               =  32 cd
    Light_Point_Unitless  10000 * 16/10000                  =  16 cd

`build_fixture_01.py` carries the same lights for a from-scratch build; keep
the two in sync by hand (it cannot re-run against an existing project -- see
rebuild_letter_f.py).

Idempotent: each light is destroyed and respawned by label.

Run:  run_ue_python.bat add_m5_lights.py
"""

import math
import traceback

import unreal

MAP_PATH = "/Game/Maps/Fixture_01"
RESULT_TAG = "ADD_M5_LIGHTS"

LUMENS_FOR_100_CANDELA = 4.0 * math.pi * 100.0   # 1256.6370614...


def log(message):
    unreal.log("[" + RESULT_TAG + "] " + str(message))


def spawn_point(actor_sub, label, location, intensity, units,
                color=(1.0, 1.0, 1.0), attenuation=500.0, cast_shadows=True):
    actor = actor_sub.spawn_actor_from_class(unreal.PointLight, unreal.Vector(*location))
    actor.set_actor_label(label)
    component = actor.point_light_component
    component.set_editor_property("intensity_units", units)
    component.set_intensity(intensity)
    component.set_light_color(unreal.LinearColor(color[0], color[1], color[2], 1.0), True)
    component.set_editor_property("attenuation_radius", attenuation)
    component.set_editor_property("cast_shadows", cast_shadows)
    return actor


def spawn_spot(actor_sub, label, location, rotation, intensity, units,
               inner, outer, color=(1.0, 1.0, 1.0), attenuation=800.0):
    actor = actor_sub.spawn_actor_from_class(
        unreal.SpotLight, unreal.Vector(*location), unreal.Rotator(*rotation))
    actor.set_actor_label(label)
    component = actor.spot_light_component
    component.set_editor_property("intensity_units", units)
    component.set_intensity(intensity)
    component.set_light_color(unreal.LinearColor(color[0], color[1], color[2], 1.0), True)
    component.set_editor_property("inner_cone_angle", inner)
    component.set_editor_property("outer_cone_angle", outer)
    component.set_editor_property("attenuation_radius", attenuation)
    return actor


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load level " + MAP_PATH)

    units = unreal.LightUnits
    wanted = {"Light_Point_Lumens", "Light_Spot_Lumens",
              "Light_Point_EV", "Light_Point_Unitless"}
    for actor in actor_sub.get_all_level_actors():
        if actor.get_actor_label() in wanted:
            actor_sub.destroy_actor(actor)

    spawn_point(actor_sub, "Light_Point_Lumens", (-300.0, 600.0, 300.0),
                LUMENS_FOR_100_CANDELA, units.LUMENS, color=(0.9, 1.0, 0.9))
    spawn_spot(actor_sub, "Light_Spot_Lumens", (-600.0, 600.0, 400.0),
               (-60.0, 0.0, 0.0), 1000.0, units.LUMENS, 10.0, 20.0,
               color=(1.0, 0.9, 0.8))
    spawn_point(actor_sub, "Light_Point_EV", (-900.0, 600.0, 300.0),
                5.0, units.EV, color=(0.8, 0.8, 1.0))
    # Unitless has no photometric meaning: the importer must approximate it
    # and SAY so (LIGHT_INTENSITY_APPROX).
    spawn_point(actor_sub, "Light_Point_Unitless", (-1200.0, 600.0, 300.0),
                10000.0, units.UNITLESS, color=(1.0, 0.8, 0.8))

    for actor in actor_sub.get_all_level_actors():
        label = actor.get_actor_label()
        if label in wanted:
            component = actor.get_component_by_class(unreal.LightComponentBase)
            log("  %-22s intensity=%.6f units=%s"
                % (label, component.get_editor_property("intensity"),
                   component.get_editor_property("intensity_units")))

    if not level_sub.save_current_level():
        raise RuntimeError("failed to save level " + MAP_PATH)
    log("level saved with %d M5 lights added" % len(wanted))


try:
    main()
except Exception:
    unreal.log_error("[" + RESULT_TAG + "] FAILED")
    unreal.log_error(traceback.format_exc())
    print("RESULT: FAIL")
    raise

print("RESULT: PASS")
