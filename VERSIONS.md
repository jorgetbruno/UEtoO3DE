# VERSIONS.md — engine version support, and what widening it would cost

**v1 targets UE 5.8 and O3DE 26.05, pinned.** The plan asks for UE 5.4–5.8 to be
*evaluated as a follow-on, not as v1 scope*; this is that evaluation.

## Why the pin is not laziness

Version sensitivity is not hypothetical here. Three concrete breaks were hit
during development, each of which silently produced wrong output or no output at
all rather than an error naming a version:

| What | Symptom |
|---|---|
| `anim_to_play` moved into `animation_data` (a `SingleAnimationPlayData` struct) | The property simply does not exist in 5.8; reading it returns nothing and every skeletal actor exports with no animation. |
| `StaticMeshEditorSubsystem` is `None` inside commandlets | Not a version difference but the same class of trap: an API that exists, resolves, and returns `None` in one host. |
| `unreal.Transform(rotation=...)` wants a `Rotator`, not a `Quat` | A `TypeError` at the call site, which is the *good* case. |

None of these would be caught by "it imports without an exception". That is why
the exporter's API surface is worth counting before promising five versions.

## The surface a widening has to re-verify

Measured against the exporter package (`ueo3de/`):

| Surface | Count | Risk |
|---|---|---|
| distinct `unreal.*` symbols | 65 | Renames and moves between minor versions. |
| named editor properties read via `get_editor_property` | 21 | **Highest.** A renamed property reads as absent, not as an error — the `anim_to_play` failure mode. |
| GeometryScript entry points | 11 | GeometryScript was Experimental through 5.3 and its Python surface churned; Lane B's bake depends on `scale_mesh` and `copy_mesh_from_component` behaving exactly as measured. |
| FBX export path | `AssetExportTask` + `FbxExportOption` | The Lane B contract depends on UE's FBX writer negating Y (LANE_B.md). If a version changes that, geometry imports **mirrored** and every suite still passes except the artifact tests. |

Direct attribute reads on UE objects (`component.static_mesh`, `light.intensity`,
…) are additional surface not counted above and fail the same silent way.

## The decisive structural constraint

UE assets are **forward-compatible only**: 5.8 opens a 5.4 asset and upgrades it;
5.4 cannot open anything 5.8 has saved. `UEtoO3DEFixture` and every level in it
were authored and saved in 5.8, so they cannot be opened by 5.4, 5.5, 5.6 or 5.7
at all — not "with warnings", not at all.

So widening is not "run the existing suites against another engine". It requires
**authoring the fixture project in the oldest supported version** and letting the
newer ones upgrade it on open. Every acceptance golden would then be produced by
the oldest version and asserted against by all of them, which is the right shape
but a different project setup from the one that exists.

## What it would actually take

1. Rebuild `UEtoO3DEFixture` in the oldest target (5.4), including `Fixture_01`,
   `Fixture_02` and the M8 skeletal canaries. The goldens are regenerated once,
   from 5.4, and reviewed.
2. Run the probe scripts already in `Tests/ue/` per version — they exist precisely
   to answer "what does this API do in this build" and are the cheapest part.
3. Add a version column to the probe results and treat every difference as a
   mapping decision, the way `DIVERGENCES.md` already treats backend differences.
4. Pin the FBX/Lane B measurement per version (`Tests/ue/export_lane_b.py`), since
   a change there is invisible until geometry comes out mirrored.

Estimated cost is dominated by (1), not by code changes.

## Recommendation

Keep v1 at 5.8. Widening is a follow-on with a clear shape and one hard
prerequisite (a 5.4-authored fixture), and it should be driven by an actual user
on 5.4 rather than speculatively — the version-specific breaks found so far were
all discovered by *running* against a version, never by reading the API.
