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


def test_implausible_exposure_bias_is_clamped_and_reported():
    """A ×4096 exposure passed every test in this file until it shipped.

    MEASURED on Docks/VOL4_Albert `Demonstration` (905 entities): two UE
    volumes carried auto_exposure_bias 12.0 and 9.5, both were copied straight
    into Atom's `Manual Compensation` -- a property in EV STOPS -- and the
    imported level rendered PURE WHITE. Every other check passed: 905
    entities, 693/693 colliders verified, 0 Asset Processor errors.

    Nothing here asserted MAGNITUDE, and every fixture used a plausible value
    (1.5, 1.75), so a four-thousand-fold error was invisible. That is the gap
    this test exists to close -- the bound matters more than the exact number.
    """
    blown = dict(PPV, overrides={"auto_exposure_bias": 12.0})
    plans, warnings = env_build.plan_environment(blown, "ppv")
    check(env_build.EXPOSURE_CONTROL not in components_of(plans),
          "an out-of-range bias with NO eye adaptation must author NO "
          "Exposure Control at all. Clamping it to the range edge was tried "
          "first and MEASURED to fail: +5 EV manual still clipped 51%% of a "
          "real level's frame to white -- without auto-exposure there is "
          "nothing for the bias to offset, so no in-range substitute exists")
    check(any(code == "ENV_VALUE_IMPLAUSIBLE" for code, _ in warnings),
          "dropping the setting must be REPORTED, never silent")
    check(any("12" in detail for _code, detail in warnings),
          "the report must name the ORIGINAL value so it can be re-tuned")

    # WITH eye adaptation the compensation offsets a normalised scene --
    # close to UE's own semantics -- so there clamping is a usable
    # approximation and the component is still authored.
    adaptive_blown = dict(PPV, overrides={"auto_exposure_bias": 12.0,
                                          "auto_exposure_speed_up": 2.0})
    plans, warnings = env_build.plan_environment(adaptive_blown, "ppv")
    compensation = value_at(plans, env_build.EXPOSURE_CONTROL,
                            env_build.P_EXPOSURE_COMPENSATION)
    low, high = env_build.EXPOSURE_EV_RANGE
    check(compensation is not None and low <= compensation <= high,
          "with eye adaptation present the bias is clamped into %r, not "
          "dropped; got %r" % (env_build.EXPOSURE_EV_RANGE, compensation))

    # The other direction: a plausible value must pass through untouched, or
    # the guard would quietly flatten every artist's grading.
    sane = dict(PPV, overrides={"auto_exposure_bias": 1.75})
    plans, warnings = env_build.plan_environment(sane, "ppv")
    check(value_at(plans, env_build.EXPOSURE_CONTROL,
                   env_build.P_EXPOSURE_COMPENSATION) == 1.75,
          "a plausible compensation must be imported EXACTLY")
    check(not any(code == "ENV_VALUE_IMPLAUSIBLE" for code, _ in warnings),
          "a plausible value must not be reported as implausible")

    negative = dict(PPV, overrides={"auto_exposure_bias": -30.0})
    plans, warnings = env_build.plan_environment(negative, "ppv")
    check(env_build.EXPOSURE_CONTROL not in components_of(plans),
          "an implausibly DARK bias is dropped by the same rule as a bright "
          "one -- a level that imports pure black is no better than pure "
          "white, and -30 EV has no translatable manual value either")
    check(any(code == "ENV_VALUE_IMPLAUSIBLE" for code, _ in warnings),
          "the dark drop must be reported too")


