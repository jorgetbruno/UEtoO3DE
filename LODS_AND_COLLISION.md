# LODS_AND_COLLISION.md — LOD chains, Nanite, and multi-hull collision

What the pipeline does with a Nanite mesh's geometry and a mesh's simple
collision, why, and what was measured before each decision. Everything here
was established on the VOL4 RetroCars and Docks packs (UE 5.8 → O3DE 26.05),
2026-08-23 to 2026-08-25. Knob reference at the end.

## 1. The LOD chain

An FBX export of a multi-LOD mesh wraps it in an `FbxLODGroup`. SceneAPI
flattens that to `RootNode.<name>` with children `<name>_LOD<i>`; the sidecar's
render group selects `_LOD0` and its `LodRule` selects one node per further
LOD, and the Asset Processor produces one `.azmodel` with N `.azlod`
products (index buffers halving with the triangle counts — measured
81,699 / 41,077 / 20,761 / 10,609 bytes on the first probe). Without the
sidecar the AP fragments the group into one single-LOD model per node.

Single-mesh files are exported **without** the LODGroup wrapper: wrapping a
lone mesh changes every node path, and every existing sidecar pins the bare
path. glTF exports carry no chain (a glb with several mesh nodes is exactly
what staging refuses), so the chain is FBX-only.

Rules the chain writer learned the hard way:

* **Five LODs total, on both sides.** Of 2,272 NYC meshes the Asset
  Processor failed exactly two — the only two with six LOD nodes (five UE
  render LODs plus the source): Atom's `ModelAssetCreator::AddLodAsset`
  crashed the AssetBuilder outright (0xC0000005, no error message).
  Atom's `LodCountMax` is 10, so the sixth trips something deeper; five is
  the measured ceiling. The exporter emits LOD 0 + four reductions. The
  sidecar also caps its selection at five, but that alone does NOT save a
  six-node file: SceneAPI takes an `FbxLODGroup`'s children as LODs
  regardless of what the LodRule selects (measured — the capped sidecar
  crashed the same way), so a file that carries six must be re-exported.

* `ScriptProcessorRule` must be a top-level manifest entry — nested in a
  group's rules it is silently ignored.
* The export's intermediate bounds expectation is the **union** of every
  exported LOD's box: quadric reduction bulges far LODs a centimetre or two
  outside LOD0 (the car reached −253.43 where LOD0 ends at −251.95), and the
  1e-3 cm tolerance stays exact against the union.
* The material-slot remap is computed over the **union of ids across the
  whole chain** and applied to every LOD (`_compact_slots`): the first chain
  export remapped LOD0 only, and any mesh whose LOD0 used a sparse or
  reordered slot subset rendered its far LODs with the wrong materials.

## 2. Nanite: what UE shows versus what UE stores

