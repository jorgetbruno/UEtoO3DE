"""
env_build.py — UE environment actors → Atom (plan M6).

Same split as `light_build`: `plan_environment` is PURE (manifest block in,
ordered component plan out, no editor), `author_environment` is the thin half
that talks to `azlmbr`. The conversions here are approximations far more often
than the light ones were, so the pure half is where each one is pinned by a
test instead of by a screenshot.

--------------------------------------------------------------------------
The mappings, and why each is what it is
--------------------------------------------------------------------------
**SkyLight → Physical Sky (+ a reported approximation).** UE's SkyLight is
image-based lighting from a captured scene or a specified cubemap. Atom's
equivalent, `Global Skylight (IBL)`, needs *two image assets* (diffuse and
specular irradiance) and does nothing without them. A UE real-time-capture
skylight has no such asset to export -- the capture only exists inside UE's
renderer -- so authoring a Global Skylight would produce a component that
looks configured and lights nothing. `Physical Sky` is authored instead: it
is a real sky that actually lights the scene, which is what the milestone's
goal ("imported levels aren't lit in a black void") is asking for.
`ENV_SKYLIGHT_APPROX` records the swap every time.

**SkyAtmosphere → Physical Sky as well**, and the two are de-duplicated by
the caller: UE levels usually carry both a SkyLight and a SkyAtmosphere, and
two Physical Sky components in one level fight over the same sky.

**ExponentialHeightFog → PostFX Layer + Deferred Fog.** The density models
are different functions -- UE's is exponential in height with a falloff
exponent, Atom's is a start/end distance ramp with a height band -- so the
mapping is documented, approximate, and reported (`ENV_FOG_APPROX`), never
presented as exact.

**PostProcessVolume → PostFX Layer + Exposure Control + Bloom**, for the
subset that maps. UE applies a setting only when its `override_*` flag is
set, and the exporter carries only overridden settings, so anything present
here was chosen by a person. Settings with no Atom equivalent are reported by
the exporter (`ENV_POSTPROCESS_UNMAPPED`) rather than dropped quietly.

Atom's post-process components each carry `Overrides|... Override` weights AND
an `Enable...` flag; a component whose enable flag is false serializes into
the prefab looking configured while doing nothing, so the enable flag is
always written explicitly.

--------------------------------------------------------------------------
A NUMBER THAT CROSSES ENGINES IS NOT THE SAME NUMBER
--------------------------------------------------------------------------
Measured on a real level (Docks/VOL4_Albert `Demonstration`, 905 entities):
**the imported scene rendered pure white and every existing test passed.**
Two UE post-process volumes carried `auto_exposure_bias` 12.0 and 9.5, and
both were copied verbatim into Atom's `Manual Compensation`. That property is
in **EV stops**, so 12.0 is a 2^12 = 4096x multiply, and with both volumes
unbound and level-wide the stack came to ~21.5 EV.

The copy was wrong in KIND, not just in size. UE's `AutoExposureBias` is
entangled with UE's own exposure model -- its auto-exposure normalises the
scene before the bias is added, and in UE's manual mode the base comes from
physical camera settings. Atom's `Manual Compensation` offsets the RAW scene
from zero. **We do not export UE's `AutoExposureMethod`, so the correct
conversion is not derivable from the manifest at all.**

So the audit below is expressed as PLAUSIBLE RANGES rather than invented
conversions. A value inside its range transfers; a value outside it is
clamped and REPORTED with the original, which keeps the level usable and
tells the artist exactly what to re-tune. Inventing a factor we cannot verify
would trade a visible failure for an invisible one.

    UE key                        UE unit      Atom property        Atom unit
    auto_exposure_bias            EV, model-   Manual Compensation  EV from 0
                                  relative                          CLAMPED
    auto_exposure_min_brightness  luminance    Minimum Exposure     EV
    auto_exposure_max_brightness  luminance    Maximum Exposure     EV
                                  -> CONVERTED with log2, reported
    auto_exposure_speed_up/down   speed        Speed Up/Down        speed, 1:1
    bloom_intensity               0..1 scalar  Intensity            scalar
                                                                    CLAMPED

The luminance -> EV conversion IS derivable (EV is the base-2 log of a
luminance ratio) and so it is done rather than clamped; everything else that
cannot be derived is bounded and reported.
"""

