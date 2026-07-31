# Lane C — importing glTF instead of FBX

Branch `gltf-import`. Everything here is **measured on this machine** against
UE 5.8 and O3DE 26.05; nothing is inferred from documentation. It ends with a
specific blocker and the specific next step, because the feature is not
finished and pretending otherwise would waste the next person's day.

The fork is a BRANCH, not a git worktree, on purpose: the O3DE manifest
registers the UEImporter gem by absolute path
(`D:/Gamedev/UEtoO3DE/O3DE/Gems/UEImporter`), so an editor launched from a
worktree loads the *main* checkout's gem and silently tests the wrong code.

## What works

**UE 5.8 exports glTF from Python.** `Tests/ue/probe_gltf_export.py` (written
earlier, re-read here): `GLTFExporter`, `GLTFStaticMeshExporter`,
`GLTFSkeletalMeshExporter` are all exposed. A static mesh wrote 2,272 B of
`.gltf` + 4,608 B `.bin`, and 143,188 B as a single `.glb`; a skeletal mesh
wrote 22,778 B with `skins=1`.

**O3DE ingests both, including the skeletal case.** `Tests/o3de/probe_gltf_ingest.py`
stages UE's own output and processes it. With **no `.assetinfo` at all** the
Asset Processor produced, from `SM_LetterF.gltf`:

| product | note |
|---|---|
| `sm_letterf.gltf.azmodel` | + `.azlod`, six `.azbuffer`s |
| `sm_letterf_worldgridmaterial_….gltf.azmaterial` | material converted |
| `sm_letterf_gltf.procprefab` | procedural prefab |
| `sm_letterf.glb.azmodel` … | the `.glb` produced the same set |
| `sk_canary.actor`, `sk_canary.gltf.skinmeta` | **EMotionFX actor from glTF** |

The job log names the importer: `SceneBuilder: Using 'AssImp' Import Context
Provider`. FBX goes through a different one, which is the root of everything
below.

**Product naming is byte-for-byte the FBX convention** — `<stem>.<sourceext>.azmodel`.
So `staging.product_path_for`, `physics_product_path_for` and the whole
staging layer need **no change at all**: they append to whatever relative path
the manifest gives them. Every `.fbx` in `ueimporter/` is prose, not logic.

## SOLVED: how to address a glTF node

**A glTF scene graph has no `RootNode`.** That single fact explains every
failure below it. Dumped from inside the Scene Builder
(`Tests/o3de/gltf_manifest_script.py`):

```
node                                content=False   <- the root, EMPTY path
node nodes[0]                       content=True    <- the mesh node
node nodes[0].nodes[0]_2            content=True
```

An FBX graph is rooted at a node literally called `RootNode`, so
`RootNode.<node>` is right there — and names nothing here. Selecting a bare
`RootNode` failed for the same reason: no such node exists.

So the rule is two steps, and with both applied a UE-exported glTF produced
**`sm_letterf.gltf.azmodel` AND `sm_letterf.gltf.joltmesh`** with zero AP
errors — render and cooked physics, the whole collider pipeline:

1. **name the mesh node** (UE leaves it unnamed; `gltf_source.name_mesh_nodes`
   does it on the STAGED copy, so the file the sidecar describes is the file
   the AP reads);
2. **drop the `RootNode.` prefix** from the selection, and unselect nothing
   (`gltf_source.node_path` / `root_path`).

Implemented in `ueimporter/gltf_source.py`, applied by `assetinfo.build` when
given a `source_path`, and pinned by `Tests/perf/test_gltf.py`. Omitting
`source_path` still produces the exact FBX document, because every existing
caller means FBX and `test_pxmesh.py` byte-pins those bytes.

### SOLVED: `.glb` too — the single-file container works

`.glb` is the same scene graph with the JSON as chunk 0 of a binary container,
so naming its node means rewriting that chunk. `gltf_source` now does it, and
**UE's own `SM_LetterF.glb` staged with our sidecar produced the identical
product set with zero AP errors**:

