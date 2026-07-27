# LANE_B.md — Geometry orientation/scale contract (Lane B)

**Status: units LOCKED (M0, spike S0.2, verified empirically on 2026-07-27).
Handedness OPEN — see "The gap this file does not close" below (raised in M1).**

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
| UE 5.8 FBX export (default `FbxExportOption`) | Geometry written **verbatim** in UE units/axes (cm, Z-up, LH). Header declares `UpAxis=Z (sign +1)`, `FrontAxis=Y (sign -1)`, `CoordAxis=X (sign +1)`, `UnitScaleFactor=1.0` (cm) | `probe_fbx_globalsettings.py`: FBX vertices x[-50,50] y[-12.5,12.5] z[0,200] = UE reference exactly |
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

## The gap this file does not close (raised in M1, owned by M2)

An earlier revision of this section claimed that "a Lane A rotation about the Z axis is
all the handedness correction needs". **That is wrong and the claim is withdrawn.**
UE is left-handed and O3DE is right-handed, so the numeric map between them must have
**determinant −1**. No rotation has determinant −1. Any determinant +1 map — including
"copy the numbers and divide by 100" — renders the level as a perfect mirror of the
original: self-consistent, geometrically valid, and backwards.

Lane A now applies that reflection (negate Y). **Lane B does not yet apply it to
geometry**, and it must, or meshes end up mirrored relative to their own placement:

- What S0.2 measured is still correct: SceneAPI applies **no** unit conversion, axis
  rotation or mirroring. Geometry arrives verbatim, which is exactly the problem —
  verbatim across a handedness change *is* the mirror.
- **SceneAPI cannot fix it.** `CoordinateSystemRule` in advanced mode offers a
  rotation and a single scalar `scale`. A scalar cannot be negative-per-axis and a
  rotation cannot reflect, so no `.assetinfo` can express the required map.
- Therefore the reflection has to be baked into the FBX at export time in UE:
  negate Y on vertex positions and normals **and flip triangle winding** (a reflection
  without a winding flip turns every face inside out).

M2 must decide and record which of these it ships:

| Option | Result | Cost |
|---|---|---|
| **Bake the Y-flip at FBX export** (expected) | faithful, mirror-free | one geometry pass in the exporter; winding must flip with it |
| Leave geometry verbatim and make Lane A the identity | whole world consistently mirrored | free, and wrong — this is the bug `SM_LetterF` exists to catch |

Until M2 closes this, `SM_LetterF` is the tripwire: it is now asymmetric about all
three planes (Y most importantly, since Y is the negated axis), so M2's mirror check
will fail loudly rather than pass vacuously.
