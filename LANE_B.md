# LANE_B.md — Geometry orientation/scale contract (Lane B)

**Status: LOCKED (units M0/S0.2, handedness M2 — both verified empirically).**
The handedness question M1 raised is closed; see "Who applies the reflection"
below. The answer is not the one M1 predicted.

Every mesh milestone depends on this file. Measured with `SM_LetterF` exported
from UE 5.8 and processed by O3DE 26.05 SceneAPI. Raw evidence:
`Exports/LaneB/SM_LetterF.ue_reference.json`,
`Tests/ue/probe_fbx_globalsettings.py` output, AP product artifacts
(`*.abdata.json`, `*.procprefab`), and position-buffer deltas
(`Tests/o3de/results/laneb/`).

> **The mesh changed in M1.** As baked in M0, `SM_LetterF` was 100×25×200 cm with
> centroid `[0, 0, 132.5]` — mirror-symmetric about X and Y, because
> `GeometryScriptPrimitiveOriginMode.BASE` centers each box in X and Y and the
> three boxes stacked concentrically. A level mirrored left-to-right passed every
> assertion the canary exists to fail. It is now 100×50×200 cm with centroid
> `[-20.97, 6.59, 144.52]`, asymmetric about all three planes
> (`Tests/ue/rebuild_letter_f.py`). **The S0.2 conclusions below are unaffected** —
> they concern units and axes, which the new mesh measures identically — but the
> quoted vertex ranges are the old mesh's and are kept as the original evidence.

## What each stage actually does (measured, not assumed)

| Stage | Behavior | Evidence |
|---|---|---|
| UE 5.8 FBX export (default `FbxExportOption`) | Units written **verbatim** (cm, Z-up, `UnitScaleFactor=1.0`), but **Y is negated** — the exporter performs the left- to right-handed conversion itself. Header declares `UpAxis=Z (sign +1)`, `FrontAxis=Y (sign −1)`, `CoordAxis=X (sign +1)` — note the −1 on Front | `probe_m2_fbx_handedness.py`: UE source asset y `[−12.5, 37.5]` → FBX y `[−37.5, 12.5]`. See the correction note below |
| SceneAPI import (default manifest) | **No unit conversion, no axis rotation, no mirroring.** Product mesh = source numerically (cm values treated as meters). No correction node inserted (procedural prefab root = identity) | `abdata.json` dimension [100,25,200]; `*_fbx.procprefab` TransformComponent identity; default mesh group carries an identity `CoordinateSystemRule` (useAdvancedData, no fields) |
| SceneAPI with scale rule (the fix below) | Vertex data scaled by the factor | Buffer delta: `scale:2.0` → 204 vertex components ×2.0; `scale:0.01` → same 204 components ×0.01 |

## The correction (what the importer must generate)

Default SceneAPI output is **100× too large** (cm read as meters). No axis rotation or
mirror correction is needed — only unit scale. For every exported FBX, the importer
(M2) writes an `.assetinfo` sidecar next to the source FBX containing a mesh group
with an **advanced CoordinateSystemRule with `scale: 0.01`**, plus a bare LodRule:

```json
{"values":[{
  "$type":"{07B356B7-3635-40B5-878A-FAC4EFD5AD86} MeshGroup",
  "name":"<mesh_group_name>",
  "nodeSelectionList":{"selectedNodes":["RootNode.<FBXNodeName>"],"unselectedNodes":[]},
  "rules":{"rules":[
    {"$type":"MaterialRule"},
    {"$type":"CoordinateSystemRule","useAdvancedData":true,"originNodeName":"","scale":0.01},
    {"$type":"{6E796AC8-1484-4909-860A-6D3F22A7346F} LodRule"}
  ]}
}]}
```

Notes on the schema (learned the hard way, do not "simplify"):

- **Node paths are `RootNode.<NodeName>`** (dot-separated, FBX root prefix included).
  A wrong path yields a job failure: "No valid ModelLodAssets have been added".
- The **LodRule must be present** (bare, no `nodeSelectionList` member) or the job
  fails the same way.
- `scale` is a plain float in advanced mode (`useAdvancedData: true`).
- Reference example of a full real-world `.assetinfo` (multiple groups, procprefab):
  `C:/Users/jorge/O3DE/Projects/FirstPersonProject/Assets/Art/LightSwitch.fbx.assetinfo`.

## Verification anchors (re-run if the engine/SDK changes)