| product | `.gltf` | `.glb` |
|---|---|---|
| `sm_letterf.<ext>.azmodel` | 552 B | **552 B** |
| `sm_letterf.<ext>.joltmesh` | 2369 B | **2369 B** |

Same bytes, same count, plus the `.azlod`, six `.azbuffer`s and the
`.azmaterial`. **This is the container the exporter should target**: one file,
so `staging.stage()`'s copy-exactly-one-file behaviour needs no change at all.

The container layout is measured, not transcribed from the spec — it is what UE
5.8 actually wrote:

```
header  b'glTF' | version 2 | totalLength 143188
chunk 0 b'JSON' | len 1804    padded with SPACES  -> {"nodes":[{"mesh":0}]}
chunk 1 b'BIN\0' | len 141356  padded with a NUL   buffers[0] byteLength 141355
```

Three details there are load-bearing, and each is a silent corruption if
missed:

* a chunk's declared length **includes its padding** (12 + 8+1804 + 8+141356 =
  143188, the declared total);
* the BIN chunk's padding is **not** counted in `buffers[0].byteLength`;
* the two chunks **pad with different bytes**. Padding chunk 0 with NULs was
  tried, and `json.loads` threw `Extra data: line 1 column 1822` on the next
  read. Space is JSON whitespace; NUL is not.

So the rewrite touches chunk 0 and copies every other chunk through verbatim,
padding and all, rather than re-deriving a 141 KB buffer it was not asked to
change. `test_gltf.py` runs against the **real UE `.glb`**, not a synthetic
one, and asserts the BIN chunk comes back byte-identical — a hand-built
container would only prove the code agrees with itself. Four mutants were run
against it: dropped padding, a truncated BIN payload, a stale total-length
header and NUL-padded JSON are each caught.

### Three traps found on the way, each worth a cycle to someone else

* **An unrecognised `.assetinfo` entry is silently dropped** — no warning, no
  failed job. A `ScriptProcessorRule` with a bare `"$type": "ScriptProcessorRule"`
  reported *zero errors* and simply did nothing. Read silence as "ignored",
  never "accepted".
* **`ScriptProcessorRule` needs the UUID `$type` AND a project-relative
  `scriptFilename`.** A filename beside the source is ignored.
* **The engine does not call bare module functions.** The script must connect a
  handler to `azlmbr.scene.ScriptBuildingNotificationBus` and
  `add_callback("OnUpdateManifest", …)`. And **`scene_api` is not importable
  inside the builder** (it is in the editor), so the raw
  `GetRoot`/`GetNodeName`/`GetNodeChild` calls are required — code copied from
  an O3DE scene-scripting sample will fail here.

## What blocked it, before the graph was read

**Our `.assetinfo` cannot address a glTF node.** The importer writes an
explicit `NodeSelectionList` so the mesh group's name — and therefore the
product's name and asset id — is deterministic. For FBX the path is
`RootNode.<node UE named>`. For glTF every candidate was rejected with the
same warning, and the job failed outright:

    W: SceneAPI: MeshGroup SM_LetterF wasn't found in the list of selected nodes.
    E: ModelAsset: No valid ModelLodAssets have been added to this ModelAsset.
    E: Error: Failure during conversion and exporting.

Tried, in order, each costing an AP cycle:

| selection | result |
|---|---|
| `RootNode.SM_LetterF` (UE's own output) | rejected — UE writes the node **unnamed**; only the *mesh* is named |
| `RootNode` (take the whole scene) | rejected — selecting the root does not imply its children here |
| `RootNode.SM_LetterF` after naming the node in the JSON | rejected |
| `RootNode.SM_LetterF_2` (the name the procprefab showed) | rejected |

What was learned along the way, all measured:

* UE's glTF writer leaves `nodes[i].name` **absent**; only `meshes[i].name`
  carries `SM_LetterF`.
* Naming the node *does* reach SceneAPI — the default group's stable UUID
  changed and its products were deleted and rebuilt — but the mesh-bearing
  graph node comes out as `<name>_2`.
* That `_2` is **not** a node-vs-mesh name collision: renaming the mesh to
  `SM_LetterF_mesh0` while the node stayed `SM_LetterF` still produced
  `SM_LetterF_2`.

**The next step is to read the graph, not to guess it again.** Four guesses
were wrong. `azlmbr.scene` exposes SceneAPI *data types* (`MeshData`,
`BoneData`, …) but no scene loader (`Tests/o3de/probe_scene_graph.py`), so the
paths are not reachable from the editor's Python.

### The ScriptProcessorRule route was tried and did not wire up

O3DE has exactly the right mechanism: a `ScriptProcessorRule` names a Python
script that the **Scene Builder** runs with the loaded scene in hand, and
`scene_api.scene_data.SceneGraph` walks it (`get_root`, `get_node_child`,
`get_node_sibling`, `get_node_name`). `Tests/o3de/gltf_manifest_script.py` is
that script: it logs at import, defines both plausible entry points
(`OnUpdateManifest`, `OnPrepareForExport`) and dumps every node path.

It never ran. Two sidecar forms were tried:

| `$type` | `scriptFilename` | result |
|---|---|---|
| `ScriptProcessorRule` | `gltf_manifest_script.py` | ignored |
| `{E61EDCBC-…} ScriptProcessorRule` | same, then project-relative | ignored |

**And both runs reported zero errors.** That is the finding worth carrying
forward: *an `.assetinfo` entry the engine does not recognise is silently
dropped* — no warning, no failed job, just absent behaviour. The same class of
trap as the `NodeSelectionList` capital-N quirk, and it is why the first of
these runs looked like a success. Anything hand-authoring sidecars should
assume silence means "ignored", never "accepted".

What is left untried: the entry-point name is not knowable from the installed
engine (headers only, no `.cpp`, no samples), and the rule may need registering
some other way. The remaining route with no unknowns is to open the file in
**Scene Settings** and save — the tool writes a full `.assetinfo` including the
exact `selectedNodes`, which can then simply be read.

Until then no sidecar is written for glTF: the importer is unchanged on this
branch, because a format branch that silently emits a sidecar the AP rejects
is worse than none.

## DONE: the exporter emits `.glb`

`UEO3DE_MESH_FORMAT=glb` switches the **static mesh** container. Nothing else
moves — skeletal meshes and animations stay FBX — so a glb run is deliberately
a **mixed-format export**. That is not a compromise; it is the case worth
exercising, because staging and the sidecar writer key on each file's own
extension (`gltf_source.is_gltf_source`) and never on a global flag. A typo
**raises**: falling back to FBX would make a whole export look successful in
the wrong container.

### The bake is kept, and that makes the basis correction vanish

The measurement above (`Rz180`) compared a **raw** glTF against a **baked**
FBX. Keeping the Lane A bake for glb too should make them coincide, and it
does — measured on the fixture, both products present in one project:

| | glb product centroid | fbx product centroid | `glb == M · fbx` |
|---|---|---|---|
| `sm_letterf` | (−0.2097, −0.0659, +1.4452) | (−0.2097, −0.0659, +1.4452) | **identity** |
| `sm_letterf_mx` | (+0.2097, −0.0659, +1.4452) | (+0.2097, −0.0659, +1.4452) | **identity** |

**Identity, for the mirrored `#mx` variant as well.** So:

* the importer needs **no per-format branch at all**;
* `units.lane_b_rule` stays `negate_y_scene_rz180` — it records the *net* map,
  which is unchanged, and only the intermediate container differs.

That is why the bake is kept rather than exporting raw: matching bases beats
carrying a second correction that every downstream consumer would have to know
about.

### The export verifier converts, rather than being switched off

`export_level.py` checks every written mesh against the bounds the exporter
expects — the guard that catches a bake missing or doubled. It stays on for
glb. The exporter keeps **one** recorded expectation per mesh (the FBX-file
one) and `gltf_reader.expected_from_fbx_bounds` converts it, so the two can
never disagree:

```
baked = (fbx_x, -fbx_y, fbx_z)                  undo the FBX writer's Y flip
glTF  = (baked_x, baked_z, baked_y) / 100       Y-up, cm -> m
  =>  glTF = (fbx_x, fbx_z, -fbx_y) / 100
```

It depends only on the two writers, not on the bake, so it holds for `#mx` too.
Negating an axis **swaps that axis's min and max** — the easy thing to get
wrong, and pinned directly in `Tests/perf/test_glb_export.py` because a
symmetric mesh would not catch it. The tolerance converts too: a 1e-3 **cm**
tolerance compared against metre-scale values would pass anything.

Validated end to end on the fixture: export PASS (including the intermediate
bounds check on all 7 meshes), AP 0 errors, M2 import PASS, and **M2 acceptance
PASS** — the same 30-entity, 1 cm / 0.1°, model-asset-and-vertex-count bar the
FBX path meets. `m2_acceptance.py` now reads `UEO3DE_EXPORT` like
`m2_import.py` already did, so glb is held to the identical assertions rather
than to a weaker set written for it.

### Two predictions this page made, and the measurement that killed both

Written here *before* the exporter existed, and both **wrong**:

* ~~"a glTF manifest needs its own `lane_b_rule` rather than reusing
  `negate_y_scene_rz180`"~~ — no. The rule records the **net** map, and with
  the bake kept the net map is unchanged. The value stays.
* ~~"the `Rz180` must be applied for glTF and NOT for FBX; a mixed project is
  the dangerous case"~~ — no. With the bake kept the two products are
  **identical**, so there is no per-format branch to get wrong. The mixed
  project is now the *safe* case, and the fixture exercises it (static `.glb`,
  skeletal `.fbx`, in one level).

Both were reasoned from the raw-export measurement rather than measured on a
baked one. The lesson is the same one the four node-path guesses taught: a
prediction that costs one measurement to check is not worth writing down as a
finding.

### The embedded-texture trap — 164 MB of PNG per mesh

I had this filed above as "wasted cache, not a correctness bug." **It was far
worse than that,** and it only showed up on a real level:

| `sm_armour_a_kneeguards.glb` | |
|---|---|
| embedded PNG | **164.1 MB** (`images=10`, `image/png`) |
| geometry | 0.1 MB |

The whole Siege export was **11.9 GB** against the FBX path's 3.4 GB. What gave
it away was several completely *different* meshes coming out byte-for-byte the
same 164.2 MB — geometry does not do that. Every log line said the export
succeeded.

UE renders material graphs to textures and embeds them in the container. Three
`GLTFExportOptions` defaults are wrong for this pipeline:

```
texture_image_format = PNG            -> GLTFTextureImageFormat.NONE
bake_material_inputs = USE_MESH_DATA  -> GLTFMaterialBakeMode.DISABLED
export_preview_mesh  = True           -> False
```

Since the pipeline already exports textures and materials through the manifest
and the importer assigns from there, every one of those bytes was a duplicate.
After the fix: **158 meshes total 82.7 MB, down from 8,636 MB** — and
`sm_letterf.glb` is 5,684 bytes against the FBX's 19,584, *smaller* than what
it replaces. Product geometry is unchanged (still identity vs FBX, `#mx`
included).

**Two guards, because the failure mode is silence.** The options `raise` if any
cannot be set, and `_refuse_embedded_images` inspects the **written file** and
fails if it carries any images. Checking the result rather than the settings is
the point: a knob that stops working in a future UE version fails in precisely
the same silent way.

### Genuinely still open
* Skeletal meshes and animations still export FBX. glTF ingests skeletal fine
  (`.actor` + `.skinmeta`, measured), but the skeletal Lane B rule is a
  separate chain with its own `compose_rz180`, and switching it needs its own
  measurement — not this one, which covers statics only.

## SOLVED: the basis is one 180° yaw

glTF is Y-up right-handed in metres, and the FBX path's correctness rests on a
measured three-step chain (exporter bakes `scale_mesh(-1,-1,1)`, UE's writer
negates Y, SceneAPI applies a 180° yaw — [LANE_B.md](LANE_B.md)). None of that
carries over, so the basis was **measured, not adapted**. `SM_LetterF` is the
instrument because its asymmetry makes an orientation error visible.

### Step 1 — the cooked physics AABB (`Tests/o3de/probe_gltf_basis.py`)

`BoundsRequestBus.GetEntityLocalBoundsUnion`, which `lane_b_measure.py` used,
**does not exist in this build**: `probe_bounds_api.py` scanned every `azlmbr`
module and found only `MeshComponentNotificationBus`, with `BoundsRequestBus`
bound to `None` in `components`, `entity` and `framework` alike. The physics
AABB is a working instrument here and measures the geometry the *collider*
pipeline gets, so that is what was used — box control first, all three bodies
in one game-mode session:

| | extents (m) | AABB centre, relative to the entity |
|---|---|---|
| UE asset space | 1.0, 0.5, 2.0 | 0, **+0.125**, 1.0 |
| glTF product | 1.0, 0.5, 2.0 | 0, **+0.125**, 1.0 |
| FBX product | 1.0, 0.5, 2.0 | 0, **−0.125**, 1.0 |

So: **metres, cm→m applied, Z-up, no axis permutation, in both formats.**

### Step 2 — why the AABB could not finish the job

SM_LetterF's bounds are **symmetric in X** (−50…+50 cm), so an X mirror — which
flips winding and would ship silently as a backwards level — is *invisible* to
an AABB. The Y asymmetry it sees; the X mirror it cannot.

So `Tests/o3de/probe_gltf_vertices.py` reads the actual vertex positions out of
the `.azbuffer` products (no editor needed — plain floats on disk, located by
extent shape rather than a hard-coded header offset). **93 vertices in UE, 93 in
the glTF file, 93 in every product**, so the comparison is exact and not a
resample:

| | centroid (m) | vs UE |
|---|---|---|
| UE asset space | (−0.2097, +0.0659, +1.4452) | — |
| FBX product | (−0.2097, **−0.0659**, +1.4452) | `diag(1,−1,1)` |
| glTF product | (**+0.2097**, +0.0659, +1.4452) | `diag(−1,1,1)` |

And there the X mirror shows up — the AABB had reported the glTF as matching UE
exactly, and at vertex level it does not.

### The answer

Checked over **all 93 vertices as sets, with zero deviation**, and against seven
candidate maps of which **exactly one** fits:

```
glTF product  ==  Rz180 · FBX product        diag(-1,-1,1),  det +1
```

**A proper rotation.** The two formats differ by a lossless 180° yaw — no
mirror, no winding flip, so **none of Lane B's `#mx` mirrored-variant machinery
is needed for glTF**. The compensation is one `Rz180`, and the codebase already
has that operation as `skel_build.compose_rz180`, proven as a matrix identity in
`Tests/m8/test_skel_build.py`.

That is the good outcome. Had it come out `det −1`, glTF would have needed the
whole mirrored-variant path built for `#mx`.

## Files added on this branch

| file | purpose |
|---|---|
| `Tests/o3de/probe_gltf_ingest.py` | stages UE's glTF output, processes it, reports products |
| `Tests/o3de/probe_scene_graph.py` | asks the editor which scene APIs exist; records that no loader is exposed |
| `Tests/o3de/gltf_manifest_script.py` | the ScriptProcessorRule script that dumped the graph and settled node addressing |
| `Tests/o3de/probe_bounds_api.py` | scans `azlmbr` for a bounds bus; records that this build has none |
| `Tests/o3de/probe_gltf_basis.py` | cooked-physics AABB per format, box control first (editor) |
| `Tests/o3de/probe_gltf_vertices.py` | vertex-level basis, the one that catches an X mirror (no editor) |
| `Tests/ue/data/SM_LetterF.glb` | UE's real `.glb`, **tracked** — `Tests/**/results/` is ignored, and a suite whose only fixture is ignored output fails on a fresh clone |
