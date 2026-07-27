# LANE_B.md — Geometry orientation/scale contract (Lane B)

**Status: LOCKED (third revision — verified at the product-buffer byte level).**
This file has been wrong twice, in ways every automated check passed. Both
corrections are kept inline because *how* they were wrong is the operative
lesson: *every prior conclusion here was drawn from an intermediate artifact
or from relative measurements; the only trustworthy assertion is an absolute
read of the final product.* That assertion now exists and runs in CI.

## The contract

Lane B's job: mesh geometry must reach the O3DE product carrying the same
basis map Lane A applies to transforms — **negate Y, ÷100** (UE cm left-handed
→ O3DE meters right-handed).

It gets there through **three Y-negations that net to one**, and a unit
conversion SceneAPI performs by itself:

| # | Stage | Y | Units | Who |
|---|---|---|---|---|
| 1 | `mesh_export.py` bakes `scale_mesh(1,−1,1)` into a temp asset | negate | — | ours, deliberate |
| 2 | UE FBX export | negate | writes cm, `UnitScaleFactor=1.0` | UE, always |
| 3 | O3DE SceneAPI import | negate (honours the declared `FrontAxis=Y sign −1`) | **cm → m ÷100** (honours `UnitScaleFactor`) | O3DE, always |
| | **net** | **negate once** | **÷100** | = Lane A ✔ |

Consequences:

- The intermediate FBX on disk is **verbatim UE geometry** (stages 1+2 cancel).
  `export_fixture.py`/`export_level.py` assert exactly that on every file they
  write.
- The `.assetinfo` sidecar contains **no CoordinateSystemRule at all** — only
  the mesh group, node selection, `MaterialRule` and a bare `LodRule`:

```json
{"values":[{
  "$type":"{07B356B7-3635-40B5-878A-FAC4EFD5AD86} MeshGroup",
  "name":"<mesh_group_name>",
  "nodeSelectionList":{"selectedNodes":["RootNode.<FBXNodeName>"],"unselectedNodes":[]},
  "rules":{"rules":[
    {"$type":"MaterialRule"},
    {"$type":"{6E796AC8-1484-4909-860A-6D3F22A7346F} LodRule"}
  ]}
}]}
```

  Schema notes that remain true (M0, do not "simplify"): node paths are
  `RootNode.<NodeName>` and a wrong path fails the AP job with "No valid
  ModelLodAssets have been added"; the bare `LodRule` must be present or the
  job fails the same way.
- The bake needs the **GeometryScripting** plugin; `export_level.bat` passes
  `-EnablePlugins=GeometryScripting` so arbitrary source projects need no
  setup. A missing plugin fails loudly at the first `DynamicMesh()`.
- `scale_mesh` with a negative-determinant scale corrects triangle winding by
  itself (`probe_m2_mirror2.py`: all 48 face normals map to `B·n`, none to
  `−B·n`). No manual flip — adding one re-inverts every face.

## The product-level assertion (what finally makes this file trustworthy)

O3DE reflects no bounds API to Python and the product buffers are AZ object
streams — but the buffers embed the raw little-endian float32 vertex data, so
known coordinates can be asserted by **byte-pattern search**, immune to the
surrounding serialization. `Tests/m2/test_m2_artifacts.py` does this on every
run:

- engine cube product contains `+0.5f`/`−0.5f` (correct meters), and zero hits
  for `±50` (units unconverted) or `±0.005` (a rule stacked on the unit
  conversion);
- F-mesh product contains `−0.375f` (the Y-asymmetric nub on the negated
  side) and **zero** hits for `+0.375f` (the mirrored signature).

## Correction history — two wrong revisions and why every check passed

**Revision 1 (M0, spike S0.2): "SceneAPI applies no unit conversion and no
axis conversion; the sidecar must carry `scale: 0.01`."** Both halves wrong.
The evidence was `abdata.json` dimensions (source-scene metadata, not product
data), the procprefab's identity transform (node transforms, not vertex data),
and buffer *deltas* between two rule values (relative ratios, never absolute
floats). Byte-level reads show the M0 default-manifest product was already
correct meters — `2.0/0.5/0.125`, zero cm-scale hits — meaning SceneAPI
honours `UnitScaleFactor`, and the 0.01 rule stacked a second ÷100 on top.
Every imported mesh was 100× too small. **Caught by a human**: a bench next to
the ~1 m shader ball, needing uniform scale 100 to look right. No automated
check measured absolute product scale; the fixture acceptance checked entity
*transforms* (correct) and that models loaded (they did — tiny).

**Revision 2 (M2 mid-milestone): "UE's FBX exporter applies the reflection, so
the exporter must not mirror."** True about the FBX writer, wrong as a
pipeline conclusion, because stage 3 was unknown: SceneAPI negates Y *again*
converting the declared FBX axes into O3DE's frame. With no bake, the two
engine negations cancelled and the product came back in **UE's original
handedness** — mirrored relative to Lane A transforms. The FBX-level check
that motivated removing the bake was asserting the wrong artifact; found by
reading the product buffers (`+0.00375` present, `−0.00375` absent, the exact
mirrored signature of the nub).

**Why S0.2 couldn't see either problem:** the M0 canary was mirror-symmetric
about Y (`y ∈ [−12.5, 12.5]`), so Y-negations were invisible; and no absolute
product float was ever read. The FBX header's `FrontAxisSign = −1` — printed
by S0.2's own probe — was the clue to stage 2 and 3 sitting unread in the
original evidence.

## Verification anchors (re-run if the engine/SDK changes)

- `Tests/ue/export_sm_letterf.py` → FBX + `SM_LetterF.ue_reference.json`.
- `Tests/ue/probe_m2_fbx_handedness.py` → stage-2 negation, measured.
- `Tests/ue/probe_m2_mirror2.py` → winding under `scale_mesh(1,−1,1)`.
- `Tests/m2/test_m2_artifacts.py` → the product byte-pattern assertions
  (scale + mirror) and the verbatim-FBX intermediate check.
- `Tests/m1/test_lane_a.py` → the transform half: determinant sign, rotation
  equivariance, composition homomorphism.

## Interaction with Lane A (transforms)

Lane B touches **geometry only**. Actor transforms are Lane A: positions ÷100
with Y negated, rotations conjugated by the same map (negate the quaternion's
x and z), scale untouched and always positive (`ueo3de/lane_a.py`,
`MAPPING.md`). Each manifest records both conventions (`units.lane_a_rule`,
`units.lane_b_rule = "negate_y_net_of_three"`) and the importer refuses a
document that does not match, rather than silently importing a mirrored or
mis-scaled level.
