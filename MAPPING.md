# MAPPING.md — UE concept → O3DE component

Status: M1 (manifest export). Filled milestone by milestone; from M3 onward the physics
column is two-column (Jolt / PhysX) per the milestone plan.

## Coordinate conversion

### Lane A — transforms (locked in M1)

UE is **centimeters, Z-up, left-handed** (+X forward, +Y right, +Z up). O3DE is
**meters, Z-up, right-handed** (+X right, +Y forward, +Z up). Opposite handedness means
the numeric map between them **must have determinant −1**. A determinant +1 map — which
is what "just divide by 100" is — produces a level that renders as a perfect mirror:
self-consistent, geometrically valid, and backwards.

The basis map is **negate Y, scale 1/100**:

| Quantity | Rule | Note |
|---|---|---|
| position | `(x, y, z)` cm → `(x/100, −y/100, z/100)` m | |
| rotation | `(x, y, z, w)` → `(−x, y, −z, w)` | conjugation by the same map; canonicalized to `w ≥ 0` |
| scale | `(sx, sy, sz)` unchanged | always positive. Negative UE scales are FOLDED (M4.5): an even count of negative axes is exactly a 180° rotation composed into the quaternion; an odd count additionally swaps the mesh reference to a baked mirror-X `#mx` variant (`lane_a.fold_scale_signs`, verified as a matrix identity for all eight sign patterns). Only mirrors inside attach hierarchies fall back to `XFORM_NEGATIVE_SCALE` + absolute value |
| length (radius, extent, attenuation) | `÷100` | |

The rotation rule follows from `R' = B R B⁻¹` with `B = diag(1, −1, 1)`: because `B` is
improper, conjugation maps a rotation about axis `a` by angle `θ` to a rotation about
`B a` by `−θ`, which in quaternion terms is exactly "negate x and z". This is asserted
numerically rather than trusted — `Tests/m1/test_lane_a.py::test_rotation_equivariance`
checks `convert(R·v) == convert(R)·convert(v)` for a fixed set of rotations and vectors,
and `test_orientation_is_reversed` fails the moment the determinant turns positive.

Implementation: [lane_a.py](UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python/ueo3de/lane_a.py).
Recorded in every manifest as `units.lane_a_rule: "negate_y"` so an importer can refuse
a document produced under a different convention instead of silently mirroring a level.

**Known consequence.** Negating Y maps UE's forward (+X) onto O3DE's +X, which is
O3DE's *right*. The port is faithful in shape and free of mirroring, but yawed 90°
relative to O3DE's forward convention. Nothing in v1 scope (meshes, lights, physics,
terrain) depends on that convention. The alternative with determinant −1 that also keeps
forward on forward is swapping X and Y; it is a strictly larger change (it permutes the
scale components too) and is not what the plan specifies.

### Lane B — geometry (corrected THREE times — read LANE_B.md before touching)

Geometry reaches the product carrying the same basis map as the transforms. The
measured stages (revision 4):

| # | Stage | Map | Units |
|---|---|---|---|
| 1 | exporter bakes `scale_mesh(−1,−1,1)` | diag(−1,−1,1) | — |
| 2 | UE FBX export | diag(1,−1,1) (LH→RH) | writes cm |
| 3 | SceneAPI import | **diag(−1,−1,1) — a 180° yaw, a proper rotation** | **cm → m ÷100** (`UnitScaleFactor`) |
| | **net** | diag(1,−1,1) = exactly Lane A | ÷100 |

The intermediate FBX is therefore **mirror-X of the UE source** (verbatim for `#mx`
mirrored variants, whose bake is `scale_mesh(1,−1,1)`), and the `.assetinfo` carries
**no CoordinateSystemRule** — SceneAPI owns both conversions. Three shipped bugs came
from getting a stage wrong (meshes 100× too small; a net-zero Y mirror; every mesh
locally X-mirrored because stage 3 was recorded as "negate Y" when it is a yaw — the
two agree on the entire Y axis). The permanent assertions read the **product position
buffers** by float byte-pattern on BOTH asymmetric axes
(`Tests/m2/test_m2_artifacts.py`); full history and evidence: [LANE_B.md](LANE_B.md).

## Interchange (M1)

| UE source | Manifest field | Note |
|---|---|---|
| Actor label | `entities[].name` | not stable across renames; `id` is |
| Actor object path | `entities[].id` | UUIDv5 over the path in the project namespace |
| Actor class | `entities[].ue_class` | |
| Attach parent | `entities[].parent_id` | null for roots |
| Component mobility | `entities[].mobility` | `static` / `stationary` / `movable` |
| World transform | `entities[].transform.world` | Lane A converted |
| Transform relative to attach parent | `entities[].transform.local` | Lane A converted; `compose(parent.world, child.local) == child.world` is asserted |
| `UStaticMesh` reference | `entities[].mesh.asset_guid` → `assets[]` | deduplicated by GUID |
| Material per slot | `entities[].mesh.material_slots[]` | effective material, override or asset default; slot order preserved |
| UE package path | `assets[].ue_path` + `o3de_relative_path` | sanitized deterministically; collisions are an error, never an overwrite |
| `UStaticMesh::GetBodySetup()->AggGeom` | `assets[].collision.shapes[]` | read from the mesh asset, not the actor (plan M3) |
| Mesh bounds | `assets[].bounds_local` | Lane A converted, min/max re-derived (negating Y swaps them) |
| `FBodyInstance` flags | `entities[].physics` | absent entirely when the actor does not collide |
| Point / Spot / Directional light | `entities[].light` | see below |
| SkyLight, ExponentialHeightFog, PostProcessVolume | `entities[].kind = "environment"` | transform preserved, `ACTOR_DEFERRED` (M6) |

### Path sanitization

`/Game/Foo/SM_Bar.SM_Bar` → `uetoo3de/game/foo/sm_bar.fbx`

