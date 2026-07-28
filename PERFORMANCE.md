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
| Wall clock | **73 s** (1 min 13 s) |
| Per entity | **25.2 ms** |
| Entities created | 2905 / 2905 |
| Physics bodies | 2787 |
| Mesh colliders baked | 2501 |
| …of which reached the prefab with geometry | **2501** — checked, not assumed |
| Materials assigned | 2791 (2904 slots) |
| Product assets waited for | 224 |
| Saved prefab on disk | **62.04 MB** |

### Where the time actually goes

`import_level` carries a phase stopwatch, and `m11_realworld.py` fails the run
if the phases account for less than 95% of the wall clock — so this table
cannot quietly omit anything. It accounted for 73.3 s of 73.3 s.

| Phase | Seconds | Share |
|---|---:|---:|
| settle: collider bakes | 20.4 | 27.8% |
| save prefab | 12.1 | 16.5% |
| stage + resolve product paths | 10.9 | 14.8% |
| create entities | 10.2 | 14.0% |
| physics authoring | 8.9 | 12.2% |
| materials | 7.8 | 10.6% |
| open level | 1.9 | 2.6% |
| everything else (ledger, bake verification, re-import diff, lights, environment, skeletal, decals, cameras, backend detect, asset wait) | 1.1 | 1.5% |

## Four attributions, all of them wrong until something measured them

Each correction here was found by measuring, and each one uncovered the next.
They are kept because the pattern is the point: every belief below was reached
by reading the code carefully, and every one was wrong.

| Believed | Measured |
|---|---|
| "Dominated by the collider bakes" | physics authoring is **12.2%** |
| then: materials — probably the uncached component-type lookup | that lookup: **3%** of the materials phase |
| then: the settle needs 41,040 frames | it needs **under 30** |
| then: what is left of `model_wait` is models streaming in | it is one **tick**, and it was being paid 1217 times |

### 1. The poll quantum (−36%)

Sub-timings inside the materials phase put **95% of it in one place** — not the
per-entity assignment work, but a *poll granularity*. `MODEL_READY_POLL_FRAMES`
was 30, and it is a quantum rather than a budget: every multi-material entity's
model was unready on the first check and ready well inside one quantum, so each
was charged all 30 frames. The counters make it exact — 1217 multi-material
entities, 1217 that waited, 1217 × 30 = 36,510 frames burned.

Dropping the quantum to 2 (the 600-frame **cap is unchanged**) took the import
from 806.2 s to 519.6 s with the assignment counts identical.

### 2. The settle (−70% again), and a silent defect underneath it

The settle before serialization was then 76.7% of the import, and it was a
blind formula — `60 + 5·bakes + 5·assigned + 5·slots + 10·skeletal`, 41,040
frames on this level — that had grown across three rounds of tuning a
`CreatePrefabInMemory` failure which turned out not to be a streaming race at
all, but a stale prefab instance in the scratch level. Its terms were never
re-measured once that cause was found.

**The first experiment found a bug, not a saving.** Importing with
`UEO3DE_SETTLE_FRAMES=0` produced a prefab with **2486 of 2501** collider
bakes. The import reported PASS, raised no error, and `mesh_colliders` read
2501 in *both* runs.

A Jolt mesh collider bakes on its component's tick and the result is serialized
into the prefab as `ShapeConfiguration.CookedData`. Serialize before the bake
finishes and the component is written out fully configured with **no geometry**:
a collider that collides with nothing, in a file that saved cleanly. The 15
lost were the heaviest meshes in the level — Landscape, whose cooked data is
3 MB, and SM_Mountain_3 at 262 KB. Nothing in the importer could see it, and
nothing in this repo's twelve suites went red.

**So the settle should become a readiness poll.** Four probes went looking for
something to poll, and all four came back negative:

| Probe | Result |
|---|---|
| every reflected property on a Jolt Mesh Collider | 17 paths, none mentions cooking, baking or shape readiness |
| baked vs unbaked entities, side by side in one session | **every readable property identical** |
| the physics request buses | `SimulatedBodyComponentRequestsBus`, `ColliderComponentRequestBus` do not answer for editor entities |
| re-flush the in-memory template after the bakes land | 12 flushes over 3600 further frames: 2486/2501, unchanged at every step |
| re-create the prefab after settling more | O3DE refuses: *"Creating prefab as an override edit is currently not supported"* |

The bake is invisible from Python, the template is a snapshot that does not
track late bakes, and the one snapshot a session gets cannot be retaken. There
is nothing to wait on and nothing to repair afterwards.

**What was done instead.** Two things, and the first matters more than the
speed:

1. **The saved file is now read back and checked.** Any mesh collider that
   arrived without geometry is reported as `PHYS_COLLIDER_NOT_BAKED` (error),
   one per collider, naming each entity. A constant that is one day too small
   now fails loudly instead of silently. `Tests\perf\run_perf.bat` guards this
   on real content, with a planted blanked bake as the control that proves the
   detector is still detecting.
2. **The constant was measured rather than grown.** `UEO3DE_SETTLE_FRAMES`
   overrides it, which is how this table exists:

| settle | bakes in the file | import |
|---:|---|---:|
| 0 | 2486 / 2501 — **15 lost, silently** | 139.6 s |
| 30 | 2501 / 2501 | 111.6 s |
| 120 | 2501 / 2501 | 112.9 s |
| 200 | 2501 / 2501 | 113.4 s |
| 1500 | 2501 / 2501 | 124.7 s |
| 41,040 (the old formula) | 2501 / 2501 | 519.6 s |

The two material terms were **measured to guard nothing**: at settle=0 every
material asset id in the prefab was identical to the control, and only cooked
collider data differed. They existed against a serialization throw, which has
its own retry and which did not occur at settle=0 either. They are gone.

