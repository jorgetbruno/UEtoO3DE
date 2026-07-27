"""
test_env_build.py — the M6 environment mapping rules, offline.

M6 is mostly approximations, so this is where each one is pinned. The bar is
the same as M5's: assert the RULE (and the property that makes it defensible),
not the number this code happened to produce.

Run:  python Tests/m6/test_env_build.py
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts"))

from ueimporter import env_build  # noqa: E402
from ueimporter.report import CODES as REPORT_CODES  # noqa: E402

failures = []


def fail(message):
    failures.append(str(message))
    print("FAIL: " + str(message))


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def components_of(plans):
    return [plan["component"] for plan in plans]


def value_at(plans, component, wanted):
    for plan in plans:
        if plan["component"] != component:
            continue
        for path, _kind, value in plan["properties"]:
            if path == wanted:
                return value
    return None


def paths_of(plans, component):
    return [path for plan in plans if plan["component"] == component
            for path, _kind, _value in plan["properties"]]


SKYLIGHT = {"type": "skylight", "intensity": 0.8, "color_linear": [0.85, 0.9, 1.0],
            "real_time_capture": True, "source_type": "captured_scene",
            "cubemap_ue_path": None, "lower_hemisphere_is_black": True}
FOG = {"type": "fog", "fog_density": 0.05, "fog_height_falloff": 0.2,
       "fog_inscattering_color_linear": [0.4, 0.5, 0.7], "start_distance": 5.0,
       "fog_cutoff_distance": 0.0, "fog_max_opacity": 0.9, "fog_height_m": 0.0}
PPV = {"type": "post_process", "priority": 2.0, "blend_weight": 1.0,
       "unbound": True, "enabled": True,
       "overrides": {"auto_exposure_bias": 1.75, "bloom_intensity": 0.9,
                     "bloom_threshold": 0.8, "vignette_intensity": 0.55}}
ATMOSPHERE = {"type": "sky_atmosphere", "ground_albedo_linear": [0.4, 0.4, 0.4],
              "rayleigh_scattering_scale": 0.0331,
              "mie_scattering_scale": 0.003996, "multi_scattering_factor": 1.0}


# ---------------------------------------------------------------------------

def test_skylight_becomes_a_physical_sky_and_says_so():
    plans, warnings = env_build.plan_environment(SKYLIGHT, "Atmo_SkyLight")
    codes = {code for code, _detail in warnings}
    check(components_of(plans) == [env_build.PHYSICAL_SKY],
          "a skylight should author exactly one Physical Sky, got %r"
          % components_of(plans))
    check("ENV_SKYLIGHT_APPROX" in codes,
          "swapping IBL for a Physical Sky is an approximation and must be "
          "reported, got %r" % codes)


def test_sky_intensity_mode_is_written_before_the_intensity():
    """Otherwise the intensity is silently discarded.

    Measured in `Tests/o3de/probe_m6_sky_intensity.py`: writing "Sky
    Intensity" on a fresh Physical Sky without writing "Intensity Mode" first
    stores 1.0 no matter what value is sent, and the set call still reports
    success. The sky then lights the level at a brightness nobody chose.
    """
    plans, _warnings = env_build.plan_environment(SKYLIGHT, "Atmo_SkyLight")
    paths = paths_of(plans, env_build.PHYSICAL_SKY)
    check(env_build.P_SKY_INTENSITY_MODE in paths,
          "the intensity mode must be written even though it is already the "
          "component default; without it the intensity write is discarded")
    check(paths.index(env_build.P_SKY_INTENSITY_MODE)
          < paths.index(env_build.P_SKY_INTENSITY),
          "intensity is written before the mode, so it will be discarded")

    intensity = value_at(plans, env_build.PHYSICAL_SKY, env_build.P_SKY_INTENSITY)
    check(abs(intensity - SKYLIGHT["intensity"] * env_build.ATOM_DEFAULT_SKY_INTENSITY) < 1e-6,
          "UE's unitless skylight intensity should scale Atom's default sky "
          "intensity, got %r" % intensity)

    # A brighter UE skylight must give a brighter Atom sky.
    brighter, _w = env_build.plan_environment(dict(SKYLIGHT, intensity=2.0), "s")
    check(value_at(brighter, env_build.PHYSICAL_SKY, env_build.P_SKY_INTENSITY)
          > intensity, "a brighter UE skylight must map to a brighter sky")


def test_sky_is_authored_only_once():
    """Two Physical Sky components in one level fight over the same sky."""
    plans, warnings = env_build.plan_environment(
        ATMOSPHERE, "Atmo_SkyAtmosphere", sky_already_authored=True)
    check(plans == [], "a second sky actor must author nothing, got %r"
                       % components_of(plans))
    check("ENV_SKY_DUPLICATE" in {code for code, _d in warnings},
          "skipping the duplicate sky must be reported, not silent")

    # ... but the FIRST one still authors.
    first, _warnings = env_build.plan_environment(ATMOSPHERE, "Atmo_SkyAtmosphere")
    check(components_of(first) == [env_build.PHYSICAL_SKY],
          "the first sky actor must still author a Physical Sky")


def test_fog_needs_a_postfx_layer_with_it():
    """Atom's post-process components are inert without a PostFX Layer."""
    plans, warnings = env_build.plan_environment(FOG, "Atmo_HeightFog")
    check(env_build.POSTFX_LAYER in components_of(plans),
          "deferred fog does nothing without a PostFX Layer on the entity")
    check(env_build.DEFERRED_FOG in components_of(plans), "no Deferred Fog authored")
    check(components_of(plans).index(env_build.POSTFX_LAYER)
          < components_of(plans).index(env_build.DEFERRED_FOG),
          "the layer should be added before its member component")
    check("ENV_FOG_APPROX" in {code for code, _d in warnings},
          "the fog density models differ; that must be reported")
    check(value_at(plans, env_build.DEFERRED_FOG, env_build.P_FOG_ENABLE) is True,
          "the fog's own enable flag must be written; a disabled Deferred Fog "
          "serializes looking configured while rendering nothing")