import math

# --- component names (resolve-or-fail through prefab_build) ---------------
PHYSICAL_SKY = "Physical Sky"
POSTFX_LAYER = "PostFX Layer"
DEFERRED_FOG = "Deferred Fog"
EXPOSURE_CONTROL = "Exposure Control"
BLOOM = "Bloom"

# --- property paths (probe_m6_env.py dumped the whole surface) ------------
P = "Controller|Configuration|"

# Physical Sky
P_SKY_INTENSITY = P + "Sky Intensity"
P_SUN_INTENSITY = P + "Sun Intensity"
P_SKY_INTENSITY_MODE = P + "Intensity Mode"

# PostFX Layer
P_LAYER_PRIORITY = P + "Priority"
P_LAYER_WEIGHT = P + "Weight"

# Deferred Fog
P_FOG_ENABLE = P + "Enable Deferred Fog"
P_FOG_COLOR = P + "Fog Color"
P_FOG_DENSITY = P + "Density Control|Fog Density"
P_FOG_DENSITY_CLAMP = P + "Density Control|Fog Density Clamp"
P_FOG_START = P + "Distance|Fog Start Distance"
P_FOG_END = P + "Distance|Fog End Distance"
P_FOG_LAYER_ENABLE = P + "Enable Fog Layer"
P_FOG_BOTTOM = P + "Fog Layer|Fog Bottom Height"
P_FOG_MAX_HEIGHT = P + "Fog Layer|Fog Max Height"

# Exposure Control
P_EXPOSURE_ENABLE = P + "Enable"
P_EXPOSURE_TYPE = P + "Control Type"
P_EXPOSURE_COMPENSATION = P + "Manual Compensation"
P_EXPOSURE_MIN = P + "Eye Adaptation|Minimum Exposure"
P_EXPOSURE_MAX = P + "Eye Adaptation|Maximum Exposure"
P_EXPOSURE_SPEED_UP = P + "Eye Adaptation|Speed Up"
P_EXPOSURE_SPEED_DOWN = P + "Eye Adaptation|Speed Down"

# Bloom
P_BLOOM_ENABLE = P + "Enable Bloom"
P_BLOOM_INTENSITY = P + "Intensity"
P_BLOOM_THRESHOLD = P + "Threshold"

# ExposureControlComponentConfig::ExposureControlType
EXPOSURE_MANUAL = 0
EXPOSURE_EYE_ADAPTATION = 1

# --- plausible ranges, and why these numbers -----------------------------
#
# The bound is not a style preference: it is the line past which a value
# cannot have come from the unit the property is in.
#
# EXPOSURE_EV_RANGE: Atom's Manual Compensation offsets the raw scene in
# stops. Photographic exposure compensation spans about +/-3 stops and no
# artist hand-dials 12 (a 4096x multiply) on a scene that is already
# correctly exposed. +/-5 leaves generous headroom for deliberate grading
# while still catching the measured 12.0 and 9.5.
EXPOSURE_EV_RANGE = (-5.0, 5.0)
# Atom's eye-adaptation clamps are EV; +/-16 covers starlight to noon sun.
EXPOSURE_LIMIT_EV_RANGE = (-16.0, 16.0)
# UE bloom intensity is a 0..1-ish scalar and so is Atom's. Negative is
# meaningless; the upper bound is loose because bloom is a taste control.
BLOOM_INTENSITY_RANGE = (0.0, 10.0)
# Below this, a UE brightness is zero or negative and log2 is undefined.
MIN_POSITIVE_LUMINANCE = 1e-6


