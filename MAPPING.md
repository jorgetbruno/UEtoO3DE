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
| scale | `(sx, sy, sz)` unchanged | always positive; a negative UE scale is reported as `XFORM_NEGATIVE_SCALE` and exported as its absolute value |
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

### Lane B — geometry (settled in M2, corrected twice — read LANE_B.md)

Geometry reaches the product carrying the same basis map as the transforms through
**three Y-negations that net to one**, plus a unit conversion SceneAPI performs itself:

| # | Stage | Y | Units |
|---|---|---|---|
| 1 | exporter bakes `scale_mesh(1,−1,1)` | negate | — |
| 2 | UE FBX export | negate | writes cm |
| 3 | SceneAPI import | negate (declared FBX axes) | **cm → m ÷100** (`UnitScaleFactor`) |
| | **net** | negate once | ÷100 = exactly Lane A |

The intermediate FBX is therefore **verbatim UE geometry**, and the `.assetinfo`
carries **no CoordinateSystemRule** — SceneAPI owns both conversions. Getting either
of those wrong produced the two bugs that shipped mid-M2 (meshes 100× too small; a
net-zero mirror), both invisible to every intermediate check. The permanent assertion
reads the **product position buffers** by float byte-pattern
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
| `XFORM_NEGATIVE_SCALE` | warn | Negative UE scale; absolute value exported. |
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

## Content mapping (later milestones)

| UE source | O3DE target | Milestone |
|---|---|---|
| Static mesh + placement | Mesh component in a `.prefab` | M2 ✔ |
| Physics bodies / colliders | `PhysicsBackendAdapter` → Jolt | M3 ✔ (PhysX: M3b) |
| Material graph subset | StandardPBR `.material` | M4 |
| Point / Spot / Directional light | Atom lights | M5 |
| Sky, skylight, fog, post-process | Atom environment | M6 |
| Landscape | baked static mesh + triangle-mesh collider | M7 |
| Skeletal mesh + animation | Actor component + Motion | M8 |
