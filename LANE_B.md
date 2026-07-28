# LANE_B.md — Geometry orientation/scale contract (Lane B)

**Status: LOCKED (fourth revision — verified at the product-buffer byte level
on BOTH asymmetric axes).**
This file has been wrong three times, in ways every automated check passed.
All corrections are kept inline because *how* they were wrong is the operative
lesson: *conclusions drawn from intermediate artifacts, relative measurements,
or a single axis do not hold; the only trustworthy assertion is an absolute
read of the final product on every axis the canary can distinguish.* Those
assertions now exist and run in CI.

## The contract

Lane B's job: mesh geometry must reach the O3DE product carrying the same
basis map Lane A applies to transforms — **negate Y, ÷100** (UE cm left-handed
→ O3DE meters right-handed).

The measured stages:

| # | Stage | Map | Units | Who |
|---|---|---|---|---|
| 1 | `mesh_export.py` bakes `scale_mesh(−1,−1,1)` into a temp asset | diag(−1,−1,1) | — | ours, deliberate |
| 2 | UE FBX export | diag(1,−1,1) (LH→RH) | writes cm, `UnitScaleFactor=1.0` | UE, always |
| 3 | O3DE SceneAPI import | **diag(−1,−1,1) — a 180° yaw, a PROPER rotation** | **cm → m ÷100** (honours `UnitScaleFactor`) | O3DE, always |
| | **net** | **diag(1,−1,1)** | **÷100** | = Lane A ✔ |

Stage 3 is the load-bearing fact of revision 4: *honouring a declared
coordinate frame means rotating into it.* An importer never mirrors — a mirror
would change the model, a rotation only re-expresses it. The old table wrote
stage 3 as "negate Y", and on the Y axis the two maps are indistinguishable.

Consequences:

- The intermediate FBX on disk is **mirror-X of the UE source** for normal
  meshes (stages 1+2 net diag(−1,1,1)), and **verbatim UE source** for
  mirrored `#mx` variants (whose bake is `scale_mesh(1,−1,1)`).
  `export_fixture.py`/`export_level.py` assert the expected bounds per entry.
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
- Winding: the normal bake has determinant **+1** and preserves winding; the
  `#mx` variant bake has determinant −1 and `scale_mesh` corrects winding by
  itself (`probe_m2_mirror2.py`). No manual flip in either path — signed
  volume through the whole chain is preserved (`probe_mirror_bake.py`).

## Mirrored variants (negative-scale fidelity, M4.5)

A UE actor with an odd number of negative scale axes is a true mirror, which
O3DE transforms cannot carry. The exporter folds the signs into the rotation
(`lane_a.fold_scale_signs`, exact for every pattern — `test_lane_a.py`) and
references a **mirror-X mesh variant**: a second asset entry whose `ue_path`
is the real path plus a literal `#mx`, baked with the opposite-determinant
vector. Even negative counts are pure 180° rotations and need no variant.
The importer does not know mirrors exist.

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
  side) and **zero** hits for `+0.375f` (the Y-mirrored signature);
- **X counts** (revision 4): the F's stem+nub mass sits at `−0.5`/`−0.25`
  with at least 2× the opposite plane's hits — the discriminator between
  diag(1,−1,1) and diag(−1,−1,1) that Y can never provide — and the `#mx`
  variant product carries the exact swapped counts.

## Correction history — three wrong revisions and why every check passed

**Revision 1 (M0, spike S0.2): "SceneAPI applies no unit conversion and no
axis conversion; the sidecar must carry `scale: 0.01`."** Both halves wrong.
The evidence was `abdata.json` dimensions (source-scene metadata, not product
data), the procprefab's identity transform (node transforms, not vertex data),
and buffer *deltas* between two rule values (relative ratios, never absolute
floats). Byte-level reads show the M0 default-manifest product was already
correct meters — meaning SceneAPI honours `UnitScaleFactor`, and the 0.01 rule
stacked a second ÷100 on top. Every imported mesh was 100× too small.
**Caught by a human**: a bench next to the ~1 m shader ball.

**Revision 2 (M2 mid-milestone): "UE's FBX exporter applies the reflection, so
the exporter must not mirror."** True about the FBX writer, wrong as a
pipeline conclusion, because stage 3 was unknown. With no bake the product
came back Y-mirrored relative to Lane A transforms. Found by reading the
product buffers (`+0.00375` present, `−0.00375` absent).

**Revision 3 (M2..M6): "stage 3 negates Y; three negations net one."** The
revision-2 fix asserted the product's **Y axis only** — and on Y,
"negate Y" and "180° yaw" are the same map. Stage 3 is actually
diag(−1,−1,1), so with the (1,−1,1) bake the product was
diag(−1,−1,1)·source: **every imported mesh locally X-mirrored**, perfectly
self-consistently — colliders bake from the same geometry, entity transforms
were all correct, and the letterF's X *bounds* are symmetric, so bounds
checks were blind too. The product had 45 vertices at `+0.5 m` where the
source has its mass at `−0.5 m`. **Caught by an adversarial review agent**
that refused to extrapolate the Y-only evidence while verifying the
mirrored-variant design, and confirmed by independent byte reads. The fix
swapped the bake to `scale_mesh(−1,−1,1)` — and the X-count assertions above
make a fourth revision of this kind impossible to ship quietly.

**The compounding lesson:** each revision was falsified exactly where the
previous revision's evidence stopped. Metadata → absolute floats → the second
axis. The canary mesh is asymmetric on all three axes for a reason; assert
all of them.

## Verification anchors (re-run if the engine/SDK changes)

- `Tests/ue/export_sm_letterf.py` → FBX + `SM_LetterF.ue_reference.json`.
- `Tests/ue/probe_m2_fbx_handedness.py` → stage-2 negation, measured.
- `Tests/ue/probe_m2_mirror2.py` → winding under a det −1 `scale_mesh`.
- `Tests/ue/probe_mirror_bake.py` → the det +1 bake: mirror-X FBX, signed
  volume preserved, no manual winding flip.
- `Tests/m2/test_m2_artifacts.py` → the product byte-pattern assertions
  (scale + Y mirror + X counts, base and `#mx` variant) and the FBX
  intermediate checks.
- `Tests/m1/test_lane_a.py` → the transform half: determinant sign, rotation
  equivariance, composition homomorphism, sign-folding matrix identity.

## Interaction with Lane A (transforms)

Lane B touches **geometry only**. Actor transforms are Lane A: positions ÷100
with Y negated, rotations conjugated by the same map (negate the quaternion's
x and z), scale positive always — negative UE scales are folded into the
rotation, with true mirrors carried by `#mx` mesh variants (`ueo3de/lane_a.py`,
`MAPPING.md`). Each manifest records both conventions (`units.lane_a_rule`,
`units.lane_b_rule = "negate_y_scene_rz180"`) and the importer refuses a
document that does not match, rather than silently importing a mirrored or
mis-scaled level.
