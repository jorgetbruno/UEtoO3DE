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

## What has not been attempted

**The exporter still writes FBX.** Switching it is not just a file-format
swap:

* UE's per-asset glTF export emits a companion `.bin` and material PNGs, and
  `staging.stage()` copies exactly the one file at `o3de_relative_path`. **That
  is settled: export `.glb`.** It is a single file, so staging is unchanged,
  and the node-naming it needs is now implemented and measured against UE's own
  output (above). Choosing `.gltf` instead would mean teaching staging to carry
  companion files, for no gain.
* Our pipeline already exports textures and materials through the manifest, so
  a glTF carrying its own baked materials would duplicate them. `GLTFExportOptions`
  has the knobs (`bake_material_inputs`, `export_preview_mesh`, …) and they are
  unmeasured. Note the `.glb` above did emit its own `.azmaterial`, so this
  duplication is real and not hypothetical.

**The basis is a fresh measurement, not an adaptation.** glTF is Y-up
right-handed in **metres**. The FBX path's correctness rests on a measured
three-step chain (exporter bakes `scale_mesh(-1,-1,1)`, UE's writer negates Y,
SceneAPI applies a 180° yaw — see [LANE_B.md](LANE_B.md)). **None of it carries
over.** `SM_LetterF` is in the fixture precisely because its asymmetry makes an
orientation error visible, and that is how this should be verified — by
measuring an imported letter F, not by reasoning about handedness.

## Files added on this branch

| file | purpose |
|---|---|
| `Tests/o3de/probe_gltf_ingest.py` | stages UE's glTF output, processes it, reports products |
| `Tests/o3de/probe_scene_graph.py` | asks the editor which scene APIs exist; records that no loader is exposed |
| `Tests/o3de/gltf_manifest_script.py` | the ScriptProcessorRule script — ready to use once the rule can be made to wire up; logs at import so "did it run at all" is answerable |
