# DIVERGENCES.md — intentional behavioural differences vs UE

Started at M3, per the plan — not at the end, because testers report every one
of these as a bug unless it is written down on day one. Two columns from the
first entry (UE → Jolt, UE → PhysX). The PhysX column was **filled in by
M3b** against a real PhysX5 project; every claim in it is measured, and the
biggest one is that PhysX cannot do the render-mesh collider bake at all.

Every entry is *deliberate or structural*, not a defect: the fix, where one
exists, is a design change recorded here rather than a patch.

## Physics (M3)

| Behaviour | UE | → Jolt | → PhysX |
|---|---|---|---|
| Resting height | body rests at the analytic contact height | rests at the analytic height in the current gem build — **measured**: a 1 m cube on a flat slab rests at exactly half-extent; earlier gem builds rested ~2 cm (the contact offset) low. Tests read `adapter.contact_offset()` (currently 0.02) and accept the band `[analytic − offset − slop, analytic + slop]` rather than assuming either behaviour | **measured in M3b, and the two backends do NOT agree**: both report a contact offset of 0.02, but PhysX rests bodies at *exactly* the analytic height (sphere r=0.30 → z=0.3000) while Jolt rests them one contact offset LOW (z=0.2800) — reproduced for sphere, box and capsule in the same run. A level ported to both backends therefore sits 2 cm lower on Jolt. This is precisely why every test derives its band from `adapter.contact_offset()` instead of asserting a shared constant; note it also contradicts the Jolt-column claim above, which was measured on an imported 1 m cube rather than on adapter-authored colliders — the discrepancy is unexplained and worth re-measuring before anyone depends on Jolt's exact resting z |
| Mass without an override | UE derives mass from shape volume × density and shows the value | backend derives its own mass from shape volume × its default density; the two derivations do not match exactly. Reported per body as `MASS_FROM_DENSITY` | same divergence, third derivation — and PhysX needs a WRITE ORDER: `Compute Mass` defaults on and recomputes the value, so a mass written before disabling it is silently discarded (measured: 42 kg reads back 1.0). The adapter always writes `Compute Mass=False` first |
| Collision channels | 32 channels, per-profile Block/Overlap/Ignore responses | collision layers + collides-with mask; profiles map through `collision_profiles.json`, everything unmapped lands on the fallback layer with `PHYS_PROFILE_FALLBACK`. v1 maps all fixture profiles to `Default` — trigger semantics travel on the sensor flag, not the layer | PhysX ships its own layer/collides-with model; v1 maps every fixture profile to `Default` on both backends, so the semantics are untested beyond that and remain unguaranteed to match Jolt's |
| Non-uniform scale on colliders | collider shapes inherit the actor's full 3-axis scale | the importer bakes the entity's world scale into collider dimensions at import time (`AZ::Transform` is uniform-only and the non-uniform scale component's interaction with colliders is uncontracted). Spheres/capsules under non-uniform scale take the largest axis → `PHYS_SHAPE_APPROXIMATED` | same (importer-level, backend-neutral) — **but see the open defect below: the premise in that parenthesis is now measured, and it is false** |
| Zero-thickness collision (UE plane meshes) | UE tolerates a 0-thickness box element | clamped to 0.01 m minimum per axis → `PHYS_SHAPE_APPROXIMATED`; solvers misbehave on degenerate shapes | same |
| Complex-as-simple / no simple collision | per-poly collision against the render mesh | triangle-mesh collider baked from the render mesh (static bodies), convex hull (dynamic) → `PHYS_MESH_FROM_RENDER`. The bake runs in the editor at import; the cooked data is serialized into the prefab | **supported, by cooking instead of baking.** PhysX mesh colliders take a COOKED `.pxmesh` asset (`Shape Configuration\|Asset\|PhysX Mesh`) that the Asset Processor produces only from an FBX carrying a PhysX mesh group, and there is no bake-from-render-mesh fallback (measured) — so staging writes that group into the sidecar (`assetinfo.physics_for_asset`: convex elements → Convex cook, no simple collision → Triangle Mesh cook) and the importer attaches a collider referencing the product. The adapter advertises `CAP_SHAPE_MESH_COOKED`, still **not** trimesh/convex, which promise a render-mesh bake it cannot do. Two backend restrictions keep their reported gaps: a cooked triangle mesh is refused on a simulated dynamic body, and refused as a trigger shape (see the trigger row) |
| Friction/restitution combine | per-material combine modes (average/min/max/multiply) | Jolt's built-in rules: friction = geometric mean, restitution = max; combine-mode properties are accepted but ignored (gem DIVERGENCES) | PhysX honours per-material combine modes, so a level tuned on PhysX and re-imported on Jolt loses them |
| Per-collider settings on multi-collider bodies | per-shape everything | collision layer/group and trigger flag are taken from the FIRST collider only (Jolt GroupFilter is per-body; gem DIVERGENCES). Per-sub-shape friction/restitution are honoured | PhysX carries these per shape, so multi-collider bodies keep per-collider layers and trigger flags that Jolt collapses onto the first |
| Trigger volumes | overlap events on any collision-enabled component | collider marked as sensor: physically transparent (bodies pass through), raises trigger events. The M3 acceptance asserts the pass-through physically | same model: `Collider Configuration\|Trigger` marks the sensor, and the M3b acceptance asserts the physical pass-through on BOTH backends. **A cooked TRIANGLE MESH cannot be a trigger** — PhysX refuses the trigger flag on trimesh geometry — so a UE overlap volume whose mesh has no simple collision is reported as a gap (`PHYS_SHAPE_APPROXIMATED`, naming the blocker) rather than authored as a collider that reports healthy and never fires. Convex cooks are fine as triggers |

### OPEN DEFECT — scaled entities get collision scaled TWICE (both backends)

Not a divergence: a bug, measured and not yet fixed. Recorded here rather than
quietly patched because the fix changes collision size on every scaled entity
in every level, which is a bigger change than the cooked-mesh work that
uncovered it and deserves its own measured round.

`physics_build` multiplies every collider dimension by the entity's world scale
(`_scaled`), on the stated premise that "collider components live outside the
transform's scale". **That premise was never measured, and it is false.**
`Tests/o3de/probe_collider_scale.py` and `Tests/o3de/probe_nonuniform_collider.py`
drop a ball onto the same collider at scale 1 and scale 2 and read its resting
height in game mode, on PhysX:

| collider | how the entity is scaled | surface ratio scale2 / scale1 |
|---|---|---|
| primitive box | uniform, on the transform | **1.97** (0.6500 → 1.1000, both exactly analytic) |
| primitive box | non-uniform, on `EditorNonUniformScaleComponent` | **2.00** (0.4500 → 0.9000 surface) |
| cooked `.pxmesh` | uniform, on the transform | **2.00** (1.4536 → 2.7095, reproduced to 4 decimals across two runs) |

So the engine already applies BOTH kinds of scale to colliders, and the
importer's baking multiplies a second time: a uniformly 2x-scaled UE actor
gets **4x** collision. On a 4.27-era siege map, 1,924 of 3,290
collidable entities are scaled, 849 of them non-uniformly.

Two consequences worth keeping straight:

  * The **cooked-mesh path is correct as written** and must stay that way: it
    passes no dimensions, so there is nothing doubled, and the measurement
    above is exactly why it must NOT also set `Asset Scale` (that property
    works — ratio 2.00 — and setting it on top of the transform would
    reintroduce the squaring).
  * The remaining **AABB-box fallback and every primitive path are wrong on
    scaled entities**, and were before this work. Fixing it means removing the
    `_scaled` multiplication, which needs: the same measurement on Jolt (its
    probe run crashed, so Jolt is unmeasured), the cooked-mesh-under-
    non-uniform-scale case (the reading failed its own settle check), and a
    re-run of the M3/M3b/M11 acceptances whose fixtures are all at scale 1 and
    therefore cannot currently see the difference either way.

## Transforms (M2, recorded here for completeness)

| Behaviour | UE | → O3DE (both backends) |
|---|---|---|
| Non-uniform scale inheritance | scales propagate through the attachment hierarchy | `EditorNonUniformScaleComponent` applies at its own entity only; children do not inherit it. Reported as `XFORM_NONUNIFORM_SCALE_NOT_INHERITED` when a non-uniformly scaled entity has children |
| Negative scale (flat actors) | legal, mirrors geometry | **faithful since M4.5.** Even negative-axis counts are exactly a 180° rotation and fold into the quaternion (lossless); odd counts fold all but one canonical mirror, and the entity references a baked mirror-X mesh variant (`#mx` asset, mirrored collision) → `XFORM_MIRRORED_MESH_VARIANT` (info). Cost: one extra mesh asset per (mesh, mirror) pair — L_Showcase's 381 mirrored actors share 36 variants |
| Negative scale (in attach hierarchies) | children inherit the mirror | not representable: folding rewrites the parent frame out from under its children. Absolute value imported + `XFORM_NEGATIVE_SCALE` (both measured levels have zero such actors) |
| Blueprint actors | class + components + scripts | the class and scripts have no mapping; StaticMeshComponents are extracted as child entities (`ACTOR_COMPONENTS_EXTRACTED`), child-actor components dedup against their own actor entries, and sky-sphere Blueprints are skipped in favour of M6's Physical Sky. Anything scripted (interaction, animation, spawning) is gone |

## Terrain (M7)

| Behaviour | UE | → O3DE |
|---|---|---|
| Representation | live heightfield (sculptable, LOD-morphing, per-component streaming) | a baked static grid mesh sampled at 2 m spacing (`UEO3DE_TERRAIN_SPACING`), triangle-mesh collider. Detail between grid nodes is quantized away; cliff faces are as steep as one cell allows → `TERRAIN_BAKED_TO_MESH` |
| Layer painting | weight-blended material layers per component | the single converted material over the whole terrain; the classifier's nearest-texture rule picks one layer per channel → `TERRAIN_LAYERS_FLATTENED` |
| Pivot/transform | landscape actor transform (scale 100 etc.) | the terrain entity sits at IDENTITY and the mesh carries world-space geometry — moving the terrain entity moves the world, but the UE pivot is not preserved |
| Landscape holes | hole material carves visibility + collision | holes are sampled as their neighbours (C0 fill); neither the visual nor the collision hole survives |
| Streaming proxies | `LandscapeStreamingProxy` per region | not supported (`ACTOR_DEFERRED`); single-Landscape levels only |
| Heightfield physics | native heightfield collider | triangle mesh from the render bake (the plan's v1 path). The Jolt gem's heightfield collider needs O3DE's Terrain gem as provider — that integration is the plan's stretch goal, prepared for by the exported heightmap TGA |
| World orientation | +X forward | the negate-Y basis map lands UE's forward on O3DE's +X (O3DE's right): faithful shape, mirror-free, yawed 90° versus O3DE's forward convention. Nothing in v1 scope depends on it (MAPPING.md, Lane A) |

## Skeletal meshes + animations (M8)

| Behaviour | UE | → O3DE |
|---|---|---|
| Animation Blueprints | state machines, blend spaces, per-instance logic | **gone.** The component imports with its Actor in bind pose + `ANIM_BLUEPRINT_UNMAPPED`; only single-node playback (`anim_to_play`) maps, to a Simple Motion component |
| Root motion | extracted by AnimBlueprints/Characters when `enable_root_motion` is set | not extracted (`ANIM_ROOT_MOTION_DROPPED`); the motion plays in place. A plain UE `SkeletalMeshActor` does not extract it either, so the showcase-style levels match; a Character that walked away in UE will walk in place in O3DE |
| Skeletal collision | per-bone PhysicsAsset bodies (query + ragdoll) | none (`SKEL_PHYSICS_DROPPED`): per-bone bodies have no v1 mapping and a bind-pose trimesh on an animated character would collide wrongly all the time |
| Materials | per-component override list | the FBX's auto-generated azmaterials by default; converted UE materials assign through the same Material-component path as static meshes (the Actor is a material consumer) |
| Negative scale | mirrors the skinned mesh | not representable: no skinned mirror-variant bake exists, so a mirrored skeletal actor takes the **unmirrored fallback** — absolute scale, orientation untouched, `XFORM_NEGATIVE_SCALE`. It must NOT take the fold path: folding decomposes an odd sign pattern as `SIGMA_rot · mirror` and keeps `SIGMA_rot` in the rotation on the promise of a variant that never arrives, so a ghoul mirrored `(1,-1,1)` would have imported facing backwards while the warning claimed only that the mirror was lost |
| Playback verification | n/a | EMotionFX exposes **no Python bus** in 26.05, so joint transforms are unverifiable headless; the acceptance proves playback by frame-capture pixel deltas and bone fidelity by byte-searching every manifest bone name in the `.actor` product |
| Morph targets, cloth, physics-driven tails | animated at runtime | morph targets travel in the FBX (`export_morph_targets`) but nothing drives them; cloth/physics assets are dropped with the PhysicsAsset |

## Foliage, decals, splines, LODs, cameras (M9)

| Behaviour | UE | → O3DE |
|---|---|---|
| Instanced meshes / foliage | one component draws N instances; painting tools, per-instance culling | N individual entities sharing one mesh asset (Atom re-instances identical models at render time). Editor scalability caps the expansion at `UEO3DE_MAX_INSTANCES` (default 2000) **per component** — beyond it instances are DROPPED (`INSTANCES_TRUNCATED`); a 100k-instance forest is out of v1 scope. Note the ceiling is per component, not per level: fifty components of 1900 instances each import 95k entities without tripping it, so a foliage-heavy level still needs judgement rather than trust in the guard |
| Spline meshes | live spline: move a control point, the mesh re-deforms | a frozen bake of the current deformation (`SPLINE_BAKED`); per-instance unique mesh assets, no dedup across identical splines |
| LODs | per-distance LOD chain + screen-size switching | LOD0 everywhere (`LOD_FLATTENED`): full detail at every distance — visually identical, costs GPU on heavy scenes |
| Decals | deferred decal material domain, per-channel blend modes, fade | Atom Decal with a **StandardPBR** material (`DECAL_MATERIAL_APPROX`): projection works, blending semantics differ; `fade_screen_size` has no mapping. The projection-axis remap (local Ry(−90) + extent scale) is derived from both engines' documented conventions, not yet screenshot-verified |
| Cameras | horizontal FOV, aspect constraints, camera post-process | vertical-FOV Camera component (converted); no aspect constraint, no per-camera post-process; orthographic cameras dropped (`CAMERA_UNSUPPORTED_MODE`) |

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

## Environment (M6)

| Behaviour | UE | → O3DE (Atom) |
|---|---|---|
| Skylight | image-based lighting from a captured scene or a specified cubemap; contributes real indirect light | **a Physical Sky, not IBL.** Atom's `Global Skylight (IBL)` needs diffuse *and* specular irradiance image assets; a real-time-capture skylight has no such asset to export, so authoring one would produce a component that looks configured and lights nothing. The Physical Sky does light the scene, but the indirect bounce is the sky's, not UE's captured environment → `ENV_SKYLIGHT_APPROX`. Closing this properly means exporting/baking a cubemap, which is its own asset pipeline |
| Sky + atmosphere together | a SkyLight *and* a SkyAtmosphere routinely coexist | only one Physical Sky is authored — two would fight over the same sky. The SkyLight wins (it carries the artist's intensity) → `ENV_SKY_DUPLICATE` |
| Atmospheric scattering | Rayleigh/Mie coefficients, ground albedo, planet radius | not representable; a default-turbidity Physical Sky stands in → `ENV_SKY_ATMOSPHERE_APPROX` |
| Fog density model | exponential in height: density × e^(−falloff × h) | a distance ramp (start→end) with a separate height band. The two are different functions, so no scale factor makes them agree everywhere; density is matched at the defaults and the height band derived from the falloff → `ENV_FOG_APPROX`. Expect the largest visual difference at extreme heights and distances |
| Post-process scope | a volume applies inside its shape, blending over `BlendRadius` | an imported PostFX layer applies to the **whole level**: bounded PostFX needs a shape component plus a weight modifier, which v1 does not author → `ENV_POSTPROCESS_UNBOUNDED`. A level with several bounded volumes will therefore look wrong — they all apply at once, ordered only by priority |
| Non-overridden settings | a PostProcessVolume setting applies only when its `override_*` flag is set | the exporter carries **only** overridden settings, so UE defaults are never imported as if an artist had chosen them. The cost is that a level relying on project-wide defaults imports with fewer post-process components than it visually had |
| Bloom threshold | `-1` means "bloom everything" | no sentinel in Atom; 0.0 is the nearest honest value → `ENV_BLOOM_THRESHOLD_APPROX` |
| Exposure | UE's histogram auto-exposure with min/max brightness in a physical camera model | Atom's eye adaptation with its own curve; the compensation value carries across, the adaptation behaviour will not match |
| Reflection captures | box/sphere capture actors | not imported in v1 (the schema carries the type; nothing authors it) → `ENV_TYPE_UNSUPPORTED` |

## Re-import and the UX (M10)

| Behaviour | UE | → O3DE |
|---|---|---|
| Re-importing a level someone has edited | no equivalent — UE is the source of truth and a re-import is a fresh import | **the O3DE edit wins.** An entity whose transform differs from what the previous import authored is treated as hand-edited: it is reported as `REIMPORT_ENTITY_CONFLICT` and the manifest's transform is *not* applied — even when the actor also moved in UE, in which case the report says so explicitly. This is deliberate and is the divergence most likely to be reported as a bug ("my UE change didn't come through"). The alternative is silently discarding someone's manual fixes on every re-run, which is worse for a tool meant to be run repeatedly. `--reimport=0` opts out and authors everything from the manifest |
| Matching entities across re-imports | — | by manifest entity id, a uuid5 of the UE actor path. **Renaming an actor in UE therefore reads as delete + add**, not as a move: the renamed actor arrives as a new entity and the old one is reported `REIMPORT_ENTITY_REMOVED`. Any hand edit on it is lost, because there is nothing to match it to |
| Detecting hand edits | — | by entity **name** within the prefab, since that is the only durable link between a manifest entity and a saved prefab entity. A name carried by more than one entity cannot be written back to safely, so those entities are excluded from conflict detection AND from preservation, and reported as `REIMPORT_NAME_COLLISION`. This includes an actor whose UE label equals the **level name**: the saved prefab holds a level-root entity under that name alongside the actors. No manifest produced so far has a duplicate name (checked across five levels, including a 2905-entity one) |
| Renaming an imported entity **in O3DE** | — | **breaks the link.** The ledger knows the entity by the name the import gave it; renaming it in O3DE means no entity of that name is found, so its hand edits cannot be matched and are replaced by the manifest's values. Reported as `REIMPORT_ENTITY_UNMATCHED` rather than done in silence. Rename the actor in UE instead — that keeps the id and carries the new name through |
| What counts as an edit | — | translation, rotation or uniform scale differing by more than 1e-4 from what the import authored. **Component and material edits are not tracked**: change a material in O3DE and a re-import will overwrite it without reporting a conflict |
| Exporting from the menu vs from CI | — | the menu item exports the level **as it stands in memory, including unsaved edits**; the batch path (`--load` default) reloads the map from disk first, so it exports the saved version. The difference is deliberate: reloading the level a user is standing in would discard their unsaved work before exporting the older copy |
