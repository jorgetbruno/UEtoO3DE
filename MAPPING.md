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
| Collision profile | `collision_profiles.json` lookup; unmapped → `PHYS_PROFILE_FALLBACK` | Collision Layer |
| Mass override off | `mass=None` + `MASS_FROM_DENSITY` | gem's density-derived default |

Entity world scale is baked into collider dimensions by the importer
(backend-neutral); shapes without a per-axis image take the largest axis with
`PHYS_SHAPE_APPROXIMATED`. Capability negotiation compares the manifest's
required shapes against `adapter.capabilities()` before authoring.
`adapter.contact_offset()` (read live from a scratch collider, currently
0.02 m) supplies every rest-height tolerance — never hard-coded. Divergences:
[DIVERGENCES.md](DIVERGENCES.md).

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
