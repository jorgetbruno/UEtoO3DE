# PERFORMANCE.md — a real level, ported end to end

The plan's M11 asks for "one medium UE demo level ported end to end; performance
sanity check in O3DE game mode; entity-count and memory figures recorded". These
are those figures, produced by `Tests\m11\run_m11.bat` (`m11_realworld.py`), which
fails rather than skipping if the level or any figure is missing.

Every other measurement in this repo comes from a fixture built to be measurable.
This one is third-party asset-pack content authored for a different engine by
people who had never heard of this tool, which is the only kind of input that
finds what fixtures cannot.

## The level

| | |
|---|---|
| Level | `L_Showcase` (UE 5.8) |
| Entities in the manifest | 2905 |
| Assets | 453 |
| Export warnings | 619 |
| Content | third-party asset packs, a Landscape, foliage, 2161 root-level actors |

## Import

Measured on a **clean slate**: the test deletes any prefab and ledger left by a
previous run and asserts the import reported `first_import`, so these describe
porting a level rather than re-importing one. (The first version of this test
did neither and passed anyway — an adversarial review caught it. The figures
happened to be within 1% because the leftover prefab held no hand edits to
preserve, but the measurement was not entitled to be trusted.)

| Measure | Figure |
|---|---|
| Wall clock | **520 s** (8 min 40 s) |
| Per entity | **178.9 ms** |
| Entities created | 2905 / 2905 |
| Physics bodies | 2787 |
| Mesh colliders baked | 2501 |
| Materials assigned | 2791 (2904 slots) |
| Product assets waited for | 224 |
| Saved prefab on disk | **62.04 MB** |

### Where the time actually goes

`import_level` carries a phase stopwatch, and `m11_realworld.py` fails the run
if the phases account for less than 95% of the wall clock — so this table
cannot quietly omit anything. It accounted for 519.6 s of 519.6 s.

| Phase | Seconds | Share |
|---|---:|---:|
| **settle: collider bakes + asset streaming** | **398.7** | **76.7%** |
| materials | 72.6 | 14.0% |
| save prefab | 13.5 | 2.6% |
| stage + resolve product paths | 11.9 | 2.3% |
| create entities | 10.2 | 2.0% |
| physics authoring | 10.1 | 1.9% |
| open level | 1.9 | 0.4% |
| everything else (ledger, re-import diff, lights, environment, skeletal, decals, cameras, backend detect, asset wait) | <1 | ~0.1% |

#### What the stopwatch has already corrected

Two attributions in this document were wrong before anything measured them.

**"Dominated by the collider bakes."** Physics authoring is **1.9%**. That
sentence was a guess in a document that otherwise contains only measurements.

**Then: materials, at 50.7% of an 806 s import.** Sub-timings inside that phase
put **95% of it in one place** — not the per-entity assignment work, and not
the uncached component-type lookup (3%), but a *poll granularity*.
`MODEL_READY_POLL_FRAMES` was 30, and it is a quantum rather than a budget:
every multi-material entity's model was unready on the first check and ready
well inside one quantum, so each was charged all 30 frames. The counters make
it exact — 1217 multi-material entities, 1217 that waited, 1217 × 30 = 36,510
frames burned.

Dropping the quantum to 2 (the 600-frame **cap is unchanged**, so a genuinely
slow model still gets it) gives, at full scale:

| | before | after |
|---|---:|---:|
| total import | 806.2 s | **519.6 s** (−36%) |
| per entity | 277.5 ms | **178.9 ms** |
| materials phase | 408.8 s | **72.6 s** (−82%) |
| frames spent polling | 36,510 | **2,434** |
| slots / materials assigned | 2904 / 2791 | 2904 / 2791 (identical) |

Note the settle row *rose* (347.8 → 398.7 s) while the total fell by 286 s. The
two phases are coupled: the frames formerly burned polling in the materials
phase were also ticking the editor, so some asset streaming that used to finish
"for free" during those 36,510 frames now happens in the settle. Only the total
is meaningful.

#### The next target, with its arithmetic exposed

