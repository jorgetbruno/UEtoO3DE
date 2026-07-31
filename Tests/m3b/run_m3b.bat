@echo off
rem run_m3b.bat - the M3b acceptance run (plan v2.2, M3b).
rem
rem The SAME adapter contract, asserted on BOTH backends:
rem   1. Jolt  project: detection + capabilities + shape rest heights +
rem      kinematic + trigger pass-through
rem   2. PhysX project: the identical assertions, plus the PhysX-specific
rem      honesty checks (no trimesh advertised, mesh collider refuses loudly)
rem
rem Each run pins UEO3DE_EXPECT_BACKEND, so a project that silently resolved
rem the OTHER backend fails instead of passing assertions about a backend
rem nobody is testing ("available != active", constraint 5).
rem
rem The test drives the adapter DIRECTLY and builds its entities in-session,
rem so it needs no staged assets or AP products in either project.
rem CI asserts on THIS script's exit code, never on console text.
setlocal EnableExtensions

set "REPO=%~dp0..\.."
call "%REPO%\Tests\paths.cmd"
if errorlevel 1 exit /b 2
set "PY=python"
set "AP=%O3DE_BIN%\AssetProcessorBatch.exe"
set "JOLT=%O3DE_PROJECT_JOLT%"
set "PHYSX=%O3DE_PROJECT_PHYSX%"

rem Steps 2 and 4 import a REAL manifest, so both projects need the fixture
rem staged and its products built. Staging is idempotent and AP is a no-op
rem when nothing changed, so this is cheap on repeat runs -- and without it
rem the PhysX project fails in wait_for_asset (correctly, but confusingly)
rem rather than testing anything about the backend.
echo === 0/8  stage the fixture into both projects ===
%PY% "%REPO%\Tests\m2\m2_stage.py" --project "%JOLT%" >nul
if errorlevel 1 goto :failed
"%AP%" --project-path="%JOLT%" --platforms=pc >nul 2>&1
if errorlevel 1 goto :failed
%PY% "%REPO%\Tests\m2\m2_stage.py" --project "%PHYSX%" >nul
if errorlevel 1 goto :failed
"%AP%" --project-path="%PHYSX%" --platforms=pc >nul 2>&1
if errorlevel 1 goto :failed

echo === 1/8  Jolt project ===
set "UEO3DE_EXPECT_BACKEND=jolt"
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_backend_smoke.py" "%REPO%\Tests\m3b\results\m3b_jolt_result.txt" "%JOLT%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_jolt_result.txt
  goto :failed
)

echo === 2/8  Jolt project: real manifest import ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_manifest_import.py" "%REPO%\Tests\m3b\results\m3b_jolt_import_result.txt" "%JOLT%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_jolt_import_result.txt
  goto :failed
)

echo === 3/8  PhysX project ===
set "UEO3DE_EXPECT_BACKEND=physx"
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_backend_smoke.py" "%REPO%\Tests\m3b\results\m3b_physx_result.txt" "%PHYSX%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_physx_result.txt
  goto :failed
)

rem The step that matters most: a REAL manifest through physics_build on the
rem backend that cannot bake render-mesh colliders. Before M3b's review this
rem aborted the entire import, and nothing anywhere ran it.
echo === 4/8  PhysX project: real manifest import ===
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_manifest_import.py" "%REPO%\Tests\m3b\results\m3b_physx_import_result.txt" "%PHYSX%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_physx_import_result.txt
  goto :failed
)

rem A SCALED entity, on both backends. Every other fixture in the repo sits at
rem scale 1, where scale and scale-squared are the same number -- which is why
rem the importer squared collision on every scaled entity for months with every
rem suite green. This step is the one that fails when that returns.
echo === 5/8  Jolt project: scaled entity authored and measured ===
set "UEO3DE_EXPECT_BACKEND=jolt"
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_scale_acceptance.py" "%REPO%\Tests\m3b\results\m3b_jolt_scale_result.txt" "%JOLT%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_jolt_scale_result.txt
  goto :failed
)

echo === 6/8  PhysX project: scaled entity authored and measured ===
set "UEO3DE_EXPECT_BACKEND=physx"
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_scale_acceptance.py" "%REPO%\Tests\m3b\results\m3b_physx_scale_result.txt" "%PHYSX%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_physx_scale_result.txt
  goto :failed
)

rem Steps 2 and 4 assert that the right components and asset references were
rem WRITTEN. These two load the SAVED prefab in a fresh session and ask the
rem physics system what it actually built -- the difference between "the
rem colliders were authored" and "the colliders exist in the running world",
rem which is where an unfinished bake and a dead asset reference both hide.
echo === 7/8  Jolt project: the imported level actually collides ===
set "UEO3DE_EXPECT_BACKEND=jolt"
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_level_collides.py" "%REPO%\Tests\m3b\results\m3b_jolt_collides.txt" "%JOLT%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_jolt_collides.txt
  goto :failed
)

echo === 8/8  PhysX project: the imported level actually collides ===
set "UEO3DE_EXPECT_BACKEND=physx"
call "%REPO%\Tests\o3de\run_o3de_python.bat" "%REPO%\Tests\m3b\m3b_level_collides.py" "%REPO%\Tests\m3b\results\m3b_physx_collides.txt" "%PHYSX%"
if errorlevel 1 (
  echo   see Tests\m3b\results\m3b_physx_collides.txt
  goto :failed
)

echo.
echo RESULT: PASS
endlocal & exit /b 0

:failed
echo.
echo RESULT: FAIL
endlocal & exit /b 1