- `Tests/ue/export_sm_letterf.py` → FBX + `SM_LetterF.ue_reference.json`.
- `Tests/ue/probe_fbx_globalsettings.py` → FBX header + stored vertex ranges.
- Buffer-delta procedure (documented in M0 notes): diff the `*_position0.fbx.azbuffer`
  between a default manifest and a ruled manifest; float ratios cluster at the rule factor.
- Mirror/handedness watch: the vertex-level mirror assertion lives in **M2's acceptance
  test** (F-mesh world AABB + centroid vs UE reference). S0.2 found no rotation or
  mirror anywhere in the chain — which, across a handedness change, is itself the
  defect described above, not a clean bill of health.
- M1 already asserts the transform half: `Tests/m1/test_lane_a.py` checks that the
  basis map's determinant is negative and that the quaternion rule is equivariant with
  vector rotation, and `Tests/m1/test_m1_acceptance.py` checks the F mesh's converted
  bounds against `SM_LetterF.ue_reference.json` and its per-axis asymmetry.

## Interaction with Lane A (transforms)

Lane B touches **geometry only** (vertex data inside the FBX/product). Actor transforms
are Lane A: positions ÷100 with **Y negated**, rotations conjugated by the same map
(negate the quaternion's x and z), scale untouched and always positive. The rule is
implemented in `ueo3de/lane_a.py`, documented in `MAPPING.md`, and property-tested in
`Tests/m1/test_lane_a.py`.

Both lanes now apply the same basis map — Lane A in the exporter's arithmetic, Lane B
in UE's FBX writer — which is why the imported level is mirror-free. Each manifest
records which convention produced it (`units.lane_a_rule`, `units.lane_b_rule`) and the
importer refuses a document that does not match, rather than silently importing a
mirrored level.

## Who applies the reflection (settled in M2)

UE is left-handed and O3DE is right-handed, so the numeric map between them must have
**determinant −1**. No rotation has determinant −1, so an earlier revision of this file
claiming "a Lane A rotation about the Z axis is all the handedness correction needs"
was wrong; that claim stays withdrawn. Any determinant +1 map — including "copy the
numbers and divide by 100" — renders the level as a perfect mirror: self-consistent,
geometrically valid, and backwards.

M1 predicted that the exporter would therefore have to bake the reflection into the FBX
itself. **It does not, because UE's FBX exporter already applies it.** Measured
(`Tests/ue/probe_m2_fbx_handedness.py`), exporting the same mesh twice:

```
UE source asset      y = [-12.500, 37.500]
FBX from source      y = [-37.500, 12.500]   <- exporter negated Y
baked mirrored asset y = [-37.500, 12.500]
FBX from mirrored    y = [-12.500, 37.500]   <- second mirror cancelled it out
```

So the pipeline is Lane-A-consistent with **no mirroring of our own**:

| Stage | Applies |
|---|---|
| UE FBX export | negate Y (left- → right-handed) |
| SceneAPI + `.assetinfo` | ÷100 (units only) |
| **net** | `(x, −y, z) / 100` = exactly Lane A |

`mesh_export.py` therefore exports source assets directly. A revision that mirrored
them first shipped briefly during M2 and produced perfectly cancelled reflections —
the FBX came back with the original UE geometry and nothing downstream noticed. What
caught it was checking the written file rather than the intention, which is now a
permanent step: `Tests/ue/export_fixture.py` re-reads every FBX it writes and compares
its bounds against `lane_a.convert_position` of the source asset's bounds.

**Why S0.2 said "verbatim".** It was measuring the M0 canary, which was symmetric about
Y (`y ∈ [−12.5, 12.5]`), so a Y negation was invisible in its vertex ranges. The same
blind spot that made the mirror canary useless made the exporter look verbatim. The
rebuilt canary is asymmetric on all three axes, which is what made this measurable.
The FBX header's `FrontAxisSign = −1` was the clue sitting in the original evidence.

### One link measured indirectly

Nothing here reads vertices back out of the O3DE **product**. O3DE 26.05 reflects no
bounds API to Python (`BoundsRequestBus` has no binding — measured in M0) and the
product `.azbuffer` is compressed, so there is no supported way to do it yet. What
covers that step instead: the `.assetinfo` assertions (`scale: 0.01`, correct
`RootNode.<node>`, LodRule present) in `Tests/m2/test_m2_artifacts.py`, the Asset
Processor reporting zero failed jobs, and `m2_acceptance.py` confirming each Mesh
component resolves to the expected product and reports non-zero geometry. If a
reflected bounds API appears in a later O3DE release, close this by asserting product
bounds directly.