Settle is now 76.7%, and it is a **blind fixed wait**, not a measurement:

    frames = 60 + 5·bakes + 5·assigned + 5·slots + 10·skeletal
           = 60 + 5(2501) + 5(2791) + 5(2904) + 0  =  41,040 frames

At ~9.7 ms/frame that is ~398 s, which is the measured figure to within noise.
Nothing in it asks whether anything is *actually* still pending — it is the
same class of mistake as the 30-frame quantum, one size larger. Replacing it
with a real readiness check is the obvious next win, and needs care: the wait
exists because serializing mid-bake made `CreatePrefabInMemory` throw, and the
threshold was measured as cumulative rather than per-entity. A readiness signal
has to cover both the collider bakes and material/texture streaming before the
formula can go.

## Memory (editor process working set)

| Point | Working set | Δ |
|---|---|---|
| Before import | 847 MB | — |
| Peak, immediately after import | **5304 MB** | +4458 MB |
| Level open, prefab instantiated | **3312 MB** | +2465 MB |

The two deltas measure different things and both matter. **+4458 MB is the peak
cost of running the import** — the scratch level, 2905 live entities, every
streamed model and material, and the prefab template all resident at once. That
is the figure that decides whether the import fits in a machine's RAM. **+2465 MB
is what the finished level costs** once the editor has reopened a level and
instantiated the prefab; the difference is the import's working state being
released.

An import of this size therefore wants ~5 GB of headroom, which is worth knowing
before pointing it at something four times larger.

## Simulation sanity check

| Measure | Figure |
|---|---|
| Instantiate the saved prefab | 7.3 s |
| 300 frames in game mode | 2.5 s (**8.3 ms/frame**) |

**This is a headless batch editor** (`-BatchMode -autotest_mode`), not a shipping
runtime. 8.3 ms/frame says the level simulates at a plausible rate and nothing
pathological happens with 2787 physics bodies present. It is **not** a frame rate
and must not be quoted as one.

## Fidelity: what a real level actually loses

The honest scorecard, and the reason the warning catalogue exists:

| Code | Count | What it means here |
|---|---|---|
| `PHYS_SHAPE_APPROXIMATED` | **286** | ~10% of entities got a substituted collision shape — mostly non-uniform scale on spheres/capsules, which take the largest axis. |
| `PHYS_MESH_FROM_RENDER` | 14 | No simple collision in UE; a collider was baked from render geometry. |
| `XFORM_NONUNIFORM_SCALE_COMPONENT` | 14 | Non-uniform scale moved onto a separate component (and so does not reach children — see DIVERGENCES.md). |
| `MAT_SLOT_UNUSED` | 12 | The mesh asset lists a slot no render triangle uses. Nothing lost. |
| `ENV_SKYLIGHT_APPROX`, `ENV_FOG_APPROX`, `ENV_SKY_DUPLICATE`, `ENV_BLOOM_THRESHOLD_APPROX` | 1 each | The environment approximations described in DIVERGENCES.md. |
| `LIGHT_RADIUS_EXPLICIT` | 3 | UE's explicit attenuation radius pinned rather than derived. |
| `LIGHT_TEMPERATURE_DROPPED` | 1 | Colour temperature has no Atom equivalent. |

286 approximated collision shapes on one level is the single largest fidelity
cost, and it is *reported* rather than silent — which is the whole design. A
level ported to the PhysX backend instead would report considerably more, because
that backend cannot bake render-mesh colliders at all (DIVERGENCES.md, M3b).

## Reproducing

```
Tests\ue\export_level.bat                     export a real level from UE
python Tests\m2\m2_stage.py --project <p> --manifest Exports\<L>\manifest.json ^
                            --source-assets Exports\<L>\Assets
AssetProcessorBatch.exe --project-path=<p> --platforms=pc
set UEO3DE_EXPORT=D:\Gamedev\UEtoO3DE\Exports\<L>
Tests\m11\run_m11.bat
```

Figures land in `Tests\m11\results\figures.md` (JSON, gitignored — this file is
the committed record).