Drop the object suffix, drop the leading slash, lowercase (the Asset Processor lowercases
product paths), map every character outside `[a-z0-9_.-]` to `_`, collapse runs of `_`,
strip leading/trailing `_` and `.`, suffix Windows reserved device names, prefix
`uetoo3de/`. The rule is lossy by construction, so two UE assets landing on one
sanitized path is an `ASSET_PATH_COLLISION` error that aborts the export.

### Collision primitives

| UE (`KAggregateGeom`) | Manifest shape | Note |
|---|---|---|
| `KBoxElem` | `box` | UE's `X/Y/Z` are **full** extents; halved on export |
| `KSphereElem` | `sphere` | |
| `KSphylElem` | `capsule` | both `segment_height` and `total_height` emitted; UE's `length` is the cylinder only |
| `KConvexElem` | `convex` | vertex count + local AABB, recovered from `export_text()` (the vertex data is a protected UPROPERTY) |
| `KTaperedCapsuleElem`, level sets | — | `PHYS_SHAPE_UNSUPPORTED` |
| no primitives at all | `collision.source = "none"` | `PHYS_NO_SIMPLE_COLLISION`; M3 falls back to a mesh collider |

### Lights

| UE | Manifest | Note |
|---|---|---|
| `Intensity` | `light.intensity` | value carried verbatim; the photometric conversion is M5's |
| `IntensityUnits` | `light.intensity_units` | `candelas`/`lumens`/`ev`/`nits`/`unitless`; a directional light has no enum and is always `lux` |
| `LightColor` (FColor) | `light.color_srgb8` + `light.color_linear` | UE stores sRGB-encoded bytes; the linear value is decoded with the standard sRGB EOTF |
| `AttenuationRadius` | `light.attenuation_radius` | meters |
| Inner/outer cone | `light.inner_cone_angle_deg` / `outer_cone_angle_deg` | degrees, spot only |
| `Temperature` / `bUseTemperature` | `light.temperature_k` / `use_temperature` | |

## Import (M2)

| Manifest | O3DE | Note |
|---|---|---|
| `assets[].o3de_relative_path` | `<project>/Assets/<path>` + `.assetinfo` | staged outside the editor so AP can be run to completion first |
| `assets[].fbx_node_name` | `.assetinfo` `RootNode.<name>` | a wrong node path fails the AP job with "No valid ModelLodAssets have been added" |
| — | stale instances of the target prefab are removed from the scratch level *before* it opens | a level holding an instance of the prefab being rewritten makes `CreatePrefabInMemory` throw an opaque "unknown exception". It scales with scene size, so it reads as an asset-streaming race and was twice misdiagnosed as one; no amount of settling fixes it (900/1800/3600 frames all fail), removing the instance fixes it instantly ([prefab_build.detach_conflicting_instances](O3DE/Gems/UEImporter/Editor/Scripts/ueimporter/prefab_build.py)) |
| every product asset | `wait_for_asset` before it is referenced | constraint 8; a Mesh component pointing at an unprocessed asset renders nothing and reports no error |
| `entities[]` | one editor entity each, parents before children | O3DE needs the parent's EntityId at creation time |
| `entities[].transform.local` | `SetLocalTranslation` / `SetLocalRotationQuaternion` / `SetLocalUniformScale` | |
| non-uniform `scale` | `EditorNonUniformScaleComponent` + uniform scale 1.0 | `AZ::Transform` is uniform-scale only; see below |
| `entities[].mesh` | Mesh component + `Controller\|Configuration\|Model Asset` | |
| the level | one root entity at identity, then a `.prefab` | see below |

### Non-uniform scale

