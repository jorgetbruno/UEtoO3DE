"""
probe_m6_env.py — M6: which UE environment properties actually exist.

UE Python hides readable UPROPERTYs from `dir()`, so availability can never be
probed with `hasattr` (MAPPING.md, learned in M1). The only reliable test is to
ask for the property and see whether it raises. This spawns one actor of each
environment class in a scratch level and reports, per candidate property, the
value or the failure -- so the exporter is written against what UE has rather
than against what its documentation implies.

Covers: SkyLight, ExponentialHeightFog, SkyAtmosphere, PostProcessVolume
(whose settings live in a nested FPostProcessSettings struct, probed
separately).

Output: Tests/ue/results/probe_m6_env.txt
Run:    run_ue_python.bat probe_m6_env.py
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m6_env.txt"

_lines = []


def out(msg=""):
    _lines.append(str(msg))


def probe(obj, label, names):
    out("  --- %s (%s) ---" % (label, type(obj).__name__))
    for name in names:
        try:
            value = obj.get_editor_property(name)
            out("    %-34s = %r" % (name, value))
        except Exception as exc:
            out("    %-34s   MISSING (%s)" % (name, type(exc).__name__))


SKYLIGHT = ["intensity", "light_color", "real_time_capture", "source_type",
            "cubemap", "cubemap_resolution", "sky_distance_threshold",
            "cast_shadows", "volumetric_scattering_intensity", "lower_hemisphere_is_black"]

FOG = ["fog_density", "fog_height_falloff", "fog_inscattering_color",
       "fog_inscattering_luminance", "start_distance", "fog_max_opacity",
       "fog_cutoff_distance", "directional_inscattering_color",
       "directional_inscattering_exponent", "directional_inscattering_start_distance",
       "volumetric_fog", "volumetric_fog_scattering_distribution",
       "volumetric_fog_albedo", "volumetric_fog_extinction_scale",
       "volumetric_fog_distance", "second_fog_data",
       "sky_atmosphere_ambient_contribution_color_scale"]

ATMOSPHERE = ["multi_scattering_factor", "rayleigh_scattering_scale",
              "rayleigh_scattering", "mie_scattering_scale", "mie_absorption_scale",
              "ground_albedo", "bottom_radius", "atmosphere_height",
              "transform_mode", "aerial_perspective_view_distance_scale"]

PPV = ["priority", "blend_radius", "blend_weight", "enabled", "unbound", "settings"]

PP_SETTINGS = [
    "auto_exposure_method", "auto_exposure_bias", "auto_exposure_min_brightness",
    "auto_exposure_max_brightness", "auto_exposure_speed_up", "auto_exposure_speed_down",
    "override_auto_exposure_bias", "override_auto_exposure_method",
    "bloom_intensity", "bloom_threshold", "bloom_method",
    "override_bloom_intensity", "override_bloom_threshold",
    "depth_of_field_focal_distance", "depth_of_field_fstop",
    "override_depth_of_field_focal_distance",
    "color_saturation", "color_contrast", "color_gamma", "film_slope",
    "vignette_intensity", "override_vignette_intensity",
    "motion_blur_amount", "ambient_occlusion_intensity",
]


def main():
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    spawned = []

    def spawn(cls, location):
        actor = actor_sub.spawn_actor_from_class(cls, unreal.Vector(*location))
        spawned.append(actor)
        return actor

    try:
        out("=== SkyLight ===")
        sky = spawn(unreal.SkyLight, (0.0, 0.0, 8000.0))
        probe(sky.light_component, "SkyLightComponent", SKYLIGHT)

        out("")
        out("=== ExponentialHeightFog ===")
        fog = spawn(unreal.ExponentialHeightFog, (200.0, 0.0, 8000.0))
        probe(fog.component, "ExponentialHeightFogComponent", FOG)

        out("")
        out("=== SkyAtmosphere ===")
        atmosphere_class = getattr(unreal, "SkyAtmosphere", None)
        if atmosphere_class is None:
            out("  unreal.SkyAtmosphere does not exist")
        else:
            atmosphere = spawn(atmosphere_class, (400.0, 0.0, 8000.0))
            component = atmosphere.get_component_by_class(
                unreal.SkyAtmosphereComponent)
            probe(component, "SkyAtmosphereComponent", ATMOSPHERE)

        out("")
        out("=== PostProcessVolume ===")
        ppv = spawn(unreal.PostProcessVolume, (600.0, 0.0, 8000.0))
        probe(ppv, "PostProcessVolume", PPV)
        try:
            settings = ppv.get_editor_property("settings")
            out("")
            out("  settings struct type: %s" % type(settings).__name__)
            probe(settings, "FPostProcessSettings", PP_SETTINGS)
        except Exception as exc:
            out("  settings unreadable: %r" % exc)

        out("")
        out("=== PostProcessVolume brush extents (for bounded volumes) ===")
        for name in ("brush_component", "bounds", "get_actor_bounds"):
            try:
                value = getattr(ppv, name, None)
                if callable(value):
                    out("    %-20s callable -> %r" % (name, value(False)))
                else:
                    out("    %-20s = %r" % (name, value))
            except Exception as exc:
                out("    %-20s raised %s" % (name, type(exc).__name__))
    finally:
        for actor in spawned:
            try:
                actor_sub.destroy_actor(actor)
            except Exception:
                pass


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