def test_exposure_brightness_limits_are_luminance_not_ev():
    """UE's min/max BRIGHTNESS is a luminance; Atom's clamp is in EV.

    The second unit mismatch in the same block, found by auditing after the
    first. Unlike the bias, this conversion IS derivable -- EV is the base-2
    log of a luminance ratio -- so it is converted rather than clamped.
    Copying 0.03 verbatim would mean "EV 0.03" where "EV -5.06" was intended.
    """
    volume = dict(PPV, overrides={"auto_exposure_min_brightness": 0.03,
                                  "auto_exposure_max_brightness": 8.0})
    plans, warnings = env_build.plan_environment(volume, "ppv")
    minimum = value_at(plans, env_build.EXPOSURE_CONTROL, env_build.P_EXPOSURE_MIN)
    maximum = value_at(plans, env_build.EXPOSURE_CONTROL, env_build.P_EXPOSURE_MAX)
    check(abs(maximum - 3.0) < 1e-6,
          "log2(8) is 3 EV; got %r" % maximum)
    check(abs(minimum - (-5.058893689053568)) < 1e-6,
          "log2(0.03) is about -5.06 EV; got %r" % minimum)
    check(minimum != 0.03 and maximum != 8.0,
          "the raw luminances must NOT reach the EV properties")
    check(any(code == "ENV_EXPOSURE_LIMIT_CONVERTED" for code, _ in warnings),
          "the conversion must be reported")

    # log2 is undefined at zero, and UE writes 0 to mean "no lower clamp".
    zero = dict(PPV, overrides={"auto_exposure_min_brightness": 0.0})
    plans, warnings = env_build.plan_environment(zero, "ppv")
    floor = value_at(plans, env_build.EXPOSURE_CONTROL, env_build.P_EXPOSURE_MIN)
    check(floor == env_build.EXPOSURE_LIMIT_EV_RANGE[0],
          "a zero brightness has no logarithm and must fall to the EV floor")
    check(any(code == "ENV_EXPOSURE_LIMIT_APPROX" for code, _ in warnings),
          "the floor substitution must be reported")


def test_bloom_intensity_is_bounded():
    """Same class as the exposure bias: a scalar whose meaning may not carry."""
    plans, warnings = env_build.plan_environment(
        dict(PPV, overrides={"bloom_intensity": 500.0}), "ppv")
    low, high = env_build.BLOOM_INTENSITY_RANGE
    check(low <= value_at(plans, env_build.BLOOM,
                          env_build.P_BLOOM_INTENSITY) <= high,
          "an absurd bloom intensity must be clamped")
    check(any(code == "ENV_VALUE_IMPLAUSIBLE" for code, _ in warnings),
          "clamped bloom must be reported")


def test_only_one_level_wide_volume_authors_exposure():
    """Exposure is global. Two enabled Exposure Controls is always a bug.

    MEASURED on `Demonstration`: two DIFFERENT UE volumes, both named
    `PostProcessVolume2`, both unbound, both priority 0, biases 12.0 and 9.5.
    Both were authored, so the level carried two enabled Exposure Controls.
    UE resolves overlapping unbound volumes by priority into ONE result;
    authoring both is not a faithful import of that, it is a stack.
    """
    volume = dict(PPV, overrides={"auto_exposure_bias": 1.5,
                                  "bloom_intensity": 0.5})

    first, _warnings = env_build.plan_environment(volume, "ppv")
    check(env_build.EXPOSURE_CONTROL in components_of(first),
          "the first level-wide volume must author exposure")

    second, warnings = env_build.plan_environment(
        volume, "ppv2", exposure_already_authored=True)
    check(env_build.EXPOSURE_CONTROL not in components_of(second),
          "a second level-wide volume must NOT author another Exposure "
          "Control; got %r" % components_of(second))
    check(any(code == "ENV_EXPOSURE_ALREADY_AUTHORED" for code, _ in warnings),
          "dropping the second volume's exposure must be reported, not silent")
    check(env_build.POSTFX_LAYER in components_of(second),
          "the layer itself must still be authored -- only EXPOSURE is "
          "global; the volume's other settings are its own")
    check(env_build.BLOOM in components_of(second),
          "bloom is per-layer and must survive the exposure de-duplication")


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