def _clamped_value(value, bounds, key, atom_property, unit, warnings):
    """`value` if it is plausible, else the nearest bound -- and a report.

    Clamping rather than raising is deliberate. The manifest is a record of
    what a person set in UE, and refusing to import a level because one slider
    is out of range would be worse than importing it usable and saying so.
    """
    low, high = bounds
    if low <= value <= high:
        return value
    clamped = low if value < low else high
    warnings.append((
        "ENV_VALUE_IMPLAUSIBLE",
        "UE %s is %.4g, which is outside the plausible range %g..%g for "
        "Atom's %r (%s). It has been clamped to %.4g so the level is usable; "
        "the two engines do not share this unit's meaning, so re-tune it in "
        "O3DE rather than trusting the UE number."
        % (key, value, low, high, atom_property, unit, clamped)))
    return clamped


def _luminance_to_ev(value, key, warnings):
    """UE's auto-exposure brightness clamps (luminance) as Atom's EV clamps.

    This one IS derivable -- EV is the base-2 log of a luminance ratio -- so
    it is converted instead of clamped. Copying the raw luminance would put
    0.03 into a field that means "EV -5", which is a 32x error in the
    direction nothing would notice until a scene was too dark.
    """
    if value <= MIN_POSITIVE_LUMINANCE:
        warnings.append((
            "ENV_EXPOSURE_LIMIT_APPROX",
            "UE %s is %.4g, which has no logarithm; Atom's matching EV clamp "
            "is set to its floor instead" % (key, value)))
        return EXPOSURE_LIMIT_EV_RANGE[0]
    converted = math.log(value, 2.0)
    warnings.append((
        "ENV_EXPOSURE_LIMIT_CONVERTED",
        "UE %s is a LUMINANCE (%.4g); Atom's matching clamp is in EV, so it "
        "is imported as log2(%.4g) = %.3f EV" % (key, value, value, converted)))
    return converted

# PhotometricUnit::Ev100Luminance -- PhysicalSkyComponentConfig's own default.
PHOTOMETRIC_EV100_LUMINANCE = 4
# PhysicalSkyDefaultIntensity, the value a fresh Physical Sky ships with.
ATOM_DEFAULT_SKY_INTENSITY = 4.0

KIND_INT = "int"
KIND_FLOAT = "float"
KIND_BOOL = "bool"
KIND_COLOR3 = "color3"     # Atom's fog colour is a Vector3, not a Color

# UE fog density is a unitless coefficient (0.02 default) and Atom's is a
# 0..1 density with its own falloff; the ratio below lines the two defaults up
# (UE 0.02 <-> Atom 0.33) and is the documented approximation, not a physical
# conversion.
FOG_DENSITY_UE_DEFAULT = 0.02
FOG_DENSITY_ATOM_DEFAULT = 0.33
# Beyond this the ramp is past anything the artist meant; Atom clamps hard.
FOG_DENSITY_MAX = 1.0
# UE fog has no far distance; Atom needs one. Derived from the fog's own
# falloff so a thinner fog reaches further, and reported as approximate.
FOG_END_DISTANCE_DEFAULT = 5000.0


class EnvBuildError(Exception):
    pass


def fog_density_to_atom(ue_density):
    """UE's density coefficient → Atom's 0..1 density. Approximate by design."""
    if ue_density <= 0.0:
        return 0.0
    scaled = (float(ue_density) / FOG_DENSITY_UE_DEFAULT) * FOG_DENSITY_ATOM_DEFAULT
    return min(FOG_DENSITY_MAX, scaled)


