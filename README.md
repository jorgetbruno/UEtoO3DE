# UEtoO3DE

Unreal Engine level/asset porter targeting **O3DE 26.05** (engine `2.6.0`), with selectable
physics backends: the **JoltPhysics gem** and stock **PhysX**. Scope: assets + scene layout +
physics. Blueprints/gameplay logic are out of scope.

The milestone plan (`ue-to-o3de-milestone-plan-v2.md`) is the source of truth. Work proceeds one
milestone per session; each session starts from that file.

## Environment pins

- **UE 5.8** at `D:\Epic Games\UE_5.8` (one pinned version, no version-conditional code).
- **O3DE 26.05** SDK at `C:\O3DE\26.05` (engine `o3de-sdk`, version `2.6.0`).
- **JoltPhysics gem** at `C:\Users\jorge\O3DE\Gems\JoltPhysics`.
- cmake: bundled with VS 2022 — `C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin` (not on PATH by default).

## Layout

- `UE/UEtoO3DEFixture/` — UE 5.8 fixture project hosting the `Fixture_01` acceptance level.
  - `Plugins/UEO3DEExporter/` — the exporter editor plugin (build:
    `RunUAT.bat BuildPlugin -Plugin=<path>.uplugin -Package=UE/Build/UEO3DEExporter -TargetPlatforms=Win64`,
    then copy `UE/Build/UEO3DEExporter/Binaries` into the plugin folder).
    - `Content/Python/ueo3de/` — the exporter itself. Everything except `ue_level.py`
      is pure Python with no `unreal` import, so the coordinate conversion, asset
      identity and warning catalogue are testable without an editor.
- `O3DE/Gems/UEImporter/` — the O3DE-side importer, packaged as a tool gem.
  `Editor/Scripts/ueimporter/` holds it; everything except `asset_wait` and
  `prefab_build` is pure Python and testable without an editor.
  `Editor/Scripts/bootstrap.py` is the gem's editor entry point and installs the
  **Tools → Import UE Manifest…** menu item; `install_gem.py` puts the gem into a
  project (see *Installing the gem* below).
- `Tools/` — the headless pipeline. `run_pipeline.bat` drives UE export → Asset
  Processor → O3DE import as one command with one exit code; `ue_export.py` and
  `o3de_import.py` are the two halves it invokes.
- `Schema/manifest.schema.json` — the interchange contract (plan constraint 7).
- `Tests/ue/` — UE Editor Python scripts (fixture builder, S0.2 export, manifest export,
  API probes) + `run_ue_python.bat`.
- `Tests/o3de/` — O3DE headless test scripts + `run_s0_1.bat`.
- `Tests/m1/` — the M1 acceptance suite; `run_m1.bat` runs all of it and CI asserts on
  its exit code. `golden/Fixture_01.expected.json` is the M1 contract and is regenerated
  only by an explicit, reviewed commit (`test_m1_acceptance.py --update-golden`).
- `Exports/` — interchange output (manifest, FBX, textures; generated, not committed).
- `MAPPING.md` / `LANE_B.md` / `DIVERGENCES.md` (started at M3, two-column) / `VERSIONS.md` —
  the documentation contract defined by the plan. `MAPPING.md`'s warning tables are not
  maintained by hand alone: `Tests\m11\test_docs.py` fails if a code exists in either
  catalogue and not in the table, if a severity drifts, or if a row survives a code's
  deletion.

## First run on a new machine

Every runner reads `Tests/paths.config` for the engine and project locations.
Copy the template and edit it:

```
copy Tests\paths.config.template Tests\paths.config
python Tests\paths.py            verifies every path exists; exit code is the verdict
```

An environment variable of the same name overrides the file, so CI can point a
run at a different project without editing anything:

```
set O3DE_PROJECT_JOLT=C:\ci\work\Jolt && Tests\m2\run_m2.bat
```