def test_fog_density_is_monotonic_and_clamped():
    """The exact curve is an approximation; these properties are not."""
    denser = env_build.fog_density_to_atom(0.08)
    thinner = env_build.fog_density_to_atom(0.02)
    check(denser > thinner,
          "denser UE fog must map to denser Atom fog (%r vs %r)" % (denser, thinner))
    check(env_build.fog_density_to_atom(0.0) == 0.0,
          "zero density must stay zero")
    check(env_build.fog_density_to_atom(1000.0) <= env_build.FOG_DENSITY_MAX,
          "density must clamp rather than run away")
    check(abs(env_build.fog_density_to_atom(env_build.FOG_DENSITY_UE_DEFAULT)
              - env_build.FOG_DENSITY_ATOM_DEFAULT) < 1e-6,
          "UE's default density should land on Atom's default density")


def test_fog_ramp_is_ordered():
    plans, _warnings = env_build.plan_environment(FOG, "Atmo_HeightFog")
    start = value_at(plans, env_build.DEFERRED_FOG, env_build.P_FOG_START)
    end = value_at(plans, env_build.DEFERRED_FOG, env_build.P_FOG_END)
    check(start < end, "fog start (%r) must be nearer than fog end (%r)" % (start, end))
    check(abs(start - FOG["start_distance"]) < 1e-6,
          "UE's start distance should carry across unchanged (metres already)")

    # A UE cutoff distance, when set, is the honest end of the ramp.
    with_cutoff = dict(FOG, fog_cutoff_distance=120.0)
    plans, _warnings = env_build.plan_environment(with_cutoff, "fog")
    check(abs(value_at(plans, env_build.DEFERRED_FOG, env_build.P_FOG_END) - 120.0) < 1e-6,
          "a UE fog cutoff distance should become Atom's fog end distance")