def plan_environment(environment, subject, sky_already_authored=False,
                     exposure_already_authored=False):
    """Manifest `environment` block → authoring plan.

    Returns `(plans, warnings)` where `plans` is a list of
    `{"component": name, "properties": [(path, kind, value), ...]}` -- a list
    because Atom's post-process components only work as members of a PostFX
    Layer, so fog and post-process each author two components on one entity.

    `exposure_already_authored` exists for the same reason
    `sky_already_authored` does: EXPOSURE IS A GLOBAL, NON-ADDITIVE PROPERTY,
    and a second level-wide Exposure Control is always wrong. Measured on
    `Demonstration`: two DIFFERENT UE volumes both named `PostProcessVolume2`,
    both `unbound`, both priority 0, carrying bias 12.0 and 9.5 -- so the
    level got two enabled Exposure Controls and rendered white. UE resolves
    overlapping unbound volumes by priority into ONE result; authoring both is
    not a faithful import of that, it is a stack.
    """
    warnings = []
    kind = (environment.get("type") or "unknown").lower()

    if kind in ("skylight", "sky_atmosphere"):
        if sky_already_authored:
            warnings.append((
                "ENV_SKY_DUPLICATE",
                "another actor already authored the sky for this level; this "
                "one is skipped so two Physical Sky components do not fight "
                "over the same sky"))
            return [], warnings
        if kind == "skylight":
            source = environment.get("source_type")
            warnings.append((
                "ENV_SKYLIGHT_APPROX",
                "UE skylight (%s) is image-based lighting; Atom's Global "
                "Skylight needs diffuse+specular image assets that a %s "
                "skylight cannot provide, so a Physical Sky is authored "
                "instead" % (source, source)))
            intensity = float(environment.get("intensity", 1.0))
        else:
            warnings.append((
                "ENV_SKY_ATMOSPHERE_APPROX",
                "UE SkyAtmosphere's Rayleigh/Mie scattering parameters have no "
                "Atom equivalent; a Physical Sky with default turbidity is "
                "authored in its place"))
            intensity = 1.0
        properties = [
            # Mode BEFORE intensity, and the mode is written even though it is
            # already the component default. Measured in
            # `probe_m6_sky_intensity.py`: writing "Sky Intensity" on a fresh
            # Physical Sky *without* first writing "Intensity Mode" silently
            # stores 1.0 -- for EVERY value, with the set call reporting
            # success. Write the mode first and the intensity sticks exactly.
            # Same shape as the Directional Light in M5, one step nastier:
            # there the value was converted, here it is discarded.
            (P_SKY_INTENSITY_MODE, KIND_INT, PHOTOMETRIC_EV100_LUMINANCE),
            # UE's skylight intensity is unitless and behaves as a multiplier
            # on the sky's brightness (1.0 = "normal"), so it scales Atom's
            # default sky intensity rather than being used as an absolute.
            (P_SKY_INTENSITY, KIND_FLOAT,
             max(0.0, intensity) * ATOM_DEFAULT_SKY_INTENSITY),
        ]
        return [{"component": PHYSICAL_SKY, "properties": properties}], warnings

    if kind == "fog":
        density = fog_density_to_atom(float(environment.get("fog_density", 0.0)))
        start = float(environment.get("start_distance", 0.0))
        cutoff = float(environment.get("fog_cutoff_distance", 0.0))
        end = cutoff if cutoff > start else max(start + 1.0, FOG_END_DISTANCE_DEFAULT)
        colour = environment.get("fog_inscattering_color_linear") or [0.5, 0.6, 0.7]
        bottom = float(environment.get("fog_height_m", 0.0))
        falloff = float(environment.get("fog_height_falloff", 0.2)) or 0.2
        warnings.append((
            "ENV_FOG_APPROX",
            "UE fog is exponential in height (density %.4f, falloff %.3f); "
            "Atom's deferred fog is a distance ramp with a height band, so "
            "density %.3f and the %.1f..%.1f m ramp are an approximation"
            % (float(environment.get("fog_density", 0.0)), falloff, density,
               start, end)))
        layer = {"component": POSTFX_LAYER,
                 "properties": [(P_LAYER_PRIORITY, KIND_INT, 0),
                                (P_LAYER_WEIGHT, KIND_FLOAT, 1.0)]}
        fog = {"component": DEFERRED_FOG, "properties": [
            (P_FOG_ENABLE, KIND_BOOL, True),
            (P_FOG_COLOR, KIND_COLOR3, colour),
            (P_FOG_DENSITY, KIND_FLOAT, density),
            (P_FOG_DENSITY_CLAMP, KIND_FLOAT,
             float(environment.get("fog_max_opacity", 1.0))),
            (P_FOG_START, KIND_FLOAT, start),
            (P_FOG_END, KIND_FLOAT, end),
            (P_FOG_LAYER_ENABLE, KIND_BOOL, True),
            (P_FOG_BOTTOM, KIND_FLOAT, bottom),
            # UE's falloff is "density halves every 1/falloff units"; the
            # height band is derived from it so a slower falloff reaches
            # higher. Approximate, and covered by ENV_FOG_APPROX above.
            (P_FOG_MAX_HEIGHT, KIND_FLOAT, bottom + max(1.0, 1.0 / falloff)),
        ]}
        return [layer, fog], warnings

    if kind == "post_process":
        if not environment.get("enabled", True):
            warnings.append((
                "ENV_POSTPROCESS_DISABLED",
                "the UE post-process volume is disabled; no PostFX layer is "
                "authored"))
            return [], warnings

        overrides = environment.get("overrides") or {}
        if not environment.get("unbound", False):
            warnings.append((
                "ENV_POSTPROCESS_UNBOUNDED",
                "UE volume is bounded (it applies only inside its shape); the "
                "imported PostFX layer applies to the whole level, because "
                "bounded PostFX needs a shape component plus a weight modifier"))

        plans = [{"component": POSTFX_LAYER, "properties": [
            (P_LAYER_PRIORITY, KIND_INT, int(environment.get("priority", 0))),
            (P_LAYER_WEIGHT, KIND_FLOAT, float(environment.get("blend_weight", 1.0))),
        ]}]

        exposure = []
        eye_adaptation = any(key in overrides for key in (
            "auto_exposure_min_brightness", "auto_exposure_max_brightness",
            "auto_exposure_speed_up", "auto_exposure_speed_down"))
        if "auto_exposure_bias" in overrides:
            bias = float(overrides["auto_exposure_bias"])
            low, high = EXPOSURE_EV_RANGE
            if low <= bias <= high:
                exposure.append((P_EXPOSURE_COMPENSATION, KIND_FLOAT, bias))
            elif eye_adaptation:
                # With adaptation on, the compensation offsets a NORMALISED
                # scene -- close to what UE's bias means -- so clamping into
                # range is a usable approximation.
                exposure.append((P_EXPOSURE_COMPENSATION, KIND_FLOAT,
                                 _clamped_value(bias, EXPOSURE_EV_RANGE,
                                                "auto_exposure_bias",
                                                "Manual Compensation",
                                                "EV stops from zero",
                                                warnings)))
            else:
                # MEASURED, and it killed the clamp-and-hope approach: this
                # exact value CLAMPED to +5 EV still clipped 51% of a real
                # level's frame to white (Docks Demonstration, the render
                # check). Without eye adaptation, Atom's Manual Compensation
                # multiplies the RAW scene -- there is no auto-exposure for
                # the bias to offset, so no in-range substitute exists. An
                # untranslatable setting is dropped LOUDLY, not translated
                # wrongly.
                warnings.append((
                    "ENV_VALUE_IMPLAUSIBLE",
                    "UE auto_exposure_bias is %.4g EV with no eye-adaptation "
                    "settings; as MANUAL compensation that is a raw 2^%.3g "
                    "multiply of the scene (clamping to the range edge was "
                    "measured to clip half the frame to white). No Exposure "
                    "Control is authored -- set exposure in O3DE for this "
                    "level." % (bias, bias)))
        # Luminance clamps are CONVERTED (log2); speeds are the same kind of
        # quantity in both engines and pass through.
        for key, path in (("auto_exposure_min_brightness", P_EXPOSURE_MIN),
                          ("auto_exposure_max_brightness", P_EXPOSURE_MAX)):
            if key in overrides:
                exposure.append((path, KIND_FLOAT, _luminance_to_ev(
                    float(overrides[key]), key, warnings)))
        for key, path in (("auto_exposure_speed_up", P_EXPOSURE_SPEED_UP),
                          ("auto_exposure_speed_down", P_EXPOSURE_SPEED_DOWN)):
            if key in overrides:
                exposure.append((path, KIND_FLOAT, float(overrides[key])))
        if exposure and exposure_already_authored:
            # The PostFX layer is still authored -- this volume's fog, bloom
            # and weights are its own -- but the level's exposure is decided
            # once. Reported, never silent: losing a setting quietly is how
            # the stack that produced a white level went unnoticed.
            warnings.append((
                "ENV_EXPOSURE_ALREADY_AUTHORED",
                "another level-wide post-process volume already authored the "
                "Exposure Control; exposure is global and does not stack, so "
                "this volume's exposure settings are NOT applied. UE resolves "
                "overlapping unbound volumes by priority into one result -- if "
                "this one should win, raise its priority in UE and re-export"))
        elif exposure:
            head = [
                (P_EXPOSURE_ENABLE, KIND_BOOL, True),
                (P_EXPOSURE_TYPE, KIND_INT,
                 EXPOSURE_EYE_ADAPTATION if eye_adaptation else EXPOSURE_MANUAL),
            ]
            plans.append({"component": EXPOSURE_CONTROL,
                          "properties": head + exposure})

        bloom = []
        if "bloom_intensity" in overrides:
            bloom.append((P_BLOOM_INTENSITY, KIND_FLOAT,
                          _clamped_value(
                              float(overrides["bloom_intensity"]),
                              BLOOM_INTENSITY_RANGE, "bloom_intensity",
                              "Intensity", "0..1-ish scalar", warnings)))
        if "bloom_threshold" in overrides:
            threshold = float(overrides["bloom_threshold"])
            if threshold < 0.0:
                # UE uses -1 to mean "bloom everything"; Atom has no sentinel,
                # so the nearest honest value is a zero threshold.
                warnings.append((
                    "ENV_BLOOM_THRESHOLD_APPROX",
                    "UE bloom threshold %.3f is UE's 'no threshold' sentinel; "
                    "Atom has no equivalent, so 0.0 is used" % threshold))
                threshold = 0.0
            bloom.append((P_BLOOM_THRESHOLD, KIND_FLOAT, threshold))
        if bloom:
            plans.append({"component": BLOOM,
                          "properties": [(P_BLOOM_ENABLE, KIND_BOOL, True)] + bloom})

        return plans, warnings

    warnings.append((
        "ENV_TYPE_UNSUPPORTED",
        "environment type %r has no v1 mapping; the entity keeps its transform "
        "only" % kind))
    return [], warnings


def author_environment(entity_id, plans, entity_name, resolve_component_type):
    """Add each planned component and write its properties, in order."""
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.math as azmath

    authored = []
    for plan in plans:
        type_id = resolve_component_type(plan["component"])
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
        if not outcome or not outcome.IsSuccess():
            raise EnvBuildError("%s: AddComponentsOfType(%s) failed"
                                % (entity_name, plan["component"]))
        pair = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()

        for path, kind, value in plan["properties"]:
            if kind == KIND_COLOR3:
                payload = azmath.Vector3(float(value[0]), float(value[1]),
                                         float(value[2]))
            elif kind == KIND_INT:
                payload = int(value)
            elif kind == KIND_BOOL:
                payload = bool(value)
            else:
                payload = float(value)
            set_outcome = editor.EditorComponentAPIBus(
                bus.Broadcast, 'SetComponentProperty', pair, path, payload)
            if not set_outcome or not set_outcome.IsSuccess():
                raise EnvBuildError("%s: setting %s on %s failed"
                                    % (entity_name, path, plan["component"]))
        authored.append(plan["component"])
    return authored