The config is gitignored; the template is committed. `REPO_ROOT` is deliberately
*not* a config key — it is derived from each script's own location, because a
value that can be computed cannot be configured wrong.

**Known debt:** every `.bat` and every Python file the suites actually run is
portable. About 34 one-off probe scripts under `Tests/ue/` and `Tests/o3de/`
still hardcode this developer's paths. They are historical evidence rather than
CI, so they were left alone rather than changed without being re-run — but a
probe you cannot run is worth less than one you can, and they should be
converted when next touched.

## Running the tests

```
Tests\m1\run_m1.bat          M1: UE export -> property tests -> validator -> golden diff
Tests\m2\run_m2.bat [--cold] M2: export -> stage -> AP -> import -> prefab assertions
Tests\m3\run_m3.bat          M3: detection tests -> seam guard -> simulated smoke import -> gem regression
Tests\m3b\run_m3b.bat        M3b: the same adapter contract asserted on BOTH backends (Jolt + PhysX)
Tests\m4\run_m4.bat          M4: material/texture artifacts -> assignments in the saved prefab
Tests\m5\run_m5.bat          M5: light conversion + write order -> lights in the saved prefab
Tests\m6\run_m6.bat          M6: sky/fog/post-process mapping -> environment in the saved prefab
Tests\m7\run_m7.bat [dir]    M7: terrain contract -> sphere drop on the imported terrain
Tests\m8\run_m8.bat          M8: skeletal frame math -> .actor/.motion products -> playback by frame capture
Tests\m9\run_m9.bat          M9: Fixture_02 export -> instance/spline/LOD/decal/camera assertions -> import readbacks
Tests\m10\run_m10.bat        M10: menus both sides -> import dialog -> re-import diff -> full headless pipeline
Tests\m11\run_m11.bat        M11: doc contract enforced -> a real level ported end to end with figures
Tests\o3de\run_s0_1.bat      M0 spike S0.1: prefab authoring from Python
```

`run_m4.bat`, `run_m5.bat` and `run_m6.bat` assert against the prefab
`run_m2.bat` produced, so run M2 first; `run_m8.bat` needs M2's staged +
AP-processed products too. `run_m7.bat` needs an EXPORTED LEVEL
THAT CONTAINS A LANDSCAPE (default `Exports\L_Showcase`, staged and
AP-processed) — the fixture cannot host one: spawning a Landscape in a
scripted session trips the engine's `!IsRunningCommandlet()` assertion, so
terrain coverage lives against real content and the suite fails hard when
that content is missing. Since M7, `export_level.bat` runs a FULL editor
session (`-ExecutePythonScript`) because terrain sampling needs the physics
scene commandlets don't have; since M8 `export_fixture.bat` does the same
because the native skeletal FBX exporter asserts on the render objects
commandlets lack (`MeshObject`). Both assert on the export result file.
NOTE (Windows): invoke `export_level.bat` from cmd or PowerShell, not Git
Bash — MSYS path conversion mangles the `/Game/...` package argument into
`C:/Program Files/Git/Game/...`.

NOTE (UE argument passing): when `-ExecutePythonScript=` carries **arguments**,
UE unescapes backslashes in the value. This repo's own path arrived at the
interpreter as `D:\GamedevtoO3DE\Tools_export.py` — `\U` and `\ue` consumed as
escape sequences — and failed as "Could not load Python file" naming a path
nobody typed. Use forward slashes for the script path whenever arguments follow
it (`Tools\run_pipeline.bat` does). The no-argument form is unaffected, which is
why only the M10 pipeline hit it.

The M8 fixture canaries import once from `Tests\ue\data\SK_Canary.fbx`
(CC0, Quaternius "Platformer Game Kit") via `Tests\ue\add_m8_skeletal.py`,
which also regenerates `Tests\m8\skel_reference.json` (the UE-side truth the
artifact test compares against).