def test_postprocess_authors_only_what_was_overridden():
    plans, _warnings = env_build.plan_environment(PPV, "PPV_01")
    check(env_build.POSTFX_LAYER in components_of(plans), "no PostFX Layer authored")
    check(env_build.EXPOSURE_CONTROL in components_of(plans),
          "auto_exposure_bias was overridden; Exposure Control should be authored")
    check(env_build.BLOOM in components_of(plans),
          "bloom settings were overridden; Bloom should be authored")
    check(abs(value_at(plans, env_build.BLOOM, env_build.P_BLOOM_INTENSITY) - 0.9) < 1e-6,
          "bloom intensity should carry across")
    check(value_at(plans, env_build.BLOOM, env_build.P_BLOOM_ENABLE) is True,
          "Bloom's enable flag must be written explicitly")

    # Nothing overridden -> a layer, but no exposure/bloom components invented.
    bare = dict(PPV, overrides={})
    plans, _warnings = env_build.plan_environment(bare, "PPV_bare")
    check(components_of(plans) == [env_build.POSTFX_LAYER],
          "with no overrides only the layer should be authored, got %r"
          % components_of(plans))


def test_exposure_type_follows_the_overridden_settings():
    manual = dict(PPV, overrides={"auto_exposure_bias": 1.5})
    plans, _warnings = env_build.plan_environment(manual, "ppv")
    check(value_at(plans, env_build.EXPOSURE_CONTROL, env_build.P_EXPOSURE_TYPE)
          == env_build.EXPOSURE_MANUAL,
          "a bare exposure compensation is manual exposure")

    adaptive = dict(PPV, overrides={"auto_exposure_bias": 1.5,
                                    "auto_exposure_speed_up": 2.0})
    plans, _warnings = env_build.plan_environment(adaptive, "ppv")
    check(value_at(plans, env_build.EXPOSURE_CONTROL, env_build.P_EXPOSURE_TYPE)
          == env_build.EXPOSURE_EYE_ADAPTATION,
          "eye-adaptation settings mean eye-adaptation exposure")


def test_ue_bloom_threshold_sentinel():
    """UE's -1 means 'bloom everything'; Atom has no sentinel."""
    sentinel = dict(PPV, overrides={"bloom_intensity": 0.7, "bloom_threshold": -1.0})
    plans, warnings = env_build.plan_environment(sentinel, "ppv")
    threshold = value_at(plans, env_build.BLOOM, env_build.P_BLOOM_THRESHOLD)
    check(threshold == 0.0,
          "UE's -1 threshold must not be written literally, got %r" % threshold)
    check("ENV_BLOOM_THRESHOLD_APPROX" in {code for code, _d in warnings},
          "substituting the sentinel must be reported")


def test_bounded_and_disabled_volumes():
    bounded = dict(PPV, unbound=False, extents_m=[10.0, 10.0, 5.0])
    _plans, warnings = env_build.plan_environment(bounded, "ppv")
    check("ENV_POSTPROCESS_UNBOUNDED" in {code for code, _d in warnings},
          "a bounded UE volume becoming level-wide is a real behaviour change "
          "and must be reported")

    disabled = dict(PPV, enabled=False)
    plans, warnings = env_build.plan_environment(disabled, "ppv")
    check(plans == [], "a disabled volume must author nothing")
    check("ENV_POSTPROCESS_DISABLED" in {code for code, _d in warnings},
          "skipping a disabled volume must be reported")


def test_unknown_type_is_reported_not_silent():
    plans, warnings = env_build.plan_environment({"type": "reflection_capture"}, "rc")
    check(plans == [], "an unmapped environment type must author nothing")
    check("ENV_TYPE_UNSUPPORTED" in {code for code, _d in warnings},
          "an unmapped environment type must be reported")


def test_every_emitted_code_is_in_the_catalogue():
    emitted = set()
    for block in (SKYLIGHT, FOG, PPV, ATMOSPHERE,
                  dict(PPV, unbound=False), dict(PPV, enabled=False),
                  dict(PPV, overrides={"bloom_threshold": -1.0}),
                  {"type": "reflection_capture"}):
        _plans, warnings = env_build.plan_environment(block, "x")
        emitted.update(code for code, _detail in warnings)
    _plans, warnings = env_build.plan_environment(
        ATMOSPHERE, "x", sky_already_authored=True)
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
