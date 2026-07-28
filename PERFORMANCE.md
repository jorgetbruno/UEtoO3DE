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
| Wall clock | **806 s** (13 min 26 s) |
| Per entity | **277.5 ms** |
| Entities created | 2905 / 2905 |
| Physics bodies | 2787 |
| Mesh colliders baked | 2501 |
| Materials assigned | 2791 (2904 slots) |
| Product assets waited for | 224 |
| Saved prefab on disk | **62.04 MB** |

### Where the time actually goes

`import_level` carries a phase stopwatch, and `m11_realworld.py` fails the run
if the phases account for less than 95% of the wall clock — so this table
cannot quietly omit anything. It accounted for 806.2 s of 806.2 s.

| Phase | Seconds | Share |
|---|---:|---:|
| **materials** | **408.8** | **50.7%** |
| settle: collider bakes + asset streaming | 347.8 | 43.1% |
| save prefab | 15.0 | 1.9% |
| create entities | 11.9 | 1.5% |
| physics authoring | 10.4 | 1.3% |
| stage + resolve product paths | 9.5 | 1.2% |
| open level | 2.0 | 0.3% |
| everything else (ledger, re-import diff, lights, environment, skeletal, decals, cameras, backend detect, asset wait) | <1 | ~0.1% |

**An earlier version of this document attributed the cost to the collider
bakes. That was wrong, and it was a guess.** Physics authoring is 1.3%.
**Material assignment is half the import** — 408.8 s across 2791 entities and
2904 slots, about 146 ms per entity — and it was not previously mentioned at
all. This is why the stopwatch exists.

The settle phase is a *combined* wait and not purely collider bakes either: its
budget is `60 + 5·bakes + 5·assigned + 5·slots + 10·skeletal` frames, so on this
level the material terms (5×2791 + 5×2904 = 28,475 frames) outweigh the collider
term (5×2501 = 12,505). Between the two rows, **materials account for a clear
majority of the wall clock.**

So the optimisation target is material assignment, in two places: the per-entity
assignment work itself, and the material-driven share of the settle budget.
Neither is the collider bake. A finer breakdown *inside* the materials phase is
the next measurement anyone optimising this should take, rather than repeating
the mistake above and reasoning about which part is slow.

## Memory (editor process working set)

| Point | Working set | Δ |
|---|---|---|
| Before import | 846 MB | — |
| Peak, immediately after import | **5215 MB** | +4368 MB |
| Level open, prefab instantiated | **3163 MB** | +2316 MB |

The two deltas measure different things and both matter. **+4368 MB is the peak
cost of running the import** — the scratch level, 2905 live entities, every
streamed model and material, and the prefab template all resident at once. That
is the figure that decides whether the import fits in a machine's RAM. **+2316 MB
is what the finished level costs** once the editor has reopened a level and
instantiated the prefab; the difference is the import's working state being
released.

An import of this size therefore wants ~5 GB of headroom, which is worth knowing
before pointing it at something four times larger.

## Simulation sanity check

| Measure | Figure |
|---|---|
| Instantiate the saved prefab | 7.8 s |
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