`Fixture_02` (the M9 feature level: instanced meshes, a bent spline mesh, a
two-LOD mesh, a decal, a camera) is built once by
`Tests\ue\build_fixture_02.py` — FULL editor run (StaticMeshEditorSubsystem
is None in commandlets). Fixture_01 stays frozen per the plan.

Each `.bat` propagates a real exit code and CI must assert on that, never on console
text (plan constraint 10). `run_m2.bat --cold` deletes the staged sources and their
cache products first, so the run cannot pass on a warm cache alone.

The pure-Python parts need no editor: `python Tests\m1\test_lane_a.py`,
`python Tests\m1\validate_manifest.py --self-test`,
`python Tests\m2\test_m2_artifacts.py` and `python Tests\m5\test_light_build.py`
each run in about a second.

## Using it as a tool (M10)

**UE side.** With the plugin enabled, **Tools → Export Level to O3DE…** opens an
options dialog whose folder field carries a native browse button, then exports with
a progress bar. It exports the level *as it stands in memory*, deliberately: the
batch path reloads the map first, and doing that to the level someone is standing
in would discard their unsaved edits and export the older version from disk.

**O3DE side.** Install the gem into a project first:

```
python O3DE\Gems\UEImporter\install_gem.py --project <project-path>
python O3DE\Gems\UEImporter\install_gem.py --project <project-path> --check
```

Then **Tools → Import UE Manifest…** picks a manifest, names the prefab, and offers
the physics backend — pre-selected from detection and disabled when only one
backend resolves. The summary afterwards lists every warning with its meaning and
exports as `.txt`.

Installation is two steps for a reason worth knowing: registering and enabling the
gem is *not* enough for a gem with no compiled code. A prebuilt SDK editor mounts
the gems its build told it about, so the gem resolves by name yet is never mounted
and `bootstrap.py` is silently never read — the menu item simply does not appear,
with nothing in the log to say why. `install_gem.py` writes the one Registry entry
that would otherwise come from a project rebuild.

**Headless / CI.** One command, one exit code:

```
Tools\run_pipeline.bat /Game/Maps/L_Showcase D:\Exports\Showcase Showcase <project> [jolt|physx]
```

Re-running an import is incremental by default: entities are matched by manifest id
(a uuid5 of the UE actor path), removals are reported, and anything edited by hand
in O3DE is reported as `REIMPORT_ENTITY_CONFLICT` and **kept**. Pass
`--reimport=0` to author everything from the manifest and overwrite those edits.

Each import writes a ledger beside its prefab (`<Prefab>.ueimport.json`) recording
what that import *authored*. It is what makes hand-edit detection possible, so
**commit it with the prefab** — a teammate who has the prefab but not the ledger
gets `REIMPORT_LEDGER_MISSING` and their next import silently replaces any manual
fixes. See `DIVERGENCES.md` for what re-import does and does not track (transforms
yes; component and material edits no).

## O3DE test projects (outside this repo, per O3DE convention)

- `C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt` — JoltPhysics enabled, PhysX5 disabled,
  EditorPythonBindings enabled. Build: `cmake -S . -B build/windows -G "Visual Studio 17 2022"`,
  `cmake --build build/windows --config profile --parallel`.
- `C:\Users\jorge\O3DE\Projects\UEtoO3DETest-PhysX` — PhysX5 enabled, JoltPhysics **absent**,
  EditorPythonBindings enabled; otherwise gem-for-gem identical to the Jolt project, so anything
  that differs between them is attributable to the backend rather than the environment. Created
  with `o3de create-project` and built the same way. Note `cmake` is not on PATH by default —
  prepend the VS 2022 path pinned above, or `o3de.bat` fails with "Unable to calculate engine ID".

## Headless test pattern (Global Constraint 10)

`Editor.exe --project-path=<proj> -BatchMode -autotest_mode --runpython <test>.py` — the script
writes `RESULT: PASS`/`RESULT: FAIL` to a result file and CI asserts on the **process exit code**,
never on console text.