The formula is now `300 + bakes/2 + 10·skeletal` — 1550 frames here, some fifty
times the measured need. That margin is deliberate: the failure cannot be
repaired once the prefab is written, one level on one machine is a thin basis
for a threshold, and 20 s of a 73 s import is a cheap premium. Collider count
is a **proxy** for bake work, which is really geometry volume; the proxy is
acceptable only because being wrong is now loud. The skeletal term is unchanged
and remains **unmeasured** — L_Showcase has no skeletal entities.

### 3. The wait that should have been shared (−50% again)

With the settle cut, materials was the largest phase again and 65.5 s of it was
still `model_wait`. The obvious reading — models genuinely streaming in — is
wrong, and the counters said so before any code changed.

A finer split inside the wait separated the **probe** calls from the **idle**
frames, because the two imply opposite fixes: probes are per entity and buy
nothing for anyone else, while idle frames are shared — a frame spent waiting
for one entity advances every other entity too. On a 400-entity sample:
`wait_idle` **1.1 s**, `wait_probe` **0.0 s**. The frames were the whole cost
and the probes were free.

Then the decisive counter. Every one of the 1217 multi-slot entities was
unready on its first probe and ready after exactly one quantum — never two,
never zero, *including the last entity processed*, long after every model in
the level had finished streaming. Streaming does not behave like that. A tick
that must elapse between adding a component and its rows appearing does, and a
tick is shared.

So the assignment became two passes: add every Material component, wait **once**
for the level, then read the rows and assign. `wait_for_model_rows` re-probes
only the stragglers each round, so the bound stays per entity rather than in
aggregate, and the fallback for an entity that never becomes ready is unchanged.

| | before | after |
|---|---:|---:|
| frames idled waiting for rows | 2434 | **2** |
| materials phase | 73.2 s | **7.8 s** |
| probe calls | 2434 | 2434 (still free) |
| slots / materials assigned | 2904 / 2791 | 2904 / 2791 (identical) |

The probe count is unchanged on purpose: probing is what makes the wait honest
per entity, and it costs nothing, so there was no reason to trade correctness
for it.

### The three changes together

| | before | after quantum | after settle | after batching |
|---|---:|---:|---:|---:|
| total import | 806.2 s | 519.6 s | 146.8 s | **73.3 s** |
| per entity | 277.5 ms | 178.9 ms | 50.5 ms | **25.2 ms** |
| materials phase | 408.8 s | 72.6 s | 73.2 s | **7.8 s** |
| settle phase | 347.8 s | 398.7 s | 31.0 s | 20.4 s |
| frames idled for model rows | 36,510 | 2,434 | 2,434 | **2** |
| slots / materials assigned | 2904 / 2791 | 2904 / 2791 | 2904 / 2791 | 2904 / 2791 |
| collider bakes in the file | 2501 | 2501 | 2501 | 2501 |

**11× faster, and the content is identical.** Not "the counters match" —
`Tests\perf\prefab_diff.py` compares the two saved prefabs entity by entity on
component types, every asset id, every transform and the length of every baked
collider, keyed by name because entity ids are minted per run and comparing
duplicated names as a multiset because a saved prefab legitimately contains
them. Verdict after every one of the three changes: **EQUIVALENT**, 0 differences across 2906 entities.

(That comparator is mutation-tested: blank one `CookedData` or alter one asset
guid in a copy and it must report the difference, because a comparator that
cannot fail proves nothing about the run where it passed.)

## Memory (editor process working set)

| Point | Working set | Δ |
|---|---|---|
| Before import | 845 MB | — |
| Peak, immediately after import | **4751 MB** | +3906 MB |
| Level open, prefab instantiated | **3184 MB** | +2339 MB |

The two deltas measure different things and both matter. **+3906 MB is the peak
cost of running the import** — the scratch level, 2905 live entities, every
streamed model and material, and the prefab template all resident at once. That
is the figure that decides whether the import fits in a machine's RAM. **+2339 MB
is what the finished level costs** once the editor has reopened a level and
instantiated the prefab; the difference is the import's working state being
released.

An import of this size therefore wants ~4 GB of headroom, which is worth knowing
before pointing it at something four times larger.

## Simulation sanity check

| Measure | Figure |
|---|---|
| Instantiate the saved prefab | 7.5 s |
| 300 frames in game mode | 2.5 s (**8.34 ms/frame**) |

**This is a headless batch editor** (`-BatchMode -autotest_mode`), not a shipping
runtime, and the figure is a **cap, not a cost**. Four runs of this test, on
prefab content verified **identical** every time, reported:

    8.34, 16.68, 16.68, 8.34 ms

which is 119.9 Hz and 59.95 Hz — exactly 2:1, each reproduced to the
hundredth. A work measurement does not land on two discrete values and repeat
to four significant figures; a tick cap does. The level is not getting faster
and slower, it is being pinned to whatever cap the session came up under.

So it bounds nothing and must never be quoted as a frame rate. What it is good
for is what it is used for: 300 frames complete, and nothing pathological
happens with 2787 physics bodies present. Measuring the real cost would need a
profile-mode runtime with the cap off, which this repo does not build.

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
| `PHYS_COLLIDER_NOT_BAKED` | **0** | Every authored bake reached the file. This row reading 0 is now an assertion, not an assumption. |

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
Tests\m11\run_m11.bat                         figures
Tests\perf\run_perf.bat                       every authored bake reached the file
```

Figures land in `Tests\m11\results\figures.md` (JSON, gitignored — this file is
the committed record). To re-measure the settle rather than trust it:

```
set UEO3DE_SETTLE_FRAMES=0
Tests\o3de\run_o3de_python.bat Tests\perf\settle_sweep_point.py
```