A Nanite asset carries two geometries: the **source** (what Nanite renders at
every distance — 90,023 tris on SM_Car_24a, 157,892 on the box truck) and a
**fallback** chain of classic render LODs that Nanite never displays
(auto-generated at build, roughly 6/3/1.5/0.7 % of the source — the box
truck's is `[10732, 5365, 2682, 1342]`). The Static Mesh Editor's LOD
dropdown shows the fallback chain; `r.Nanite 0` shows it in a level.

Two read paths exist:

| `UEO3DE_NANITE_FALLBACK` | LOD 0 | far LODs | when |
|---|---|---|---|
| unset / `0` (**default**) | the Nanite **source** (`MAX_AVAILABLE`), reduced to `UEO3DE_LOD0_RATIO` (0.25) | the source simplified to `UEO3DE_LOD_RATIOS` budgets (10/4/1.5/0.6 %) | always, unless you know the fallback is sound |
| `1` | UE's fallback mesh (`RENDER_DATA` LOD 0) | UE's render LODs 1..N verbatim | **hazard, see below** |

**The fallback mesh's material sections are wrong on this pack** — measured
on 2026-08-25 after a day of the fallback as default brought the black
wagons back at LOD 0 only. Per-id regions of the `RENDER_DATA` LOD 0 copy
and the LOD 1 copy, same read path, same space: the wagon's fallback id 1
covers the chassis (z −1..69 cm) where LOD 1's id 1 is the body (z 24..153);
the box truck's fallback id 2 is the box body where LOD 1's id 2 is the
undercarriage. Triangle counts per id still halve neatly from LOD 0 to LOD 1,
so counts hide it. It happens on exactly the nine meshes whose source
sections are permuted (§3), and `get_section_material_list` reports the
identity for the fallback, so no remap can be derived from the API. Nanite
never renders the fallback, and LOD 1..3 are generated from the source
description rather than from it, which is how a pack ships with a broken
LOD 0 nobody has seen. Until a rule is measured, the fallback read is opt-in
and documented as such; the light default is the source read, reduced.

Under the source read the far LODs are **simplified from the source**, never
read from the fallback chain, floored by the fallback count and capped at
LOD 0's count; the reduction runs through
`apply_editor_simplify_to_triangle_count` — the same quadric reducer UE's
own LOD generation uses (no options object; editor-only, which the export
session always is). At the fallback-chain budgets (~9 % at LOD 1) and the
GeometryScript reducer, LOD 1 already collapsed hoods at moderate range —
"it gets worse when I fall back more, this is lod 1".

Measured chains on SM_Truck_02a (157,892 source tris):

```
UEO3DE_NANITE_FALLBACK=1 (fallback read)  [10732, 5365, 2682, 1342]   materials WRONG at LOD 0
source read, LOD0 1.0, ratios .2/.08/.03/.012   [157892, 31578, 12630, 4736, 1894]
source read, LOD0 0.5                     [78946, 31578, 12630, 4736, 1894]
source read, LOD0 0.25, ratios .15/.05    [39473, 23684, 7894, 3947, 3947]
default (LOD0 0.25, ratios .1/.04/.015/.006)  ~[39473, 15789, 6316, 2368, 947]
```

And the default measured on the exported fleet (the fallback-count floor
takes over where a ratio would drop below UE's own chain):

```
sm_tow_truck_01b (100,554 source)  [25136, 10054, 4717, 2358, 1179]
sm_wagon_01a      (93,712 source)  [23427,  9370, 4323, 2162, 1081]
sm_car_24a        (90,023 source)  [22504,  9001, 3599, 1692,  845]
```

Every LOD beyond the ratio list uses half the last entry. The far ladder is
budgeted against the **original** source count, so reducing LOD 0 does not
shrink LOD 1..N with it; `UEO3DE_LOD0_RATIO=1.0` restores the full source.

Not yet done: O3DE switches LODs on its own screen-coverage defaults. UE
stores per-LOD screen sizes on every mesh (`EditorStaticMeshLibrary.
get_lod_screen_sizes`; the fleet drops to LOD 1 at ~0.10–0.18) and the
importer does not author them yet. If detail drops sooner than UE's, that is
the lever.

## 3. Material ids are section ordinals (the "black car")

`copy_mesh_from_static_mesh` numbers a copied mesh's triangle material ids by
**section order of the read LOD**, not by the asset's material-slot order.
`_compact_slots` trusted them as `static_materials` indices. On most meshes
the two orders agree; where UE stores the sections permuted —
SM_Wagon_01a's source sections run `[(MI_Wagon_01a → slot 1),
(MI_Wagon_01b → slot 0), …]`, SM_Truck_02a's `[0, 4, 5, 1, 2, 3]` — every
exported material name landed on the wrong geometry. Per mesh, in the glb
and FBX containers alike, since the first export; the glb "ground truth"
carried the identical mislabeling, which is why every glb comparison kept
confirming the wrong thing.

The measurement that cracked it was the user's hand-fix — swapping
`mi_wagon_01a`/`mi_wagon_01b` on the slots made the wagon render right — and
the roof probe that confirmed it: the up-facing roof skin of SM_Wagon_01a
carried the name `MI_Wagon_01b`, whose atlas (`TX_Wagon_01b`) is the interior
fabric; the paint atlas is `TX_Wagon_01a` (the license plate 5X0H785 sits in
it). Disambiguating outer skin from headliner by triangle **facing** was the
instrument; texture thumbnails were the ground truth.

Fix: `_remap_section_ids_to_slots` rewrites the ids through
`get_section_material_list_from_static_mesh`, which returns each section's
MaterialIndex for both read paths, `MAX_AVAILABLE` included (probed on 5.8).
Identity maps are left alone; a failed query on a permuted mesh raises rather
than mislabels. Verification instrument, offline: per-LOD per-material
area-share and centroid inside the exported FBX — 9 of 63 meshes disagreed
between LOD0 and LOD1 before, 0 after.

## 4. Collision: three ways to represent UE's hulls

UE keeps simple collision as a list of elements on the mesh
(`BodySetup.AggGeom`). Boxes, spheres and capsules author as primitives.
Convex elements are the interesting case: a single convex hull cannot hold a
concavity, so one hull over the whole render mesh fills in truck beds and
wheel wells. `UEO3DE_COLLISION`, read at **staging**, selects:

| mode | cooked collider | cost | notes |
|---|---|---|---|
| `single` (default) | one convex hull of the render mesh | none | the original behaviour; sidecar bytes unchanged |
| `vhacd` | V-HACD decomposition of the render mesh, capped at UE's element count (`UEO3DE_DECOMPOSE=<n>` lowers the cap) | minutes per dense mesh — so it cooks from LOD 1 when a chain exists | approximates UE's hulls with a different algorithm |
| `ue` | UE's own hull elements | milliseconds | exact where elements are disjoint; overlapping elements merge |

How `ue` mode works, each step measured:

1. `KConvexElem.transform` and `vertex_data` are protected from Python, so
   elements cannot be edited or baked — but the **whole `agg_geom` struct
   assigns** onto the temp asset's BodySetup intact (10 of 10 on
   SM_Truck_02a), and `export_text()` serializes even the protected fields
   (every element's transform is identity on this pack).
2. Exporting with `collision=True` writes them as **one merged node**,
   `UCX_<node>_LOD0` beside a LOD chain (`UCX_<node>` without), 132 vertices
   for the truck — not the one-node-per-element shape UE's own import
   convention suggests. Every FBX now carries the hulls it has; there is no
   export-side switch, staging decides.
3. The hulls are verbatim **source space** while the render mesh is baked
   under `diag(-1,-1,1)` (a half-turn about Z). SceneAPI converts both
   identically, so the hull cloud arrives as the render cloud's 180° twin —
   offline, the box truck's render centroid sits at y = +87 cm and the hull
   cloud's at −82 cm. The physics group (and only it) carries a
   `CoordinateSystemRule` half-turn about Z (`useAdvancedData`, quaternion
   `[0, 0, 1, 0]`); a half-turn is its own inverse, so there is no direction
   to get wrong. Mirrored `#mx` variants keep the whole-mesh hull — a
   reflection is not a rotation.
4. One selected node would cook back into one hull, so the group also
   decomposes, capped at UE's element count. V-HACD over a ~100-vertex cloud
   of convex pieces recovers the disjoint ones and merges overlapping ones:
   tow truck 10 of 10, pickup 9 of 10, wagon 4, box truck 5 of 10. Products
   4–8 KB against 5.7 MB for the whole-mesh hull.
5. Both gems' exporters cook **one shape per selected node** on a convex
   group — merging lives in `TriangleMeshAssetParams` in Jolt and PhysX
   alike (read from the Jolt source; PhysX from its headers, the binary
   install ships no `.cpp`). Jolt is measured; PhysX is structural.

The export's mirror-bounds check (`fbx_reader.vertex_stats`) leaves `UCX_`
geometry out — it swept the source-space hulls in with the render mesh and
failed every hull-bearing car until it did.

Counting cooked shapes offline: the per-shape
`JoltAssetColliderConfiguration` type id
`{8EC9D61B-9180-47C7-87C0-17E13C5A8358}` appears once per shape in a
`.joltmesh` (`Tests/perf` has no wrapper yet; the session script
`count_joltmesh_hulls.py` is three lines).

## 5. Importer-side fixes from the same investigation

* **Model-rows wait is a stall budget.** `wait_for_model_rows` treated
  `MODEL_READY_WAIT_FRAMES` (600) as a total; LOD chains quintupled the
  product count and on a cold cache the budget can expire mid-stream, after
  which every straggler is silently flattened to one material on the default
  slot. The budget now resets whenever any entity comes ready.
* **FBX name-dedup slots.** Two UE slots filled with the same material export
  as `MI_X` and `MI_X_1`; the suffixed slot matched nothing and kept the model
  default (sm_van_02e, 2,969 of 5,602 triangles). `finish_material_slots`
  probes numeric-suffix variants of every assigned label →
  `MAT_SLOT_DEDUP_SUFFIX`.

## 6. Things that look like bugs and are not

* A Static Mesh Editor showing `0.00 MB Nanite` with a `*` on the tab is an
  **unsaved toggle** in that session; every VOL4 mesh is Nanite on disk.
* Placeholder `StaticMeshActor`s with a null mesh (the Overview map has 97 —
  vehicles from VOL packs not installed) export as transform-only entities.
  Correct, if silent; a warning code for it is pending.
* After a container switch (glb → FBX) the old `.glb.azmodel` products go
  away. Prefab instances update through the prefab; **hand-placed entities**
  that referenced the old products render fallback models until re-pointed.
* Per-slot material overrides made by hand in a level **persist as instance
  patches** and will double-invert a now-correct mesh. Revert them after a
  fix lands.

## 7. Workflow (the order that works)

```
rem export (UE)          Tests\ue\export_level.bat <uproject> /Game/Path/Map <ExportDir>
rem sweep                 delete files under <ExportDir>\Assets not referenced by manifest.json
rem stage (O3DE)          python Tests\m2\m2_stage.py --project <proj> --manifest <ExportDir>\manifest.json --source-assets <ExportDir>\Assets
rem sweep staging         delete staged files under <proj>\Assets\uetoo3de not in the manifest (+ their .assetinfo)
rem process               AssetProcessorBatch --project-path=<proj> --platforms=pc   (TWICE: the first pass saves an incomplete catalog)
rem import                set UEO3DE_EXPORT=<ExportDir> & set UEO3DE_SCRATCH_LEVEL=UEO3DE_Scratch & Tests\o3de\run_o3de_python.bat Tests\m2\m2_import.py <result> <proj>
rem verify                Tests\m6\m6_level_renders.py, Tests\m3b\m3b_level_collides.py (UEO3DE_PREFAB=<proj>/Prefabs/<Level>.prefab)
```

Imports author in the seeded `UEO3DE_Scratch` level and **refuse** any level
holding more than 24 entities (`UEO3DE_SCRATCH_OK=1` overrides) — the
consequence of an import that once stripped a user's DefaultLevel. Never
feed Python a here-string in PowerShell (`python - @'…'@` opens an
interactive REPL that spins forever on console errors); write a script file.

## 8. Knob reference

Export time (UE session; `export_level.bat` passes the environment through):

| knob | default | meaning |
|---|---|---|
| `UEO3DE_MESH_FORMAT` | `fbx` | `glb` exports static meshes as glTF binary (no LOD chain, no hull nodes) |
| `UEO3DE_LOD_CHAIN` | on | `0` exports LOD 0 only |
| `UEO3DE_NANITE_FALLBACK` | off | `1` exports UE's fallback mesh + render LODs instead of the source (materials measured wrong at LOD 0 on permuted-section meshes) |
| `UEO3DE_LOD0_RATIO` | `0.25` | LOD 0's share of the Nanite source, in (0, 1]; `1.0` = full source |
| `UEO3DE_LOD_RATIOS` | `0.10,0.04,0.015,0.006` | far-LOD shares of the source, LOD 1 outward; floored by the fallback count, capped at LOD 0 |

Staging time (`m2_stage.py` / the import dialog):

| knob | default | meaning |
|---|---|---|
| `UEO3DE_COLLISION` | `single` | `vhacd` or `ue`, see §4 |
| `UEO3DE_DECOMPOSE` | off | hull cap for V-HACD (`1` = element count, up to 64); `vhacd` mode implies it |
| `UEO3DE_JOLT_COOK` / `UEO3DE_PHYSX_COOK` | auto | force a backend's cooked-mesh groups on/off |

Import time (editor session):

| knob | default | meaning |
|---|---|---|
| `UEO3DE_EXPORT` | `Exports/Fixture_01` | export folder to import |
| `UEO3DE_SCRATCH_LEVEL` | `DefaultLevel` on test projects | level the editor checks open; user projects must pass `UEO3DE_Scratch` |
| `UEO3DE_SCRATCH_OK` | off | import into a populated level anyway |
| `UEO3DE_CHUNK` / `UEO3DE_CHUNK_CEILING` | `1/1` / 4000 | slice a level too large for one prefab |
| `UEO3DE_MODEL_POLL_FRAMES` | 2 | poll granularity of the shared model-rows wait |

Every knob's parser refuses values it does not recognise. That is a house
rule, not pedantry: each of these changes the bytes of every mesh in a
level, and a typo that silently picked a direction has cost an Asset
Processor pass over a whole project more than once.
