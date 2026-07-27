"""
add_m6_env.py — give the fixture's environment actors real, authored settings.

Fixture_01 has a SkyLight, an ExponentialHeightFog and a PostProcessVolume, but
the PPV overrides nothing: UE only *applies* a post-process setting when its
`override_*` flag is set, and the exporter carries only overridden settings for
that reason (a non-overridden value is a UE default, not an artist's intent).
With no overrides the whole override path would be untested, and the M6
acceptance would be asserting on an empty dictionary.

Sets, on the PPV:
    auto_exposure_bias        1.75   (mapped -> Atom Exposure Control)
    bloom_intensity           0.9    (mapped -> Atom Bloom)
    bloom_threshold           0.8    (mapped -> Atom Bloom)
    vignette_intensity        0.55   (NOT mapped in M6 -> must be reported)

The unmapped one is deliberate: it is the canary for
`ENV_POSTPROCESS_UNMAPPED`, and without it a regression that silently dropped
unmapped settings would look identical to correct behaviour.

Also adds a SkyAtmosphere actor (Fixture_01 has none), so the sky path has
something to convert.

Idempotent. `build_fixture_01.py` carries the same values for a from-scratch
build; keep the two in sync by hand.

Run:  run_ue_python.bat add_m6_env.py
"""

import traceback

import unreal

MAP_PATH = "/Game/Maps/Fixture_01"
RESULT_TAG = "ADD_M6_ENV"


def log(message):
    unreal.log("[" + RESULT_TAG + "] " + str(message))


def configure_ppv(actor):
    settings = actor.get_editor_property("settings")
    pairs = (
        ("auto_exposure_bias", "override_auto_exposure_bias", 1.75),
        ("bloom_intensity", "override_bloom_intensity", 0.9),
        ("bloom_threshold", "override_bloom_threshold", 0.8),
        ("vignette_intensity", "override_vignette_intensity", 0.55),
    )
    for name, flag, value in pairs:
        settings.set_editor_property(flag, True)
        settings.set_editor_property(name, value)
    actor.set_editor_property("settings", settings)
    actor.set_editor_property("priority", 2.0)

    read_back = actor.get_editor_property("settings")
    for name, flag, value in pairs:
        got = read_back.get_editor_property(name)
        overridden = read_back.get_editor_property(flag)
        log("  %-30s = %-8s override=%s" % (name, round(float(got), 4), overridden))
        if not overridden:
            raise RuntimeError("override flag did not stick for " + name)


def configure_fog(actor):
    component = actor.component
    component.set_editor_property("fog_density", 0.05)
    component.set_editor_property("fog_height_falloff", 0.2)
    component.set_editor_property("start_distance", 500.0)
    component.set_editor_property("fog_max_opacity", 0.9)
    component.set_editor_property(
        "fog_inscattering_luminance", unreal.LinearColor(0.4, 0.5, 0.7, 1.0))
    log("  fog density=%.4f falloff=%.4f start=%.1f cm"
        % (component.get_editor_property("fog_density"),
           component.get_editor_property("fog_height_falloff"),
           component.get_editor_property("start_distance")))


def configure_skylight(actor):
    component = actor.light_component
    component.set_intensity(0.8)
    component.set_editor_property("real_time_capture", True)
    # SkyLightComponent::SetLightColor takes no sRGB flag, unlike the local
    # light components' setter (measured; the extra argument is a TypeError).
    component.set_light_color(unreal.LinearColor(0.85, 0.9, 1.0, 1.0))
    log("  skylight intensity=%.3f real_time_capture=%s source=%s"
        % (component.get_editor_property("intensity"),
           component.get_editor_property("real_time_capture"),
           component.get_editor_property("source_type")))


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load level " + MAP_PATH)

    found = {}
    for actor in actor_sub.get_all_level_actors():
        label = actor.get_actor_label()
        found[label] = actor
        if label == "Atmo_SkyAtmosphere":
            actor_sub.destroy_actor(actor)

    for label, configure in (("PPV_01", configure_ppv),
                             ("Atmo_HeightFog", configure_fog),
                             ("Atmo_SkyLight", configure_skylight)):
        actor = found.get(label)
        if actor is None:
            raise RuntimeError("fixture is missing " + label)
        log(label + ":")
        configure(actor)

    atmosphere = actor_sub.spawn_actor_from_class(
        unreal.SkyAtmosphere, unreal.Vector(0.0, 0.0, 0.0))
    atmosphere.set_actor_label("Atmo_SkyAtmosphere")
    log("spawned Atmo_SkyAtmosphere")

    if not level_sub.save_current_level():
        raise RuntimeError("failed to save level " + MAP_PATH)
    log("level saved")


try:
    main()
except Exception:
    unreal.log_error("[" + RESULT_TAG + "] FAILED")
    unreal.log_error(traceback.format_exc())
    print("RESULT: FAIL")
    raise

print("RESULT: PASS")