`AZ::Transform` carries a single uniform scale float. `SetLocalScale(Vector3)` exists
but is a no-op stub — it reports `(1,1,1)` back whatever you pass it (measured,
`Tests/o3de/probe_m2_scale.py`). Non-uniform scale needs
`EditorNonUniformScaleComponent`, which appears in **no** Add Component list (it is
added through the Transform component's UI) and whose
`azlmbr.editor.AddNonUniformScaleComponent` helper does nothing in 26.05. It is added
by type id `{2933FB4F-B3DA-4CD1-8106-F37300730777}`, read from
`EditorNonUniformScaleComponent.h` in the SDK rather than guessed, and it survives a
prefab save/reload (`Tests/o3de/probe_m2_nonuniform.py`).

Divergence to carry into `DIVERGENCES.md`: O3DE applies non-uniform scale at the
component, not in the transform hierarchy, so it does **not** reach child entities the
way UE's does. The importer reports `XFORM_NONUNIFORM_SCALE_NOT_INHERITED` when a
non-uniformly scaled entity has children.

### Level root and the prefab container

The importer creates one entity named after the level, at identity, and parents every
manifest root to it. `CreatePrefabInMemory` places the container entity at the
**centroid** of the entities it is given and rewrites their transforms relative to it,
so handing it a single entity at the origin is what makes "instantiate the prefab at
the origin" reproduce the level exactly. Anchoring the container afterwards does not
work — transform changes made after that call never reach the serialized template.

Saving the prefab at all requires a workaround established in M0 spike S0.1:
`CreatePrefabInMemory` keeps the template in memory by design,
`CreatePrefabAndSaveToDisk` is not reflected, and level save only serializes the root
template. `PrefabLoaderScriptingBus/SaveTemplateToString` returns exactly the on-disk
JSON but needs a TemplateId that nothing maps from a path, so the id space is scanned
and the template identified by content.

## Warning codes

Every code in `manifest.warnings[]` comes from the catalogue in
[warnings.py](UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python/ueo3de/warnings.py);
the validator rejects any manifest carrying a code that is not listed there. Tests assert
on codes, never on English strings.

| Code | Severity | Meaning |
|---|---|---|
| `LEVEL_WORLD_PARTITION` | error | Level is World Partition enabled; out of v1 scope. Aborts. |
| `LEVEL_WP_DETECT_FAILED` | error | WP detection itself failed, so an empty actor list cannot be trusted. Aborts. |
| `LEVEL_EXTERNAL_ACTORS` | warn | One File Per Actor without World Partition; untested layout. |
| `ASSET_PATH_COLLISION` | error | Two UE assets sanitize onto one O3DE path. Aborts. |
| `XFORM_NEGATIVE_SCALE` | warn | Mirror inside an attach hierarchy; absolute value exported (flat actors take the variant path). |
| `XFORM_MIRRORED_MESH_VARIANT` | info | Odd negative axes folded; entity references the `#mx` mirror variant. |
| `ACTOR_COMPONENTS_EXTRACTED` | info | Blueprint actor's StaticMeshComponents exported as child entities. |
| `MAT_PARAMS_BY_NAME` | warn | Material classified from texture parameter NAMES (unwalkable function internals); heuristic. |
| `ACTOR_CLASS_UNMAPPED` | warn | No v1 mapping; placeholder entity with a valid transform. |
| `ACTOR_DEFERRED` | info | Recognized class owned by a later milestone. |
| `MESH_SLOT_EMPTY` | info | Material slot with no material assigned. |
| `PHYS_NO_SIMPLE_COLLISION` | info | Mesh has no simple collision; M3 needs a mesh collider. |
| `PHYS_DEGENERATE_SHAPE` | warn | Collision primitive with a zero/near-zero dimension. |
| `PHYS_SHAPE_UNSUPPORTED` | warn | Collision primitive kind with no v1 mapping. |
| `MAT_EXPR_UNSUPPORTED` | warn | Material property driven by an expression outside the v1 subset. Base colour → whole material falls back; otherwise only that property drops. |
| `MAT_BLEND_UNSUPPORTED` | warn | UE blend mode outside Opaque/Masked/Translucent; imported as translucent. |
| `MAT_FUNCTION_PASSTHROUGH` | info | Channel driven by unsupported math (function call, contrast, blend chain); approximated by the nearest texture beneath it. |
| `ENV_POSTPROCESS_UNMAPPED` | info | A post-process setting the artist overrode has no M6 mapping; carried in the manifest, not authored. |
| `ENV_VOLUME_BOUNDS_UNKNOWN` | warn | A bounded post-process volume's extents could not be read, so the importer cannot size the equivalent volume. |
| `TERRAIN_BAKED_TO_MESH` | info | Landscape baked to a world-space grid mesh sampled from its heightfield collision (the M7 v1 path). |
| `TERRAIN_LAYERS_FLATTENED` | info | Landscape layer blending has no O3DE equivalent; the terrain renders with one converted material. |
| `ANIM_ROOT_MOTION_DROPPED` | warn | AnimSequence has root motion; Simple Motion does not extract it to entity movement, so the character animates in place. |
| `ANIM_BLUEPRINT_UNMAPPED` | warn | Skeletal component driven by an Animation Blueprint; graph logic has no mapping, so it imports in bind pose with no motion. |
| `SKEL_PHYSICS_DROPPED` | info | Skeletal collision comes from UE's per-bone PhysicsAsset; per-bone bodies have no v1 mapping, so the entity imports without physics. |
| `ACTOR_INSTANCES_EXPANDED` | info | ISM/HISM instances expanded into individual child entities sharing one mesh asset; Atom re-instances identical models at render time. |
| `INSTANCES_TRUNCATED` | warn | More instances than the export ceiling (`UEO3DE_MAX_INSTANCES`); the excess was dropped. 100k instances as entities will not open. |
| `SPLINE_BAKED` | warn | A SplineMeshComponent's deformed geometry was baked to a static mesh; the live spline is lost. |
| `LOD_FLATTENED` | warn | Source mesh has multiple LODs; only LOD0 is exported, so it renders at full detail at every distance. |
| `DECAL_MATERIAL_APPROX` | warn | Decal material converts through StandardPBR, not an Atom decal material type; projection blending will not match UE's deferred decal. |
| `CAMERA_UNSUPPORTED_MODE` | warn | Projection mode with no v1 mapping (orthographic); the entity keeps its transform and gets no camera component. |

The importer has its own catalogue for the other direction — things the manifest
carried faithfully that O3DE cannot represent the same way
([report.py](O3DE/Gems/UEImporter/Editor/Scripts/ueimporter/report.py)). They are
separate because they are fixed in different places.

| Code | Severity | Meaning |
|---|---|---|
| `XFORM_NONUNIFORM_SCALE_COMPONENT` | info | Non-uniform scale moved onto an `EditorNonUniformScaleComponent`. |
| `XFORM_NONUNIFORM_SCALE_NOT_INHERITED` | warn | Non-uniformly scaled entity has children; O3DE does not propagate the scale to them, UE does. |
| `MESH_MISSING` | warn | Static mesh actor with no mesh reference; imported as a transform-only placeholder. |
| `ENTITY_KIND_DEFERRED` | info | Recognized entity kind owned by a later milestone. |
| `MAT_SLOT_UNMATCHED` | warn | A converted material had no matching slot label on the model. |
| `MAT_SLOT_LABEL_AMBIGUOUS` | warn | Two slots share a material *name* but use different materials. |
| `MAT_MODEL_NOT_READY` | warn | Model asset never streamed in; per-slot assignment fell back to the default slot. |
| `LIGHT_INTENSITY_APPROX` | warn | Intensity units with no exact photometric meaning (unitless/nits). |
| `LIGHT_RADIUS_EXPLICIT` | info | UE's explicit attenuation radius pinned; Atom would derive it. |
| `LIGHT_SHADOWS_UNSUPPORTED` | warn | UE light casts shadows; the mapped Atom light type cannot. |
| `LIGHT_SOURCE_RADIUS_DROPPED` | info | UE area-light source radius lost; imported as punctual. |
| `LIGHT_TEMPERATURE_DROPPED` | info | UE colour temperature has no Atom equivalent. |
| `LIGHT_TYPE_UNSUPPORTED` | warn | UE light class (rect/area) has no v1 mapping. |
| `MAT_SLOT_UNUSED` | info | A slot matched nothing and every model slot is already assigned — the asset lists a slot no render triangle uses. Nothing was lost. |
| `MAT_SLOT_BY_ELIMINATION` | info | A material matched no slot label (the asset's slot carries no default material, so the FBX has no name for it) but exactly one model slot was unclaimed. |
| `MAT_SLOT_DEDUP_SUFFIX` | info | A model slot label is an FBX name-dedup variant (`MI_X_1`: two UE slots filled with the same material); it received the base label's material. |
| `DECAL_MATERIAL_UNCONVERTED` | warn | A decal's material did not convert; the decal imports with its volume and sort key but no material. |
| `ENV_SKYLIGHT_APPROX` | warn | UE's image-based skylight has no exportable irradiance images, so a Physical Sky stands in. Lighting is approximate. |
| `ENV_SKY_ATMOSPHERE_APPROX` | warn | SkyAtmosphere scattering has no Atom equivalent; a default-turbidity Physical Sky stands in. |
| `ENV_SKY_DUPLICATE` | info | More than one actor maps to the sky; only the first is authored, because two Physical Sky components fight. |
| `ENV_FOG_APPROX` | warn | UE fog is exponential in height, Atom's is a distance ramp with a height band; density and range are approximated. |
| `ENV_POSTPROCESS_UNBOUNDED` | warn | A bounded UE post-process volume becomes a level-wide PostFX layer. |
| `ENV_POSTPROCESS_DISABLED` | info | The UE post-process volume is disabled; no layer authored. |
| `ENV_BLOOM_THRESHOLD_APPROX` | info | UE's negative bloom threshold is a "no threshold" sentinel with no Atom equivalent; 0.0 is used. |
| `ENV_TYPE_UNSUPPORTED` | warn | Environment actor type has no v1 mapping; the entity keeps its transform only. |
| `PHYS_SHAPE_APPROXIMATED` | warn | A shape could not be authored exactly **on this backend** and was substituted. The same UE level legitimately differs per backend; this makes it visible. |
| `PHYS_PROFILE_FALLBACK` | warn | A collision profile reached an entity and **no filtering was applied** — neither adapter implements the `layer` argument, so every imported body collides with everything. Reported once per distinct profile, not per body. |
| `PHYS_MESH_FROM_RENDER` | info | No simple primitives; a mesh collider was baked from the render geometry (triangle mesh static, convex hull dynamic). |
| `PHYS_COLLIDER_NOT_BAKED` | error | A mesh collider reached the saved prefab with no baked geometry, so it collides with nothing. The bake runs on the component's tick and had not finished when the prefab was serialized. Re-import with a larger `UEO3DE_SETTLE_FRAMES`; it cannot be recovered afterwards. |
| `PHYS_MESH_NOT_COOKED` | warn | The mesh needs a cooked physics mesh (`.pxmesh`) on this backend but the Asset Processor produced none — the staged sidecar predates cooked-mesh support (restage to fix) or the cook failed (check the AP log). Affected entities fall back to AABB boxes, or to no collider where a triangle mesh was needed. |
| `PHYS_MESH_ASSET_MISSING` | error | A PhysX mesh collider serialized without its cooked-mesh reference, so it collides with nothing. The asset-route sibling of `PHYS_COLLIDER_NOT_BAKED`: the editor accepted the property write, and the saved bytes are checked anyway. |
| `MASS_FROM_DENSITY` | info | No explicit UE mass override; the backend derives mass from volume × its default density, which will not match UE's figure. |
| `REIMPORT_ENTITY_ADDED` | info | The actor is new since the previous import of this prefab. |
| `REIMPORT_ENTITY_REMOVED` | info | The actor was in the previous import and is gone from this manifest; its entity is not recreated. |
| `REIMPORT_ENTITY_CONFLICT` | warn | The entity was edited by hand in O3DE since the last import. **The edit is kept** and the manifest's transform is not applied. |
| `REIMPORT_LEDGER_MISSING` | info | A re-import was asked for but the prefab has no ledger; treated as a first import, so hand edits cannot be detected. |
| `REIMPORT_NAME_COLLISION` | warn | Two manifest entities share a name. Entities match back to the prefab by name, so these two cannot be told apart for conflict detection. |
| `REIMPORT_ENTITY_UNMATCHED` | warn | The previous import authored this entity and the prefab no longer has one of that name — renamed or deleted in O3DE. Its hand edits cannot be matched and are replaced. Rename the actor in UE, not the entity in O3DE. |
| `REIMPORT_CONFLICT_NOT_PRESERVED` | error | An edit was reported as kept but could not be written back (no entity of the expected name in the rebuilt prefab). Being told an edit survived when it did not is worse than either outcome alone. |

## Lights (M5)

Intensity is the whole problem: UE carries a units enum per light and Atom's
**local** lights accept only Candela and Lumen (`GetValidPhotometricUnits` —
Nit/Ev100 require a shape component). Every UE unit is therefore converted to
candela using UE's own arithmetic, read out of
`ULocalLightComponent::GetUnitsConversionFactor` and `EV100ToLuminance` rather
than a textbook.

| UE | O3DE | Note |
|---|---|---|
| `PointLight` | `Light` component, type **SimplePoint** (6) | no shape component needed; **cannot cast shadows** → `LIGHT_SHADOWS_UNSUPPORTED` (DIVERGENCES.md) |
| `SpotLight` | `Light` component, type **SimpleSpot** (7) | cone → *shutters*: `Enable shutters` + inner/outer angle, 1:1 in degrees; supports shadows |
| `DirectionalLight` | `Directional Light` component | intensity mode **Lux** (2); its shadow property path differs from the local one by a capital letter, verified distinct |
| `RectLight` / area | — | no v1 mapping → `LIGHT_TYPE_UNSUPPORTED`, transform-only entity |
| `candelas` | `PhotometricUnit::Candela` (1), value verbatim | exact |
| `lumens` | candela = `lm / (2π(1−cos θ_outer))`; point uses the full sphere (4π) | exact; the cone is *that light's* outer half-angle |
| `ev` | candela = `2^EV` | UE's implicit 1 m² surface, per its own comment |
| `unitless` | candela = `v × 16/10000` | no photometric meaning; UE's internal factor → `LIGHT_INTENSITY_APPROX` |
| `lux` (directional only) | `PhotometricUnit::Lux` (2), value verbatim | exact |
| `AttenuationRadius` | `Attenuation radius\|Mode` = Explicit + `Radius` | Atom defaults to Automatic; pinned for fidelity → `LIGHT_RADIUS_EXPLICIT` |
| `LightColor` | `Color`, **linear** | the manifest's `color_linear`, not the sRGB bytes |
| `Temperature` | — | → `LIGHT_TEMPERATURE_DROPPED` |
| `SourceRadius` | — | → `LIGHT_SOURCE_RADIUS_DROPPED` |

**Write order is load-bearing.** `Intensity mode` must be set *before*
`Intensity`: the Directional Light component converts the stored value when
the mode changes — measured, 5.0 lux written intensity-first stores 80.0
(`Tests/o3de/probe_m5_lights2.py`). The plan produced by
[light_build.py](O3DE/Gems/UEImporter/Editor/Scripts/ueimporter/light_build.py)
is an ordered list for exactly this reason, and
[test_light_build.py](Tests/m5/test_light_build.py) asserts the order offline.

## Environment (M6)

The goal is that an imported level is not lit in a black void. Almost every
mapping here is an approximation, and each one is reported.

| UE | O3DE | Note |
|---|---|---|
| `SkyLight` | `Physical Sky` | **not** `Global Skylight (IBL)`: that needs diffuse+specular *image assets*, which a real-time-capture skylight has none of, so it would author a component that looks configured and lights nothing → `ENV_SKYLIGHT_APPROX` |
| `SkyAtmosphere` | `Physical Sky` | Rayleigh/Mie scattering has no Atom equivalent → `ENV_SKY_ATMOSPHERE_APPROX` |
| both present | **one** Physical Sky, the SkyLight's | two fight over the same sky. The SkyLight wins because it carries the artist's intensity; the atmosphere carries only parameters Atom cannot represent → `ENV_SKY_DUPLICATE` |
| SkyLight `Intensity` (unitless) | `Sky Intensity` = UE × 4.0 | UE's unitless intensity is a multiplier on "normal"; 4.0 is Atom's default sky intensity |
| `ExponentialHeightFog` | `PostFX Layer` + `Deferred Fog` | Atom's post-process components are inert without a layer on the same entity |
| fog `Density` | `Fog Density`, scaled so UE's 0.02 default lands on Atom's 0.33 | different functions (UE exponential in height, Atom a distance ramp with a height band) → `ENV_FOG_APPROX` |
| fog `StartDistance` / `FogCutoffDistance` | `Fog Start Distance` / `Fog End Distance` | UE has no far distance unless a cutoff is set; otherwise a default end is derived |
| fog `HeightFalloff` | `Fog Layer` bottom/max height | the height band is derived from the falloff — approximate |
| `PostProcessVolume` | `PostFX Layer` (+ `Exposure Control`, `Bloom`) | only settings whose `override_*` flag is set are carried at all; unmapped overridden settings → `ENV_POSTPROCESS_UNMAPPED` |
| `AutoExposureBias` | `Manual Compensation` | eye-adaptation settings switch `Control Type` to eye adaptation |
| `BloomIntensity` / `BloomThreshold` | `Intensity` / `Threshold` | UE's `-1` threshold is a "no threshold" sentinel with no Atom equivalent → 0.0, `ENV_BLOOM_THRESHOLD_APPROX` |
| bounded volume | level-wide layer | bounded PostFX needs a shape plus a weight modifier → `ENV_POSTPROCESS_UNBOUNDED` |

**Two write-order traps, both measured.** Atom's post-process components each
carry an `Enable…` flag, and one left false serializes into the prefab looking
configured while rendering nothing — so the flag is always written. And on the
Physical Sky, writing `Sky Intensity` *without* writing `Intensity Mode` first
stores **1.0 for any input** while reporting success
([probe_m6_sky_intensity.py](Tests/o3de/probe_m6_sky_intensity.py)); the mode
is therefore written even though it is already the component default. This is
the same family as M5's directional light, one step nastier — there the value
was converted, here it is discarded.

## Terrain (M7)

| UE | O3DE | Note |
|---|---|---|
| `Landscape` | one static-mesh entity at **identity transform** over a `#terrain` asset | the mesh is baked in world space: heights are line-traced per heightfield collision component (`K2_LineTraceComponent` — no filtering problem with 2900 props on the terrain) on a grid (`UEO3DE_TERRAIN_SPACING`, default 2 m), built with GeometryScript and fed through the normal Lane B bake → `TERRAIN_BAKED_TO_MESH` |
| landscape physics | the importer's existing render-mesh **triangle collider on a static body** | the asset entry says `collision.source = "none"`, which is already the trigger for that path — zero importer changes |
| landscape material + layers | the single converted material | the M4 classifier's nearest-texture rule already flattens `LandscapeLayerBlend` per channel → `TERRAIN_LAYERS_FLATTENED` |
| heightmap | `<name>_heightmap.tga` next to the manifest (8-bit visualization) + `terrain_samples.json` (five grid-node surface points, O3DE metres) | the samples are the sphere-drop acceptance's drop points and the exporter's own self-check (each is re-traced independently; a mismatch aborts the export) |
| `LandscapeStreamingProxy` | — | not supported → `ACTOR_DEFERRED` |

**Terrain export needs a FULL editor session.** Every commandlet route is
measured dead: `CopyMeshFromComponent` and `copy_collision_meshes_from_object`
return 0 triangles for landscape components, the heightmap→render-target path
asserts (no viewport), line traces hit nothing (no physics scene), and the
component heightmap textures are not exposed to Python
(`Tests/ue/probe_m7_*.py`). `export_level.bat` therefore runs
`UnrealEditor.exe -ExecutePythonScript` — full editor, auto-quits — and
asserts on the result file, since the process exit code is meaningless under
`quit_editor`. **The fixture cannot contain a Landscape at all**: spawning one
in a scripted session trips the engine's `!IsRunningCommandlet()` assertion,
so M7's suite takes a real landscape level's export directory as its input and
fails hard when it is missing.

## Skeletal meshes + animations (M8)

| UE | O3DE | Note |
|---|---|---|
| `SkeletalMeshActor` | entity with an EMotionFX **Actor** component (`Actor asset` ← the `.actor` product) | the skinned FBX ships through UE's **native** exporter (no GeometryScript bake is possible without destroying skinning); the DEFAULT scene rules already produce `<stem>.actor` + the skinned azmodel — no `.assetinfo` sidecar |
| single-node animation (`animation_data.anim_to_play` — the flat `anim_to_play` property does not exist in 5.8, measured) | **Simple Motion** component (`Configuration\|Motion` ← the `.motion` product, `Play on active`, `Loop motion` from the manifest) | each AnimSequence exports to its own FBX with `export_preview_mesh=False` — skeleton + curves, no geometry |
| skeletal frame | the importer composes a **local Rz180** into every skeletal entity's rotation | no bake stage exists, so skeletal products carry LaneA · Rz180; the manifest pins `units.lane_b_skeletal_rule` and the importer refuses a mismatch (LANE_B.md, M8) |
| Animation Blueprint | Actor component in bind pose, no motion | graph logic has no mapping → `ANIM_BLUEPRINT_UNMAPPED` |
| `enable_root_motion` on the anim | plays in place | Simple Motion does not extract root motion → `ANIM_ROOT_MOTION_DROPPED` (a plain UE `SkeletalMeshActor` does not extract it either) |
| skeletal collision (PhysicsAsset per-bone bodies) | — | no v1 mapping → `SKEL_PHYSICS_DROPPED`; a bind-pose trimesh on an animated character would be worse than nothing |
| Blueprint actors with `SkeletalMeshComponent`s | child entities under the placeholder, same skeletal recipe | the M4.5 component-extraction path extended to skeletal components (BP_Ghoul: mesh + rags + armor) |
| a child attached to a corrected entity (skeletal or decal) | the correction is undone on the CHILD's local transform (`skel_build.counter_correct_child`) | O3DE composes `child_world = parent_world · child_local`, so a frame correction that exists for one entity's own geometry would otherwise swing every descendant around it — measured 0.46 m for a prop 0.3 m off a character. Only a UNIFORM parent scale needs the ratio divided out; a non-uniform one rides `EditorNonUniformScaleComponent` and never reaches children |
| bone count / names | `bone_count` + `bone_names` in the skeletal asset entry | EMotionFX reflects **no bus** to EditorPythonBindings in 26.05 (measured, 7 probe rounds — no joint transforms, no bounds, attachment follows the entity not a joint), so the plan's bone-count assertion runs at the `.actor` product byte level: every manifest bone name must appear |
| playback proof | frame-capture pixel deltas | `FrameCaptureRequestBus` writes real screenshots headless with a measured **zero** edit-mode noise floor; the M8 acceptance requires the waving canary's frames to differ and the bind-pose control's not to (`Tests/lib/png_diff.py`) |

**Skeletal export needs a FULL editor session** — the native skeletal FBX
exporter walks render objects that do not exist under `-nullrhi`
(`Assertion failed: MeshObject`, measured). Since M8 the fixture export runs
through `export_fixture.bat` (full editor, result-file contract), same as
`export_level.bat` has since M7.

## Foliage, decals, splines, LODs, cameras (M9)

| UE | O3DE | Note |
|---|---|---|
| ISM/HISM components (incl. foliage) | one child entity **per instance**, all sharing one mesh asset | Atom re-instances identical models at render time, but the EDITOR does not scale to six figures of entities: per-component ceiling `UEO3DE_MAX_INSTANCES` (default 2000), excess dropped loudly → `ACTOR_INSTANCES_EXPANDED` / `INSTANCES_TRUNCATED`. (The measured showcase `InstancedFoliageActor` is empty; Fixture_02 carries the canary) |
| `SplineMeshComponent` | a child entity over a `#spline` baked asset | `copy_mesh_from_component` DOES return the deformed geometry (measured — unlike its landscape behaviour); baked in COMPONENT-LOCAL space through the normal Lane B pipeline, so the entity stays movable → `SPLINE_BAKED`. Collision source "none" → the render-mesh trimesh path, and the render mesh IS the deformed bake |
| static mesh LODs | a LOD chain on the FBX path: `FbxLODGroup` → `RootNode.<name>.<name>_LOD<i>`, render group selects `_LOD0`, `LodRule` selects one node per further LOD → one `.azmodel` + N `.azlod` | Nanite meshes read the source and reduce it through UE's reducer (`UEO3DE_LOD0_RATIO` 0.25, `UEO3DE_LOD_RATIOS` 10/4/1.5/0.6 %); UE's fallback mesh is opt-in (`UEO3DE_NANITE_FALLBACK=1`) — its LOD 0 material sections are measured wrong on permuted-section meshes. glb exports stay flattened → `LOD_FLATTENED`. Details and measurements: LODS_AND_COLLISION.md |
| `DecalActor` | Atom **Decal** component | UE projects along local +X with `decal_size` HALF-extents (x = depth); Atom projects along local −Z over a unit box scaled by entity scale. The importer composes a local Ry(−90) and scale `(2hz, 2hy, 2hx)` (decal_build, matrix-identity tested). The material converts through StandardPBR, not an Atom decal material type → `DECAL_MATERIAL_APPROX`; `Sort Key` maps from `sort_order` |
| `CameraActor` | **Camera** component | UE `field_of_view` is HORIZONTAL; O3DE takes VERTICAL: `2·atan(tan(h/2)/aspect)` with the manifest carrying both raw numbers. Orthographic → `CAMERA_UNSUPPORTED_MODE`, transform-only entity |

## World Partition detection

UE 5.8 exposes no direct route from Python: `UWorld` has neither `persistent_level` nor
`world_partition`, `ULevel` has no `world_partition`, and the `.umap` carries no
asset-registry tag for it (all three measured — `Tests/ue/results/probe_m1_apis*.txt`).
The working detector is `UWorld.get_world_settings()` →
`get_editor_property("world_partition")`, which returns `None` on a non-partitioned
level. Note it does not appear in `dir()`; UE Python hides readable UPROPERTYs from
`dir()`, so availability must never be probed with `hasattr`.

The guard is conservative: a partitioned level **and** a failure to determine whether the
level is partitioned both abort, because iterating an unloaded WP level yields almost
nothing and is indistinguishable from a successful export of an empty level.

## Physics (M3) — authored through `PhysicsBackendAdapter`, never by name

The importer speaks only the adapter interface
([adapters/base.py](O3DE/Gems/UEImporter/Editor/Scripts/ueimporter/adapters/base.py));
`Tests/m3/test_seam_guard.py` greps everything outside `adapters/` for
`"Jolt "`/`"PhysX "` literals in CI. Backend detection resolves component names
to type IDs first, treats the Settings Registry `DefaultBackend` as a hint
only, and **refuses to guess when both backends resolve** (available ≠ active —
authoring for the inactive backend yields a level with no physics).

| UE source (manifest) | Adapter call | Jolt component (adapter-internal) |
|---|---|---|
| Static mobility + collision | `add_static_body` | `Jolt Static Rigid Body` |
| `simulates_physics` | `add_dynamic_body(mass?, damping, gravity, ccd)` | `Jolt Rigid Body` |
| Movable + collision, no simulate | `add_dynamic_body(kinematic=True)` | `Jolt Rigid Body`, Kinematic |
| Box element | `add_box_collider(half_extents, offset, rot)` | `Jolt Box Collider` (Dimensions = FULL extents) |
| Sphere / sphyl element | `add_sphere_collider` / `add_capsule_collider(total_height)` | `Jolt Sphere/Capsule Collider` |
| Convex element | `add_mesh_collider(convex=True)` | `Jolt Mesh Collider`, Convex Hull |
| No simple collision (static) | `add_mesh_collider(convex=False)` + `PHYS_MESH_FROM_RENDER` | `Jolt Mesh Collider`, Triangle Mesh — bakes from the entity's render model automatically once it loads |
| Overlap-only volume | body + colliders + `make_trigger` | collider `Trigger` flag (sensor) |
| Collision profile | **not applied** → `PHYS_PROFILE_FALLBACK` per profile | would be Collision Layer; nothing sets it |
| Mass override off | `mass=None` + `MASS_FROM_DENSITY` | gem's density-derived default |

**Entity scale is the engine's job, not the importer's.** Both backends apply
the entity's world uniform scale times any non-uniform scale component to their
own colliders — dimensions, offsets, primitives and cooked mesh assets alike
(measured: every ratio is exactly 2.000, `Tests/o3de/probe_scale_matrix.py`).
So an adapter advertising `CAP_SCALE_ENGINE_APPLIED` is handed the manifest's
own numbers and nothing is multiplied; multiplying as well would square the
collision on every scaled entity. A backend without the capability gets the
scale baked in here instead, and only then can a shape without a per-axis image
(sphere, capsule) take the largest axis with `PHYS_SHAPE_APPROXIMATED`.
`UEO3DE_BAKE_SCALE=1` forces baking back on for a Jolt gem build predating
`ApplyOverallScale` — nothing in the component set distinguishes it. Guarded
by `Tests/perf/test_scale.py` and `Tests/m3b/m3b_scale_acceptance.py`.
Capability negotiation compares the manifest's
required shapes against `adapter.capabilities()` before authoring.
`adapter.contact_offset()` (read live from a scratch collider, currently
0.02 m) supplies every rest-height tolerance — never hard-coded. Divergences:
[DIVERGENCES.md](DIVERGENCES.md).

**Mesh collision is cooked, not baked** (`CAP_SHAPE_MESH_COOKED`) — on both
backends, once the Jolt gem moved its mesh colliders onto `.joltmesh` assets.
Staging writes that backend's mesh group into the FBX's `.assetinfo` sidecar
([assetinfo.physics_for_asset](O3DE/Gems/UEImporter/Editor/Scripts/ueimporter/assetinfo.py)):
convex elements → a Convex cook of the whole render mesh, no simple collision
→ a Triangle Mesh cook. The Asset Processor produces `<fbx>.pxmesh` /
`<fbx>.joltmesh`, and the importer waits for it and attaches a mesh collider
referencing it — one collider per entity regardless of UE's element count.

| | PhysX | Jolt |
|---|---|---|
| Group `$type` | `{5B03C8E6-…} MeshGroup` — the UUID is required, the name collides with Atom's render group | `JoltMeshGroup`, bare, as the editor itself writes it |
| `export method` default | Triangle Mesh | **Convex** — opposite, so the importer always writes it explicitly |
| Decomposition params | PhysX v2 block | Jolt's own; only `MaxConvexHulls` is shared |
| Render-mesh bake | none | still available, as `Jolt Baked Mesh Collider` |

Sidecars gain a group **only for backends whose gem the project lists**
(`UEO3DE_PHYSX_COOK` / `UEO3DE_JOLT_COOK` override, for gems activated
transitively); a project carrying both gets both groups, because which backend
a level is imported with is not staging's decision to make. Where a backend
offers both routes the **cooked asset wins** — nothing bakes on a tick, so
there is no settle to get wrong, and every instance references one shared
asset instead of carrying its own copy of the geometry — with the bake kept as
the fallback for meshes that got no product.
`UEO3DE_DECOMPOSE=1` (or a hull cap number) enables V-HACD decomposition at
cook time for multi-element meshes, trading Asset Processor time for collision
that approximates the concavities UE decomposed away. It gates **both**
backends — `UEO3DE_PHYSX_DECOMPOSE` is the historical name and still works.
V-HACD is no longer the only route. `KConvexElem`'s fields are protected
from Python (measured, `Tests/ue/probe_convex_elems.py`), but the whole
`AggGeom` struct assigns onto the baked temp asset, and UE's FBX writer then
emits the elements as one `UCX_<node>` mesh — so every FBX now carries UE's
hulls, and `UEO3DE_COLLISION=ue` at staging cooks them (selected by the
physics group, re-split into at most the element count, rotated back into
the bake's frame by a `CoordinateSystemRule`; `vhacd` and the default
`single` remain). The measurements are in LODS_AND_COLLISION.md §4. No
non-licensed fixture has a multi-convex asset, so unit coverage pins the
sidecar (`Tests/perf/test_collision_modes.py`) and the RetroCars fleet is
the live check.

Two cooked-trimesh restrictions are the backend's, and both stay reported
rather than authored: PhysX refuses triangle-mesh geometry on a **simulated
dynamic body** and refuses it as a **trigger shape**. Those entities get
`PHYS_SHAPE_APPROXIMATED` naming the blocker; a convex cook is valid in both
cases. Scaled entities: the cooked collider follows the entity's scale from the
engine side (measured at exactly 2.000 on both backends), and the importer
passes no dimensions and deliberately does not set Asset Scale on top of it.

## Materials (M4) — graph subset → StandardPBR

Recognition is **per property, not per material** (spike S4.0 measured most
"unsupported" materials failing on a single channel): each of BaseColor /
Normal / Roughness / Metallic / AO / Opacity(Mask) is classified independently,
unmapped ones warn with `MAT_EXPR_UNSUPPORTED`, and only an unmappable *base
colour* rejects the whole material — `material_data: null`, entities keep the
backend default, visibly grey rather than silently wrong. Material INSTANCES
resolve through their parent's graph with instance parameter values.

| UE | `.material` (StandardPBR) | Note |
|---|---|---|
| TextureSample → BaseColor | `baseColor.textureMap` | Multiply(texture, const) keeps the const as `baseColor.factor`/`color` |
| TextureSample → Normal | `normal.textureMap` + **`normal.flipY: true`** | UE authors DirectX-style (green down); asserted by the acceptance test |
| TextureSample.R/G/B/A → scalar prop | split grayscale TGA per channel | the ORM case; pure-Python TGA split, testable offline |
| Constant / ScalarParameter | `roughness.factor` / `metallic.factor` etc. | |
| Blend Opaque / Masked / Translucent | `opacity.mode` omitted / `Cutout` / `Blended` | anything else → `MAT_BLEND_UNSUPPORTED`, treated translucent |
| OpacityMask / Opacity from texture alpha | `opacity.alphaSource: Split` + split `_opacity.tga` | `opacity.factor` set to 1.0 explicitly (the type's default 0.5 would halve alpha) |
| TwoSided | `general.doubleSided` | |
| texture sRGB/linear | **filename role suffix** (`_basecolor`/`_normal`/`_roughness`/`_metallic`/`_ao`/`_opacity`) | the Atom image builder's own filemask table picks the preset (measured from ImageBuilder.settings) |

Assignment: a `Material` component per entity. When every mapped slot shares
one material, the default slot carries it (`Default Material|Material Asset`,
verified live) — it covers all model slots and does not depend on the model
asset having streamed in. When slots differ, assignment is **per slot by
label** (o3dimport's technique): the baked FBX carries the source's material
list, so the azmodel's slot labels are the **UE material asset names**
(slot names like `Wood` do not survive the FBX — measured in
`Tests/ue/probe_slots.py`); `FindMaterialAssignmentId(label)` maps each label
to a stable id, and the `Model Materials|[i]|Material Asset` row whose
`Material Slot Stable Id` matches gets that material's `.azmaterial`.
Importer-side degradations are reported: `MAT_SLOT_UNMATCHED`,
`MAT_SLOT_LABEL_AMBIGUOUS` (two slots, same material name, different
materials), `MAT_MODEL_NOT_READY` (model never streamed in; first material
lands on the default slot instead).

## Content mapping (later milestones)

| UE source | O3DE target | Milestone |
|---|---|---|
| Static mesh + placement | Mesh component in a `.prefab` | M2 ✔ |
| Physics bodies / colliders | `PhysicsBackendAdapter` → Jolt | M3 ✔ (PhysX: M3b) |
| Material graph subset | StandardPBR `.material` | M4 ✔ |
| Point / Spot / Directional light | Atom lights | M5 |
| Sky, skylight, fog, post-process | Atom environment | M6 |
| Landscape | baked static mesh + triangle-mesh collider | M7 |
| Skeletal mesh + animation | Actor component + Motion | M8 |
