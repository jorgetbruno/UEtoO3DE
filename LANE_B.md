# LANE_B.md — Geometry orientation/scale contract (Lane B)

**Status: LOCKED (M0, spike S0.2, verified empirically on 2026-07-27).**
Every mesh milestone depends on this file. Measured with `SM_LetterF` (asymmetric,
100×25×200 cm, centroid z=132.5 cm) exported from UE 5.8 and processed by O3DE 26.05
SceneAPI. Raw evidence: `Exports/LaneB/SM_LetterF.ue_reference.json`,
`Tests/ue/probe_fbx_globalsettings.py` output, AP product artifacts
(`*.abdata.json`, `*.procprefab`), and position-buffer deltas
(`Tests/o3de/results/laneb/`).

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
  test** (F-mesh world AABB + centroid vs UE reference) — S0.2 found no rotation/mirror
  anywhere in the chain, and M2 is the tripwire if that ever changes.

## Interaction with Lane A (transforms)

Lane B touches **geometry only** (vertex data inside the FBX/product). Actor transforms
are Lane A: positions/scales ÷100 in the UE exporter, handedness corrected in the
rotation (never negative scale). Because Lane B keeps the mesh's local axes identical
to UE's (same numbers, Z-up preserved), a Lane A rotation about the Z axis is all the
handedness correction needs — to be pinned down and documented in `MAPPING.md` at M1.
