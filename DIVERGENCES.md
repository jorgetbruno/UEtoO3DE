# DIVERGENCES.md — intentional behavioural differences vs UE

Started at M3, per the plan — not at the end, because testers report every one
of these as a bug unless it is written down on day one. Two columns from the
first entry (UE → Jolt, UE → PhysX); the PhysX column is filled by M3b, and
`(M3b)` marks the entries known to differ before that adapter exists.

Every entry is *deliberate or structural*, not a defect: the fix, where one
exists, is a design change recorded here rather than a patch.

## Physics (M3)

| Behaviour | UE | → Jolt | → PhysX |
|---|---|---|---|
| Resting height | body rests at the analytic contact height | rests at the analytic height in the current gem build — **measured**: a 1 m cube on a flat slab rests at exactly half-extent; earlier gem builds rested ~2 cm (the contact offset) low. Tests read `adapter.contact_offset()` (currently 0.02) and accept the band `[analytic − offset − slop, analytic + slop]` rather than assuming either behaviour | (M3b) different default offset → different resting z |
| Mass without an override | UE derives mass from shape volume × density and shows the value | backend derives its own mass from shape volume × its default density; the two derivations do not match exactly. Reported per body as `MASS_FROM_DENSITY` | (M3b) same divergence, third derivation |
| Collision channels | 32 channels, per-profile Block/Overlap/Ignore responses | collision layers + collides-with mask; profiles map through `collision_profiles.json`, everything unmapped lands on the fallback layer with `PHYS_PROFILE_FALLBACK`. v1 maps all fixture profiles to `Default` — trigger semantics travel on the sensor flag, not the layer | (M3b) layer *semantics* not guaranteed identical to Jolt's; separate section in the same file |
| Non-uniform scale on colliders | collider shapes inherit the actor's full 3-axis scale | the importer bakes the entity's world scale into collider dimensions at import time (`AZ::Transform` is uniform-only and the non-uniform scale component's interaction with colliders is uncontracted). Spheres/capsules under non-uniform scale take the largest axis → `PHYS_SHAPE_APPROXIMATED` | same (importer-level, backend-neutral) |
| Zero-thickness collision (UE plane meshes) | UE tolerates a 0-thickness box element | clamped to 0.01 m minimum per axis → `PHYS_SHAPE_APPROXIMATED`; solvers misbehave on degenerate shapes | same |
| Complex-as-simple / no simple collision | per-poly collision against the render mesh | triangle-mesh collider baked from the render mesh (static bodies), convex hull (dynamic) → `PHYS_MESH_FROM_RENDER`. The bake runs in the editor at import; the cooked data is serialized into the prefab | (M3b) PhysX cooked-mesh path differs |
| Friction/restitution combine | per-material combine modes (average/min/max/multiply) | Jolt's built-in rules: friction = geometric mean, restitution = max; combine-mode properties are accepted but ignored (gem DIVERGENCES) | (M3b) PhysX honours combine modes |
| Per-collider settings on multi-collider bodies | per-shape everything | collision layer/group and trigger flag are taken from the FIRST collider only (Jolt GroupFilter is per-body; gem DIVERGENCES). Per-sub-shape friction/restitution are honoured | (M3b) per-shape flags supported |
| Trigger volumes | overlap events on any collision-enabled component | collider marked as sensor: physically transparent (bodies pass through), raises trigger events. The M3 acceptance asserts the pass-through physically | (M3b) same model, PhysX trigger shapes |

## Transforms (M2, recorded here for completeness)

| Behaviour | UE | → O3DE (both backends) |
|---|---|---|
| Non-uniform scale inheritance | scales propagate through the attachment hierarchy | `EditorNonUniformScaleComponent` applies at its own entity only; children do not inherit it. Reported as `XFORM_NONUNIFORM_SCALE_NOT_INHERITED` when a non-uniformly scaled entity has children |
| Negative scale | legal, mirrors geometry | not representable (invalid on colliders); absolute value imported + `XFORM_NEGATIVE_SCALE` |
| World orientation | +X forward | the negate-Y basis map lands UE's forward on O3DE's +X (O3DE's right): faithful shape, mirror-free, yawed 90° versus O3DE's forward convention. Nothing in v1 scope depends on it (MAPPING.md, Lane A) |

## Lights (M5)

| Behaviour | UE | → O3DE (Atom) |
|---|---|---|
| Point-light shadows | point lights cast shadows by default | **lost.** UE point → Atom `SimplePoint`, and `AreaLightComponentConfig::SupportsShadows()` covers only `SpotDisk`, `Sphere` and `SimpleSpot`. The config *field* exists for every type, so writing it succeeds and reads back `true` while casting nothing — the importer therefore does **not** write it and reports `LIGHT_SHADOWS_UNSUPPORTED` instead of claiming a shadow that is not there. The fix is a design change, not a patch: map point lights to `Sphere`, which supports shadows but requires a separate shape component per light and turns a punctual light into an area light |
| Attenuation radius | explicit per light; artists tune it | Atom defaults to **Automatic** (derives the influence radius from intensity and a cutoff). The import pins `Explicit` with UE's radius, so the level matches UE rather than what a native O3DE light would do. Reported as `LIGHT_RADIUS_EXPLICIT`. *This revises the plan's M5 assumption that Atom "derives a light's influence radius rather than accepting UE's explicit radius" — measured: `Attenuation radius\|Mode` accepts `Explicit` and the radius round-trips (`probe_m5_lights2.py`)* |
| Intensity units | `ELightUnits` enum: Unitless / Candelas / Lumens / EV, per light | Atom's local lights accept **Candela and Lumen only** (`GetValidPhotometricUnits`: Nit/Ev100 need a shape component), so every UE unit is converted to candela using UE's own arithmetic (`ULocalLightComponent::GetUnitsConversionFactor`, `EV100ToLuminance`). `unitless` has no photometric meaning — UE's internal ×16/10000 factor is used and reported as `LIGHT_INTENSITY_APPROX` |
| Lumens on a spot | flux spread over the cone's solid angle | same definition, and the conversion divides by *that light's* cone: `lm / (2π(1−cos θ_outer))`. A point light uses the full sphere (4π). Two spots with equal flux and different cones therefore get different candela values, as they should |
| Directional intensity | lux, with no units enum on the component | lux directly (`PhotometricUnit::Lux`). **The mode must be written before the intensity**: the component converts the stored value on a mode change — measured, 5.0 lux written intensity-first stores 80.0 (`probe_m5_lights2.py`) |
| Spot cone | inner/outer cone half-angles | Atom's *shutters* (`Enable shutters` + inner/outer angle, degrees), mapped 1:1. The two engines' falloff **curves** between the inner and outer angle are not the same function, so a wide soft-edged spot will not match pixel-for-pixel even with identical angles |
| Colour temperature | `Temperature` + `Use Temperature` tint the light | no equivalent on Atom's light components; only the RGB colour carries over → `LIGHT_TEMPERATURE_DROPPED` |
| Source radius | non-zero radius makes a sphere/area light with soft shadows | imported as punctual; soft shadow and specular width are lost → `LIGHT_SOURCE_RADIUS_DROPPED` |
| Rect / area lights | `URectLightComponent` etc. | no v1 mapping: the entity keeps its transform and gets no light component → `LIGHT_TYPE_UNSUPPORTED` |
