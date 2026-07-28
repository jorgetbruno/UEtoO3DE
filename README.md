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
- `Schema/manifest.schema.json` — the interchange contract (plan constraint 7).
- `Tests/ue/` — UE Editor Python scripts (fixture builder, S0.2 export, manifest export,
  API probes) + `run_ue_python.bat`.
- `Tests/o3de/` — O3DE headless test scripts + `run_s0_1.bat`.
- `Tests/m1/` — the M1 acceptance suite; `run_m1.bat` runs all of it and CI asserts on
  its exit code. `golden/Fixture_01.expected.json` is the M1 contract and is regenerated
  only by an explicit, reviewed commit (`test_m1_acceptance.py --update-golden`).
- `Exports/` — interchange output (manifest, FBX, textures; generated, not committed).
- `MAPPING.md` / `LANE_B.md` / `DIVERGENCES.md` (started at M3, two-column) — the documentation contract defined by the plan.

## Running the tests

```
Tests\m1\run_m1.bat          M1: UE export -> property tests -> validator -> golden diff
Tests\m2\run_m2.bat [--cold] M2: export -> stage -> AP -> import -> prefab assertions
Tests\m3\run_m3.bat          M3: detection tests -> seam guard -> simulated smoke import -> gem regression
Tests\m4\run_m4.bat          M4: material/texture artifacts -> assignments in the saved prefab
Tests\m5\run_m5.bat          M5: light conversion + write order -> lights in the saved prefab
Tests\m6\run_m6.bat          M6: sky/fog/post-process mapping -> environment in the saved prefab
Tests\m7\run_m7.bat [dir]    M7: terrain contract -> sphere drop on the imported terrain
Tests\m8\run_m8.bat          M8: skeletal frame math -> .actor/.motion products -> playback by frame capture
Tests\m9\run_m9.bat          M9: Fixture_02 export -> instance/spline/LOD/decal/camera assertions -> import readbacks
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

## O3DE test projects (outside this repo, per O3DE convention)

- `C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt` — JoltPhysics enabled, PhysX5 disabled,
  EditorPythonBindings enabled. Build: `cmake -S . -B build/windows -G "Visual Studio 17 2022"`,
  `cmake --build build/windows --config profile --parallel`.
- `UEtoO3DETest-PhysX` — arrives in M3b.

## Headless test pattern (Global Constraint 10)

`Editor.exe --project-path=<proj> -BatchMode -autotest_mode --runpython <test>.py` — the script
writes `RESULT: PASS`/`RESULT: FAIL` to a result file and CI asserts on the **process exit code**,
never on console text.
