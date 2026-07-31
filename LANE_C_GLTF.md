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

## What blocks it

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

**The next step is to read the graph, not to guess it again.** Three guesses
in a row were wrong. `azlmbr.scene` exposes SceneAPI *data types*
(`MeshData`, `BoneData`, …) but no scene loader
(`Tests/o3de/probe_scene_graph.py`), so the paths are not reachable from the
editor's Python. The two routes that remain:

1. O3DE's **scene-manifest Python callback** (`scene_api`, run inside the
   Scene Builder), where the loaded scene and its graph are in hand; or
2. open the file in **Scene Settings** and save — the tool writes a full
   `.assetinfo` including the exact `selectedNodes`, which can then be read.

Either yields the rule in one shot. Until then no sidecar is written for glTF:
the importer is unchanged on this branch, because a format branch that
silently emits a sidecar the AP rejects is worse than none.

## What has not been attempted

**The exporter still writes FBX.** Switching it is not just a file-format
swap:

* UE's per-asset glTF export emits a companion `.bin` and material PNGs, and
  `staging.stage()` copies exactly the one file at `o3de_relative_path`. That
  argues for `.glb` (single file, staging unchanged) — which in turn means any
  node-naming fix must rewrite the JSON chunk inside a binary container
  (chunk lengths, 4-byte padding), not a plain JSON file.
* Our pipeline already exports textures and materials through the manifest, so
  a glTF carrying its own baked materials would duplicate them. `GLTFExportOptions`
  has the knobs (`bake_material_inputs`, `export_preview_mesh`, …) and they are
  unmeasured.

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
